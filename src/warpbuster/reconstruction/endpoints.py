"""Optional, FIT-only user endpoints for one-sided OSM reconstruction."""

from __future__ import annotations

import json
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from itertools import pairwise

from warpbuster.config import (
    AutomaticOSMApplicationConfig,
    CourseReconstructionConfig,
    OSMReconstructionConfig,
)
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityReport,
    TransitionClassification,
)
from warpbuster.models.reconstruction import (
    CandidateCoordinate,
    GapOrigin,
    GapRepairPlan,
    MissingCourseRunKind,
    OSMPathProvenance,
    ReconstructionGap,
    ReconstructionReason,
    RepairPlan,
    RepairPlanStatus,
)
from warpbuster.reconstruction.approximate_contract import DemComparisonConfig
from warpbuster.reconstruction.dem_choice import DemSampler, compare_dem_candidates
from warpbuster.reconstruction.gaps import position_fields_patchable
from warpbuster.reconstruction.geometry import _interpolate_coordinate
from warpbuster.reconstruction.osm import RoutingCandidateData, RoutingClient
from warpbuster.reconstruction.osm_application import _snapshot
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.reconstruction.signals import (
    allocate_signal_progress,
    qualify_distance,
    qualify_speed,
)
from warpbuster.reconstruction.timing import activity_clock


@dataclass(frozen=True, slots=True)
class UserEndpoint:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.latitude, bool)
            or isinstance(self.longitude, bool)
            or not isinstance(self.latitude, int | float)
            or not isinstance(self.longitude, int | float)
            or not math.isfinite(self.latitude)
            or not math.isfinite(self.longitude)
            or not -90 <= self.latitude <= 90
            or not -180 <= self.longitude <= 180
        ):
            raise ValueError("user endpoint must have finite WGS84 latitude and longitude")


@dataclass(frozen=True, slots=True)
class EndpointHints:
    start: UserEndpoint | None = None
    finish: UserEndpoint | None = None
    loop_point: UserEndpoint | None = None

    def __post_init__(self) -> None:
        if self.loop_point is not None and (self.start is not None or self.finish is not None):
            raise ValueError("loop point cannot be combined with start or finish")
        for point in (self.start, self.finish, self.loop_point):
            if point is not None and not isinstance(point, UserEndpoint):
                raise TypeError("endpoint hints must contain UserEndpoint values")

    @property
    def starting_point(self) -> UserEndpoint | None:
        return self.loop_point or self.start

    @property
    def finishing_point(self) -> UserEndpoint | None:
        return self.loop_point or self.finish


def note_unused_endpoints(plan: RepairPlan, hints: EndpointHints) -> RepairPlan:
    """Record supplied points when the matching FIT edge has no repair gap."""
    decisions: list[dict[str, object]] = []
    for kind, point in (
        (MissingCourseRunKind.PREFIX, hints.starting_point),
        (MissingCourseRunKind.SUFFIX, hints.finishing_point),
    ):
        if point is not None and not any(gap.kind is kind for gap in plan.gaps):
            decisions.append(
                {
                    "kind": kind.value,
                    "source": "user_supplied",
                    "endpoint": [point.latitude, point.longitude],
                    "time_binding": "activity_boundary_assumption",
                    "status": "unused",
                    "reason": "fit_edge_preserved",
                }
            )
    return replace(plan, endpoint_audit_json=json.dumps(decisions, sort_keys=True))


def endpoint_pairs(
    activity: ActivityData, integrity: IntegrityReport, plan: RepairPlan, hints: EndpointHints
) -> tuple[tuple[ReconstructionGap, UserEndpoint, tuple[float, float]], ...]:
    """Only externally supplied edges adjacent to stable original FIT context."""
    pairs = []
    for gap in plan.gaps:
        endpoint = (
            hints.starting_point
            if gap.kind is MissingCourseRunKind.PREFIX
            else hints.finishing_point
            if gap.kind is MissingCourseRunKind.SUFFIX
            else None
        )
        if endpoint is None or not _stable_anchor(activity, plan, gap, _normal_pairs(integrity)):
            continue
        index = (
            gap.anchor_after_record_index
            if gap.kind is MissingCourseRunKind.PREFIX
            else gap.anchor_before_record_index
        )
        assert index is not None
        record = activity.records[index]
        assert record.latitude is not None and record.longitude is not None
        pairs.append((gap, endpoint, (record.latitude, record.longitude)))
    return tuple(pairs)


