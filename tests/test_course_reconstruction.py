"""Safe GPX course matching and dry-run RepairPlan tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.activity_factory import eastward_observations, make_activity
from tests.gpx_factory import GpxPoint, write_gpx_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CourseData,
    CourseDirection,
    ReconstructionReason,
    RepairPlanStatus,
)
from warpbuster.reconstruction import build_course_repair_plan


def test_high_confidence_interval_builds_ready_plan_without_mutation(tmp_path: Path) -> None:
    """A unique course span yields explicit coordinate updates and unchanged timestamps."""
    activity = _single_spike_activity()
    original_records = activity.records
    course = _eastward_course(tmp_path, reverse=False)

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.READY
    assert plan.confidence is IntegrityConfidence.HIGH
    assert plan.timestamps_unchanged is True
    assert plan.trusted_records_unchanged is True
    assert plan.output_written is False
    assert activity.records == original_records
    assert len(plan.interval_plans) == 1
    interval_plan = plan.interval_plans[0]
    assert interval_plan.repair_eligible is True
    assert interval_plan.fields_to_change == ("position_lat", "position_long")
    assert interval_plan.provenance.allocation_method is AllocationMethod.TIMESTAMPS
    assert interval_plan.provenance.direction is CourseDirection.FORWARD
    assert len(interval_plan.coordinate_updates) == 1
    update = interval_plan.coordinate_updates[0]
    assert update.record_index == 2
    assert update.timestamp == activity.records[2].timestamp
    assert update.candidate_latitude == pytest.approx(55.0)
    assert update.candidate_longitude == pytest.approx(activity.records[1].longitude, abs=1e-4)
    assert activity.records[1].index not in {
        candidate.record_index for candidate in interval_plan.coordinate_updates
    }
    assert activity.records[3].index not in {
        candidate.record_index for candidate in interval_plan.coordinate_updates
    }


def test_course_direction_can_be_reverse(tmp_path: Path) -> None:
    """GPX file order does not force an activity moving the other way to be rejected."""
    activity = _single_spike_activity()
    course = _eastward_course(tmp_path, reverse=True)

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.READY
    assert plan.interval_plans[0].provenance.direction is CourseDirection.REVERSE


def test_plausible_recorded_distance_is_preferred_but_bad_distance_is_not(
    tmp_path: Path,
) -> None:
    """Device distance is evidence only and loses priority when inconsistent with course."""
    activity = _single_spike_activity()
    course = _eastward_course(tmp_path, reverse=False)
    plausible = replace(
        activity,
        records=tuple(
            replace(record, distance=float(record.index * 3)) for record in activity.records
        ),
    )
    implausible = replace(
        activity,
        records=tuple(
            replace(record, distance=float(record.index * 3_000)) for record in activity.records
        ),
    )

    plausible_plan = build_course_repair_plan(
        plausible,
        analyze_integrity(plausible),
        course,
        _short_span_config(),
    )
    implausible_plan = build_course_repair_plan(
        implausible,
        analyze_integrity(implausible),
        course,
        _short_span_config(),
    )

    assert (
        plausible_plan.interval_plans[0].provenance.allocation_method
        is AllocationMethod.RECORDED_DISTANCE
    )
    assert (
        implausible_plan.interval_plans[0].provenance.allocation_method
        is AllocationMethod.TIMESTAMPS
    )


def test_plausible_speed_is_used_when_distance_is_unavailable(tmp_path: Path) -> None:
    """Integrated speed can distribute records but is still checked against course length."""
    activity = _single_spike_activity()
    activity = replace(
        activity,
        records=tuple(replace(record, speed=3.0) for record in activity.records),
    )

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        _eastward_course(tmp_path, reverse=False),
        _short_span_config(),
    )

    assert plan.interval_plans[0].provenance.allocation_method is AllocationMethod.RECORDED_SPEED


def test_missing_timestamp_refuses_reconstruction_without_record_order_fallback(
    tmp_path: Path,
) -> None:
    """Geometry allocation requires trustworthy time; invalidation remains independent."""
    detected_activity = _single_spike_activity()
    integrity = analyze_integrity(detected_activity)
    activity = replace(
        detected_activity,
        records=tuple(
            replace(record, timestamp=None) if record.index == 2 else record
            for record in detected_activity.records
        ),
    )

    plan = build_course_repair_plan(
        activity,
        integrity,
        _eastward_course(tmp_path, reverse=False),
        _short_span_config(),
    )

    assert plan.interval_plans == ()
    assert plan.unresolved_gaps[0].reasons == (ReconstructionReason.TIMING_UNUSABLE,)


def test_implausibly_long_course_traversal_is_refused(tmp_path: Path) -> None:
    """Matched anchors are insufficient when the path cannot be covered in elapsed time."""
    activity = _single_spike_activity()
    start = activity.records[1]
    end = activity.records[3]
    assert start.latitude is not None and start.longitude is not None
    assert end.latitude is not None and end.longitude is not None
    path = tmp_path / "detour.gpx"
    write_gpx_activity(
        path,
        [
            [
                (activity.records[0].latitude, activity.records[0].longitude, None, None),
                (start.latitude, start.longitude, None, None),
                (55.002, 37.0, None, None),
                (end.latitude, end.longitude, None, None),
                (activity.records[4].latitude, activity.records[4].longitude, None, None),
            ]
        ],
    )

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(path),
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.PARTIAL
    assert plan.unresolved_gaps[0].reasons == (ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE,)


def test_medium_anchor_match_is_not_writer_eligible(tmp_path: Path) -> None:
    """A unique but distant match remains visible without becoming safe to apply."""
    activity = _single_spike_activity()
    observations = eastward_observations(
        [0.0, 1.0, 2.0, 3.0, 4.0],
        [0.0, 3.0, 6.0, 9.0, 12.0],
        latitude=55.00054,
    )
    points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in observations
        if latitude is not None and longitude is not None
    ]
    path = tmp_path / "parallel.gpx"
    write_gpx_activity(path, [points])

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(path),
        _short_span_config_for_medium(),
    )

    assert plan.status is RepairPlanStatus.PARTIAL
    assert plan.interval_plans == ()
    assert plan.unresolved_gaps[0].reasons == (ReconstructionReason.COURSE_TRAVERSAL_IMPLAUSIBLE,)


def test_self_intersection_with_equally_good_paths_is_refused(tmp_path: Path) -> None:
    """Repeated anchor locations leave route choice ambiguous and produce no coordinates."""
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0], [0.0, 3.0]),
            (50.0, 56.0, 37.0),
            *eastward_observations([100.0, 101.0], [9.0, 12.0]),
        ]
    )
    points = _ambiguous_course_points()
    path = tmp_path / "ambiguous.gpx"
    write_gpx_activity(path, [points])
    course = read_gpx_course(path)

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.PARTIAL
    assert plan.interval_plans == ()
    assert plan.unresolved_gaps[0].reasons == (ReconstructionReason.LOCAL_COURSE_MATCH_AMBIGUOUS,)


def test_unmatched_course_is_refused_without_touching_wrong_turn(tmp_path: Path) -> None:
    """A course far from trusted anchors cannot pull a real off-course section onto it."""
    activity = _single_spike_activity()
    path = tmp_path / "far.gpx"
    write_gpx_activity(
        path,
        [[(56.0, 38.0, None, None), (56.001, 38.001, None, None)]],
    )

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(path),
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.PARTIAL
    assert plan.interval_plans == ()
    assert plan.unresolved_gaps[0].reasons == (ReconstructionReason.LOCAL_COURSE_MATCH_NOT_FOUND,)


def test_one_unmatched_interval_makes_the_whole_plan_partial(tmp_path: Path) -> None:
    """A future writer cannot silently apply only the convenient part of an activity."""
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0], [0.0, 3.0]),
            (2.0, 56.0, 37.0),
            *eastward_observations([3.0, 4.0, 5.0], [9.0, 12.0, 15.0]),
            (6.0, 56.0, 37.0),
            *eastward_observations([7.0, 8.0], [21.0, 24.0]),
        ]
    )
    course = _course_for_distances(tmp_path, [0.0, 3.0, 6.0, 9.0, 12.0])

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        _short_span_config(),
    )

    assert plan.detected_interval_count == 2
    assert plan.status is RepairPlanStatus.PARTIAL
    assert len(plan.interval_plans) == 1
    assert plan.interval_plans[0].repair_eligible is True
    assert len(plan.unresolved_gaps) == 1
    assert plan.reasons == (ReconstructionReason.SOME_INTERVALS_UNRESOLVED,)


def test_clean_activity_needs_no_repair_even_with_course(tmp_path: Path) -> None:
    """Course presence never creates corruption or a coordinate plan."""
    activity = make_activity(eastward_observations([0.0, 1.0, 2.0], [0.0, 3.0, 6.0]))

    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        _eastward_course(tmp_path, reverse=False),
        _short_span_config(),
    )

    assert plan.status is RepairPlanStatus.NOT_NEEDED
    assert plan.detected_interval_count == 0
    assert plan.interval_plans == ()


def _single_spike_activity() -> ActivityData:
    return make_activity(
        [
            *eastward_observations([0.0, 1.0], [0.0, 3.0]),
            (2.0, 56.0, 37.0),
            *eastward_observations([3.0, 4.0], [9.0, 12.0]),
        ]
    )


def _eastward_course(tmp_path: Path, *, reverse: bool) -> CourseData:
    course = _course_for_distances(tmp_path, [0.0, 3.0, 6.0, 9.0, 12.0])
    if not reverse:
        return course
    points: list[GpxPoint] = [
        (point.latitude, point.longitude, None, point.elevation_m)
        for point in reversed(course.segments[0].points)
    ]
    path = tmp_path / "reverse.gpx"
    write_gpx_activity(path, [points])
    return read_gpx_course(path)


def _course_for_distances(tmp_path: Path, distances_m: list[float]) -> CourseData:
    observations = eastward_observations(
        [float(index) for index in range(len(distances_m))],
        distances_m,
    )
    points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in observations
        if latitude is not None and longitude is not None
    ]
    path = tmp_path / "forward.gpx"
    write_gpx_activity(path, [points])
    return read_gpx_course(path)


def _short_span_config() -> CourseReconstructionConfig:
    return CourseReconstructionConfig(
        anchor_match_tolerance_m=0.5,
        high_confidence_anchor_distance_m=0.5,
        minimum_course_span_m=1.0,
        anchor_candidate_deduplication_m=2.0,
        anchor_stability_min_normal_transitions=1,
        anchor_stability_scan_max_records=2,
    )


def _short_span_config_for_medium() -> CourseReconstructionConfig:
    return CourseReconstructionConfig(
        anchor_match_tolerance_m=75.0,
        high_confidence_anchor_distance_m=50.0,
        minimum_course_span_m=1.0,
        anchor_candidate_deduplication_m=25.0,
        anchor_stability_min_normal_transitions=1,
        anchor_stability_scan_max_records=2,
    )


def _ambiguous_course_points() -> list[GpxPoint]:
    base = eastward_observations([0.0, 1.0], [3.0, 9.0])
    start_latitude = base[0][1]
    start_longitude = base[0][2]
    end_latitude = base[1][1]
    end_longitude = base[1][2]
    assert start_latitude is not None and start_longitude is not None
    assert end_latitude is not None and end_longitude is not None
    outer = eastward_observations([0.0, 1.0], [0.0, 12.0])
    before = (outer[0][1], outer[0][2], None, None)
    after = (outer[1][1], outer[1][2], None, None)
    return [
        before,
        (start_latitude, start_longitude, None, None),
        (55.001, 37.0, None, None),
        (end_latitude, end_longitude, None, None),
        after,
        before,
        (start_latitude, start_longitude, None, None),
        (54.999, 37.0, None, None),
        (end_latitude, end_longitude, None, None),
        after,
    ]
