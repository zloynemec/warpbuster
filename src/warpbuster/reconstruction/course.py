"""Conservative GPX course matching and dry-run repair-plan generation."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise
from math import cos, isfinite, radians

from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    CorruptedInterval,
    IntegrityConfidence,
    IntegrityReport,
    IntegrityStatus,
)
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CandidateCoordinate,
    CourseAnchorMatch,
    CourseData,
    CourseDirection,
    CourseSegment,
    IntervalRepairPlan,
    ReconstructionReason,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedInterval,
)
from warpbuster.reconstruction.safety import (
    IntervalSafetyAssessment,
    assess_interval_safety,
)

_METRES_PER_LATITUDE_DEGREE = 111_195.0
_COORDINATE_FIELDS = ("position_lat", "position_long")
_DEPENDENT_FIELDS = (
    "record.distance",
    "lap.total_distance",
    "lap.avg_speed",
    "session.total_distance",
    "session.avg_speed",
)


@dataclass(frozen=True, slots=True)
class _CoursePair:
    before: CourseAnchorMatch
    after: CourseAnchorMatch
    score_m: float
    span_distance_m: float
    apparent_speed_mps: float


def build_course_repair_plan(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    config: CourseReconstructionConfig | None = None,
) -> RepairPlan:
    """Build a declarative plan without mutating the activity or writing FIT."""
    effective_config = config or CourseReconstructionConfig()
    intervals = integrity.corrupted_intervals
    if not intervals:
        has_unreconstructable_findings = integrity.status in {
            IntegrityStatus.CORRUPTED,
            IntegrityStatus.SUSPICIOUS,
        }
        return _repair_plan(
            activity,
            course,
            status=(
                RepairPlanStatus.REFUSED
                if has_unreconstructable_findings
                else RepairPlanStatus.NOT_NEEDED
            ),
            confidence=(
                IntegrityConfidence.LOW
                if has_unreconstructable_findings
                else IntegrityConfidence.HIGH
            ),
            detected_interval_count=0,
            interval_plans=(),
            unresolved=(),
            reasons=(
                ReconstructionReason.DETECTION_HAS_NO_RECONSTRUCTABLE_INTERVAL
                if has_unreconstructable_findings
                else ReconstructionReason.NO_CORRUPTED_INTERVALS,
            ),
        )

    interval_plans: list[IntervalRepairPlan] = []
    unresolved: list[UnresolvedInterval] = []
    for interval_index, interval in enumerate(intervals):
        if interval_index >= effective_config.maximum_reconstruction_intervals:
            unresolved.append(
                _unresolved(
                    interval,
                    ReconstructionReason.RECONSTRUCTION_INTERVAL_LIMIT_EXCEEDED,
                )
            )
            continue
        result = _reconstruct_interval(
            activity,
            integrity,
            interval,
            course,
            effective_config,
        )
        if isinstance(result, IntervalRepairPlan):
            interval_plans.append(result)
        else:
            unresolved.append(result)

    eligible_count = sum(plan.repair_eligible for plan in interval_plans)
    if eligible_count == len(intervals) and not unresolved:
        status = RepairPlanStatus.READY
        confidence = IntegrityConfidence.HIGH
        reasons = (ReconstructionReason.ALL_INTERVALS_READY,)
    elif eligible_count > 0:
        status = RepairPlanStatus.PARTIAL
        confidence = IntegrityConfidence.LOW
        reasons = (ReconstructionReason.SOME_INTERVALS_UNRESOLVED,)
    else:
        status = RepairPlanStatus.REFUSED
        confidence = IntegrityConfidence.LOW
        reasons = (ReconstructionReason.NO_INTERVAL_READY,)
    return _repair_plan(
        activity,
        course,
        status=status,
        confidence=confidence,
        detected_interval_count=len(intervals),
        interval_plans=tuple(interval_plans),
        unresolved=tuple(unresolved),
        reasons=reasons,
    )


def _reconstruct_interval(
    activity: ActivityData,
    integrity: IntegrityReport,
    interval: CorruptedInterval,
    course: CourseData,
    config: CourseReconstructionConfig,
) -> IntervalRepairPlan | UnresolvedInterval:
    if interval.confidence is not IntegrityConfidence.HIGH:
        return _unresolved(interval, ReconstructionReason.INTERVAL_CONFIDENCE_INSUFFICIENT)
    safety = assess_interval_safety(activity, integrity, interval, config)
    if not safety.anchors_stable:
        reasons: list[ReconstructionReason] = []
        if not safety.anchor_before.stable:
            reasons.append(ReconstructionReason.ANCHOR_BEFORE_UNSTABLE)
        if not safety.anchor_after.stable:
            reasons.append(ReconstructionReason.ANCHOR_AFTER_UNSTABLE)
        reasons.append(ReconstructionReason.MIXED_GNSS_REGION)
        return _unresolved(
            interval,
            tuple(reasons),
            safety=safety,
        )
    before_record = activity.records[interval.trusted_before_record_index]
    after_record = activity.records[interval.trusted_after_record_index]
    before_matches = _anchor_matches(before_record, course, config)
    after_matches = _anchor_matches(after_record, course, config)
    if not before_matches or not after_matches:
        unmatched_reasons: list[ReconstructionReason] = []
        if not before_matches:
            unmatched_reasons.append(ReconstructionReason.ANCHOR_BEFORE_NOT_MATCHED)
        if not after_matches:
            unmatched_reasons.append(ReconstructionReason.ANCHOR_AFTER_NOT_MATCHED)
        return _unresolved(
            interval,
            tuple(unmatched_reasons),
            before_count=len(before_matches),
            after_count=len(after_matches),
            safety=safety,
        )

    ordered_pairs = _ordered_pairs(before_matches, after_matches, interval, config)
    plausible_pairs = tuple(
        pair
        for pair in ordered_pairs
        if pair.apparent_speed_mps <= interval.bridge.maximum_plausible_speed_mps
    )
    if not plausible_pairs:
        return _unresolved(
            interval,
            ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE,
            before_count=len(before_matches),
            after_count=len(after_matches),
            safety=safety,
        )
    best = plausible_pairs[0]
    if any(
        candidate.score_m <= best.score_m + config.ambiguity_score_margin_m
        and not _equivalent_pair(best, candidate, config)
        for candidate in plausible_pairs[1:]
    ):
        return _unresolved(
            interval,
            ReconstructionReason.COURSE_MATCH_AMBIGUOUS,
            before_count=len(before_matches),
            after_count=len(after_matches),
            safety=safety,
        )

    segment = course.segments[best.before.course_segment_index]
    method, fractions = _allocation(activity, interval, best.span_distance_m, config)
    direction = (
        CourseDirection.FORWARD
        if best.after.course_distance_m > best.before.course_distance_m
        else CourseDirection.REVERSE
    )
    updates = tuple(
        _candidate_coordinate(
            activity.records[record_index],
            segment,
            best.before.course_distance_m
            + fraction * (best.after.course_distance_m - best.before.course_distance_m),
        )
        for record_index, fraction in zip(
            range(interval.start_record_index, interval.end_record_index + 1),
            fractions,
            strict=True,
        )
    )
    high_confidence = (
        best.before.anchor_distance_m <= config.high_confidence_anchor_distance_m
        and best.after.anchor_distance_m <= config.high_confidence_anchor_distance_m
    )
    confidence = IntegrityConfidence.HIGH if high_confidence else IntegrityConfidence.MEDIUM
    return IntervalRepairPlan(
        interval=interval,
        anchor_before=best.before,
        anchor_after=best.after,
        direction=direction,
        course_span_distance_m=best.span_distance_m,
        course_apparent_speed_mps=best.apparent_speed_mps,
        allocation_method=method,
        coordinate_updates=updates,
        fields_to_change=_COORDINATE_FIELDS,
        dependent_fields_to_recalculate=_DEPENDENT_FIELDS,
        confidence=confidence,
        repair_eligible=high_confidence,
        reasons=(
            ReconstructionReason.INTERVAL_HIGH_CONFIDENCE,
            ReconstructionReason.ANCHORS_MATCHED,
            ReconstructionReason.UNIQUE_COURSE_MATCH,
            ReconstructionReason.TEMPORAL_ORDER_PRESERVED,
            ReconstructionReason.COURSE_SPEED_PLAUSIBLE,
            _allocation_reason(method),
        ),
        anchor_before_stability=safety.anchor_before,
        anchor_after_stability=safety.anchor_after,
    )


def _anchor_matches(
    record: ActivityRecord,
    course: CourseData,
    config: CourseReconstructionConfig,
) -> tuple[CourseAnchorMatch, ...]:
    if record.latitude is None or record.longitude is None:
        return ()
    candidates: list[CourseAnchorMatch] = []
    for segment in course.segments:
        for start, end in pairwise(segment.points):
            fraction, latitude, longitude = _project_onto_edge(
                record.latitude,
                record.longitude,
                start.latitude,
                start.longitude,
                end.latitude,
                end.longitude,
            )
            anchor_distance_m = geodesic_distance_m(
                record.latitude,
                record.longitude,
                latitude,
                longitude,
            )
            if anchor_distance_m > config.anchor_match_tolerance_m:
                continue
            edge_distance_m = end.cumulative_distance_m - start.cumulative_distance_m
            candidates.append(
                CourseAnchorMatch(
                    course_segment_index=segment.index,
                    segment_start_point_index=start.point_index,
                    segment_end_point_index=end.point_index,
                    segment_fraction=fraction,
                    course_distance_m=start.cumulative_distance_m + fraction * edge_distance_m,
                    latitude=latitude,
                    longitude=longitude,
                    anchor_distance_m=anchor_distance_m,
                )
            )
    candidates.sort(key=lambda candidate: candidate.anchor_distance_m)
    deduplicated: list[CourseAnchorMatch] = []
    for candidate in candidates:
        if any(
            existing.course_segment_index == candidate.course_segment_index
            and abs(existing.course_distance_m - candidate.course_distance_m)
            <= config.anchor_candidate_deduplication_m
            for existing in deduplicated
        ):
            continue
        deduplicated.append(candidate)
        if len(deduplicated) >= config.maximum_anchor_candidates:
            break
    return tuple(deduplicated)


def _ordered_pairs(
    before_matches: tuple[CourseAnchorMatch, ...],
    after_matches: tuple[CourseAnchorMatch, ...],
    interval: CorruptedInterval,
    config: CourseReconstructionConfig,
) -> tuple[_CoursePair, ...]:
    pairs: list[_CoursePair] = []
    for before in before_matches:
        for after in after_matches:
            if before.course_segment_index != after.course_segment_index:
                continue
            span_distance_m = abs(after.course_distance_m - before.course_distance_m)
            if span_distance_m < config.minimum_course_span_m:
                continue
            pairs.append(
                _CoursePair(
                    before=before,
                    after=after,
                    score_m=before.anchor_distance_m + after.anchor_distance_m,
                    span_distance_m=span_distance_m,
                    apparent_speed_mps=span_distance_m / interval.bridge.elapsed_seconds,
                )
            )
    return tuple(sorted(pairs, key=lambda pair: pair.score_m))


def _equivalent_pair(
    first: _CoursePair,
    second: _CoursePair,
    config: CourseReconstructionConfig,
) -> bool:
    return (
        first.before.course_segment_index == second.before.course_segment_index
        and abs(first.before.course_distance_m - second.before.course_distance_m)
        <= config.anchor_candidate_deduplication_m
        and abs(first.after.course_distance_m - second.after.course_distance_m)
        <= config.anchor_candidate_deduplication_m
    )


def _allocation(
    activity: ActivityData,
    interval: CorruptedInterval,
    course_span_distance_m: float,
    config: CourseReconstructionConfig,
) -> tuple[AllocationMethod, tuple[float, ...]]:
    records = activity.records[
        interval.trusted_before_record_index : interval.trusted_after_record_index + 1
    ]
    target_count = interval.record_count

    distances = tuple(record.distance for record in records)
    if all(value is not None and isfinite(value) for value in distances):
        numeric_distances = tuple(float(value) for value in distances if value is not None)
        distance_delta = numeric_distances[-1] - numeric_distances[0]
        if (
            distance_delta > 0
            and all(current >= previous for previous, current in pairwise(numeric_distances))
            and _signal_matches_course(distance_delta, course_span_distance_m, config)
        ):
            return AllocationMethod.RECORDED_DISTANCE, tuple(
                (value - numeric_distances[0]) / distance_delta for value in numeric_distances[1:-1]
            )

    speed_fractions = _speed_fractions(records, course_span_distance_m, config)
    if speed_fractions is not None:
        return AllocationMethod.RECORDED_SPEED, speed_fractions

    timestamps = tuple(record.timestamp for record in records)
    if all(timestamp is not None for timestamp in timestamps):
        concrete_timestamps = tuple(timestamp for timestamp in timestamps if timestamp is not None)
        elapsed_seconds = (concrete_timestamps[-1] - concrete_timestamps[0]).total_seconds()
        elapsed_offsets = tuple(
            (timestamp - concrete_timestamps[0]).total_seconds()
            for timestamp in concrete_timestamps
        )
        if elapsed_seconds > 0 and all(
            current >= previous for previous, current in pairwise(elapsed_offsets)
        ):
            return AllocationMethod.TIMESTAMPS, tuple(
                elapsed / elapsed_seconds for elapsed in elapsed_offsets[1:-1]
            )

    return AllocationMethod.RECORD_ORDER, tuple(
        position / (target_count + 1) for position in range(1, target_count + 1)
    )


def _speed_fractions(
    records: tuple[ActivityRecord, ...],
    course_span_distance_m: float,
    config: CourseReconstructionConfig,
) -> tuple[float, ...] | None:
    if any(
        record.speed is None
        or not isfinite(record.speed)
        or record.speed < 0
        or record.timestamp is None
        for record in records
    ):
        return None
    cumulative = [0.0]
    for previous, current in pairwise(records):
        if previous.timestamp is None or current.timestamp is None:
            return None
        elapsed_seconds = (current.timestamp - previous.timestamp).total_seconds()
        if elapsed_seconds < 0 or previous.speed is None or current.speed is None:
            return None
        cumulative.append(cumulative[-1] + elapsed_seconds * (previous.speed + current.speed) / 2.0)
    total = cumulative[-1]
    if total <= 0 or not _signal_matches_course(total, course_span_distance_m, config):
        return None
    return tuple(value / total for value in cumulative[1:-1])


def _signal_matches_course(
    signal_distance_m: float,
    course_span_distance_m: float,
    config: CourseReconstructionConfig,
) -> bool:
    ratio = signal_distance_m / course_span_distance_m
    return config.signal_course_length_ratio_min <= ratio <= config.signal_course_length_ratio_max


def _candidate_coordinate(
    record: ActivityRecord,
    segment: CourseSegment,
    course_distance_m: float,
) -> CandidateCoordinate:
    latitude, longitude = _coordinate_at_distance(segment, course_distance_m)
    return CandidateCoordinate(
        record_index=record.index,
        timestamp=record.timestamp,
        original_latitude=record.latitude,
        original_longitude=record.longitude,
        candidate_latitude=latitude,
        candidate_longitude=longitude,
        course_distance_m=course_distance_m,
    )


def _coordinate_at_distance(
    segment: CourseSegment,
    distance_m: float,
) -> tuple[float, float]:
    clamped = min(segment.length_m, max(0.0, distance_m))
    cumulative = tuple(point.cumulative_distance_m for point in segment.points)
    end_index = min(len(segment.points) - 1, max(1, bisect_right(cumulative, clamped)))
    start = segment.points[end_index - 1]
    end = segment.points[end_index]
    edge_distance = end.cumulative_distance_m - start.cumulative_distance_m
    fraction = (
        0.0 if edge_distance <= 0 else (clamped - start.cumulative_distance_m) / edge_distance
    )
    return _interpolate_coordinate(
        start.latitude,
        start.longitude,
        end.latitude,
        end.longitude,
        fraction,
    )


def _project_onto_edge(
    latitude: float,
    longitude: float,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> tuple[float, float, float]:
    reference_latitude = (start_latitude + end_latitude + latitude) / 3.0
    longitude_scale = _METRES_PER_LATITUDE_DEGREE * cos(radians(reference_latitude))
    edge_longitude_delta = _wrapped_longitude_delta(end_longitude, start_longitude)
    point_longitude_delta = _wrapped_longitude_delta(longitude, start_longitude)
    edge_x = edge_longitude_delta * longitude_scale
    edge_y = (end_latitude - start_latitude) * _METRES_PER_LATITUDE_DEGREE
    point_x = point_longitude_delta * longitude_scale
    point_y = (latitude - start_latitude) * _METRES_PER_LATITUDE_DEGREE
    denominator = edge_x * edge_x + edge_y * edge_y
    fraction = 0.0 if denominator == 0 else (point_x * edge_x + point_y * edge_y) / denominator
    fraction = min(1.0, max(0.0, fraction))
    projected_latitude, projected_longitude = _interpolate_coordinate(
        start_latitude,
        start_longitude,
        end_latitude,
        end_longitude,
        fraction,
    )
    return fraction, projected_latitude, projected_longitude


def _interpolate_coordinate(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    fraction: float,
) -> tuple[float, float]:
    longitude_delta = _wrapped_longitude_delta(end_longitude, start_longitude)
    longitude = ((start_longitude + longitude_delta * fraction + 180.0) % 360.0) - 180.0
    latitude = start_latitude + (end_latitude - start_latitude) * fraction
    return latitude, longitude


def _wrapped_longitude_delta(longitude: float, reference: float) -> float:
    return ((longitude - reference + 180.0) % 360.0) - 180.0


def _allocation_reason(method: AllocationMethod) -> ReconstructionReason:
    return {
        AllocationMethod.RECORDED_DISTANCE: ReconstructionReason.RECORDED_DISTANCE_ALLOCATION,
        AllocationMethod.RECORDED_SPEED: ReconstructionReason.RECORDED_SPEED_ALLOCATION,
        AllocationMethod.TIMESTAMPS: ReconstructionReason.TIMESTAMP_ALLOCATION,
        AllocationMethod.RECORD_ORDER: ReconstructionReason.RECORD_ORDER_ALLOCATION,
    }[method]


def _unresolved(
    interval: CorruptedInterval,
    reason: ReconstructionReason | tuple[ReconstructionReason, ...],
    *,
    before_count: int = 0,
    after_count: int = 0,
    safety: IntervalSafetyAssessment | None = None,
) -> UnresolvedInterval:
    return UnresolvedInterval(
        interval=interval,
        confidence=IntegrityConfidence.LOW,
        reasons=reason if isinstance(reason, tuple) else (reason,),
        anchor_before_candidate_count=before_count,
        anchor_after_candidate_count=after_count,
        anchor_before_stability=(safety.anchor_before if safety is not None else None),
        anchor_after_stability=(safety.anchor_after if safety is not None else None),
        mixed_region=safety.mixed_region if safety is not None else None,
    )


def _repair_plan(
    activity: ActivityData,
    course: CourseData,
    *,
    status: RepairPlanStatus,
    confidence: IntegrityConfidence,
    detected_interval_count: int,
    interval_plans: tuple[IntervalRepairPlan, ...],
    unresolved: tuple[UnresolvedInterval, ...],
    reasons: tuple[ReconstructionReason, ...],
) -> RepairPlan:
    return RepairPlan(
        activity_path=activity.preservation.source_path,
        course_path=course.source_path,
        status=status,
        confidence=confidence,
        detected_interval_count=detected_interval_count,
        interval_plans=interval_plans,
        unresolved_intervals=unresolved,
        reasons=reasons,
        timestamps_unchanged=True,
        trusted_records_unchanged=True,
        output_written=False,
    )