def _normal_pairs(integrity: IntegrityReport) -> set[tuple[int, int]]:
    return {
        (item.from_record_index, item.to_record_index)
        for item in integrity.transitions
        if item.classification is TransitionClassification.NORMAL
    }


def _stable_anchor(
    activity: ActivityData,
    plan: RepairPlan,
    gap: ReconstructionGap,
    normal: set[tuple[int, int]],
) -> bool:
    if gap.reasons or gap.record_count > AutomaticOSMApplicationConfig().maximum_gap_records:
        return False
    if not position_fields_patchable(activity, gap):
        return False
    if gap.kind is MissingCourseRunKind.PREFIX:
        anchor = gap.anchor_after_record_index
        direction = 1
        boundary = gap.start_record_index == 0
    elif gap.kind is MissingCourseRunKind.SUFFIX:
        anchor = gap.anchor_before_record_index
        direction = -1
        boundary = gap.end_record_index == len(activity.records) - 1
    else:
        return False
    if not boundary or anchor is None or not plan.coordinate_mask[anchor].anchor_eligible:
        return False
    minimum = CourseReconstructionConfig().anchor_stability_min_normal_transitions
    for step in range(minimum):
        neighbor = anchor + direction * (step + 1)
        previous = anchor + direction * step
        if not 0 <= neighbor < len(activity.records):
            return False
        if (
            activity.records[neighbor].continuity_id != gap.continuity_id
            or not plan.coordinate_mask[neighbor].anchor_eligible
            or (min(previous, neighbor), max(previous, neighbor)) not in normal
        ):
            return False
    return True


