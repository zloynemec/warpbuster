"""Whole-course assumptions are limited to originally coordinate-free FIT inputs."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.cli import main
from warpbuster.config import CourseReconstructionConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import AllocationMethod, GapRepairPlan
from warpbuster.pipeline import PipelineConfig, run_repair
from warpbuster.reconstruction import build_repair_plan, select_repair_intervals
from warpbuster.report.html import write_repair_html
from warpbuster.report.repair import repair_console, repair_report


@pytest.mark.parametrize("position_fields", [True, False])
def test_default_pipeline_reconstructs_and_preserves_original_fields(
    tmp_path: Path, position_fields: bool
) -> None:
    activity, course = local_fixture(
        tmp_path, count=100, missing=((0, 99),), position_fields=position_fields
    )
    source = activity.preservation.source_path
    preview = run_repair(source, course.source_path, dry_run=True)
    assert preview.selection.has_changes
    assert preview.write_result is None
    candidate = preview.plan.interval_plans[0]
    assert isinstance(candidate, GapRepairPlan)
    assert candidate.confidence is IntegrityConfidence.MEDIUM
    assert candidate.interval.kind.value == "whole_activity"
    assert candidate.provenance is not None
    assert candidate.provenance.endpoint_source == "whole_course_assumption"
    assert candidate.provenance.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert not candidate.provenance.context_ranges
    assert preview.integrity.position_record_count == 0
    assert not preview.integrity.corrupted_intervals
    assert not select_repair_intervals(preview.plan).has_changes

    written = run_repair(source, course.source_path)
    assert written.write_result is not None
    assert written.fixed_activity is not None
    assert written.write_result.post_write_verified
    diff = written.write_result.diff
    assert diff.unexpected_changed_field_count == 0
    assert diff.timestamps.percentage == diff.sensors.percentage == 100
    assert diff.unknown_fields.percentage == diff.developer_fields.percentage == 100
    assert written.write_result.distance_field_change_count == 0
    assert written.write_result.summary_field_change_count == 0
    assert source.read_bytes() == activity.preservation.raw_bytes
    for original, fixed in zip(activity.records, written.fixed_activity.records, strict=True):
        assert fixed.latitude is not None and fixed.longitude is not None
        assert (fixed.timestamp, fixed.distance, fixed.speed, fixed.altitude) == (
            original.timestamp,
            original.distance,
            original.speed,
            original.altitude,
        )


def test_progress_uses_distance_offsets_and_stops(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    activity = replace(
        activity,
        records=tuple(
            replace(r, distance=500.0 + (0 if r.index <= 10 else (r.index - 10) * 198 / 89))
            for r in activity.records
        ),
    )
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    assert len(plan.interval_plans) == 1  # no missing-completion flag needed
    updates = plan.interval_plans[0].coordinate_updates
    assert len({(u.candidate_latitude, u.candidate_longitude) for u in updates[:11]}) == 1
    assert updates[0].course_distance_m == 0
    assert updates[-1].course_distance_m == pytest.approx(course.total_distance_m)
    assert updates[50].course_distance_m == pytest.approx(course.total_distance_m * 40 / 89)


@pytest.mark.parametrize("ratio,accepted", [(0.10, True), (0.099, False), (0.101, True)])
def test_distance_threshold_is_inclusive_and_configurable(
    tmp_path: Path, ratio: float, accepted: bool
) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    observed = course.total_distance_m / 1.1
    activity = replace(
        activity,
        records=tuple(replace(r, distance=r.index * observed / 99) for r in activity.records),
    )
    config = CourseReconstructionConfig(coordinate_free_max_distance_ratio_error=ratio)
    plan = build_repair_plan(activity, analyze_integrity(activity), course, config)
    assert bool(plan.interval_plans) is accepted
    if not accepted:
        assert plan.unresolved_gaps[0].reasons[0].value == "observed_distance_inconsistent"


@pytest.mark.parametrize("value", [-0.01, 1.0, float("nan"), float("inf"), True])
def test_distance_threshold_rejects_invalid_configuration(value: float) -> None:
    with pytest.raises(ValueError, match="coordinate_free_max_distance_ratio_error"):
        CourseReconstructionConfig(coordinate_free_max_distance_ratio_error=value)


@pytest.mark.parametrize("distance_factor", [0.8, 1.2])
def test_distance_difference_is_checked_in_both_directions(
    tmp_path: Path, distance_factor: float
) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    activity = replace(
        activity,
        records=tuple(replace(r, distance=r.distance * distance_factor) for r in activity.records),
    )
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "observed_distance_inconsistent"


@pytest.mark.parametrize("conflict,open_pause", [(False, False), (True, False), (False, True)])
def test_pauses_freeze_progress_and_conflicts_refuse(
    tmp_path: Path, conflict: bool, open_pause: bool
) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    event = activity.events[0]
    events = [
        event,
        replace(
            event,
            index=event.index + 1,
            fields={
                **event.fields,
                "timestamp": activity.records[40].timestamp,
                "event_type": "stop",
            },
        ),
    ]
    if not open_pause:
        events.append(
            replace(
                event,
                index=event.index + 2,
                fields={
                    **event.fields,
                    "timestamp": activity.records[50].timestamp,
                    "event_type": "start",
                },
            )
        )
    records = tuple(
        replace(
            r,
            distance=r.index * 2
            if conflict
            else (r.index if r.index <= 40 else 40 if r.index <= 50 else r.index - 10) * 198 / 89,
        )
        for r in activity.records
    )
    activity = replace(activity, records=records, events=tuple(events))
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    if conflict or open_pause:
        assert not plan.interval_plans
        assert plan.unresolved_gaps[0].reasons[0].value == (
            "timer_state_unresolved" if open_pause else "pause_distance_conflict"
        )
    else:
        updates = plan.interval_plans[0].coordinate_updates
        assert len({(u.candidate_latitude, u.candidate_longitude) for u in updates[40:51]}) == 1


@pytest.mark.parametrize(
    "bad",
    [
        "no_course",
        "segments",
        "missing_distance",
        "zero_distance",
        "reset",
        "timestamp",
        "continuity",
        "latitude",
        "longitude",
        "position",
        "session_position",
    ],
)
def test_ineligible_inputs_do_not_get_whole_course_assumption(tmp_path: Path, bad: str) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    records = list(activity.records)
    if bad == "no_course":
        course = None
    elif bad == "segments":
        course = replace(course, segments=(*course.segments, course.segments[0]))
    elif bad in {"missing_distance", "zero_distance"}:
        records = [replace(r, distance=None if bad == "missing_distance" else 0.0) for r in records]
    elif bad == "reset":
        records[50] = replace(records[50], distance=0.0)
    elif bad == "timestamp":
        records[50] = replace(records[50], timestamp=records[49].timestamp)
    elif bad == "continuity":
        records[50:] = [replace(r, continuity_id=1) for r in records[50:]]
    elif bad in {"latitude", "longitude"}:
        records[50] = replace(records[50], **{bad: 44.0})
    elif bad == "position":
        records[50] = replace(records[50], latitude=44.0, longitude=33.0)
    elif bad == "session_position":
        session = activity.sessions[0]
        activity = replace(
            activity,
            sessions=(
                replace(
                    session,
                    fields={
                        **session.fields,
                        "start_position_lat": 1000,
                    },
                ),
            ),
        )
    activity = replace(activity, records=tuple(records))
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert not plan.interval_plans


def test_cli_and_reports_explain_full_course_assumption(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    activity, course = local_fixture(tmp_path, count=100, missing=((0, 99),))
    args = [
        "process",
        str(activity.preservation.source_path),
        str(course.source_path),
        "--osm-mode",
        "disabled",
        "--dry-run",
        "--json",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    notice = report["coordinate_free_reconstruction"]
    assert notice["status"] == "planned"
    assert notice["confidence"] == "medium"
    assert notice["recorded_distance_m"] == 198
    assert notice["course_distance_m"] == pytest.approx(course.total_distance_m)
    assert "no coordinates" in notice["message"]
    assert "first to last" in notice["message"]
    run = run_repair(
        activity.preservation.source_path, course.source_path, dry_run=True, config=PipelineConfig()
    )
    config = run.reconstruction_config
    console = repair_console(
        run.plan, course, config, minimum_confidence=IntegrityConfidence.MEDIUM
    )
    assert notice["message"] in console
    path = tmp_path / "preview.html"
    write_repair_html(
        activity,
        run.integrity,
        course,
        run.plan,
        config,
        path,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    payload = json.loads(
        path.read_text()
        .split('<script id="warpbuster-report-data" type="application/json">', 1)[1]
        .split("</script>", 1)[0]
    )
    assert notice["message"] in payload["metrics_comparison"]["notes"]
    written = run_repair(activity.preservation.source_path, course.source_path)
    assert written.write_result is not None
    applied = repair_report(
        written.write_result.plan, course, config, minimum_confidence=IntegrityConfidence.MEDIUM
    )
    assert applied["coordinate_free_reconstruction"]["status"] == "applied"
