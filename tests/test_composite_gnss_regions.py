"""Generic Task 006C composite GNSS failure-region regressions."""

from pathlib import Path
from typing import cast

from tests.activity_factory import eastward_observations, make_activity
from tests.fit_factory import write_trajectory_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntervalDetectionKind
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CourseData,
    GnssComponentKind,
    GnssComponentState,
    IntervalRepairPlan,
    RepairPlanStatus,
)
from warpbuster.reconstruction import build_course_repair_plan, select_repair_intervals
from warpbuster.reconstruction.safety import assess_interval_safety
from warpbuster.report.repair import repair_console, repair_report


def test_composite_region_describes_ordered_course_free_components() -> None:
    activity = _composite_activity()
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[0],
        _config(),
    )
    region = safety.mixed_region

    assert region is not None
    assert (region.start_record_index, region.end_record_index) == (3, 10)
    assert region.detected_core_ranges == ((4, 4), (8, 8))
    assert [component.kind for component in region.components] == [
        GnssComponentKind.POSITIONED,
        GnssComponentKind.MISSING,
        GnssComponentKind.POSITIONED,
        GnssComponentKind.MISSING,
    ]
    assert [component.state for component in region.components] == [
        GnssComponentState.TAINTED,
        GnssComponentState.MISSING,
        GnssComponentState.TAINTED,
        GnssComponentState.MISSING,
    ]
    assert [component.duration_seconds for component in region.components] == [2.0, 0.0, 2.0, 0.0]
    assert region.all_positioned_components_tainted is True
    assert region.reconstructable is True


def test_composite_course_candidate_is_one_explicit_medium_planning_unit(
    tmp_path: Path,
) -> None:
    activity = _composite_activity()
    integrity = analyze_integrity(activity)
    course = _course(tmp_path)

    plan = build_course_repair_plan(activity, integrity, course, _config())

    assert plan.detected_interval_count == 1
    assert len(plan.interval_plans) == 1
    assert plan.unresolved_intervals == ()
    candidate = plan.interval_plans[0]
    assert isinstance(candidate, IntervalRepairPlan)
    assert candidate.interval.detection_kind is IntervalDetectionKind.COMPOSITE_REGION
    assert (candidate.interval.start_record_index, candidate.interval.end_record_index) == (3, 10)
    assert candidate.confidence is IntegrityConfidence.MEDIUM
    assert candidate.repair_eligible is False
    assert candidate.allocation_method is AllocationMethod.TIMESTAMPS
    assert len(candidate.coordinate_updates) == 8
    assert candidate.composite_region is not None

    assert select_repair_intervals(plan).selected_interval_plans == ()
    medium = select_repair_intervals(plan, IntegrityConfidence.MEDIUM)
    assert medium.selected_interval_plans == (candidate,)

    report = repair_report(
        plan,
        course,
        _config(),
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    interval = cast(list[dict[str, object]], report["interval_plans"])[0]
    assert interval["existing_coordinate_update_count"] == 6
    assert interval["missing_coordinate_update_count"] == 2
    composite = cast(dict[str, object], interval["composite_gnss_region"])
    assert composite["reconstructable"] is True
    components = cast(list[dict[str, object]], composite["components"])
    assert components[0]["duration_seconds"] == 2.0
    console = repair_console(
        plan,
        course,
        _config(),
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert "scope=3..10" in console


def test_plausible_positioned_component_is_preserved_by_partial_reconstruction(
    tmp_path: Path,
) -> None:
    activity = _composite_with_plausible_component()
    integrity = analyze_integrity(activity)
    config = CourseReconstructionConfig(
        anchor_stability_min_normal_transitions=2,
        anchor_stability_scan_max_records=4,
        mixed_region_search_max_records=100,
        mixed_region_max_clean_gap_records=3,
    )

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[0],
        config,
    )
    region = safety.mixed_region
    assert region is not None
    assert GnssComponentState.PLAUSIBLE in {component.state for component in region.components}
    assert region.all_positioned_components_tainted is False
    assert region.reconstructable is True

    plan = build_course_repair_plan(activity, integrity, _course(tmp_path), config)
    assert len(plan.interval_plans) == 1
    assert plan.unresolved_intervals == ()
    candidate = plan.interval_plans[0]
    assert candidate.reconstruction_scope_ranges == ((3, 6), (10, 10))
    assert {update.record_index for update in candidate.coordinate_updates} == {
        3,
        4,
        5,
        6,
        10,
    }
    assert all(
        activity.records[index].latitude is not None
        and activity.records[index].longitude is not None
        for index in range(7, 10)
    )


def test_writer_applies_disjoint_composite_scope_and_preserves_plausible_component(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "composite.fit"
    source_activity = _composite_with_plausible_component()
    start = source_activity.records[0].timestamp
    assert start is not None
    observations = [
        (
            int((record.timestamp - start).total_seconds()),
            record.latitude,
            record.longitude,
        )
        for record in source_activity.records
        if record.timestamp is not None
    ]
    write_trajectory_activity(
        source_path,
        observations,
        retain_invalid_position_fields=True,
    )
    activity = read_fit(source_path)
    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        _course(tmp_path),
        CourseReconstructionConfig(
            anchor_stability_min_normal_transitions=2,
            anchor_stability_scan_max_records=4,
            mixed_region_search_max_records=100,
            mixed_region_max_clean_gap_records=3,
        ),
    )

    result = write_repaired_fit(
        activity,
        plan,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    repaired = read_fit(result.output_path)

    assert all(
        repaired.records[index].latitude == activity.records[index].latitude
        and repaired.records[index].longitude == activity.records[index].longitude
        for index in range(7, 10)
    )
    assert repaired.records[6].latitude is not None
    assert repaired.records[10].latitude is not None
    assert result.diff.unexpected_changed_field_count == 0


def test_unknown_positioned_component_is_not_added_to_reconstruction_scope(
    tmp_path: Path,
) -> None:
    activity = _composite_with_unknown_component()
    integrity = analyze_integrity(activity)
    config = CourseReconstructionConfig(
        anchor_stability_min_normal_transitions=2,
        anchor_stability_scan_max_records=4,
        mixed_region_search_max_records=100,
        mixed_region_max_clean_gap_records=3,
    )

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[0],
        config,
    )
    assert safety.mixed_region is not None
    assert GnssComponentState.UNKNOWN in {
        component.state for component in safety.mixed_region.components
    }

    plan = build_course_repair_plan(activity, integrity, _course(tmp_path), config)
    candidate = plan.interval_plans[0]
    assert 7 not in {update.record_index for update in candidate.coordinate_updates}


def test_missing_run_without_corruption_seed_does_not_create_composite_region(
    tmp_path: Path,
) -> None:
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0], [0.0, 3.0]),
            (2.0, None, None),
            *eastward_observations([3.0, 4.0], [9.0, 12.0]),
        ]
    )
    integrity = analyze_integrity(activity)

    plan = build_course_repair_plan(activity, integrity, _course(tmp_path), _config())

    assert integrity.corrupted_intervals == ()
    assert plan.status is RepairPlanStatus.NOT_NEEDED
    assert plan.interval_plans == ()