def apply_endpoint_routes(
    activity: ActivityData,
    integrity: IntegrityReport,
    plan: RepairPlan,
    graph_id: str,
    client: RoutingClient,
    hints: EndpointHints,
    *,
    minimum_confidence: IntegrityConfidence,
    config: AutomaticOSMApplicationConfig | None = None,
    remaining_queries: int | None = None,
) -> RepairPlan:
    """Add bounded endpoint candidates, leaving internal OSM decisions intact."""
    cfg = config or AutomaticOSMApplicationConfig()
    candidates = {
        item.interval.gap_id: item
        for item in plan.interval_plans
        if isinstance(item, GapRepairPlan)
    }
    failures = {item.interval.gap_id: item for item in plan.unresolved_gaps}
    decisions: list[dict[str, object]] = json.loads(plan.endpoint_audit_json or "[]")
    endpoint_alternatives: list[GapRepairPlan] = []
    queries = 0
    normal = _normal_pairs(integrity)
    for gap in plan.gaps:
        if gap.kind not in {MissingCourseRunKind.PREFIX, MissingCourseRunKind.SUFFIX}:
            continue
        endpoint = (
            hints.starting_point
            if gap.kind is MissingCourseRunKind.PREFIX
            else hints.finishing_point
        )
        decision: dict[str, object] = {
            "gap_id": gap.gap_id,
            "kind": gap.kind.value,
            "source": "user_supplied" if endpoint is not None else "absent",
            "endpoint": [endpoint.latitude, endpoint.longitude] if endpoint is not None else None,
            "time_binding": "activity_boundary_assumption" if endpoint is not None else None,
            "status": "unresolved",
        }
        decisions.append(decision)
        if endpoint is None:
            decision["reason"] = "endpoint_not_supplied"
            continue
        if gap.gap_id in candidates:
            decision["status"] = "already_selected"
            continue
        if minimum_confidence is IntegrityConfidence.HIGH:
            decision["reason"] = "confidence_threshold_too_high"
            continue
        strict_scope = gap.origin is not GapOrigin.ORIGINAL_MISSING
        if strict_scope and (
            gap.invalidated_count == 0
            or gap.invalidation_confidence
            not in {IntegrityConfidence.MEDIUM, IntegrityConfidence.HIGH}
        ):
            decision["reason"] = "insufficient_corruption_proof"
            continue
        if not _stable_anchor(activity, plan, gap, normal):
            decision["reason"] = "unsafe_fit_context"
            continue
        if remaining_queries is not None and queries >= remaining_queries:
            decision["reason"] = "query_limit_reached"
            continue
        anchor_index = (
            gap.anchor_after_record_index
            if gap.kind is MissingCourseRunKind.PREFIX
            else gap.anchor_before_record_index
        )
        assert anchor_index is not None
        anchor = activity.records[anchor_index]
        assert anchor.latitude is not None and anchor.longitude is not None
        first = (endpoint.latitude, endpoint.longitude)
        last = (anchor.latitude, anchor.longitude)
        if gap.kind is MissingCourseRunKind.SUFFIX:
            first, last = last, first
        queries += 1
        decision["queried"] = True
        try:
            routes = client.alternatives(
                graph_id, first, last, OSMReconstructionConfig().requested_alternatives
            )
        except Exception as error:
            decision["reason"] = "routing_failed"
            decision["error_type"] = type(error).__name__
            decision["error_code"] = getattr(error, "code", None)
            continue
        if routes.status != "READY":
            decision["reason"] = "route_unavailable"
            continue
        attempts = []
        for route in routes.candidates[: cfg.maximum_candidate_attempts]:
            result = _allocate_endpoint(
                activity, integrity, plan, gap, endpoint, route, routes.document, graph_id, cfg
            )
            if isinstance(result, GapRepairPlan):
                attempts.append(result)
        if not attempts:
            decision["reason"] = "no_safe_candidate"
            continue
        # The candidate set is bounded and not exhaustive. Distance is advisory.
        attempts.sort(
            key=lambda item: (
                item.reconstruction_path_distance_m,
                item.osm_provenance.route_id if item.osm_provenance else "",
            )
        )
        selected = None
        accepted: list[GapRepairPlan] = []
        for candidate in attempts:
            proposed = replace(
                plan,
                interval_plans=tuple((*candidates.values(), candidate)),
                unresolved_gaps=tuple(
                    item for item in plan.unresolved_gaps if item.interval != gap
                ),
            )
            from warpbuster.fit.writer import FitWriteError, _validate_composed_geometry

            try:
                _validate_composed_geometry(
                    activity,
                    proposed,
                    select_repair_intervals(proposed, minimum_confidence),
                )
            except FitWriteError:
                continue
            accepted.append(candidate)
            if selected is None:
                selected = candidate
        if selected is None:
            decision["reason"] = "writer_preflight_rejected"
            continue
        if strict_scope:
            # A bounded route search and FIT distance/speed of unknown provenance
            # cannot establish which path was actually taken through corrupt GNSS.
            decision["reason"] = "strict_route_not_established"
            continue
        candidates[gap.gap_id] = selected
        endpoint_alternatives.extend(accepted)
        failures.pop(gap.gap_id, None)
        decision.update(
            status="osm_selected",
            selection_mode="approximate_low_evidence",
            approximate=True,
            selected_route_id=selected.osm_provenance.route_id if selected.osm_provenance else None,
        )
    final = replace(
        plan,
        interval_plans=tuple(
            candidates[gap.gap_id] for gap in plan.gaps if gap.gap_id in candidates
        ),
        unresolved_gaps=tuple(failures[gap.gap_id] for gap in plan.gaps if gap.gap_id in failures),
        endpoint_audit_json=json.dumps(decisions, sort_keys=True),
        endpoint_alternatives=tuple(endpoint_alternatives),
    )
    selection = select_repair_intervals(final, minimum_confidence)
    return replace(
        final,
        status=RepairPlanStatus.PARTIAL
        if selection.has_changes and final.unresolved_gaps
        else RepairPlanStatus.READY
        if selection.has_changes
        else RepairPlanStatus.NOT_NEEDED
        if not plan.gaps
        else RepairPlanStatus.REFUSED,
    )


