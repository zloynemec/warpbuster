"""Byte-preserving FIT writer and repair application tests."""

from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

from tests.activity_factory import eastward_observations
from tests.fit_factory import write_repairable_activity
from tests.gpx_factory import GpxPoint, write_gpx_activity
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import FitWriteError, default_output_path, write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData, FitPreservationData
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    GapRepairPlan,
    ReconstructionReason,
    RepairIntervalAction,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedGap,
)
from warpbuster.reconstruction import build_course_repair_plan
from warpbuster.report.fit import write_result_console, write_result_report


def test_ready_plan_writes_valid_preserved_fit_and_corrects_distance(tmp_path: Path) -> None:
    """Only planned coordinates and supported derived fields change in a valid FIT."""
    source_path, activity, plan = _ready_fixture(tmp_path)
    original_bytes = source_path.read_bytes()
    original_timestamps = tuple(record.timestamp for record in activity.records)
    original_sensors = _sensor_rows(activity)

    result = write_repaired_fit(activity, plan)

    assert result.output_path == default_output_path(source_path)
    assert result.output_path.exists()
    assert result.bytes_written == len(original_bytes)
    assert result.output_path.stat().st_size == len(original_bytes)
    assert source_path.read_bytes() == original_bytes
    assert result.validation.valid is True
    assert result.validation.crc_valid is True
    assert result.diff.structure_compatible is True
    assert result.diff.definitions_unchanged is True
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == 100.0
    assert result.diff.sensors.percentage == 100.0
    assert result.diff.developer_fields.percentage == 100.0
    assert result.coordinate_field_change_count == 2
    assert result.distance_field_change_count > 0
    assert result.summary_field_change_count == 4

    fixed = read_fit(result.output_path)
    assert tuple(record.timestamp for record in fixed.records) == original_timestamps
    assert _sensor_rows(fixed) == original_sensors
    assert fixed.records[15].latitude == activity.records[15].latitude
    assert fixed.records[17].latitude == activity.records[17].latitude
    assert fixed.records[16].latitude == pytest.approx(55.0, abs=1e-6)
    assert fixed.records[16].longitude != activity.records[16].longitude
    assert fixed.records[-1].distance == pytest.approx(192.0, abs=0.05)
    assert all(
        current.distance is not None
        and previous.distance is not None
        and current.distance >= previous.distance
        for previous, current in pairwise(fixed.records)
    )
    assert fixed.laps[0].fields["total_distance"] == pytest.approx(192.0, abs=0.05)
    assert fixed.sessions[0].fields["total_distance"] == pytest.approx(192.0, abs=0.05)
    assert fixed.laps[0].fields["enhanced_avg_speed"] == pytest.approx(6.0, abs=0.002)
    assert fixed.sessions[0].fields["enhanced_avg_speed"] == pytest.approx(6.0, abs=0.002)


def test_writer_uses_elapsed_time_when_summary_timestamp_is_inconsistent(
    tmp_path: Path,
) -> None:
    """A broken summary timestamp must not leave or invert cumulative corrections."""
    source_path = tmp_path / "broken-summary-time.fit"
    write_repairable_activity(source_path, summary_timestamp_at_start=True)
    course_path = tmp_path / "course.gpx"
    observations = eastward_observations(
        [float(index) for index in range(33)],
        [float(index * 6) for index in range(33)],
    )
    write_gpx_activity(
        course_path,
        [
            [
                (latitude, longitude, None, None)
                for _elapsed, latitude, longitude in observations
                if latitude is not None and longitude is not None
            ]
        ],
    )
    activity = read_fit(source_path)
    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(course_path),
    )

    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)

    assert fixed.records[-1].distance == pytest.approx(192.0, abs=0.05)
    assert fixed.recorded_distance_m == pytest.approx(192.0, abs=0.05)
    assert fixed.laps[0].fields["total_distance"] == pytest.approx(192.0, abs=0.05)
    assert fixed.sessions[0].fields["total_distance"] == pytest.approx(192.0, abs=0.05)
    assert fixed.laps[0].fields["timestamp"] == activity.laps[0].fields["timestamp"]
    assert fixed.sessions[0].fields["timestamp"] == activity.sessions[0].fields["timestamp"]