def _config() -> CourseReconstructionConfig:
    return CourseReconstructionConfig(
        anchor_stability_min_normal_transitions=2,
        anchor_stability_scan_max_records=4,
        mixed_region_search_max_records=100,
        mixed_region_max_clean_gap_records=1,
    )


def _course(tmp_path: Path) -> CourseData:
    path = tmp_path / "course.gpx"
    write_gpx_activity(
        path,
        [[(55.0, 37.0, None, None), (55.0, 37.001, None, None)]],
    )
    return read_gpx_course(path)


def _composite_activity() -> ActivityData:
    return make_activity(
        [
            *eastward_observations([0.0, 1.0, 2.0, 3.0], [0.0, 3.0, 6.0, 9.0]),
            (4.0, 56.0, 37.0),
            *eastward_observations([5.0], [15.0]),
            (6.0, None, None),
            *eastward_observations([7.0], [21.0]),
            (8.0, 56.0, 37.0),
            *eastward_observations([9.0], [27.0]),
            (10.0, None, None),
            *eastward_observations([11.0, 12.0, 13.0, 14.0], [33.0, 36.0, 39.0, 42.0]),
        ]
    )


def _composite_with_plausible_component() -> ActivityData:
    return make_activity(
        [
            *eastward_observations([0.0, 1.0, 2.0, 3.0], [0.0, 3.0, 6.0, 9.0]),
            (4.0, 56.0, 37.0),
            *eastward_observations([5.0], [15.0]),
            (6.0, None, None),
            *eastward_observations([7.0, 8.0, 9.0], [21.0, 24.0, 27.0]),
            (10.0, None, None),
            *eastward_observations([11.0, 12.0, 13.0, 14.0], [33.0, 36.0, 39.0, 42.0]),
        ]
    )


def _composite_with_unknown_component() -> ActivityData:
    return make_activity(
        [
            *eastward_observations([0.0, 1.0, 2.0, 3.0], [0.0, 3.0, 6.0, 9.0]),
            (4.0, 56.0, 37.0),
            *eastward_observations([5.0], [15.0]),
            (6.0, None, None),
            *eastward_observations([7.0], [21.0]),
            (8.0, None, None),
            *eastward_observations([9.0, 10.0, 11.0, 12.0], [27.0, 30.0, 33.0, 36.0]),
        ]
    )