def _allocate_endpoint(
    activity: ActivityData,
    integrity: IntegrityReport,
    plan: RepairPlan,
    gap: ReconstructionGap,
    endpoint: UserEndpoint,
    route: RoutingCandidateData,
    routing_document: dict[str, object],
    graph_id: str,
    cfg: AutomaticOSMApplicationConfig,
) -> GapRepairPlan | ReconstructionReason:
    """Allocate one audited OSM route to every originally missing endpoint record."""
    audit = route.document.get("audit")
    if not isinstance(audit, dict) or audit.get("status") not in {"PASS", "WARN"}:
        return ReconstructionReason.OSM_DISCOVERY_UNRESOLVED
    points = tuple(route.coordinates)
    if len(points) < 2 or any(
        not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)
        for lat, lon in points
    ):
        return ReconstructionReason.OSM_GEOMETRY_INVALID
    anchor_index = (
        gap.anchor_after_record_index
        if gap.kind is MissingCourseRunKind.PREFIX
        else gap.anchor_before_record_index
    )
    assert anchor_index is not None
    anchor = activity.records[anchor_index]
    assert anchor.latitude is not None and anchor.longitude is not None
    start = (endpoint.latitude, endpoint.longitude)
    end = (anchor.latitude, anchor.longitude)
    if gap.kind is MissingCourseRunKind.SUFFIX:
        start, end = end, start
    connectors = (
        geodesic_distance_m(start[0], start[1], points[0][0], points[0][1]),
        geodesic_distance_m(points[-1][0], points[-1][1], end[0], end[1]),
    )
    if max(connectors) > cfg.maximum_connector_m:
        return ReconstructionReason.OSM_CONNECTOR_TOO_LONG
    path = (start, *points, end)
    cumulative = [0.0]
    for a, b in pairwise(path):
        cumulative.append(cumulative[-1] + geodesic_distance_m(*a, *b))
    length = cumulative[-1]
    if not math.isfinite(length) or length <= 0:
        return ReconstructionReason.OSM_GEOMETRY_INVALID
    records = (
        activity.records[gap.start_record_index : anchor_index + 1]
        if gap.kind is MissingCourseRunKind.PREFIX
        else activity.records[anchor_index : gap.end_record_index + 1]
    )
    clock = activity_clock(activity, records)
    if clock is None or clock.audit.open_pause or clock.audit.active_seconds <= 0:
        return ReconstructionReason.TIMING_UNUSABLE
    limit = min(cfg.maximum_speed_mps, plan.maximum_new_transition_speed_mps)
    if length / clock.audit.active_seconds > limit:
        return ReconstructionReason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    tolerance = max(cfg.distance_absolute_tolerance_m, cfg.distance_relative_tolerance * length)
    allocation = allocate_signal_progress(
        records,
        length,
        integrity.config,
        clock,
        error_budget_m=tolerance,
        maximum_speed_mps=limit,
    )
    if isinstance(allocation, ReconstructionReason):
        return allocation
    method, fractions, diagnostics = allocation
    positions = []
    for fraction in fractions:
        value = fraction * length
        index = min(len(path) - 1, max(1, bisect_right(cumulative, value)))
        span = cumulative[index] - cumulative[index - 1]
        positions.append(
            _interpolate_coordinate(
                path[index - 1][0],
                path[index - 1][1],
                path[index][0],
                path[index][1],
                (value - cumulative[index - 1]) / span if span else 0.0,
            )
        )
    positions[0], positions[-1] = start, end
    for (a, b), active in zip(pairwise(positions), clock.active_deltas, strict=True):
        displacement = geodesic_distance_m(*a, *b)
        if (active == 0 and displacement > 0) or (active > 0 and displacement / active > limit):
            return ReconstructionReason.CANDIDATE_TRANSITION_IMPLAUSIBLE
    target_records = records[:-1] if gap.kind is MissingCourseRunKind.PREFIX else records[1:]
    target_positions = positions[:-1] if gap.kind is MissingCourseRunKind.PREFIX else positions[1:]
    updates = tuple(
        CandidateCoordinate(
            record.index, record.timestamp, record.latitude, record.longitude, *point
        )
        for record, point in zip(target_records, target_positions, strict=True)
    )
    distance = qualify_distance(records, integrity.config)
    speed = qualify_speed(records, integrity.config, active_deltas=clock.active_deltas)
    provenance = OSMPathProvenance(
        graph_id,
        route.route_id,
        None,
        sha256(activity.preservation.raw_bytes).hexdigest(),
        json.dumps(route.document, sort_keys=True, default=str),
        json.dumps(routing_document, sort_keys=True, default=str),
        json.dumps(asdict(cfg), sort_keys=True),
        json.dumps(asdict(integrity.config), sort_keys=True, default=str),
        method,
        distance.status,
        speed.status,
        tolerance,
        sum(connectors),
        clock.audit,
        snapshot_sha256=_snapshot(activity, plan),
        identity_basis="user_endpoint_assumption",
        distance_quality="estimated" if method.value == "timestamps" else "source_unverified",
        signal_diagnostics=diagnostics,
        observed_distance_m=distance.cumulative[-1] if distance.cumulative else None,
        integrated_speed_distance_m=speed.cumulative[-1] if speed.cumulative else None,
        user_endpoint=(endpoint.latitude, endpoint.longitude),
    )
    return GapRepairPlan(
        gap,
        updates,
        IntegrityConfidence.MEDIUM,
        (ReconstructionReason.OSM_ROUTE_AUTOMATIC, ReconstructionReason.COURSE_ASSUMPTION),
        length,
        True,
        osm_provenance=provenance,
    )


