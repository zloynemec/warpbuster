"""Independent, bounded GPX reconstruction of course-independent geometry gaps."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace
from hashlib import sha256
from itertools import pairwise, product
from math import cos, isfinite, radians

from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityReport,
    TransitionClassification,
)
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CandidateCoordinate,
    CoordinateDisposition,
    CoordinateState,
    CourseAnchorMatch,
    CourseData,
    CourseDirection,
    CoursePathProvenance,
    CourseSegment,
    GapRepairPlan,
    LocalAlignmentEvidence,
    MissingCourseRunKind,
    ReconstructionGap,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedGap,
)
from warpbuster.models.reconstruction import (
    ReconstructionReason as Reason,
)
from warpbuster.reconstruction.gaps import (
    CONFIDENCE_RANK,
    coordinate_mask,
    has_position,
    inventory_gaps,
    position_fields_patchable,
)
from warpbuster.reconstruction.geometry import (
    _interpolate_coordinate,
    _project_onto_edge,
    _wrapped_longitude_delta,
)
from warpbuster.reconstruction.signals import qualify_distance, qualify_speed
from warpbuster.reconstruction.timing import (
    AllocationClock,
    activity_clock,
    allocation_clock,
    timer_timeline,
)

_METRES_PER_DEGREE = 111_195.0


class _LimitReached(Exception):
    pass


@dataclass(frozen=True)
class _AnchorOption:
    match: CourseAnchorMatch
    direction: CourseDirection
    score: float
    evidence: LocalAlignmentEvidence


@dataclass(frozen=True)
class _Path:
    before: CourseAnchorMatch
    after: CourseAnchorMatch
    direction: CourseDirection
    score: float
    alignment_contexts: tuple[LocalAlignmentEvidence, ...]

    @property
    def span(self) -> float:
        return abs(self.after.course_distance_m - self.before.course_distance_m)

    @property
    def length(self) -> float:
        return self.span + self.before.anchor_distance_m + self.after.anchor_distance_m


class _CourseIndex:
    """Reuse edge bounding boxes, projections and chainage arrays across local gaps."""

    def __init__(self, course: CourseData, config: CourseReconstructionConfig):
        self.course = course
        self.source_hash = sha256(course.raw_bytes).hexdigest()
        self.config = config
        self.segments = {segment.index: segment for segment in course.segments}
        self.chainages = {
            segment.index: tuple(point.cumulative_distance_m for point in segment.points)
            for segment in course.segments
        }
        self.edges = tuple(
            (segment.index, a, b, min(a.latitude, b.latitude), max(a.latitude, b.latitude))
            for segment in course.segments
            for a, b in pairwise(segment.points)
        )
        self.cache: dict[int, tuple[CourseAnchorMatch, ...]] = {}

    def matches(self, record: ActivityRecord) -> tuple[CourseAnchorMatch, ...]:
        if record.index in self.cache:
            return self.cache[record.index]
        if record.latitude is None or record.longitude is None:
            return ()
        radius = self.config.anchor_match_tolerance_m / _METRES_PER_DEGREE
        longitude_scale = abs(cos(radians(record.latitude)))
        matches = []
        for segment_index, a, b, min_lat, max_lat in self.edges:
            if not min_lat - radius <= record.latitude <= max_lat + radius:
                continue
            edge_lon = _wrapped_longitude_delta(b.longitude, a.longitude)
            point_lon = _wrapped_longitude_delta(record.longitude, a.longitude)
            # Longitude bounds are advisory at the poles; exact distance still decides.
            if longitude_scale > 0 and not (
                min(0, edge_lon) - radius / longitude_scale
                <= point_lon
                <= max(0, edge_lon) + radius / longitude_scale
            ):
                continue
            fraction, lat, lon = _project_onto_edge(
                record.latitude, record.longitude, a.latitude, a.longitude, b.latitude, b.longitude
            )
            error = geodesic_distance_m(record.latitude, record.longitude, lat, lon)
            if error <= self.config.anchor_match_tolerance_m:
                matches.append(
                    CourseAnchorMatch(
                        segment_index,
                        a.point_index,
                        b.point_index,
                        fraction,
                        a.cumulative_distance_m
                        + fraction * (b.cumulative_distance_m - a.cumulative_distance_m),
                        lat,
                        lon,
                        error,
                    )
                )
        matches.sort(
            key=lambda match: (
                match.anchor_distance_m,
                match.course_segment_index,
                match.course_distance_m,
            )
        )
        distinct: list[CourseAnchorMatch] = []
        for match in matches:
            if any(
                item.course_segment_index == match.course_segment_index
                and abs(item.course_distance_m - match.course_distance_m)
                <= self.config.anchor_candidate_deduplication_m
                for item in distinct
            ):
                continue
            distinct.append(match)
            if len(distinct) > self.config.maximum_anchor_candidates:
                raise _LimitReached
        result = tuple(distinct)
        self.cache[record.index] = result
        return result

    def position(self, segment_index: int, chainage: float) -> tuple[float, float]:
        segment = self.segments[segment_index]
        clamped = min(segment.length_m, max(0.0, chainage))
        end = min(
            len(segment.points) - 1, max(1, bisect_right(self.chainages[segment_index], clamped))
        )
        a, b = segment.points[end - 1], segment.points[end]
        length = b.cumulative_distance_m - a.cumulative_distance_m
        fraction = (clamped - a.cumulative_distance_m) / length if length > 0 else 0.0
        return _interpolate_coordinate(a.latitude, a.longitude, b.latitude, b.longitude, fraction)


def build_repair_plan(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData | None = None,
    config: CourseReconstructionConfig | None = None,
    *,
    fill_missing_from_course: bool = False,
    minimum_invalidation_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> RepairPlan:
    """Plan all gaps from the original snapshot, with independent invalidation policy."""
    config = config or CourseReconstructionConfig()
    mask = coordinate_mask(activity, integrity, minimum_invalidation_confidence)
    gaps = inventory_gaps(activity, mask)
    index = _CourseIndex(course, config) if course is not None else None
    transitions = {
        (item.from_record_index, item.to_record_index): item.classification
        for item in integrity.transitions
    }
    timeline = timer_timeline(activity)
    candidates = []
    unresolved = []
    for number, gap in enumerate(gaps):
        start = gap.anchor_before_record_index
        end = gap.anchor_after_record_index
        clock = allocation_clock(
            activity.records[
                (start if start is not None else gap.start_record_index) : (
                    end if end is not None else gap.end_record_index
                )
                + 1
            ],
            timeline.pauses,
            open_starts=timeline.open_starts,
        )
        reason = (
            Reason.SEARCH_LIMIT_REACHED
            if number >= config.maximum_reconstruction_intervals
            else gap.reasons[0]
            if gap.reasons
            else Reason.NO_COURSE
            if index is None
            else Reason.MISSING_COMPLETION_DISABLED
            if not fill_missing_from_course
            and (gap.original_missing_count or gap.kind is not MissingCourseRunKind.INTERNAL)
            else Reason.SEARCH_LIMIT_REACHED
            if gap.record_count > config.missing_completion_max_run_records
            else Reason.POSITION_FIELDS_UNPATCHABLE
            if not position_fields_patchable(activity, gap)
            else None
        )
        if reason is not None:
            unresolved.append(UnresolvedGap(gap, (reason,), timing=clock.audit if clock else None))
            continue
        assert index is not None
        result = _reconstruct_gap(
            activity, mask, transitions, gap, index, config, integrity.config, clock=clock
        )
        if isinstance(result, GapRepairPlan):
            candidates.append(result)
        else:
            unresolved.append(replace(result, timing=clock.audit if clock else None))
    invalidations = any(item.state is CoordinateState.INVALIDATED for item in mask)
    status = (
        RepairPlanStatus.PARTIAL
        if (candidates or invalidations) and unresolved
        else RepairPlanStatus.READY
        if candidates or invalidations
        else RepairPlanStatus.REFUSED
        if gaps
        else RepairPlanStatus.NOT_NEEDED
    )
    return RepairPlan(
        activity_path=activity.preservation.source_path,
        course_path=course.source_path if course is not None else None,
        status=status,
        confidence=min(
            (item.confidence for item in candidates),
            key=CONFIDENCE_RANK.__getitem__,
            default=IntegrityConfidence.LOW,
        ),
        detected_interval_count=len(integrity.corrupted_intervals),
        interval_plans=tuple(candidates),
        unresolved_intervals=(),
        reasons=(
            Reason.SOME_INTERVALS_UNRESOLVED
            if status is RepairPlanStatus.PARTIAL
            else Reason.ALL_INTERVALS_READY
            if status is RepairPlanStatus.READY
            else Reason.NO_CORRUPTED_INTERVALS
            if status is RepairPlanStatus.NOT_NEEDED
            else Reason.NO_INTERVAL_READY,
        ),
        timestamps_unchanged=True,
        trusted_records_unchanged=True,
        output_written=False,
        missing_completion_enabled=fill_missing_from_course,
        coordinate_mask=mask,
        gaps=gaps,
        unresolved_gaps=tuple(unresolved),
        minimum_invalidation_confidence=minimum_invalidation_confidence,
        maximum_new_transition_speed_mps=config.missing_completion_max_connector_speed_mps,
    )


def _context(
    activity: ActivityData,
    mask: tuple[CoordinateDisposition, ...],
    transitions: dict[tuple[int, int], TransitionClassification],
    anchor_index: int,
    side: int,
    config: CourseReconstructionConfig,
) -> tuple[ActivityRecord, ...]:
    anchor = activity.records[anchor_index]
    if not mask[anchor_index].anchor_eligible or anchor.timestamp is None:
        return ()
    records = [anchor]
    current = anchor_index
    while len(records) < config.local_alignment_max_context_records:
        following = current + side
        if not 0 <= following < len(activity.records):
            break
        record = activity.records[following]
        pair = (min(current, following), max(current, following))
        if (
            not mask[following].anchor_eligible
            or record.continuity_id != anchor.continuity_id
            or record.timestamp is None
            or abs((record.timestamp - anchor.timestamp).total_seconds())
            > config.local_alignment_max_context_seconds
            or transitions.get(pair) is not TransitionClassification.NORMAL
        ):
            break
        records.append(record)
        current = following
    return tuple(records)


def _observed_cumulative(
    records: tuple[ActivityRecord, ...],
    maximum_speed_mps: float,
) -> tuple[tuple[float, ...], str] | None:
    values = tuple(record.distance for record in records)
    if all(value is not None and isfinite(value) for value in values):
        numeric = tuple(float(value) for value in values if value is not None)
        increments = tuple(b - a for a, b in pairwise(numeric))
        if all(value >= 0 for value in increments) or all(value <= 0 for value in increments):
            cumulative = tuple(abs(value - numeric[0]) for value in numeric)
            duration = (
                abs((records[-1].timestamp - records[0].timestamp).total_seconds())
                if records[-1].timestamp is not None and records[0].timestamp is not None
                else 0.0
            )
            if (
                cumulative[-1] > 0
                and duration > 0
                and cumulative[-1] / duration <= maximum_speed_mps
            ):
                return cumulative, "recorded_distance_source_unverified"
    cumulative_geometry = [0.0]
    for a, b in pairwise(records):
        if not has_position(a) or not has_position(b):
            return None
        assert a.latitude is not None and a.longitude is not None
        assert b.latitude is not None and b.longitude is not None
        cumulative_geometry.append(
            cumulative_geometry[-1]
            + geodesic_distance_m(a.latitude, a.longitude, b.latitude, b.longitude)
        )
    return (
        (tuple(cumulative_geometry), "preserved_gps_geometry")
        if cumulative_geometry[-1] > 0
        else None
    )


def _options(
    context: tuple[ActivityRecord, ...],
    side: int,
    index: _CourseIndex,
    config: CourseReconstructionConfig,
) -> tuple[tuple[_AnchorOption, ...], Reason]:
    progression = _observed_cumulative(context, config.missing_completion_max_course_speed_mps)
    if progression is None or progression[0][-1] < config.minimum_course_span_m:
        return (), Reason.LOCAL_DISTANCE_INCONSISTENT
    cumulative, source = progression
    observed = cumulative[-1]
    options: dict[tuple[int, float, CourseDirection], _AnchorOption] = {}
    for anchor, outer in product(index.matches(context[0]), index.matches(context[-1])):
        if anchor.course_segment_index != outer.course_segment_index:
            continue
        signed_span = outer.course_distance_m - anchor.course_distance_m
        if abs(signed_span) < config.minimum_course_span_m:
            continue
        # Projection can shorten a short, laterally displaced observation window.
        # Bound that uncertainty by the measured errors at its two endpoints;
        # all intermediate observations must still fit the course below.
        error_budget = (
            observed * config.missing_alignment_max_distance_ratio_error
            + anchor.anchor_distance_m
            + outer.anchor_distance_m
        )
        if abs(observed - abs(signed_span)) > error_budget:
            continue
        errors = []
        for record, distance in zip(context, cumulative, strict=True):
            lat, lon = index.position(
                anchor.course_segment_index,
                anchor.course_distance_m + signed_span * distance / observed,
            )
            assert record.latitude is not None and record.longitude is not None
            error = geodesic_distance_m(record.latitude, record.longitude, lat, lon)
            if error > config.anchor_match_tolerance_m:
                break
            errors.append(error)
        if len(errors) != len(context):
            continue
        direction = CourseDirection.FORWARD if signed_span * side > 0 else CourseDirection.REVERSE
        option = _AnchorOption(
            anchor,
            direction,
            anchor.anchor_distance_m
            + outer.anchor_distance_m
            + abs(observed - abs(signed_span))
            + sum(errors) / len(errors),
            LocalAlignmentEvidence(
                (min(r.index for r in context), max(r.index for r in context)),
                observed,
                source,
                abs(signed_span),
                error_budget,
                max(errors),
            ),
        )
        key = (anchor.course_segment_index, anchor.course_distance_m, direction)
        if key not in options or option.score < options[key].score:
            options[key] = option
    if options:
        return tuple(options.values()), Reason.UNIQUE_COURSE_MATCH
    reason = (
        Reason.LOCAL_DISTANCE_INCONSISTENT
        if index.matches(context[0]) and index.matches(context[-1])
        else Reason.LOCAL_COURSE_MATCH_NOT_FOUND
    )
    return (), reason


def _endpoint(segment: CourseSegment, end: bool) -> CourseAnchorMatch:
    point = segment.points[-1] if end else segment.points[0]
    return CourseAnchorMatch(
        segment.index,
        len(segment.points) - 2 if end else 0,
        len(segment.points) - 1 if end else 1,
        1.0 if end else 0.0,
        segment.length_m if end else 0.0,
        point.latitude,
        point.longitude,
        0.0,
    )


def _paths(
    before: tuple[_AnchorOption, ...],
    after: tuple[_AnchorOption, ...],
    gap: ReconstructionGap,
    index: _CourseIndex,
) -> tuple[_Path, ...]:
    paths = []
    if gap.kind is MissingCourseRunKind.INTERNAL:
        for a, b in product(before, after):
            if (
                a.direction is not b.direction
                or a.match.course_segment_index != b.match.course_segment_index
            ):
                continue
            sign = 1 if a.direction is CourseDirection.FORWARD else -1
            if sign * (b.match.course_distance_m - a.match.course_distance_m) < 0:
                continue
            paths.append(
                _Path(
                    a.match,
                    b.match,
                    a.direction,
                    a.score + b.score,
                    (a.evidence, b.evidence),
                )
            )
    else:
        prefix = gap.kind is MissingCourseRunKind.PREFIX
        for option in after if prefix else before:
            forward = option.direction is CourseDirection.FORWARD
            end = forward != prefix
            # A segment endpoint is not an activity endpoint unless it also is the
            # corresponding end of the entire GPX, in the selected direction.
            segment = index.course.segments[-1] if end else index.course.segments[0]
            if option.match.course_segment_index != segment.index:
                continue
            endpoint = _endpoint(segment, end)
            paths.append(
                _Path(
                    endpoint if prefix else option.match,
                    option.match if prefix else endpoint,
                    option.direction,
                    option.score,
                    (option.evidence,),
                )
            )
    return tuple(paths)


def _reconstruct_gap(
    activity: ActivityData,
    mask: tuple[CoordinateDisposition, ...],
    transitions: dict[tuple[int, int], TransitionClassification],
    gap: ReconstructionGap,
    index: _CourseIndex,
    config: CourseReconstructionConfig,
    integrity_config: IntegrityConfig,
    *,
    clock: AllocationClock | None = None,
) -> GapRepairPlan | UnresolvedGap:
    before = (
        _context(activity, mask, transitions, gap.anchor_before_record_index, -1, config)
        if gap.anchor_before_record_index is not None
        else ()
    )
    after = (
        _context(activity, mask, transitions, gap.anchor_after_record_index, 1, config)
        if gap.anchor_after_record_index is not None
        else ()
    )
    required = max(
        config.missing_alignment_min_position_records
        if gap.kind is not MissingCourseRunKind.INTERNAL
        else 2,
        config.anchor_stability_min_normal_transitions + 1,
    )
    # Time validity is independent of the GPX branch. Reject once, before a path
    # search can exhaust its budget and obscure this definitive local failure.
    start = gap.anchor_before_record_index
    end = gap.anchor_after_record_index
    timed_records = activity.records[
        (start if start is not None else gap.start_record_index) : (
            end if end is not None else gap.end_record_index
        )
        + 1
    ]
    stamps = tuple(r.timestamp for r in timed_records if r.timestamp is not None)
    if len(stamps) != len(timed_records) or any(b <= a for a, b in pairwise(stamps)):
        return UnresolvedGap(gap, (Reason.TIMING_UNUSABLE,))
    clock = clock or activity_clock(activity, timed_records)
    if clock is None:
        return UnresolvedGap(gap, (Reason.TIMING_UNUSABLE,))
    if clock.audit.open_pause:
        return UnresolvedGap(gap, (Reason.TIMER_STATE_UNRESOLVED,))
    if clock.audit.active_seconds <= 0:
        return UnresolvedGap(gap, (Reason.NO_ACTIVE_TIME,))
    contexts = [context for context in (before, after) if context]
    if (
        not contexts
        or (gap.anchor_before_record_index is not None and len(before) < required)
        or (gap.anchor_after_record_index is not None and len(after) < required)
    ):
        inspected = tuple((min(r.index for r in c), max(r.index for r in c)) for c in contexts)
        return UnresolvedGap(gap, (Reason.NO_TRUSTED_LOCAL_ANCHOR,), inspected)
    reason = Reason.LOCAL_COURSE_MATCH_NOT_FOUND
    ranges: tuple[tuple[int, int], ...] = ()
    # Nearest sufficient context first. Extend only to resolve ambiguity or lack of
    # matching information; the hard caps never move the actual edit boundaries.
    window = required
    evaluated_paths = 0
    allocations: dict[
        tuple[CourseAnchorMatch, CourseAnchorMatch, CourseDirection], GapRepairPlan | Reason
    ] = {}
    while True:
        used_before, used_after = before[:window], after[:window]
        ranges = tuple(
            (min(r.index for r in c), max(r.index for r in c))
            for c in (used_before, used_after)
            if c
        )
        try:
            before_options, before_reason = (
                _options(used_before, -1, index, config) if used_before else ((), reason)
            )
            after_options, after_reason = (
                _options(used_after, 1, index, config) if used_after else ((), reason)
            )
            paths = _paths(before_options, after_options, gap, index)
            if not paths:
                reason = (
                    before_reason
                    if used_before and not before_options
                    else after_reason
                    if used_after and not after_options
                    else Reason.LOCAL_COURSE_MATCH_NOT_FOUND
                )
            valid: list[tuple[_Path, GapRepairPlan]] = []
            for path_number, path in enumerate(sorted(paths, key=lambda item: item.score)):
                # Higher-scoring paths outside the ambiguity margin cannot change
                # the decision. Never declare uniqueness after a truncated search.
                if valid and path.score - valid[0][0].score > config.ambiguity_score_margin_m:
                    break
                key = (path.before, path.after, path.direction)
                if key not in allocations:
                    if evaluated_paths >= config.local_alignment_max_path_evaluations:
                        raise _LimitReached
                    evaluated_paths += 1
                    allocations[key] = _allocate_path(
                        activity, gap, path, index, ranges, config, integrity_config, clock=clock
                    )
                result = allocations[key]
                if isinstance(result, GapRepairPlan):
                    assert result.provenance is not None
                    result = replace(
                        result,
                        provenance=replace(
                            result.provenance,
                            context_ranges=ranges,
                            alignment_contexts=path.alignment_contexts,
                        ),
                    )
                    valid.append((path, result))
                elif path_number == 0:
                    reason = result
            valid.sort(
                key=lambda item: (
                    item[0].score,
                    item[0].before.course_segment_index,
                    item[0].before.course_distance_m,
                    item[0].after.course_distance_m,
                )
            )
            if valid:
                best, candidate = valid[0]
                ambiguous = any(
                    other.score - best.score <= config.ambiguity_score_margin_m
                    and not _equivalent(best, other, config)
                    for other, _candidate in valid[1:]
                )
                if not ambiguous:
                    return candidate
                reason = Reason.LOCAL_COURSE_MATCH_AMBIGUOUS
        except _LimitReached:
            return UnresolvedGap(gap, (Reason.SEARCH_LIMIT_REACHED,), ranges)
        if window >= max(len(before), len(after)):
            break
        window = min(window + required, max(len(before), len(after)))
    return UnresolvedGap(gap, (reason,), ranges)


def _equivalent(a: _Path, b: _Path, config: CourseReconstructionConfig) -> bool:
    return (
        a.direction is b.direction
        and a.before.course_segment_index == b.before.course_segment_index
        and abs(a.before.course_distance_m - b.before.course_distance_m)
        <= config.anchor_candidate_deduplication_m
        and abs(a.after.course_distance_m - b.after.course_distance_m)
        <= config.anchor_candidate_deduplication_m
    )


def _allocate_path(
    activity: ActivityData,
    gap: ReconstructionGap,
    path: _Path,
    index: _CourseIndex,
    ranges: tuple[tuple[int, int], ...],
    config: CourseReconstructionConfig,
    integrity_config: IntegrityConfig,
    *,
    clock: AllocationClock | None = None,
) -> GapRepairPlan | Reason:
    start = (
        gap.anchor_before_record_index
        if gap.anchor_before_record_index is not None
        else gap.start_record_index
    )
    end = (
        gap.anchor_after_record_index
        if gap.anchor_after_record_index is not None
        else gap.end_record_index
    )
    records = activity.records[start : end + 1]
    if len(records) < 2 or any(record.timestamp is None for record in records):
        return Reason.TIMING_UNUSABLE
    stamps = tuple(record.timestamp for record in records if record.timestamp is not None)
    elapsed = (stamps[-1] - stamps[0]).total_seconds()
    if elapsed <= 0 or any(b <= a for a, b in pairwise(stamps)):
        return Reason.TIMING_UNUSABLE
    clock = clock or activity_clock(activity, records)
    assert clock is not None
    if clock.audit.open_pause:
        return Reason.TIMER_STATE_UNRESOLVED
    if clock.audit.active_seconds <= 0:
        return Reason.NO_ACTIVE_TIME
    if (
        clock.audit.paused_seconds
        and path.length / clock.audit.active_seconds
        > config.missing_completion_max_course_speed_mps
    ):
        return Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    if path.length <= 0 or path.length / elapsed > config.missing_completion_max_course_speed_mps:
        return Reason.COURSE_TRAVERSAL_IMPLAUSIBLE
    allocation = _fractions(records, path.length, config, integrity_config, clock=clock)
    if isinstance(allocation, Reason):
        return allocation
    method, fractions, diagnostics = allocation
    if clock.audit.paused_seconds:
        for (previous_fraction, next_fraction), active in zip(
            pairwise(fractions), clock.active_deltas, strict=True
        ):
            travelled = (next_fraction - previous_fraction) * path.length
            if (active == 0 and travelled > 0) or (
                active > 0
                and travelled / active > config.missing_completion_max_connector_speed_mps
            ):
                return Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    updates = []
    before_record, after_record = records[0], records[-1]
    for record, fraction in zip(records, fractions, strict=True):
        if not gap.start_record_index <= record.index <= gap.end_record_index:
            continue
        travelled = fraction * path.length
        before_connector = path.before.anchor_distance_m
        after_connector = path.after.anchor_distance_m
        if travelled < before_connector and before_connector > 0:
            assert before_record.latitude is not None and before_record.longitude is not None
            lat, lon = _interpolate_coordinate(
                before_record.latitude,
                before_record.longitude,
                path.before.latitude,
                path.before.longitude,
                travelled / before_connector,
            )
            chainage = path.before.course_distance_m
        elif travelled > before_connector + path.span and after_connector > 0:
            assert after_record.latitude is not None and after_record.longitude is not None
            lat, lon = _interpolate_coordinate(
                path.after.latitude,
                path.after.longitude,
                after_record.latitude,
                after_record.longitude,
                min(1.0, (travelled - before_connector - path.span) / after_connector),
            )
            chainage = path.after.course_distance_m
        else:
            sign = 1 if path.direction is CourseDirection.FORWARD else -1
            chainage = path.before.course_distance_m + sign * min(
                path.span, max(0.0, travelled - before_connector)
            )
            lat, lon = index.position(path.before.course_segment_index, chainage)
        updates.append(
            CandidateCoordinate(
                record.index,
                record.timestamp,
                record.latitude,
                record.longitude,
                lat,
                lon,
                chainage,
            )
        )
    override = {
        update.record_index: (update.candidate_latitude, update.candidate_longitude)
        for update in updates
    }
    for a, b in pairwise(records):
        a_lat, a_lon = override.get(a.index, (a.latitude, a.longitude))
        b_lat, b_lon = override.get(b.index, (b.latitude, b.longitude))
        if a_lat is None or a_lon is None or b_lat is None or b_lon is None:
            return Reason.NO_TRUSTED_LOCAL_ANCHOR
        assert a.timestamp is not None and b.timestamp is not None
        if (
            geodesic_distance_m(a_lat, a_lon, b_lat, b_lon)
            / (b.timestamp - a.timestamp).total_seconds()
            > config.missing_completion_max_connector_speed_mps
        ):
            return Reason.CANDIDATE_TRANSITION_IMPLAUSIBLE
    endpoint_source = "course_assumption" if gap.kind is not MissingCourseRunKind.INTERNAL else None
    high = (
        not gap.original_missing_count
        and endpoint_source is None
        and gap.invalidation_confidence is IntegrityConfidence.HIGH
        and max(path.before.anchor_distance_m, path.after.anchor_distance_m)
        <= config.high_confidence_anchor_distance_m
    )
    distance_signal = qualify_distance(records, integrity_config)
    speed_signal = qualify_speed(
        records,
        integrity_config,
        active_deltas=clock.active_deltas if clock.audit.paused_seconds else None,
    )
    preserve = not distance_signal.correction_supported
    return GapRepairPlan(
        interval=gap,
        coordinate_updates=tuple(updates),
        confidence=IntegrityConfidence.HIGH if high else IntegrityConfidence.MEDIUM,
        reasons=(
            Reason.UNIQUE_COURSE_MATCH,
            Reason.ANCHOR_CONNECTORS_PLAUSIBLE,
            Reason.COURSE_ASSUMPTION,
        ),
        reconstruction_path_distance_m=path.length,
        preserve_recorded_distance=preserve,
        dependent_fields_to_recalculate=()
        if preserve
        else (
            "record.distance",
            "lap.total_distance",
            "lap.avg_speed",
            "session.total_distance",
            "session.avg_speed",
        ),
        provenance=CoursePathProvenance(
            index.course.source_path,
            index.source_hash,
            path.direction,
            path.before,
            path.after,
            ranges,
            path.span,
            path.before.anchor_distance_m + path.after.anchor_distance_m,
            endpoint_source,
            method,
            "estimated" if method is AllocationMethod.TIMESTAMPS else "source_unverified",
            diagnostics,
            path.alignment_contexts,
            distance_signal.status,
            speed_signal.status,
            distance_signal.cumulative[-1] if distance_signal.cumulative else None,
            speed_signal.cumulative[-1] if speed_signal.cumulative else None,
            _signal_error_budget(path.length, config),
            clock.audit,
        ),
    )


def _fractions(
    records: tuple[ActivityRecord, ...],
    length: float,
    config: CourseReconstructionConfig,
    integrity_config: IntegrityConfig,
    *,
    clock: AllocationClock | None = None,
) -> tuple[AllocationMethod, tuple[float, ...], tuple[str, ...]] | Reason:
    diagnostics: list[str] = []
    paused = clock is not None and clock.audit.paused_seconds > 0
    active_deltas = clock.active_deltas if paused and clock else None
    distance_signal = qualify_distance(records, integrity_config)
    if paused and clock:
        if clock.audit.open_pause:
            return Reason.TIMER_STATE_UNRESOLVED
        if clock.audit.active_seconds <= 0:
            return Reason.NO_ACTIVE_TIME
        diagnostics.append("timer_pauses_excluded")
        if distance_signal.status == "plausible" and any(
            active == 0 and b > a
            for (a, b), active in zip(
                pairwise(distance_signal.cumulative), clock.active_deltas, strict=True
            )
        ):
            return Reason.PAUSE_DISTANCE_CONFLICT
    conflict = False
    for label, method, signal in (
        (
            "distance",
            AllocationMethod.RECORDED_DISTANCE,
            distance_signal,
        ),
        (
            "speed",
            AllocationMethod.RECORDED_SPEED,
            qualify_speed(records, integrity_config, active_deltas=active_deltas),
        ),
    ):
        if signal.status in {"plausible", "zero"}:
            total = signal.cumulative[-1]
            if total > 0 and abs(total - length) <= _signal_error_budget(length, config):
                return (
                    method,
                    tuple(value / total for value in signal.cumulative),
                    tuple(diagnostics),
                )
            conflict = True
            diagnostics.append(f"{label}_path_mismatch" if total > 0 else f"{label}_zero")
        else:
            diagnostics.append(f"{label}_{signal.status}")
    # A qualified alternative can resolve uncertain source disagreement, but time
    # alone must not override plausible measurements supporting a different path.
    if conflict:
        return Reason.LOCAL_DISTANCE_INCONSISTENT
    if paused and clock:
        return (
            AllocationMethod.TIMESTAMPS,
            tuple(value / clock.audit.active_seconds for value in clock.active_cumulative),
            (*diagnostics, "active_time_estimated"),
        )
    stamps = tuple(record.timestamp for record in records if record.timestamp is not None)
    elapsed = (stamps[-1] - stamps[0]).total_seconds()
    return (
        AllocationMethod.TIMESTAMPS,
        tuple((stamp - stamps[0]).total_seconds() / elapsed for stamp in stamps),
        tuple(diagnostics),
    )


def _signal_error_budget(length: float, config: CourseReconstructionConfig) -> float:
    return max(
        config.signal_distance_absolute_tolerance_m,
        length * config.missing_alignment_max_distance_ratio_error,
    )
