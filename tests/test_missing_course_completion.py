"""Task 006D course-backed missing endpoint completion regressions."""

import json
from pathlib import Path

from tests.activity_factory import eastward_observations
from tests.fit_factory import write_trajectory_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    AllocationMethod,
    MissingCourseCompletionPlan,
    MissingCourseRunKind,
    ReconstructionReason,
    RepairIntervalAction,
    RepairPlanStatus,
)
from warpbuster.reconstruction import (
    build_course_repair_plan,
    build_missing_course_plan,
    merge_repair_plans,
    select_repair_intervals,
)
from warpbuster.report.html import write_repair_html
from warpbuster.report.repair import repair_console, repair_report

_DATA_PREFIX = '<script id="warpbuster-report-data" type="application/json">'
_DATA_SUFFIX = "</script>"


def test_endpoint_missing_runs_are_independent_explicit_medium_candidates(
    tmp_path: Path,
) -> None:
    source_path, course_path = _fixture(tmp_path)
    activity = read_fit(source_path)
    integrity = analyze_integrity(activity)
    course = read_gpx_course(course_path)
    config = _config()

    primary = build_course_repair_plan(activity, integrity, course, config)
    missing = build_missing_course_plan(activity, integrity, course, config)
    plan = merge_repair_plans(primary, missing)

    assert primary.status is RepairPlanStatus.NOT_NEEDED
    assert plan.status is RepairPlanStatus.PARTIAL
    assert plan.missing_completion_enabled is True
    assert plan.detected_interval_count == 2
    assert len(plan.interval_plans) == 2
    assert plan.unresolved_missing_runs == ()
    prefix, suffix = plan.interval_plans
    assert isinstance(prefix, MissingCourseCompletionPlan)
    assert isinstance(suffix, MissingCourseCompletionPlan)
    assert prefix.interval.kind is MissingCourseRunKind.PREFIX
    assert suffix.interval.kind is MissingCourseRunKind.SUFFIX
    assert prefix.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert suffix.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert prefix.preserve_recorded_distance is True
    assert suffix.preserve_recorded_distance is True
    assert all(
        update.original_latitude is None and update.original_longitude is None
        for candidate in (prefix, suffix)
        for update in candidate.coordinate_updates
    )

    high = select_repair_intervals(plan)
    assert high.selected_interval_plans == ()
    assert all(decision.action is RepairIntervalAction.SKIPPED for decision in high.decisions)
    medium = select_repair_intervals(plan, IntegrityConfidence.MEDIUM)
    assert len(medium.selected_interval_plans) == 2

    report = repair_report(
        plan,
        course,
        config,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert report["summary"]["missing_completion_candidate_count"] == 2  # type: ignore[index]
    console = repair_console(
        plan,
        course,
        config,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert "missing prefix records 0..2: APPLIED" in console
    assert "missing suffix records 10..12: APPLIED" in console
    assert "distance=preserved" in console

    html_path = tmp_path / "missing-preview.html"
    write_repair_html(
        activity,
        integrity,
        course,
        plan,
        config,
        html_path,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    rendered = html_path.read_text(encoding="utf-8")
    payload = json.loads(
        rendered.split(_DATA_PREFIX, maxsplit=1)[1].split(_DATA_SUFFIX, maxsplit=1)[0]
    )
    assert "Missing-position completion" in rendered
    assert payload["metrics_comparison"]["rows"][2]["embedded_distance_m"] == (
        activity.recorded_distance_m
    )
    assert len(payload["repair"]["interval_plans"]) == 2


def test_writer_fills_only_missing_coordinates_and_preserves_distance(
    tmp_path: Path,
) -> None:
    source_path, course_path = _fixture(tmp_path)
    activity = read_fit(source_path)
    course = read_gpx_course(course_path)
    config = _config()
    plan = merge_repair_plans(
        build_course_repair_plan(activity, analyze_integrity(activity), course, config),
        build_missing_course_plan(activity, analyze_integrity(activity), course, config),
    )
    original_coordinates = tuple((record.latitude, record.longitude) for record in activity.records)
    original_distances = tuple(record.distance for record in activity.records)

    result = write_repaired_fit(
        activity,
        plan,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    repaired = read_fit(result.output_path)

    assert all(
        record.latitude is not None and record.longitude is not None for record in repaired.records
    )
    assert (
        tuple((record.latitude, record.longitude) for record in repaired.records[3:10])
        == original_coordinates[3:10]
    )
    assert tuple(record.distance for record in repaired.records) == original_distances
    assert repaired.recorded_distance_m == activity.recorded_distance_m
    assert result.distance_field_change_count == 0
    assert result.summary_field_change_count == 0
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == 100.0
    assert result.diff.sensors.percentage == 100.0


def test_missing_completion_refuses_source_without_stable_observed_run(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "short.fit"
    write_trajectory_activity(
        source_path,
        [(0, None, None), (5, 55.0, 37.0), (10, None, None)],
        retain_invalid_position_fields=True,
        distances_m=[0.0, 10.0, 20.0],
        speeds_mps=[2.0, 2.0, 2.0],
    )
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(course_path, [[(55.0, 37.0, None, None), (55.0, 37.001, None, None)]])
    activity = read_fit(source_path)

    plan = build_missing_course_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(course_path),
        _config(),
    )

    assert plan.status is RepairPlanStatus.REFUSED
    assert plan.interval_plans == ()
    assert len(plan.unresolved_missing_runs) == 2


def test_missing_completion_refuses_inconsistent_recorded_distance(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "inconsistent-distance.fit"
    full = eastward_observations(
        [float(index * 5) for index in range(13)],
        [float(index * 10) for index in range(13)],
    )
    write_trajectory_activity(
        source_path,
        [
            (
                int(elapsed if elapsed is not None else 0.0),
                latitude if 3 <= index <= 9 else None,
                longitude if 3 <= index <= 9 else None,
            )
            for index, (elapsed, latitude, longitude) in enumerate(full)
        ],
        retain_invalid_position_fields=True,
        distances_m=[float(index * 100) for index in range(13)],
        speeds_mps=[2.0] * 13,
    )
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(
        course_path,
        [
            [
                (latitude, longitude, None, None)
                for _elapsed, latitude, longitude in full
                if latitude is not None and longitude is not None
            ]
        ],
    )
    activity = read_fit(source_path)

    plan = build_missing_course_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(course_path),
        _config(),
    )

    assert plan.status is RepairPlanStatus.REFUSED
    assert plan.interval_plans == ()
    assert len(plan.unresolved_missing_runs) == 2
    assert all(
        unresolved.reasons == (ReconstructionReason.OBSERVED_DISTANCE_INCONSISTENT,)
        for unresolved in plan.unresolved_missing_runs
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_path = tmp_path / "endpoint-missing.fit"
    full = eastward_observations(
        [float(index * 5) for index in range(13)],
        [float(index * 10) for index in range(13)],
    )
    observations = [
        (
            int(elapsed if elapsed is not None else 0.0),
            latitude if 3 <= index <= 9 else None,
            longitude if 3 <= index <= 9 else None,
        )
        for index, (elapsed, latitude, longitude) in enumerate(full)
    ]
    write_trajectory_activity(
        source_path,
        observations,
        retain_invalid_position_fields=True,
        distances_m=[100.0 + float(index * 10) for index in range(13)],
        speeds_mps=[2.0] * 13,
    )
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(
        course_path,
        [
            [
                (latitude, longitude, None, None)
                for _elapsed, latitude, longitude in full
                if latitude is not None and longitude is not None
            ]
        ],
    )
    return source_path, course_path


def _config() -> CourseReconstructionConfig:
    return CourseReconstructionConfig(
        anchor_stability_min_normal_transitions=2,
        anchor_stability_scan_max_records=4,
        missing_alignment_min_position_records=5,
        missing_alignment_max_distance_ratio_error=0.1,
        missing_completion_max_course_speed_mps=5.0,
        missing_completion_max_connector_speed_mps=5.0,
    )
