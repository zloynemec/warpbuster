"""Explicitly confirmed OSM paths, after GPX planning and independent detection.

A confirmation is a caller assertion of the travelled route, not a uniqueness
claim inferred from non-exhaustive Valhalla alternatives or GNSS-derived signals.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import asdict, replace
from hashlib import sha256
from itertools import pairwise
from math import isfinite

from warpbuster.config import IntegrityConfig, OSMApplicationConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CandidateCoordinate,
    CoordinateState,
    GapRepairPlan,
    OSMDryRunResult,
    OSMGapEvaluation,
    OSMGapOutcome,
    OSMPathProvenance,
    OSMRouteCandidate,
    OSMRouteConfirmation,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedGap,
)
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction.gaps import (
    CONFIDENCE_RANK,
    coordinate_mask,
    inventory_gaps,
    position_fields_patchable,
)
from warpbuster.reconstruction.geometry import _interpolate_coordinate
from warpbuster.reconstruction.osm import _anchors
from warpbuster.reconstruction.signals import (
    allocate_signal_progress,
    qualify_distance,
    qualify_speed,
)
from warpbuster.reconstruction.timing import activity_clock


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _snapshot(activity: ActivityData, plan: RepairPlan) -> str:
    return sha256(
        _json(
            {
                "fit_sha256": sha256(activity.preservation.raw_bytes).hexdigest(),
                # Include the normalized view too: stale caller replacements must not replay.
                "records": [
                    (
                        r.index,
                        r.timestamp,
                        r.latitude,
                        r.longitude,
                        r.distance,
                        r.speed,
                        r.continuity_id,
                    )
                    for r in activity.records
                ],
                "events": [
                    (
                        event.index,
                        sorted(
                            (
                                (type(key).__name__, str(key), value)
                                for key, value in event.fields.items()
                            ),
                            key=lambda item: item[:2],
                        ),
                    )
                    for event in activity.events
                ],
                "mask": [asdict(item) for item in plan.coordinate_mask],
                "gaps": [asdict(gap) for gap in plan.gaps],
                "maximum_new_transition_speed_mps": plan.maximum_new_transition_speed_mps,
            }
        ).encode()
    ).hexdigest()


def _fingerprint(
    snapshot: str,
    graph_id: str,
    evaluation: OSMGapEvaluation,
    route: OSMRouteCandidate,
    config: OSMApplicationConfig,
    integrity_config: IntegrityConfig,
) -> str:
    return sha256(
        _json(
            {
                "policy": "confirmed-osm-2d-v1",
                "snapshot": snapshot,
                "graph_id": graph_id,
                "evaluation": asdict(evaluation),
                "selected_route": asdict(route),
                "config": asdict(config),
                "integrity_config": asdict(integrity_config),
            }
        ).encode()
    ).hexdigest()


def osm_route_fingerprint(
    activity: ActivityData,
    plan: RepairPlan,
    discovery: OSMDryRunResult,
    gap_id: str,
    route_id: str,
    *,
    config: OSMApplicationConfig | None = None,
    integrity_config: IntegrityConfig,
) -> str:
    """Identify an exact review snapshot; this function does NOT confirm a route."""
    evaluations = [e for e in discovery.evaluations if e.interval.gap_id == gap_id]
    if len(evaluations) != 1:
        raise ValueError("confirmation requires exactly one matching gap evaluation")
    evaluation = evaluations[0]
    routes = [route for route in evaluation.candidates if route.route_id == route_id]
    if len(routes) != 1:
        raise ValueError("confirmation requires exactly one matching route")
    return _fingerprint(
        _snapshot(activity, plan),
        discovery.graph_id,
        evaluation,
        routes[0],
        config or OSMApplicationConfig(),
        integrity_config,
    )


def apply_confirmed_osm_routes(
    activity: ActivityData,
    integrity: IntegrityReport,
    plan: RepairPlan,
    discovery: OSMDryRunResult,
    confirmations: tuple[OSMRouteConfirmation, ...] = (),
    *,
    config: OSMApplicationConfig | None = None,
) -> RepairPlan:
    """Merge safely allocated OSM fallback into a base plan; never write here.

    Rejected paths have no candidate, so lowering the selection threshold cannot
    bypass any gate. The existing writer remains the only publication boundary.
    """
    config = config or OSMApplicationConfig()
    mask = coordinate_mask(activity, integrity, plan.minimum_invalidation_confidence)
    if (
        plan.activity_path != activity.preservation.source_path
        or plan.coordinate_mask != mask
        or plan.gaps != inventory_gaps(activity, mask)
        or tuple(e.interval for e in discovery.evaluations) != plan.gaps
    ):
        raise ValueError("OSM application requires the same independent source/mask/gap snapshot")
    gap_ids = {gap.gap_id for gap in plan.gaps}
    if any(c.gap_id not in gap_ids for c in confirmations):
        raise ValueError("confirmation names a gap outside the independent inventory")
    if any(not isinstance(c, GapRepairPlan) for c in plan.interval_plans):
        raise ValueError("OSM application requires local gap candidates")
    candidates = {c.interval.gap_id: c for c in plan.interval_plans if isinstance(c, GapRepairPlan)}
    failures = {f.interval.gap_id: f for f in plan.unresolved_gaps}
    snapshot = _snapshot(activity, plan)
    for evaluation in discovery.evaluations:
        gap = evaluation.interval
        if gap.gap_id in candidates:
            continue  # GPX has priority, including candidates below the chosen threshold.
        anchors, anchor_reason = _anchors(activity, plan, gap)
        reason: Reason | None = None
        if anchors is None or anchor_reason is not None:
            reason = Reason.NO_TRUSTED_LOCAL_ANCHOR
        elif gap.reasons:
            reason = gap.reasons[0]
        elif evaluation.outcome is not OSMGapOutcome.CANDIDATES_AVAILABLE:
            reason = Reason.OSM_DISCOVERY_UNRESOLVED
        elif (evaluation.anchor_before, evaluation.anchor_after) != anchors:
            reason = Reason.OSM_CONFIRMATION_STALE
        elif gap.record_count > config.maximum_gap_records:
            reason = Reason.SEARCH_LIMIT_REACHED
        elif not position_fields_patchable(activity, gap):
            reason = Reason.POSITION_FIELDS_UNPATCHABLE
        confirmed = [c for c in confirmations if c.gap_id == gap.gap_id]
        if reason is None:
            reason = (
                Reason.OSM_ROUTE_UNCONFIRMED
                if not confirmed
                else Reason.OSM_CONFIRMATION_CONFLICT
                if len(confirmed) != 1
                else None
            )
        if reason is None:
            confirmation = confirmed[0]
            routes = [r for r in evaluation.candidates if r.route_id == confirmation.route_id]
            if (
                len(routes) != 1
                or not confirmation.evidence.strip()
                or confirmation.fingerprint
                != _fingerprint(
                    snapshot, discovery.graph_id, evaluation, routes[0], config, integrity.config
                )
            ):
                reason = Reason.OSM_CONFIRMATION_STALE
            else:
                result = _allocate(
                    activity,
                    plan,
                    evaluation,
                    routes[0],
                    confirmation,
                    discovery.graph_id,
                    config,
                    integrity.config,
                    snapshot,
                )
                if isinstance(result, GapRepairPlan):
                    candidates[gap.gap_id] = result
                    failures.pop(gap.gap_id, None)
                    continue
                reason = result
        assert reason is not None
        clock = (
            activity_clock(
                activity, activity.records[anchors[0].record_index : anchors[1].record_index + 1]
            )
            if anchors is not None
            else None
        )
        failures[gap.gap_id] = UnresolvedGap(gap, (reason,), timing=clock.audit if clock else None)
    ordered = tuple(candidates[g.gap_id] for g in plan.gaps if g.gap_id in candidates)
    unresolved = tuple(failures[g.gap_id] for g in plan.gaps if g.gap_id in failures)
    changed = bool(
        ordered
        or any(m.state is CoordinateState.INVALIDATED for m in mask)
        or plan.distance_spike_repairs
    )
    status = (
        RepairPlanStatus.PARTIAL
        if changed and unresolved
        else RepairPlanStatus.READY
        if changed
        else RepairPlanStatus.REFUSED
        if plan.gaps
        else RepairPlanStatus.NOT_NEEDED
    )
    return replace(
        plan,
        interval_plans=ordered,
        unresolved_gaps=unresolved,
        status=status,
        confidence=min(
            (c.confidence for c in ordered),
            key=CONFIDENCE_RANK.__getitem__,
            default=plan.confidence,
        ),
        reasons=(Reason.SOME_INTERVALS_UNRESOLVED if unresolved else Reason.ALL_INTERVALS_READY,),
    )


def _allocate(
    activity: ActivityData,
    plan: RepairPlan,
    evaluation: OSMGapEvaluation,
    route: OSMRouteCandidate,
    confirmation: OSMRouteConfirmation | None,
    graph_id: str,
    config: OSMApplicationConfig,
    integrity_config: IntegrityConfig,
    snapshot: str,
) -> GapRepairPlan | Reason:
    gap = evaluation.interval
    before, after = evaluation.anchor_before, evaluation.anchor_after
    assert before is not None and after is not None
    points = tuple((p.latitude, p.longitude) for p in route.coordinates)
    if len(points) < 2 or any(
        not (isfinite(lat) and isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)
        for lat, lon in points
    ):
        return Reason.OSM_GEOMETRY_INVALID
    connectors = (
        geodesic_distance_m(before.latitude, before.longitude, *points[0]),
        geodesic_distance_m(*points[-1], after.latitude, after.longitude),
    )
    if max(connectors) > config.maximum_connector_m:
        return Reason.OSM_CONNECTOR_TOO_LONG
    points = ((before.latitude, before.longitude), *points, (after.latitude, after.longitude))
    chainages = [0.0]
    for point_a, point_b in pairwise(points):
        chainages.append(chainages[-1] + geodesic_distance_m(*point_a, *point_b))
    length = chainages[-1]
    if not isfinite(length) or length <= 0:
        return Reason.OSM_GEOMETRY_INVALID
    records = activity.records[before.record_index : after.record_index + 1]
    clock = activity_clock(activity, records)
    if clock is None:
        return Reason.TIMING_UNUSABLE
    if clock.audit.open_pause:
        return Reason.TIMER_STATE_UNRESOLVED
    if clock.audit.active_seconds <= 0:
        return Reason.NO_ACTIVE_TIME
    limit = min(config.maximum_speed_mps, plan.maximum_new_transition_speed_mps)
    if not isfinite(limit) or limit <= 0 or length / clock.audit.active_seconds > limit:
        return Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    distance = qualify_distance(records, integrity_config)
    speed = qualify_speed(records, integrity_config, active_deltas=clock.active_deltas)
    tolerance = max(
        config.distance_absolute_tolerance_m, config.distance_relative_tolerance * length
    )
    diagnostics: tuple[str, ...] = ()
    if confirmation is None:
        allocation = allocate_signal_progress(
            records,
            length,
            integrity_config,
            clock,
            error_budget_m=tolerance,
            maximum_speed_mps=limit,
        )
        if isinstance(allocation, Reason):
            return allocation
        method, fractions, diagnostics = allocation
        travelled = tuple(value * length for value in fractions)
    else:
        # Historical explicit-confirmation API retains its strict signal contract.
        for signal in (distance, speed):
            if signal.status in {"plausible", "zero"}:
                if any(
                    active == 0 and b > a
                    for (a, b), active in zip(
                        pairwise(signal.cumulative), clock.active_deltas, strict=True
                    )
                ):
                    return Reason.PAUSE_DISTANCE_CONFLICT
                if signal.cumulative[-1] <= 0 or abs(signal.cumulative[-1] - length) > tolerance:
                    return Reason.LOCAL_DISTANCE_INCONSISTENT
        if distance.status == "plausible":
            cumulative, method = distance.cumulative, AllocationMethod.RECORDED_DISTANCE
        elif speed.status == "plausible":
            cumulative, method = speed.cumulative, AllocationMethod.RECORDED_SPEED
        else:
            cumulative, method = clock.active_cumulative, AllocationMethod.TIMESTAMPS
        travelled = tuple(value / cumulative[-1] * length for value in cumulative)
    for (a, b), active in zip(pairwise(travelled), clock.active_deltas, strict=True):
        if (active == 0 and b > a) or (active > 0 and (b - a) / active > limit):
            return Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    positions = []
    for value in travelled:
        end = min(len(points) - 1, max(1, bisect_right(chainages, value)))
        span = chainages[end] - chainages[end - 1]
        positions.append(
            _interpolate_coordinate(
                *points[end - 1], *points[end], (value - chainages[end - 1]) / span if span else 0.0
            )
        )
    # Keep the actual FIT anchor pair, regardless of interpolation round-off.
    positions[0], positions[-1] = points[0], points[-1]
    for (point_a, point_b), active in zip(pairwise(positions), clock.active_deltas, strict=True):
        displacement = geodesic_distance_m(*point_a, *point_b)
        if (active == 0 and displacement > 0) or (active > 0 and displacement / active > limit):
            return Reason.CANDIDATE_TRANSITION_IMPLAUSIBLE
    updates = tuple(
        CandidateCoordinate(r.index, r.timestamp, r.latitude, r.longitude, *position)
        for r, position in zip(records[1:-1], positions[1:-1], strict=True)
    )
    return GapRepairPlan(
        interval=gap,
        coordinate_updates=updates,
        confidence=IntegrityConfidence.HIGH if confirmation else IntegrityConfidence.MEDIUM,
        reasons=(
            Reason.OSM_ROUTE_CONFIRMED if confirmation else Reason.OSM_ROUTE_AUTOMATIC,
            Reason.ANCHOR_CONNECTORS_PLAUSIBLE,
        ),
        reconstruction_path_distance_m=length,
        preserve_recorded_distance=True,
        osm_provenance=OSMPathProvenance(
            graph_id,
            route.route_id,
            confirmation,
            sha256(activity.preservation.raw_bytes).hexdigest(),
            _json(route.as_dict()),
            _json(evaluation.routing_document()),
            _json(asdict(config)),
            _json(asdict(integrity_config)),
            method,
            distance.status,
            speed.status,
            tolerance,
            sum(connectors),
            clock.audit,
            discovery_evaluation_json=_json(asdict(evaluation)),
            snapshot_sha256=snapshot,
            identity_basis="caller_confirmed_route" if confirmation else "automatic_osm_selection",
            signal_diagnostics=diagnostics,
            observed_distance_m=distance.cumulative[-1] if distance.cumulative else None,
            integrated_speed_distance_m=speed.cumulative[-1] if speed.cumulative else None,
            distance_quality="estimated"
            if confirmation is None and method is AllocationMethod.TIMESTAMPS
            else "source_unverified",
        ),
    )
