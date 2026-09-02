"""Explicit course-backed completion of missing endpoint coordinates."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite

from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord, FitPreservationData
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityReport,
    TransitionClassification,
)
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CandidateCoordinate,
    CourseAnchorMatch,
    CourseData,
    CourseDirection,
    CourseSegment,
    MissingCourseCompletionPlan,
    MissingCourseRun,
    MissingCourseRunKind,
    ReconstructionReason,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedMissingCourseRun,
)
from warpbuster.reconstruction.course import _anchor_matches, _coordinate_at_distance

_COORDINATE_FIELDS = ("position_lat", "position_long")


@dataclass(frozen=True, slots=True)
class _ObservedAlignment:
    run_start_record_index: int
    run_end_record_index: int
    before: CourseAnchorMatch
    after: CourseAnchorMatch
    direction: CourseDirection
    observed_distance_m: float
    course_span_distance_m: float
    distance_ratio_error: float


@dataclass(frozen=True, slots=True)
class _AlignmentCandidate:
    before: CourseAnchorMatch
    after: CourseAnchorMatch
    direction: CourseDirection
    observed_distance_m: float
    course_span_distance_m: float
    distance_ratio_error: float
    score_m: float


def build_missing_course_plan(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    config: CourseReconstructionConfig | None = None,
) -> RepairPlan:
    """Build opt-in MEDIUM candidates for missing prefix/suffix positions only."""
    effective_config = config or CourseReconstructionConfig()
    targets = _endpoint_targets(activity)
    if not targets:
        return _plan(
            activity,
            course,
            status=RepairPlanStatus.NOT_NEEDED,
            candidates=(),
            unresolved=(),
            reasons=(ReconstructionReason.MISSING_COMPLETION_ENABLED,),
        )

    alignment, alignment_reason = _observed_alignment(
        activity,
        integrity,
        course,
        effective_config,
    )
    if alignment is None:
        unresolved = tuple(
            UnresolvedMissingCourseRun(
                interval=target,
                confidence=IntegrityConfidence.LOW,
                reasons=(alignment_reason,),
            )
            for target in targets
        )
        return _plan(
            activity,
            course,
            status=RepairPlanStatus.REFUSED,
            candidates=(),
            unresolved=unresolved,
            reasons=(ReconstructionReason.NO_INTERVAL_READY,),
        )

    candidates: list[MissingCourseCompletionPlan] = []
    unresolved_runs: list[UnresolvedMissingCourseRun] = []
    for target in targets:
        result = _endpoint_completion(
            activity,
            integrity,
            course,
            alignment,
            target,
            effective_config,
        )
        if isinstance(result, MissingCourseCompletionPlan):
            candidates.append(result)
        else:
            unresolved_runs.append(result)

    status = RepairPlanStatus.PARTIAL if candidates else RepairPlanStatus.REFUSED
    reasons = (
        (ReconstructionReason.SOME_INTERVALS_UNRESOLVED,)
        if candidates and unresolved_runs
        else (
            (ReconstructionReason.MISSING_COMPLETION_CANDIDATE,)
            if candidates
            else (ReconstructionReason.NO_INTERVAL_READY,)
        )
    )
    return _plan(
        activity,
        course,
        status=status,
        candidates=tuple(candidates),
        unresolved=tuple(unresolved_runs),
        reasons=reasons,
    )


def _endpoint_targets(activity: ActivityData) -> tuple[MissingCourseRun, ...]:
    records = activity.records
    if not records:
        return ()
    positioned = [record.index for record in records if _has_position(record)]
    if not positioned:
        return ()
    first = positioned[0]
    last = positioned[-1]
    targets: list[MissingCourseRun] = []
    if first > 0 and all(not _has_position(record) for record in records[:first]):
        targets.append(
            MissingCourseRun(
                start_record_index=0,
                end_record_index=first - 1,
                start_timestamp=records[0].timestamp,
                end_timestamp=records[first - 1].timestamp,
                kind=MissingCourseRunKind.PREFIX,
            )
        )
    if last + 1 < len(records) and all(not _has_position(record) for record in records[last + 1 :]):
        targets.append(
            MissingCourseRun(
                start_record_index=last + 1,
                end_record_index=len(records) - 1,
                start_timestamp=records[last + 1].timestamp,
                end_timestamp=records[-1].timestamp,
                kind=MissingCourseRunKind.SUFFIX,
            )
        )
    return tuple(targets)


def _observed_alignment(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    config: CourseReconstructionConfig,
) -> tuple[_ObservedAlignment | None, ReconstructionReason]:
    runs = _position_runs(activity)
    if not runs:
        return None, ReconstructionReason.NO_STABLE_POSITION_RUN
    run_start, run_end = max(runs, key=lambda bounds: bounds[1] - bounds[0] + 1)
    if run_end - run_start + 1 < config.missing_alignment_min_position_records:
        return None, ReconstructionReason.NO_STABLE_POSITION_RUN
    classifications = {
        (transition.from_record_index, transition.to_record_index): transition.classification
        for transition in integrity.transitions
    }
    if any(
        classifications.get((index, index + 1)) is not TransitionClassification.NORMAL
        for index in range(run_start, run_end)
    ):
        return None, ReconstructionReason.NO_STABLE_POSITION_RUN

    before_matches = _anchor_matches(activity.records[run_start], course, config)
    after_matches = _anchor_matches(activity.records[run_end], course, config)
    if not before_matches or not after_matches:
        return None, ReconstructionReason.ANCHOR_BEFORE_NOT_MATCHED
    observed_distance = _observed_run_distance(activity.records[run_start : run_end + 1])
    if observed_distance is None or observed_distance < config.minimum_course_span_m:
        return None, ReconstructionReason.OBSERVED_DISTANCE_INCONSISTENT

    candidates: list[_AlignmentCandidate] = []
    for before in before_matches:
        for after in after_matches:
            if before.course_segment_index != after.course_segment_index:
                continue
            span = abs(after.course_distance_m - before.course_distance_m)
            if span < config.minimum_course_span_m:
                continue
            ratio_error = abs(observed_distance / span - 1.0)
            if ratio_error > config.missing_alignment_max_distance_ratio_error:
                continue
            candidates.append(
                _AlignmentCandidate(
                    before=before,
                    after=after,
                    direction=(
                        CourseDirection.FORWARD
                        if after.course_distance_m > before.course_distance_m
                        else CourseDirection.REVERSE
                    ),
                    observed_distance_m=observed_distance,
                    course_span_distance_m=span,
                    distance_ratio_error=ratio_error,
                    score_m=(
                        before.anchor_distance_m
                        + after.anchor_distance_m
                        + abs(observed_distance - span)
                    ),
                )
            )
    if not candidates:
        return None, ReconstructionReason.OBSERVED_DISTANCE_INCONSISTENT
    candidates.sort(key=lambda candidate: candidate.score_m)
    best = candidates[0]
    if any(
        candidate.score_m <= best.score_m + config.ambiguity_score_margin_m
        and not _equivalent_alignment(best, candidate, config)
        for candidate in candidates[1:]
    ):
        return None, ReconstructionReason.OBSERVED_COURSE_ALIGNMENT_AMBIGUOUS
    return (
        _ObservedAlignment(
            run_start_record_index=run_start,
            run_end_record_index=run_end,
            before=best.before,
            after=best.after,
            direction=best.direction,
            observed_distance_m=best.observed_distance_m,
            course_span_distance_m=best.course_span_distance_m,
            distance_ratio_error=best.distance_ratio_error,
        ),
        ReconstructionReason.OBSERVED_COURSE_ALIGNMENT,
    )


def _endpoint_completion(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    alignment: _ObservedAlignment,
    target: MissingCourseRun,
    config: CourseReconstructionConfig,
) -> MissingCourseCompletionPlan | UnresolvedMissingCourseRun:
    if target.record_count > config.missing_completion_max_run_records:
        return _unresolved(target, ReconstructionReason.MISSING_RUN_TOO_LARGE)
    if not _position_fields_patchable(activity, target):
        return _unresolved(target, ReconstructionReason.MISSING_POSITION_FIELDS_UNAVAILABLE)
    if not _boundary_has_normal_context(activity, integrity, alignment, target, config):
        return _unresolved(target, ReconstructionReason.NO_STABLE_POSITION_RUN)

    segment = course.segments[alignment.before.course_segment_index]
    forward = alignment.direction is CourseDirection.FORWARD
    if target.kind is MissingCourseRunKind.PREFIX:
        observed_anchor_index = alignment.run_start_record_index
        observed_anchor = activity.records[observed_anchor_index]
        anchor_match = alignment.before
        endpoint_distance = 0.0 if forward else segment.length_m
        course_span = abs(anchor_match.course_distance_m - endpoint_distance)
        connector = anchor_match.anchor_distance_m
        path_distance = course_span + connector
        records = (
            *activity.records[target.start_record_index : target.end_record_index + 1],
            observed_anchor,
        )
        allocation = _allocation(records, path_distance, target.kind, config)
        if allocation is None:
            return _unresolved(target, ReconstructionReason.OBSERVED_DISTANCE_INCONSISTENT)
        method, fractions, _signal_distance = allocation
        updates = tuple(
            _prefix_coordinate(
                record,
                segment,
                endpoint_distance,
                anchor_match,
                observed_anchor,
                fraction,
                course_span,
                connector,
            )
            for record, fraction in zip(records[:-1], fractions, strict=True)
        )
        anchor_before = _endpoint_match(segment, endpoint_distance)
        anchor_after = anchor_match
        before_record_index = None
        after_record_index = observed_anchor_index
    else:
        observed_anchor_index = alignment.run_end_record_index
        observed_anchor = activity.records[observed_anchor_index]
        anchor_match = alignment.after
        endpoint_distance = segment.length_m if forward else 0.0
        course_span = abs(endpoint_distance - anchor_match.course_distance_m)
        connector = anchor_match.anchor_distance_m
        path_distance = connector + course_span
        records = (
            observed_anchor,
            *activity.records[target.start_record_index : target.end_record_index + 1],
        )
        allocation = _allocation(records, path_distance, target.kind, config)
        if allocation is None:
            return _unresolved(target, ReconstructionReason.OBSERVED_DISTANCE_INCONSISTENT)
        method, fractions, _signal_distance = allocation
        updates = tuple(
            _suffix_coordinate(
                record,
                segment,
                endpoint_distance,
                anchor_match,
                observed_anchor,
                fraction,
                course_span,
                connector,
            )
            for record, fraction in zip(records[1:], fractions, strict=True)
        )
        anchor_before = anchor_match
        anchor_after = _endpoint_match(segment, endpoint_distance)
        before_record_index = observed_anchor_index
        after_record_index = None

    elapsed_seconds = _elapsed_for_records(records)
    if (
        elapsed_seconds is None
        or elapsed_seconds <= 0
        or path_distance / elapsed_seconds > config.missing_completion_max_course_speed_mps
    ):
        return _unresolved(target, ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE)
    if not _candidate_transitions_plausible(
        records,
        updates,
        config.missing_completion_max_connector_speed_mps,
    ):
        return _unresolved(
            target,
            ReconstructionReason.MISSING_CANDIDATE_TRANSITION_IMPLAUSIBLE,
        )
    return MissingCourseCompletionPlan(
        interval=target,
        observed_run_start_record_index=alignment.run_start_record_index,
        observed_run_end_record_index=alignment.run_end_record_index,
        anchor_before_record_index=before_record_index,
        anchor_after_record_index=after_record_index,
        anchor_before=anchor_before,
        anchor_after=anchor_after,
        direction=alignment.direction,
        course_span_distance_m=course_span,
        course_apparent_speed_mps=path_distance / elapsed_seconds,
        anchor_connector_distance_m=connector,
        reconstruction_path_distance_m=path_distance,
        observed_distance_m=alignment.observed_distance_m,
        observed_course_span_distance_m=alignment.course_span_distance_m,
        observed_distance_ratio_error=alignment.distance_ratio_error,
        allocation_method=method,
        coordinate_updates=updates,
        fields_to_change=_COORDINATE_FIELDS,
        dependent_fields_to_recalculate=(),
        confidence=IntegrityConfidence.MEDIUM,
        repair_eligible=False,
        reasons=(
            ReconstructionReason.INTERVAL_MEDIUM_CONFIDENCE,
            ReconstructionReason.MISSING_COMPLETION_CANDIDATE,
            (
                ReconstructionReason.MISSING_PREFIX
                if target.kind is MissingCourseRunKind.PREFIX
                else ReconstructionReason.MISSING_SUFFIX
            ),
            ReconstructionReason.OBSERVED_COURSE_ALIGNMENT,
            ReconstructionReason.OBSERVED_DISTANCE_CONSISTENT,
            ReconstructionReason.COURSE_ENDPOINT_USED,
            ReconstructionReason.ANCHOR_CONNECTORS_PLAUSIBLE,
            _allocation_reason(method),
            ReconstructionReason.MISSING_COORDINATES_INFERRED,
            ReconstructionReason.RECORDED_DISTANCE_PRESERVED,
        ),
        reconstruction_scope_ranges=((target.start_record_index, target.end_record_index),),
    )


def _allocation(
    records: tuple[ActivityRecord, ...],
    path_distance_m: float,
    kind: MissingCourseRunKind,
    config: CourseReconstructionConfig,
) -> tuple[AllocationMethod, tuple[float, ...], float] | None:
    distances = tuple(record.distance for record in records)
    if all(value is not None and isfinite(value) for value in distances):
        numeric = tuple(float(value) for value in distances if value is not None)
        monotonic = all(current >= previous for previous, current in pairwise(numeric))
        signal_distance = numeric[-1] - numeric[0]
        if (
            monotonic
            and signal_distance > 0
            and _distance_matches(signal_distance, path_distance_m, config)
        ):
            fractions = (
                tuple((value - numeric[0]) / signal_distance for value in numeric[:-1])
                if kind is MissingCourseRunKind.PREFIX
                else tuple((value - numeric[0]) / signal_distance for value in numeric[1:])
            )
            if all(0.0 <= fraction <= 1.0 for fraction in fractions):
                return AllocationMethod.RECORDED_DISTANCE, fractions, signal_distance

    cumulative = _integrated_speed(records)
    if cumulative is not None and _distance_matches(cumulative[-1], path_distance_m, config):
        fractions = (
            tuple(value / cumulative[-1] for value in cumulative[:-1])
            if kind is MissingCourseRunKind.PREFIX
            else tuple(value / cumulative[-1] for value in cumulative[1:])
        )
        return AllocationMethod.RECORDED_SPEED, fractions, cumulative[-1]

    timestamps = tuple(record.timestamp for record in records)
    if all(timestamp is not None for timestamp in timestamps):
        concrete = tuple(timestamp for timestamp in timestamps if timestamp is not None)
        total = (concrete[-1] - concrete[0]).total_seconds()
        if total > 0:
            offsets = tuple((timestamp - concrete[0]).total_seconds() for timestamp in concrete)
            if all(current >= previous for previous, current in pairwise(offsets)):
                fractions = (
                    tuple(value / total for value in offsets[:-1])
                    if kind is MissingCourseRunKind.PREFIX
                    else tuple(value / total for value in offsets[1:])
                )
                return AllocationMethod.TIMESTAMPS, fractions, path_distance_m

    missing_count = len(records) - 1
    fractions = (
        tuple(index / missing_count for index in range(missing_count))
        if kind is MissingCourseRunKind.PREFIX
        else tuple(index / missing_count for index in range(1, missing_count + 1))
    )
    return AllocationMethod.RECORD_ORDER, fractions, path_distance_m


def _prefix_coordinate(
    record: ActivityRecord,
    segment: CourseSegment,
    endpoint_distance: float,
    match: CourseAnchorMatch,
    anchor: ActivityRecord,
    fraction: float,
    course_span_m: float,
    connector_m: float,
) -> CandidateCoordinate:
    travelled = fraction * (course_span_m + connector_m)
    if travelled <= course_span_m or connector_m <= 0:
        sign = 1.0 if match.course_distance_m >= endpoint_distance else -1.0
        course_distance = endpoint_distance + sign * min(travelled, course_span_m)
        latitude, longitude = _coordinate_at_distance(segment, course_distance)
    else:
        connector_fraction = min(1.0, (travelled - course_span_m) / connector_m)
        latitude, longitude = _interpolate(
            match.latitude,
            match.longitude,
            anchor.latitude,
            anchor.longitude,
            connector_fraction,
        )
        course_distance = match.course_distance_m
    return _candidate(record, latitude, longitude, course_distance)


def _suffix_coordinate(
    record: ActivityRecord,
    segment: CourseSegment,
    endpoint_distance: float,
    match: CourseAnchorMatch,
    anchor: ActivityRecord,
    fraction: float,
    course_span_m: float,
    connector_m: float,
) -> CandidateCoordinate:
    travelled = fraction * (connector_m + course_span_m)
    if travelled <= connector_m and connector_m > 0:
        latitude, longitude = _interpolate(
            anchor.latitude,
            anchor.longitude,
            match.latitude,
            match.longitude,
            travelled / connector_m,
        )
        course_distance = match.course_distance_m
    else:
        along_course = min(course_span_m, max(0.0, travelled - connector_m))
        sign = 1.0 if endpoint_distance >= match.course_distance_m else -1.0
        course_distance = match.course_distance_m + sign * along_course
        latitude, longitude = _coordinate_at_distance(segment, course_distance)
    return _candidate(record, latitude, longitude, course_distance)


def _candidate(
    record: ActivityRecord,
    latitude: float,
    longitude: float,
    course_distance_m: float,
) -> CandidateCoordinate:
    return CandidateCoordinate(
        record_index=record.index,
        timestamp=record.timestamp,
        original_latitude=record.latitude,
        original_longitude=record.longitude,
        candidate_latitude=latitude,
        candidate_longitude=longitude,
        course_distance_m=course_distance_m,
    )


def _candidate_transitions_plausible(
    records: tuple[ActivityRecord, ...],
    updates: tuple[CandidateCoordinate, ...],
    maximum_speed_mps: float,
) -> bool:
    update_by_index = {update.record_index: update for update in updates}
    coordinates: list[tuple[ActivityRecord, float, float]] = []
    for record in records:
        update = update_by_index.get(record.index)
        latitude = update.candidate_latitude if update is not None else record.latitude
        longitude = update.candidate_longitude if update is not None else record.longitude
        if latitude is None or longitude is None:
            return False
        coordinates.append((record, latitude, longitude))
    for previous, current in pairwise(coordinates):
        previous_record, previous_latitude, previous_longitude = previous
        current_record, current_latitude, current_longitude = current
        if previous_record.timestamp is None or current_record.timestamp is None:
            return False
        elapsed = (current_record.timestamp - previous_record.timestamp).total_seconds()
        if elapsed <= 0:
            return False
        distance = geodesic_distance_m(
            previous_latitude,
            previous_longitude,
            current_latitude,
            current_longitude,
        )
        if distance / elapsed > maximum_speed_mps:
            return False
    return True


def _boundary_has_normal_context(
    activity: ActivityData,
    integrity: IntegrityReport,
    alignment: _ObservedAlignment,
    target: MissingCourseRun,
    config: CourseReconstructionConfig,
) -> bool:
    classifications = {
        (transition.from_record_index, transition.to_record_index): transition.classification
        for transition in integrity.transitions
    }
    if target.kind is MissingCourseRunKind.PREFIX:
        start = alignment.run_start_record_index
        pairs = (
            (index, index + 1)
            for index in range(start, start + config.anchor_stability_min_normal_transitions)
        )
    else:
        end = alignment.run_end_record_index
        pairs = (
            (index - 1, index)
            for index in range(end, end - config.anchor_stability_min_normal_transitions, -1)
        )
    return all(
        0 <= before < after < len(activity.records)
        and classifications.get((before, after)) is TransitionClassification.NORMAL
        for before, after in pairs
    )


def _position_fields_patchable(activity: ActivityData, target: MissingCourseRun) -> bool:
    preservation = activity.preservation
    if not isinstance(preservation, FitPreservationData):
        return False
    for record in activity.records[target.start_record_index : target.end_record_index + 1]:
        fields = preservation.messages[record.source.message_index].fields
        if "position_lat" not in fields or "position_long" not in fields:
            return False
    return True


def _position_runs(activity: ActivityData) -> tuple[tuple[int, int], ...]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for record in activity.records:
        if _has_position(record):
            if start is None:
                start = record.index
        elif start is not None:
            runs.append((start, record.index - 1))
            start = None
    if start is not None:
        runs.append((start, len(activity.records) - 1))
    return tuple(runs)


def _observed_run_distance(records: tuple[ActivityRecord, ...]) -> float | None:
    distances = tuple(record.distance for record in records)
    if all(value is not None and isfinite(value) for value in distances):
        numeric = tuple(float(value) for value in distances if value is not None)
        if all(current >= previous for previous, current in pairwise(numeric)):
            delta = numeric[-1] - numeric[0]
            if delta > 0:
                return delta
    if any(not _has_position(record) for record in records):
        return None
    return sum(
        geodesic_distance_m(
            previous.latitude,
            previous.longitude,
            current.latitude,
            current.longitude,
        )
        for previous, current in pairwise(records)
        if previous.latitude is not None
        and previous.longitude is not None
        and current.latitude is not None
        and current.longitude is not None
    )


def _integrated_speed(records: tuple[ActivityRecord, ...]) -> tuple[float, ...] | None:
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
        if (
            previous.timestamp is None
            or current.timestamp is None
            or previous.speed is None
            or current.speed is None
        ):
            return None
        elapsed = (current.timestamp - previous.timestamp).total_seconds()
        if elapsed < 0:
            return None
        cumulative.append(cumulative[-1] + elapsed * (previous.speed + current.speed) / 2.0)
    return tuple(cumulative) if cumulative[-1] > 0 else None


def _distance_matches(
    signal_distance_m: float,
    path_distance_m: float,
    config: CourseReconstructionConfig,
) -> bool:
    return abs(signal_distance_m / path_distance_m - 1.0) <= (
        config.missing_alignment_max_distance_ratio_error
    )


def _elapsed_for_records(records: tuple[ActivityRecord, ...]) -> float | None:
    before = records[0].timestamp
    after = records[-1].timestamp
    if before is None or after is None:
        return None
    return (after - before).total_seconds()


def _endpoint_match(segment: CourseSegment, course_distance_m: float) -> CourseAnchorMatch:
    point = segment.points[0] if course_distance_m == 0.0 else segment.points[-1]
    adjacent = segment.points[1] if course_distance_m == 0.0 else segment.points[-2]
    return CourseAnchorMatch(
        course_segment_index=segment.index,
        segment_start_point_index=min(point.point_index, adjacent.point_index),
        segment_end_point_index=max(point.point_index, adjacent.point_index),
        segment_fraction=0.0 if course_distance_m == 0.0 else 1.0,
        course_distance_m=course_distance_m,
        latitude=point.latitude,
        longitude=point.longitude,
        anchor_distance_m=0.0,
    )


def _equivalent_alignment(
    first: _AlignmentCandidate,
    second: _AlignmentCandidate,
    config: CourseReconstructionConfig,
) -> bool:
    return (
        first.before.course_segment_index == second.before.course_segment_index
        and first.direction is second.direction
        and abs(first.before.course_distance_m - second.before.course_distance_m)
        <= config.anchor_candidate_deduplication_m
        and abs(first.after.course_distance_m - second.after.course_distance_m)
        <= config.anchor_candidate_deduplication_m
    )


def _interpolate(
    latitude_a: float | None,
    longitude_a: float | None,
    latitude_b: float | None,
    longitude_b: float | None,
    fraction: float,
) -> tuple[float, float]:
    if latitude_a is None or longitude_a is None or latitude_b is None or longitude_b is None:
        raise AssertionError("observed completion anchor must have coordinates")
    return (
        latitude_a + fraction * (latitude_b - latitude_a),
        longitude_a + fraction * (longitude_b - longitude_a),
    )


def _allocation_reason(method: AllocationMethod) -> ReconstructionReason:
    return {
        AllocationMethod.RECORDED_DISTANCE: ReconstructionReason.RECORDED_DISTANCE_ALLOCATION,
        AllocationMethod.RECORDED_SPEED: ReconstructionReason.RECORDED_SPEED_ALLOCATION,
        AllocationMethod.TIMESTAMPS: ReconstructionReason.TIMESTAMP_ALLOCATION,
        AllocationMethod.RECORD_ORDER: ReconstructionReason.RECORD_ORDER_ALLOCATION,
    }[method]


def _unresolved(
    target: MissingCourseRun,
    reason: ReconstructionReason,
) -> UnresolvedMissingCourseRun:
    return UnresolvedMissingCourseRun(
        interval=target,
        confidence=IntegrityConfidence.LOW,
        reasons=(reason,),
    )


def _has_position(record: ActivityRecord) -> bool:
    return record.latitude is not None and record.longitude is not None


def _plan(
    activity: ActivityData,
    course: CourseData,
    *,
    status: RepairPlanStatus,
    candidates: tuple[MissingCourseCompletionPlan, ...],
    unresolved: tuple[UnresolvedMissingCourseRun, ...],
    reasons: tuple[ReconstructionReason, ...],
) -> RepairPlan:
    preservation = activity.preservation
    return RepairPlan(
        activity_path=preservation.source_path,
        course_path=course.source_path,
        status=status,
        confidence=(IntegrityConfidence.MEDIUM if candidates else IntegrityConfidence.LOW),
        detected_interval_count=len(candidates) + len(unresolved),
        interval_plans=candidates,
        unresolved_intervals=(),
        reasons=reasons,
        timestamps_unchanged=True,
        trusted_records_unchanged=True,
        output_written=False,
        unresolved_missing_runs=unresolved,
        missing_completion_enabled=True,
    )
