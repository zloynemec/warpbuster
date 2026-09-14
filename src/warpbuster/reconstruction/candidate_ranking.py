"""Provider-neutral, advisory ranking of already discovered gap paths."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict
from hashlib import sha256
from itertools import pairwise
from typing import Any

from warpbuster.config import GapCandidateRankingConfig, IntegrityConfig, OSMReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData
from warpbuster.models.reconstruction import (
    CandidateProvider,
    CandidateRankingResult,
    CandidateRankingStatus,
    GapCandidateRanking,
    GapRepairPlan,
    OSMDryRunResult,
    OSMPathPoint,
    RankedGapCandidate,
    RepairPlan,
)
from warpbuster.reconstruction.osm_context import collect_start_context
from warpbuster.reconstruction.signals import DistanceSignal, qualify_distance, qualify_speed
from warpbuster.reconstruction.timing import AllocationClock, activity_clock

POLICY_VERSION = "gap-candidate-ranking-v1"
type Point = tuple[float, float]
type ContextPoint = tuple[int, float, float, float]


def rank_gap_candidates(
    activity: ActivityData,
    plan: RepairPlan,
    osm_result: OSMDryRunResult | None,
    *,
    config: GapCandidateRankingConfig | None = None,
    integrity_config: IntegrityConfig | None = None,
) -> CandidateRankingResult:
    """Rank collected GPX/OSM paths without changing the repair plan."""
    cfg = config or GapCandidateRankingConfig()
    integrity = integrity_config or IntegrityConfig.for_sport(activity.sport)
    osm_by_gap = {
        item.interval.gap_id: item for item in (osm_result.evaluations if osm_result else ())
    }
    gpx_by_gap = {
        item.interval.gap_id: item
        for item in plan.interval_plans
        if isinstance(item, GapRepairPlan) and item.provenance is not None
    }
    rankings: list[GapCandidateRanking] = []
    total_points = 0
    total_work = 0
    for gap in plan.gaps:
        if gap.anchor_before_record_index is None or gap.anchor_after_record_index is None:
            rankings.append(
                GapCandidateRanking(
                    gap.gap_id,
                    CandidateRankingStatus.NOT_SUPPORTED,
                    (),
                    reasons=("two_anchors_required",),
                )
            )
            continue
        specs: list[tuple[CandidateProvider, str, str | None, tuple[Point, ...], str | None]] = []
        gpx = gpx_by_gap.get(gap.gap_id)
        if gpx is not None:
            points = _gpx_points(gpx)
            specs.append((CandidateProvider.GPX, _gpx_id(gpx, points), None, points, None))
        evaluation = osm_by_gap.get(gap.gap_id)
        if evaluation is not None:
            scope_reason = (
                "scope_mismatch"
                if evaluation.interval != gap
                else "anchor_mismatch"
                if evaluation.anchor_before is None
                or evaluation.anchor_after is None
                or evaluation.anchor_before.record_index != gap.anchor_before_record_index
                or evaluation.anchor_after.record_index != gap.anchor_after_record_index
                else None
            )
            specs.extend(
                (
                    CandidateProvider.OSM,
                    candidate.route_id,
                    candidate.role,
                    tuple((point.latitude, point.longitude) for point in candidate.coordinates),
                    scope_reason or ("routing_audit_failed" if _audit_failed(candidate) else None),
                )
                for candidate in evaluation.candidates
            )
        if len(specs) > cfg.maximum_candidates_per_gap:
            rankings.append(
                GapCandidateRanking(
                    gap.gap_id,
                    CandidateRankingStatus.RESOURCE_LIMIT,
                    (),
                    search_complete=False,
                    reasons=("candidate_limit",),
                )
            )
            continue
        point_count = sum(len(item[3]) for item in specs)
        total_points += point_count
        if total_points > cfg.maximum_candidate_points:
            rankings.append(
                GapCandidateRanking(
                    gap.gap_id,
                    CandidateRankingStatus.RESOURCE_LIMIT,
                    (),
                    search_complete=False,
                    reasons=("candidate_point_limit",),
                )
            )
            continue
        context = collect_start_context(
            activity,
            plan,
            gap.anchor_before_record_index,
            OSMReconstructionConfig(
                context_maximum_points=cfg.context_maximum_points,
                context_maximum_age_s=cfg.context_maximum_age_s,
                context_maximum_length_m=cfg.context_maximum_length_m,
                context_maximum_step_s=cfg.context_maximum_step_s,
            ),
        )
        total_work += len(context.points) * sum(max(0, len(item[3]) - 1) for item in specs)
        if total_work > cfg.maximum_projection_work:
            rankings.append(
                GapCandidateRanking(
                    gap.gap_id,
                    CandidateRankingStatus.RESOURCE_LIMIT,
                    (),
                    search_complete=False,
                    reasons=("projection_work_limit",),
                )
            )
            continue
        records = activity.records[
            gap.anchor_before_record_index : gap.anchor_after_record_index + 1
        ]
        clock = activity_clock(activity, records)
        distance = qualify_distance(records, integrity)
        speed = qualify_speed(
            records,
            integrity,
            active_deltas=clock.active_deltas if clock is not None else None,
        )
        ranked = tuple(
            _rank_one(
                activity,
                plan,
                gap.anchor_before_record_index,
                gap.anchor_after_record_index,
                provider,
                source_id,
                role,
                points,
                context.points,
                clock,
                distance,
                speed,
                cfg,
                forced_reason,
            )
            for provider, source_id, role, points, forced_reason in specs
        )
        search_complete = not _search_incomplete(evaluation)
        rankings.append(_decide(gap.gap_id, ranked, search_complete, cfg))
    return CandidateRankingResult(
        tuple(rankings),
        POLICY_VERSION,
        json.dumps(asdict(cfg), sort_keys=True, separators=(",", ":")),
    )


def _gpx_points(candidate: GapRepairPlan) -> tuple[tuple[float, float], ...]:
    provenance = candidate.provenance
    assert provenance is not None
    middle = tuple(
        (item.candidate_latitude, item.candidate_longitude)
        for item in sorted(candidate.coordinate_updates, key=lambda item: item.record_index)
    )
    return (
        (provenance.anchor_before.latitude, provenance.anchor_before.longitude),
        *middle,
        (provenance.anchor_after.latitude, provenance.anchor_after.longitude),
    )


def _gpx_id(candidate: GapRepairPlan, points: tuple[tuple[float, float], ...]) -> str:
    provenance = candidate.provenance
    assert provenance is not None
    payload = {
        "policy": POLICY_VERSION,
        "provider": "gpx",
        "gap_id": candidate.interval.gap_id,
        "source_sha256": provenance.source_sha256,
        "direction": provenance.direction.value,
        "points": points,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + sha256(encoded.encode()).hexdigest()


def _rank_one(
    activity: ActivityData,
    plan: RepairPlan,
    before_index: int,
    after_index: int,
    provider: CandidateProvider,
    source_id: str,
    role: str | None,
    points: tuple[Point, ...],
    context: tuple[ContextPoint, ...],
    clock: AllocationClock | None,
    distance: DistanceSignal,
    speed: DistanceSignal,
    cfg: GapCandidateRankingConfig,
    forced_reason: str | None,
) -> RankedGapCandidate:
    reasons: list[str] = []
    evidence: dict[str, object] = {}
    if forced_reason is not None:
        return _ineligible(provider, source_id, role, points, [forced_reason], evidence)
    valid = len(points) >= 2 and all(_valid_point(point) for point in points)
    if not valid:
        reasons.append("invalid_geometry")
        return _ineligible(provider, source_id, role, points, reasons, evidence)
    before = activity.records[before_index]
    after = activity.records[after_index]
    assert before.latitude is not None and before.longitude is not None
    assert after.latitude is not None and after.longitude is not None
    connectors = (
        geodesic_distance_m(before.latitude, before.longitude, *points[0]),
        geodesic_distance_m(*points[-1], after.latitude, after.longitude),
    )
    path_length = sum(geodesic_distance_m(*a, *b) for a, b in pairwise(points))
    full_length = path_length + sum(connectors)
    evidence["attachment"] = {
        "start_m": connectors[0],
        "end_m": connectors[1],
        "maximum_m": max(connectors),
    }
    evidence["length"] = {
        "path_m": path_length,
        "connectors_m": sum(connectors),
        "total_m": full_length,
    }
    if max(connectors) > cfg.maximum_connector_m:
        reasons.append("connector_too_long")
    if not math.isfinite(path_length) or path_length <= 0:
        reasons.append("invalid_geometry")
    if clock is None:
        reasons.append("timing_unusable")
    elif clock.audit.open_pause:
        reasons.append("timer_state_unresolved")
    elif clock.audit.active_seconds <= 0:
        reasons.append("no_active_time")
    elif full_length / clock.audit.active_seconds > plan.maximum_new_transition_speed_mps:
        reasons.append("active_time_traversal_implausible")
    if reasons:
        return _ineligible(provider, source_id, role, points, reasons, evidence)

    attachment_score = _band(max(connectors), cfg.connector_good_m, cfg.connector_fair_m)
    context_score, context_observed, context_evidence = _context_score(context, points, cfg)
    direction_score, direction_observed, direction_evidence = _direction_score(context, points, cfg)
    distance_score, distance_observed, distance_evidence = _distance_score(
        distance, speed, full_length, cfg
    )
    components = (
        ("attachment", attachment_score),
        ("context", context_score),
        ("direction", direction_score),
        ("distance", distance_score),
    )
    score = (
        attachment_score * cfg.attachment_weight
        + context_score * cfg.context_weight
        + direction_score * cfg.direction_weight
        + distance_score * cfg.distance_weight
    )
    evidence.update(
        context=context_evidence,
        direction=direction_evidence,
        distance=distance_evidence,
        weights={
            "attachment": cfg.attachment_weight,
            "context": cfg.context_weight,
            "direction": cfg.direction_weight,
            "distance": cfg.distance_weight,
        },
    )
    candidate_id = _candidate_id(provider, source_id, points)
    return RankedGapCandidate(
        candidate_id,
        provider,
        True,
        score,
        1 + context_observed + direction_observed + distance_observed,
        components,
        _json(evidence),
        (),
        tuple(OSMPathPoint(*point) for point in points),
        source_id,
        role,
    )


def _ineligible(
    provider: CandidateProvider,
    source_id: str,
    role: str | None,
    points: tuple[Point, ...],
    reasons: list[str],
    evidence: dict[str, object],
) -> RankedGapCandidate:
    return RankedGapCandidate(
        _candidate_id(provider, source_id, points),
        provider,
        False,
        None,
        0,
        (),
        _json(evidence),
        tuple(reasons),
        tuple(OSMPathPoint(*p) for p in points if _valid_point(p)),
        source_id,
        role,
    )


def _context_score(
    context: tuple[ContextPoint, ...], points: tuple[Point, ...], cfg: GapCandidateRankingConfig
) -> tuple[int, int, dict[str, object]]:
    if len(context) < cfg.context_minimum_points:
        return 1, 0, {"status": "missing", "point_count": len(context)}
    head = _prefix(points, cfg.context_maximum_length_m)
    distances = [_distance_to_polyline((p[2], p[3]), head) for p in context]
    median = statistics.median(distances)
    return (
        _band(median, cfg.context_good_m, cfg.context_fair_m),
        1,
        {"status": "observed", "point_count": len(context), "median_error_m": median},
    )


def _direction_score(
    context: tuple[ContextPoint, ...], points: tuple[Point, ...], cfg: GapCandidateRankingConfig
) -> tuple[int, int, dict[str, object]]:
    if len(context) < 2:
        return 1, 0, {"status": "missing"}
    source = ((context[0][2], context[0][3]), (context[-1][2], context[-1][3]))
    progress = geodesic_distance_m(*source[0], *source[1])
    sampled = _point_at(points, cfg.direction_path_sample_m)
    path_progress = geodesic_distance_m(*points[0], *sampled)
    if (
        progress < cfg.direction_minimum_progress_m
        or path_progress < cfg.direction_minimum_progress_m
    ):
        return (
            1,
            0,
            {"status": "missing", "source_progress_m": progress, "path_progress_m": path_progress},
        )
    angle = _angle(source[0], source[1], points[0], sampled)
    return (
        _band(angle, cfg.direction_good_degrees, cfg.direction_fair_degrees),
        1,
        {
            "status": "observed",
            "angle_degrees": angle,
            "source_progress_m": progress,
            "path_progress_m": path_progress,
        },
    )


def _distance_score(
    distance: DistanceSignal,
    speed: DistanceSignal,
    length: float,
    cfg: GapCandidateRankingConfig,
) -> tuple[int, int, dict[str, object]]:
    signals: list[tuple[float, float]] = []
    recorded: dict[str, object] = {}
    integrated: dict[str, object] = {}
    for name, signal in (("recorded_distance", distance), ("integrated_speed", speed)):
        status = signal.status
        value = signal.cumulative[-1] if status == "plausible" and signal.cumulative else None
        item: dict[str, object] = {"status": status, "distance_m": value}
        if value is not None:
            tolerance = max(
                cfg.distance_absolute_tolerance_m, cfg.distance_relative_tolerance * value
            )
            error = abs(length - value)
            item.update(error_m=error, tolerance_m=tolerance)
            signals.append((error, tolerance))
        yield_item = item
        # Store below without relying on dynamic object mutation.
        if name == "recorded_distance":
            recorded = yield_item
        else:
            integrated = yield_item
    evidence = {"recorded_distance": recorded, "integrated_speed": integrated}
    if not signals:
        return 1, 0, {"status": "missing", **evidence}
    penalty = max(
        0 if error <= tolerance else 1 if error <= 2 * tolerance else 2
        for error, tolerance in signals
    )
    return penalty, 1, {"status": "observed", **evidence}


def _decide(
    gap_id: str,
    candidates: tuple[RankedGapCandidate, ...],
    search_complete: bool,
    cfg: GapCandidateRankingConfig,
) -> GapCandidateRanking:
    eligible = sorted(
        (item for item in candidates if item.eligible),
        key=lambda item: (item.score, item.candidate_id),
    )
    if not eligible:
        return GapCandidateRanking(
            gap_id,
            CandidateRankingStatus.NO_ELIGIBLE_CANDIDATES,
            candidates,
            search_complete=search_complete,
            reasons=("no_eligible_candidates",),
        )
    best = eligible[0]
    runner = eligible[1] if len(eligible) > 1 else None
    margin = (
        runner.score - best.score
        if runner is not None and runner.score is not None and best.score is not None
        else None
    )
    near = tuple(
        item.candidate_id
        for item in eligible[1:]
        if item.score is not None
        and best.score is not None
        and item.score - best.score <= cfg.near_best_score_delta
    )
    status: CandidateRankingStatus
    recommended: str | None
    reasons: tuple[str, ...]
    if (
        best.score is None
        or best.score > cfg.maximum_recommended_score
        or best.observed_components < cfg.minimum_observed_evidence_components
    ):
        status, recommended, reasons = (
            CandidateRankingStatus.INSUFFICIENT_EVIDENCE,
            None,
            ("insufficient_evidence",),
        )
    elif margin is not None and margin < cfg.minimum_score_margin:
        status, recommended, reasons = (
            CandidateRankingStatus.AMBIGUOUS,
            None,
            ("score_margin_too_small",),
        )
    else:
        status = CandidateRankingStatus.RECOMMENDED
        recommended = best.candidate_id
        reasons = ("best_of_incomplete_search",) if not search_complete else ()
    return GapCandidateRanking(
        gap_id, status, candidates, recommended, best.score, margin, near, search_complete, reasons
    )


def _search_incomplete(evaluation: Any) -> bool:
    if evaluation is None or not evaluation.queried:
        return False
    document = evaluation.routing_document()
    search = document.get("search") if document else None
    return not isinstance(search, dict) or search.get("exhaustive") is not True


def _audit_failed(candidate: Any) -> bool:
    audit = candidate.as_dict().get("audit")
    return isinstance(audit, dict) and audit.get("status") not in (None, "PASS", "WARN")


def _candidate_id(provider: CandidateProvider, source_id: str, points: tuple[Point, ...]) -> str:
    encoded = json.dumps(
        (
            provider.value,
            source_id,
            tuple(
                tuple(value if math.isfinite(value) else str(value) for value in point)
                for point in points
            ),
        ),
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + sha256(encoded.encode()).hexdigest()


def _valid_point(point: Point) -> bool:
    return (
        len(point) == 2
        and all(isinstance(v, int | float) and math.isfinite(v) for v in point)
        and -90 <= point[0] <= 90
        and -180 <= point[1] <= 180
    )


def _band(value: float, good: float, fair: float) -> int:
    return 0 if value <= good else 1 if value <= fair else 2


def _prefix(points: tuple[Point, ...], length: float) -> tuple[Point, ...]:
    result: list[Point] = [points[0]]
    remaining = length
    for a, b in pairwise(points):
        span = geodesic_distance_m(*a, *b)
        if span <= remaining:
            result.append(b)
            remaining -= span
            continue
        if span > 0:
            ratio = remaining / span
            result.append((a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio))
        break
    return tuple(result)


def _point_at(points: tuple[Point, ...], target: float) -> Point:
    for a, b in pairwise(points):
        span = geodesic_distance_m(*a, *b)
        if span >= target and span > 0:
            ratio = target / span
            return a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio
        target -= span
    return points[-1]


def _xy(origin: Point, point: Point) -> Point:
    lat = math.radians((origin[0] + point[0]) / 2)
    return ((point[1] - origin[1]) * 111_195 * math.cos(lat), (point[0] - origin[0]) * 111_195)


def _distance_to_polyline(point: Point, line: tuple[Point, ...]) -> float:
    best = math.inf
    for a, b in pairwise(line):
        bx, by = _xy(a, b)
        px, py = _xy(a, point)
        denom = bx * bx + by * by
        t = max(0.0, min(1.0, (px * bx + py * by) / denom)) if denom else 0.0
        best = min(best, math.hypot(px - t * bx, py - t * by))
    return best


def _angle(a: Point, b: Point, c: Point, d: Point) -> float:
    v1, v2 = _xy(a, b), _xy(c, d)
    cosine = max(
        -1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (math.hypot(*v1) * math.hypot(*v2)))
    )
    return math.degrees(math.acos(cosine))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