def choose_dem_endpoint_routes(
    activity: ActivityData,
    plan: RepairPlan,
    fallback: RepairPlan,
    sampler: DemSampler,
    snapshot_id: str,
    config: DemComparisonConfig,
) -> RepairPlan:
    """Merge endpoint plans after internal DEM selection; DEM may only reorder safe paths."""
    from warpbuster.fit.writer import FitWriteError, _validate_composed_geometry

    candidates = {
        item.interval.gap_id: item
        for item in plan.interval_plans
        if isinstance(item, GapRepairPlan)
    }
    fallback_endpoints = {
        item.interval.gap_id: item
        for item in fallback.interval_plans
        if isinstance(item, GapRepairPlan)
        and item.interval.kind in {MissingCourseRunKind.PREFIX, MissingCourseRunKind.SUFFIX}
    }
    audit = json.loads(fallback.endpoint_audit_json or "[]")
    for gap in fallback.gaps:
        selected = fallback_endpoints.get(gap.gap_id)
        if selected is None:
            continue
        choices = tuple(
            item
            for item in fallback.endpoint_alternatives
            if item.interval.gap_id == gap.gap_id and item.osm_provenance is not None
        )[: config.maximum_profiles_per_gap]
        decision = next((item for item in audit if item.get("gap_id") == gap.gap_id), None)
        if len(choices) > 1:
            evidence = compare_dem_candidates(
                activity,
                gap,
                tuple(
                    (item.osm_provenance.route_id, item) for item in choices if item.osm_provenance
                ),
                sampler,
                snapshot_id,
                config,
            )
            if decision is not None:
                decision["dem_evidence"] = {
                    "status": evidence.status.value,
                    "profile_ids": evidence.profile_ids,
                    "snapshot_id": evidence.snapshot_id,
                }
            if evidence.preferred_route_id is not None:
                preferred = next(
                    item
                    for item in choices
                    if item.osm_provenance is not None
                    and item.osm_provenance.route_id == evidence.preferred_route_id
                )
                composed = replace(
                    plan,
                    interval_plans=tuple((*candidates.values(), preferred)),
                )
                try:
                    _validate_composed_geometry(
                        activity,
                        composed,
                        select_repair_intervals(composed, IntegrityConfidence.MEDIUM),
                    )
                except FitWriteError:
                    pass
                else:
                    selected = preferred
                    if decision is not None:
                        decision["selected_route_id"] = evidence.preferred_route_id
                        decision["selected_by_dem"] = True
        candidates[gap.gap_id] = selected
    selected_ids = set(fallback_endpoints)
    merged = replace(
        plan,
        interval_plans=tuple(
            candidates[gap.gap_id] for gap in plan.gaps if gap.gap_id in candidates
        ),
        unresolved_gaps=tuple(
            item for item in plan.unresolved_gaps if item.interval.gap_id not in selected_ids
        ),
        endpoint_audit_json=json.dumps(audit, sort_keys=True),
        endpoint_alternatives=fallback.endpoint_alternatives,
    )
    selection = select_repair_intervals(merged, IntegrityConfidence.MEDIUM)
    return replace(
        merged,
        status=RepairPlanStatus.PARTIAL
        if selection.has_changes and merged.unresolved_gaps
        else RepairPlanStatus.READY
        if selection.has_changes
        else RepairPlanStatus.NOT_NEEDED
        if not merged.gaps
        else RepairPlanStatus.REFUSED,
    )
