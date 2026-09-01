"""Conservative GPX course matching and dry-run repair-plan generation."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from itertools import pairwise
from math import cos, isfinite, radians

from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    BridgeResult,
    CorruptedInterval,
    IntegrityConfidence,
    IntegrityReport,
    IntegrityStatus,
    IntervalDetectionKind,
    TransitionClassification,
    TransitionResult,
)
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CandidateCoordinate,
    CourseAnchorMatch,
    CourseBoundaryRefinement,
    CourseData,
    CourseDirection,
    CourseSegment,
    GnssComponentKind,
    GnssComponentState,
    IntervalRepairPlan,
    MixedGnssRegion,
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
    covered_interval_keys: set[tuple[int, int]] = set()
    for interval in intervals:
        interval_key = (interval.start_record_index, interval.end_record_index)
        if interval_key in covered_interval_keys:
            continue
        if (
            len(interval_plans) + len(unresolved)
            >= effective_config.maximum_reconstruction_intervals
        ):
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
            composite_region = result.composite_region
        else:
            unresolved.append(result)
            composite_region = result.mixed_region
        if composite_region is not None:
            covered_interval_keys.update(
                (candidate.start_record_index, candidate.end_record_index)
                for candidate in intervals
                if candidate.end_record_index >= composite_region.start_record_index
                and candidate.start_record_index <= composite_region.end_record_index
            )

    planning_unit_count = len(interval_plans) + len(unresolved)
    eligible_count = sum(plan.repair_eligible for plan in interval_plans)
    if eligible_count == planning_unit_count and not unresolved:
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
        detected_interval_count=planning_unit_count,
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
    if interval.confidence is IntegrityConfidence.LOW:
        return _unresolved(interval, ReconstructionReason.INTERVAL_CONFIDENCE_INSUFFICIENT)
    boundary_refinement: CourseBoundaryRefinement | None = None
    if interval.detection_kind is IntervalDetectionKind.ONE_SIDED_CLUSTER:
        refined = _refine_one_sided_boundaries(activity, integrity, interval, course, config)
        if isinstance(refined, ReconstructionReason):
            return _unresolved(interval, refined)
        interval, boundary_refinement = refined
    safety = assess_interval_safety(activity, integrity, interval, config)
    composite_region: MixedGnssRegion | None = None
    if not safety.anchors_stable:
        if safety.mixed_region is not None and safety.mixed_region.reconstructable:
            composite_region = safety.mixed_region
            interval = _composite_interval(activity, interval, composite_region)
            if (
                composite_region.outer_anchor_before is None
                or composite_region.outer_anchor_after is None
            ):
                raise AssertionError("reconstructable composite region must have outer anchors")
            safety = IntervalSafetyAssessment(
                anchor_before=composite_region.outer_anchor_before,
                anchor_after=composite_region.outer_anchor_after,
                mixed_region=composite_region,
            )
        else:
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
    matching_config = _matching_config(interval, config)
    before_record = activity.records[interval.trusted_before_record_index]
    after_record = activity.records[interval.trusted_after_record_index]
    before_matches = _anchor_matches(before_record, course, matching_config)
    after_matches = _anchor_matches(after_record, course, matching_config)
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

    ordered_pairs = _ordered_pairs(before_matches, after_matches, interval, matching_config)
    plausible_pairs = tuple(
        pair
        for pair in ordered_pairs
        if _reconstruction_path_distance(pair, interval) / interval.bridge.elapsed_seconds
        <= interval.bridge.maximum_plausible_speed_mps
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
        and not _equivalent_pair(best, candidate, matching_config)
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
    reconstruction_path_distance_m = _reconstruction_path_distance(best, interval)
    method, fractions = _allocation(
        activity,
        interval,
        reconstruction_path_distance_m,
        config,
    )
    direction = (
        CourseDirection.FORWARD
        if best.after.course_distance_m > best.before.course_distance_m
        else CourseDirection.REVERSE
    )
    updates = _candidate_updates(
        activity,
        interval,
        before_record,
        after_record,
        segment,
        best,
        fractions,
    )
    updates = _filter_composite_updates(updates, composite_region)
    if _uses_anchor_connectors(interval) and _has_abnormal_candidate_transition(
        activity, interval, updates, integrity
    ):
        timestamp_fractions = _timestamp_fractions(
            activity.records[
                interval.trusted_before_record_index : interval.trusted_after_record_index + 1
            ]
        )
        if timestamp_fractions is not None:
            method = AllocationMethod.TIMESTAMPS
            updates = _candidate_updates(
                activity,
                interval,
                before_record,
                after_record,
                segment,
                best,
                timestamp_fractions,
            )
            updates = _filter_composite_updates(updates, composite_region)
    if _uses_anchor_connectors(interval) and _has_impossible_candidate_transition(
        activity, interval, updates, integrity
    ):
        return _unresolved(
            interval,
            ReconstructionReason.CANDIDATE_TRANSITION_IMPLAUSIBLE,
            before_count=len(before_matches),
            after_count=len(after_matches),
            safety=safety,
        )
    high_confidence = (
        best.before.anchor_distance_m <= config.high_confidence_anchor_distance_m
        and best.after.anchor_distance_m <= config.high_confidence_anchor_distance_m
    )
    confidence = (
        IntegrityConfidence.HIGH
        if high_confidence and interval.confidence is IntegrityConfidence.HIGH
        else IntegrityConfidence.MEDIUM
    )
    return IntervalRepairPlan(
        interval=interval,
        anchor_before=best.before,
        anchor_after=best.after,
        direction=direction,
        course_span_distance_m=best.span_distance_m,
        course_apparent_speed_mps=best.apparent_speed_mps,
        anchor_connector_distance_m=(reconstruction_path_distance_m - best.span_distance_m),
        reconstruction_path_distance_m=reconstruction_path_distance_m,
        allocation_method=method,
        coordinate_updates=updates,
        fields_to_change=_COORDINATE_FIELDS,
        dependent_fields_to_recalculate=_DEPENDENT_FIELDS,
        confidence=confidence,
        repair_eligible=confidence is IntegrityConfidence.HIGH,
        reasons=(
            (
                ReconstructionReason.INTERVAL_HIGH_CONFIDENCE
                if interval.confidence is IntegrityConfidence.HIGH
                else ReconstructionReason.INTERVAL_MEDIUM_CONFIDENCE
            ),
            ReconstructionReason.ANCHORS_MATCHED,
            ReconstructionReason.UNIQUE_COURSE_MATCH,
            ReconstructionReason.TEMPORAL_ORDER_PRESERVED,
            ReconstructionReason.COURSE_SPEED_PLAUSIBLE,
            *(
                (ReconstructionReason.ANCHOR_CONNECTORS_PLAUSIBLE,)
                if _uses_anchor_connectors(interval)
                else ()
            ),
            _allocation_reason(method),
            *((ReconstructionReason.ONE_SIDED_BOUNDARIES_REFINED,) if boundary_refinement else ()),
            *(
                (
                    ReconstructionReason.COMPOSITE_REGION_EXPANDED,
                    ReconstructionReason.MISSING_COORDINATES_INFERRED,
                )
                if composite_region is not None
                else ()
            ),
        ),
        anchor_before_stability=safety.anchor_before,
        anchor_after_stability=safety.anchor_after,
        boundary_refinement=boundary_refinement,
        composite_region=composite_region,
        reconstruction_scope_ranges=_update_ranges(updates),
    )


def _composite_interval(
    activity: ActivityData,
    interval: CorruptedInterval,
    region: MixedGnssRegion,
) -> CorruptedInterval:
    """Expand one detected core to a course-independent composite diagnostic region."""
    before_index = region.proposed_trusted_before_record_index
    after_index = region.proposed_trusted_after_record_index
    if (
        before_index is None
        or after_index is None
        or region.bridge_elapsed_seconds is None
        or region.bridge_distance_m is None
        or region.bridge_speed_mps is None
    ):
        raise AssertionError("reconstructable composite region must have a measured bridge")
    bridge = BridgeResult(
        from_record_index=before_index,
        to_record_index=after_index,
        elapsed_seconds=region.bridge_elapsed_seconds,
        distance_m=region.bridge_distance_m,
        apparent_speed_mps=region.bridge_speed_mps,
        maximum_plausible_speed_mps=region.bridge_speed_limit_mps,
    )
    return replace(
        interval,
        start_record_index=region.start_record_index,
        end_record_index=region.end_record_index,
        start_timestamp=activity.records[region.start_record_index].timestamp,
        end_timestamp=activity.records[region.end_record_index].timestamp,
        trusted_before_record_index=before_index,
        trusted_after_record_index=after_index,
        bridge=bridge,
        confidence=IntegrityConfidence.MEDIUM,
        detection_kind=IntervalDetectionKind.COMPOSITE_REGION,
    )


def _refine_one_sided_boundaries(
    activity: ActivityData,
    integrity: IntegrityReport,
    interval: CorruptedInterval,
    course: CourseData,
    config: CourseReconstructionConfig,
) -> tuple[CorruptedInterval, CourseBoundaryRefinement] | ReconstructionReason:
    """Expand a detected core to sustained course-corridor boundaries.

    The detector remains course-independent. This reconstruction-only refinement
    prevents locally speed-plausible points inside a gradual GNSS drift from being
    promoted to trusted anchors.
    """
    transition_by_pair = {
        (transition.from_record_index, transition.to_record_index): transition
        for transition in integrity.transitions
    }
    before_index = _stable_course_corridor_anchor(
        activity,
        course,
        config,
        transition_by_pair,
        interval.trusted_before_record_index,
        direction=-1,
    )
    if before_index is None:
        return ReconstructionReason.ONE_SIDED_BOUNDARY_BEFORE_NOT_FOUND
    after_index = _stable_course_corridor_anchor(
        activity,
        course,
        config,
        transition_by_pair,
        interval.trusted_after_record_index,
        direction=1,
    )
    if after_index is None:
        return ReconstructionReason.ONE_SIDED_BOUNDARY_AFTER_NOT_FOUND

    before = activity.records[before_index]
    after = activity.records[after_index]
    if (
        before.timestamp is None
        or after.timestamp is None
        or before.latitude is None
        or before.longitude is None
        or after.latitude is None
        or after.longitude is None
    ):
        return ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE
    elapsed_seconds = (after.timestamp - before.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE
    bridge_distance_m = geodesic_distance_m(
        before.latitude,
        before.longitude,
        after.latitude,
        after.longitude,
    )
    bridge = BridgeResult(
        from_record_index=before_index,
        to_record_index=after_index,
        elapsed_seconds=elapsed_seconds,
        distance_m=bridge_distance_m,
        apparent_speed_mps=bridge_distance_m / elapsed_seconds,
        maximum_plausible_speed_mps=interval.bridge.maximum_plausible_speed_mps,
    )
    if bridge.apparent_speed_mps > bridge.maximum_plausible_speed_mps:
        return ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE

    refined_start = before_index + 1
    refined_end = after_index - 1
    refined_interval = replace(
        interval,
        start_record_index=refined_start,
        end_record_index=refined_end,
        start_timestamp=activity.records[refined_start].timestamp,
        end_timestamp=activity.records[refined_end].timestamp,
        trusted_before_record_index=before_index,
        trusted_after_record_index=after_index,
        bridge=bridge,
    )
    diagnostic = CourseBoundaryRefinement(
        detected_start_record_index=interval.start_record_index,
        detected_end_record_index=interval.end_record_index,
        original_trusted_before_record_index=interval.trusted_before_record_index,
        original_trusted_after_record_index=interval.trusted_after_record_index,
        refined_start_record_index=refined_start,
        refined_end_record_index=refined_end,
        refined_trusted_before_record_index=before_index,
        refined_trusted_after_record_index=after_index,
        expanded_before_record_count=interval.start_record_index - refined_start,
        expanded_after_record_count=refined_end - interval.end_record_index,
        corridor_tolerance_m=config.one_sided_drift_corridor_tolerance_m,
        required_stable_record_count=config.one_sided_drift_stable_record_count,
        reasons=(
            ReconstructionReason.STABLE_COURSE_CORRIDOR,
            ReconstructionReason.ONE_SIDED_BOUNDARIES_REFINED,
        ),
    )
    return refined_interval, diagnostic


def _stable_course_corridor_anchor(
    activity: ActivityData,
    course: CourseData,
    config: CourseReconstructionConfig,
    transition_by_pair: dict[tuple[int, int], TransitionResult],
    start_index: int,
    *,
    direction: int,
) -> int | None:
    search_end = max(0, start_index - config.one_sided_drift_search_max_records)
    if direction > 0:
        search_end = min(
            len(activity.records) - 1,
            start_index + config.one_sided_drift_search_max_records,
        )
    index = start_index
    while index >= search_end if direction < 0 else index <= search_end:
        context_start = index - config.one_sided_drift_stable_record_count + 1
        context_end = index
        if direction > 0:
            context_start = index
            context_end = index + config.one_sided_drift_stable_record_count - 1
        if (
            context_start >= 0
            and context_end < len(activity.records)
            and _is_stable_corridor(
                activity,
                course,
                config,
                transition_by_pair,
                context_start,
                context_end,
            )
        ):
            return index
        index += direction
    return None


def _is_stable_corridor(
    activity: ActivityData,
    course: CourseData,
    config: CourseReconstructionConfig,
    transition_by_pair: dict[tuple[int, int], TransitionResult],
    start_index: int,
    end_index: int,
) -> bool:
    corridor_config = replace(
        config,
        anchor_match_tolerance_m=config.one_sided_drift_corridor_tolerance_m,
        high_confidence_anchor_distance_m=min(
            config.high_confidence_anchor_distance_m,
            config.one_sided_drift_corridor_tolerance_m,
        ),
    )
    records = activity.records[start_index : end_index + 1]
    if any(not _anchor_matches(record, course, corridor_config) for record in records):
        return False
    return all(
        (
            (transition := transition_by_pair.get((index, index + 1))) is not None
            and transition.classification is TransitionClassification.NORMAL
        )
        for index in range(start_index, end_index)
    )


def _matching_config(
    interval: CorruptedInterval,
    config: CourseReconstructionConfig,
) -> CourseReconstructionConfig:
    if interval.detection_kind is not IntervalDetectionKind.ONE_SIDED_CLUSTER:
        return config
    return replace(
        config,
        anchor_match_tolerance_m=config.one_sided_anchor_match_tolerance_m,
        anchor_candidate_deduplication_m=(config.one_sided_anchor_candidate_deduplication_m),
    )


def _reconstruction_path_distance(
    pair: _CoursePair,
    interval: CorruptedInterval,
) -> float:
    if not _uses_anchor_connectors(interval):
        return pair.span_distance_m
    return pair.before.anchor_distance_m + pair.span_distance_m + pair.after.anchor_distance_m


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

    timestamp_fractions = _timestamp_fractions(records)
    if timestamp_fractions is not None:
        return AllocationMethod.TIMESTAMPS, timestamp_fractions

    return AllocationMethod.RECORD_ORDER, tuple(
        position / (target_count + 1) for position in range(1, target_count + 1)
    )


def _timestamp_fractions(
    records: tuple[ActivityRecord, ...],
) -> tuple[float, ...] | None:
    timestamps = tuple(record.timestamp for record in records)
    if not all(timestamp is not None for timestamp in timestamps):
        return None
    concrete_timestamps = tuple(timestamp for timestamp in timestamps if timestamp is not None)
    elapsed_seconds = (concrete_timestamps[-1] - concrete_timestamps[0]).total_seconds()
    elapsed_offsets = tuple(
        (timestamp - concrete_timestamps[0]).total_seconds() for timestamp in concrete_timestamps
    )
    if elapsed_seconds <= 0 or any(
        current < previous for previous, current in pairwise(elapsed_offsets)
    ):
        return None
    return tuple(elapsed / elapsed_seconds for elapsed in elapsed_offsets[1:-1])


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


def _candidate_coordinate_for_pair(
    record: ActivityRecord,
    before_record: ActivityRecord,
    after_record: ActivityRecord,
    segment: CourseSegment,
    pair: _CoursePair,
    fraction: float,
    detection_kind: IntervalDetectionKind,
) -> CandidateCoordinate:
    if detection_kind not in {
        IntervalDetectionKind.ONE_SIDED_CLUSTER,
        IntervalDetectionKind.COMPOSITE_REGION,
    }:
        return _candidate_coordinate(
            record,
            segment,
            pair.before.course_distance_m
            + fraction * (pair.after.course_distance_m - pair.before.course_distance_m),
        )
    if before_record.latitude is None or before_record.longitude is None:
        raise AssertionError("trusted before anchor must have coordinates")
    if after_record.latitude is None or after_record.longitude is None:
        raise AssertionError("trusted after anchor must have coordinates")
    before_connector_m = pair.before.anchor_distance_m
    course_span_m = pair.span_distance_m
    after_connector_m = pair.after.anchor_distance_m
    total_m = before_connector_m + course_span_m + after_connector_m
    travelled_m = fraction * total_m
    if travelled_m <= before_connector_m and before_connector_m > 0:
        connector_fraction = travelled_m / before_connector_m
        latitude, longitude = _interpolate_coordinate(
            before_record.latitude,
            before_record.longitude,
            pair.before.latitude,
            pair.before.longitude,
            connector_fraction,
        )
        course_distance_m = pair.before.course_distance_m
    elif travelled_m <= before_connector_m + course_span_m:
        course_fraction = (
            0.0 if course_span_m <= 0 else (travelled_m - before_connector_m) / course_span_m
        )
        course_distance_m = pair.before.course_distance_m + course_fraction * (
            pair.after.course_distance_m - pair.before.course_distance_m
        )
        latitude, longitude = _coordinate_at_distance(segment, course_distance_m)
    else:
        connector_fraction = (
            1.0
            if after_connector_m <= 0
            else (travelled_m - before_connector_m - course_span_m) / after_connector_m
        )
        latitude, longitude = _interpolate_coordinate(
            pair.after.latitude,
            pair.after.longitude,
            after_record.latitude,
            after_record.longitude,
            connector_fraction,
        )
        course_distance_m = pair.after.course_distance_m
    return CandidateCoordinate(
        record_index=record.index,
        timestamp=record.timestamp,
        original_latitude=record.latitude,
        original_longitude=record.longitude,
        candidate_latitude=latitude,
        candidate_longitude=longitude,
        course_distance_m=course_distance_m,
    )


def _uses_anchor_connectors(interval: CorruptedInterval) -> bool:
    return interval.detection_kind in {
        IntervalDetectionKind.ONE_SIDED_CLUSTER,
        IntervalDetectionKind.COMPOSITE_REGION,
    }


def _filter_composite_updates(
    updates: tuple[CandidateCoordinate, ...],
    region: MixedGnssRegion | None,
) -> tuple[CandidateCoordinate, ...]:
    if region is None:
        return updates
    allowed_indices = {
        record_index
        for component in region.components
        if component.kind is GnssComponentKind.MISSING
        or component.state
        in {
            GnssComponentState.PROVEN_CORRUPTED,
            GnssComponentState.TAINTED,
        }
        for record_index in range(component.start_record_index, component.end_record_index + 1)
    }
    return tuple(update for update in updates if update.record_index in allowed_indices)


def _update_ranges(
    updates: tuple[CandidateCoordinate, ...],
) -> tuple[tuple[int, int], ...]:
    if not updates:
        return ()
    ranges: list[tuple[int, int]] = []
    start = updates[0].record_index
    end = start
    for update in updates[1:]:
        if update.record_index == end + 1:
            end = update.record_index
            continue
        ranges.append((start, end))
        start = update.record_index
        end = start
    ranges.append((start, end))
    return tuple(ranges)


def _candidate_updates(
    activity: ActivityData,
    interval: CorruptedInterval,
    before_record: ActivityRecord,
    after_record: ActivityRecord,
    segment: CourseSegment,
    pair: _CoursePair,
    fractions: tuple[float, ...],
) -> tuple[CandidateCoordinate, ...]:
    return tuple(
        _candidate_coordinate_for_pair(
            activity.records[record_index],
            before_record,
            after_record,
            segment,
            pair,
            fraction,
            interval.detection_kind,
        )
        for record_index, fraction in zip(
            range(interval.start_record_index, interval.end_record_index + 1),
            fractions,
            strict=True,
        )
    )


def _has_abnormal_candidate_transition(
    activity: ActivityData,
    interval: CorruptedInterval,
    updates: tuple[CandidateCoordinate, ...],
    integrity: IntegrityReport,
) -> bool:
    measurements = _candidate_transition_measurements(
        activity,
        interval,
        updates,
    )
    if measurements is None:
        return True
    for elapsed_seconds, distance_m in measurements:
        speed_mps = distance_m / elapsed_seconds
        impossible_limit = integrity.config.absolute_impossible_speed_mps
        if (
            impossible_limit is not None
            and speed_mps >= impossible_limit
            and distance_m >= integrity.config.absolute_impossible_distance_m
        ):
            return True
        suspicious_limit = integrity.baseline.relative_suspicious_threshold_mps
        if (
            suspicious_limit is not None
            and speed_mps >= suspicious_limit
            and distance_m >= integrity.config.relative_suspicious_distance_m
        ):
            return True
    return False


def _has_impossible_candidate_transition(
    activity: ActivityData,
    interval: CorruptedInterval,
    updates: tuple[CandidateCoordinate, ...],
    integrity: IntegrityReport,
) -> bool:
    speed_limit = integrity.config.absolute_impossible_speed_mps
    measurements = _candidate_transition_measurements(
        activity,
        interval,
        updates,
    )
    if measurements is None:
        return True
    if speed_limit is None:
        return False
    for elapsed_seconds, distance_m in measurements:
        if (
            distance_m >= integrity.config.absolute_impossible_distance_m
            and distance_m / elapsed_seconds >= speed_limit
        ):
            return True
    return False


def _candidate_transition_measurements(
    activity: ActivityData,
    interval: CorruptedInterval,
    updates: tuple[CandidateCoordinate, ...],
) -> tuple[tuple[float, float], ...] | None:
    update_by_index = {update.record_index: update for update in updates}
    coordinates = []
    for record in activity.records[
        interval.trusted_before_record_index : interval.trusted_after_record_index + 1
    ]:
        update = update_by_index.get(record.index)
        coordinates.append(
            (
                record.timestamp,
                update.candidate_latitude if update is not None else record.latitude,
                update.candidate_longitude if update is not None else record.longitude,
            )
        )
    measurements: list[tuple[float, float]] = []
    for previous, current in pairwise(coordinates):
        previous_timestamp, previous_latitude, previous_longitude = previous
        current_timestamp, current_latitude, current_longitude = current
        if (
            previous_timestamp is None
            or current_timestamp is None
            or previous_latitude is None
            or previous_longitude is None
            or current_latitude is None
            or current_longitude is None
        ):
            return None
        elapsed_seconds = (current_timestamp - previous_timestamp).total_seconds()
        if elapsed_seconds <= 0:
            return None
        distance_m = geodesic_distance_m(
            previous_latitude,
            previous_longitude,
            current_latitude,
            current_longitude,
        )
        measurements.append((elapsed_seconds, distance_m))
    return tuple(measurements)


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
