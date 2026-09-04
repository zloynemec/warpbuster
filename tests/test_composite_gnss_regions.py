"""Generic Task 006C composite GNSS failure-region regressions."""

from pathlib import Path

from tests.activity_factory import eastward_observations, make_activity
from tests.fit_factory import write_trajectory_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    CourseData,
    GnssComponentKind,
    GnssComponentState,
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


def test_composite_envelope_does_not_authorize_erasing_tainted_neighbors(tmp_path: Path) -> None:
    """Task 011 supersedes course-driven expansion: only two detector cores are removable."""
    activity = _composite_activity()
    integrity = analyze_integrity(activity)
    course = _course(tmp_path)
    plan = build_course_repair_plan(activity, integrity, course, _config())
    assert plan.detected_interval_count == 2
    assert [(g.start_record_index, g.end_record_index) for g in plan.gaps] == [
        (4, 4),
        (6, 6),
        (8, 8),
        (10, 10),
    ]
    assert {item.record_index for item in select_repair_intervals(plan).invalidations} == {4, 8}
    assert all(plan.coordinate_mask[i].state.value == "preserved" for i in (3, 5, 7, 9))
    assert plan.interval_plans == ()  # isolated immediate neighbors are not trusted anchors
    report = repair_report(plan, course, _config())
    assert report["coordinate_coverage"]["invalidated"] == 2
    assert report["coordinate_coverage"]["original_missing"] == 2
    assert len(report["gap_inventory"]) == 4
    assert "Invalidations: 2" in repair_console(plan, course, _config())


def test_plausible_positioned_component_splits_independent_edit_scopes(tmp_path: Path) -> None:
    activity = _composite_with_plausible_component()
    integrity = analyze_integrity(activity)
    plan = build_course_repair_plan(activity, integrity, _course(tmp_path), _config())
    assert [(g.start_record_index, g.end_record_index) for g in plan.gaps] == [
        (4, 4),
        (6, 6),
        (10, 10),
    ]
    assert all(plan.coordinate_mask[i].state.value == "preserved" for i in (3, 5, 7, 8, 9))
    assert all(
        i not in {u.record_index for c in plan.interval_plans for u in c.coordinate_updates}
        for i in (3, 5, 7, 8, 9)
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
    assert repaired.records[4].latitude is None
    assert repaired.records[6].latitude is None
    assert repaired.records[10].latitude is None
    assert repaired.records[3] == activity.records[3]
    assert repaired.records[5] == activity.records[5]
    assert result.diff.unexpected_changed_field_count == 0


def test_unknown_positioned_component_is_not_added_to_reconstruction_scope(tmp_path: Path) -> None:
    activity = _composite_with_unknown_component()
    integrity = analyze_integrity(activity)
    plan = build_course_repair_plan(activity, integrity, _course(tmp_path), _config())
    assert plan.coordinate_mask[7].state.value == "preserved"
    assert not any(g.start_record_index <= 7 <= g.end_record_index for g in plan.gaps)
    assert 7 not in {u.record_index for c in plan.interval_plans for u in c.coordinate_updates}


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
    assert plan.status is RepairPlanStatus.REFUSED
    assert len(plan.gaps) == 1
    assert plan.unresolved_gaps[0].reasons[0].value == "missing_completion_disabled"
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