def test_writer_refuses_overwrite_and_applies_high_candidate_from_partial_plan(
    tmp_path: Path,
) -> None:
    """Existing outputs are protected while safe candidates may be applied partially."""
    source_path, activity, plan = _ready_fixture(tmp_path)
    output_path = tmp_path / "explicit.fit"
    output_path.write_bytes(b"keep me")

    with pytest.raises(FitWriteError, match="output already exists"):
        write_repaired_fit(activity, plan, output_path)
    assert output_path.read_bytes() == b"keep me"

    result = write_repaired_fit(activity, plan, output_path, overwrite=True)
    assert result.output_path == output_path
    assert output_path.read_bytes() != b"keep me"
    fixed_preservation = read_fit(output_path).preservation
    assert isinstance(fixed_preservation, FitPreservationData)
    assert fixed_preservation.crc_valid is True

    with pytest.raises(FitWriteError, match="must differ from the original"):
        write_repaired_fit(activity, plan, source_path, overwrite=True)

    planned_candidate = plan.interval_plans[0]
    assert isinstance(planned_candidate, GapRepairPlan)
    planned_interval = planned_candidate.interval
    unresolved = UnresolvedGap(
        interval=replace(
            planned_interval,
            start_record_index=17,
            end_record_index=17,
            gap_id="gap-17-17",
            anchor_before_record_index=16,
            anchor_after_record_index=18,
        ),
        confidence=IntegrityConfidence.LOW,
        reasons=(ReconstructionReason.ANCHOR_BEFORE_NOT_MATCHED,),
    )
    partial = replace(
        plan,
        status=RepairPlanStatus.PARTIAL,
        confidence=IntegrityConfidence.LOW,
        detected_interval_count=2,
        unresolved_gaps=(unresolved,),
        gaps=(*plan.gaps, unresolved.interval),
    )
    partial_output = tmp_path / "partial.fit"
    result = write_repaired_fit(activity, partial, partial_output)

    assert partial_output.exists()
    assert result.selection.is_partial is True
    assert result.selection.applied_interval_count == 1
    assert result.selection.skipped_interval_count == 1
    assert [decision.action for decision in result.selection.decisions] == [
        RepairIntervalAction.APPLIED,
        RepairIntervalAction.SKIPPED,
    ]
    report = write_result_report(result)
    selection_report = cast(dict[str, object], report["selection"])
    interval_reports = cast(list[dict[str, object]], selection_report["intervals"])
    assert selection_report["application_status"] == "partial"
    assert [item["action"] for item in interval_reports] == [
        "applied",
        "skipped",
    ]
    console = write_result_console(result)
    assert "Application: PARTIAL (minimum=HIGH, applied=1, skipped=1)" in console
    assert "records 16..16: APPLIED" in console
    assert "records 17..17: SKIPPED" in console
    fixed = read_fit(partial_output)
    assert fixed.records[16].longitude != activity.records[16].longitude
    assert fixed.records[17].latitude == activity.records[17].latitude
    assert fixed.records[17].longitude == activity.records[17].longitude
    assert source_path.exists()


@pytest.mark.parametrize(
    "minimum_confidence",
    [IntegrityConfidence.LOW, IntegrityConfidence.MEDIUM],
)
def test_writer_uses_explicit_minimum_confidence_threshold(
    tmp_path: Path,
    minimum_confidence: IntegrityConfidence,
) -> None:
    """HIGH is the default; lowering the threshold explicitly admits MEDIUM candidates."""
    _source_path, activity, plan = _ready_fixture(tmp_path)
    medium_candidate = replace(
        plan.interval_plans[0],
        confidence=IntegrityConfidence.MEDIUM,
    )
    medium_plan = replace(
        plan,
        status=RepairPlanStatus.PARTIAL,
        confidence=IntegrityConfidence.MEDIUM,
        interval_plans=(medium_candidate,),
    )

    high_output = tmp_path / "high.fit"
    high_result = write_repaired_fit(activity, medium_plan, high_output)
    assert high_result.selection.applied_interval_count == 0
    assert read_fit(high_output).records[16].latitude is None
    assert high_result.distance_field_change_count == 0

    medium_output = tmp_path / f"{minimum_confidence.value}.fit"
    result = write_repaired_fit(
        activity,
        medium_plan,
        medium_output,
        minimum_confidence=minimum_confidence,
    )
    assert medium_output.exists()
    assert result.selection.minimum_confidence is minimum_confidence
    assert result.selection.applied_interval_count == 1


def test_writer_refuses_source_changed_after_plan(tmp_path: Path) -> None:
    """Preservation bytes cannot be applied after the source path changes underneath them."""
    source_path, activity, plan = _ready_fixture(tmp_path)
    source_path.write_bytes(source_path.read_bytes() + b"changed")

    output_path = tmp_path / "stale-plan.fit"
    with pytest.raises(FitWriteError, match="changed after it was read"):
        write_repaired_fit(activity, plan, output_path)
    assert not output_path.exists()


def _ready_fixture(tmp_path: Path) -> tuple[Path, ActivityData, RepairPlan]:
    source_path = tmp_path / "repairable.fit"
    write_repairable_activity(source_path)
    course_path = tmp_path / "course.gpx"
    observations = eastward_observations(
        [float(index) for index in range(33)],
        [float(index * 6) for index in range(33)],
    )
    points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in observations
        if latitude is not None and longitude is not None
    ]
    write_gpx_activity(course_path, [points])
    activity = read_fit(source_path)
    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(course_path),
    )
    assert plan.status is RepairPlanStatus.READY
    return source_path, activity, plan


def _sensor_rows(
    activity: ActivityData,
) -> tuple[
    tuple[float | None, float | None, int | None, int | None, int | None, float | None], ...
]:
    return tuple(
        (
            record.altitude,
            record.speed,
            record.heart_rate,
            record.cadence,
            record.power,
            record.temperature,
        )
        for record in activity.records
    )
