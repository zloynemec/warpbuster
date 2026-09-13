"""Regressions for signal-corroborated distance repair with unknown geometry."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fit_factory import write_trajectory_activity
from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityStatus,
    IntervalDetectionKind,
)
from warpbuster.models.reconstruction import CoordinateState
from warpbuster.reconstruction import build_repair_plan, select_repair_intervals
from warpbuster.report.analyze import analyze_console
from warpbuster.report.gaps import distance_policy
from warpbuster.report.html import write_repair_html


def _unknown_geometry_fit(
    tmp_path: Path,
    *,
    delayed_distance: bool = False,
    missing_speed_index: int | None = None,
) -> Path:
    """Create plausible long transitions hiding two short impossible GNSS islands."""
    observations: list[tuple[int, float | None, float | None]] = []
    metres_per_degree = 111_195.0
    for index in range(1_000):
        latitude: float | None = 55.0 + index * 2.0 / metres_per_degree
        longitude: float | None = 37.0
        if 50 <= index <= 299 or 304 <= index <= 599 or 604 <= index <= 899:
            latitude = longitude = None
        elif 300 <= index <= 303:
            latitude = 55.0 + (8_000.0 + (index - 300) * 2.0) / metres_per_degree
        elif 600 <= index <= 603:
            latitude = 55.0 + (5_000.0 + (index - 600) * 2.0) / metres_per_degree
        observations.append((index * 2, latitude, longitude))

    if delayed_distance:
        distances = [index * 2.0 for index in range(1_000)]
        for index in range(50, 300):
            distances[index] = 98.0
    else:
        jumps = {300: 5_000.0, 600: 3_000.0, 900: 4_000.0}
        distances = [0.0]
        for index in range(1, 1_000):
            distances.append(distances[-1] + jumps.get(index, 2.0))
    speeds: list[float | None] = [1.0] * 1_000
    if missing_speed_index is not None:
        speeds[missing_speed_index] = None

    path = tmp_path / "unknown-geometry.fit"
    write_trajectory_activity(
        path,
        observations,
        retain_invalid_position_fields=True,
        distances_m=distances,
        speeds_mps=speeds,
    )
    return path


def test_correlated_signals_create_medium_islands_and_distance_repairs(tmp_path: Path) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path))
    integrity = analyze_integrity(activity)

    assert integrity.status is IntegrityStatus.CORRUPTED
    assert integrity.confidence is IntegrityConfidence.MEDIUM
    assert [item.record_index for item in integrity.distance_spike_evidence] == [300, 600, 900]
    assert [
        (item.start_record_index, item.end_record_index, item.detection_kind)
        for item in integrity.corrupted_intervals
    ] == [
        (300, 303, IntervalDetectionKind.SIGNAL_CORROBORATED_ISLAND),
        (600, 603, IntervalDetectionKind.SIGNAL_CORROBORATED_ISLAND),
    ]
    console = analyze_console(activity, integrity, verbosity=2)
    assert "distance records 899->900" in console
    assert "original=4000.00 m, replacement=2.00 m" in console
    assert "Distance-spike bounds:" in console

    default_plan = build_repair_plan(activity, integrity)
    assert all(
        default_plan.coordinate_mask[index].state is CoordinateState.PRESERVED
        for index in (*range(300, 304), *range(600, 604))
    )
    assert not select_repair_intervals(default_plan).has_changes

    plan = build_repair_plan(
        activity,
        integrity,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    assert [(gap.start_record_index, gap.end_record_index) for gap in plan.gaps] == [(50, 899)]
    selection = select_repair_intervals(plan, IntegrityConfidence.MEDIUM)
    assert len(selection.invalidations) == 8
    assert len(selection.distance_spike_repairs) == 3
    assert not selection.selected_interval_plans
    assert distance_policy(selection)["policy"] == "signal_corroborated_correction"
    assert distance_policy(selection)["unresolved_geometry"] is True


def test_writer_removes_only_proven_distance_jumps_without_inventing_positions(
    tmp_path: Path,
) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path))
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(
        activity,
        integrity,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )

    result = write_repaired_fit(
        activity,
        plan,
        tmp_path / "fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    fixed = read_fit(result.output_path)

    assert fixed.records[-1].distance == pytest.approx(1_998.0, abs=0.1)
    assert fixed.sessions[0].fields["total_distance"] == pytest.approx(1_998.0, abs=0.1)
    assert result.distance_field_change_count > 0
    assert result.coordinate_field_change_count == 16
    assert all(fixed.records[index].latitude is None for index in range(50, 900))
    assert fixed.records[49].latitude == activity.records[49].latitude
    assert fixed.records[900].latitude == activity.records[900].latitude
    assert [record.timestamp for record in fixed.records] == [
        record.timestamp for record in activity.records
    ]
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes
    report_path = write_repair_html(
        activity,
        integrity,
        None,
        plan,
        CourseReconstructionConfig(),
        tmp_path / "report.html",
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fixed_activity=fixed,
        write_result=result,
    )
    rendered = report_path.read_text()
    payload = json.loads(
        rendered.split('<script id="warpbuster-report-data" type="application/json">', 1)[1].split(
            "</script>", 1
        )[0]
    )
    assert payload["repair"]["distance"]["corrected_distance_spike_count"] == 3
    assert 'id="distance-spike-corrections"' in rendered
    assert '"Replacement increment"' in rendered


def test_delayed_odometer_catchup_is_not_treated_as_corruption(tmp_path: Path) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path, delayed_distance=True))
    integrity = analyze_integrity(activity)

    assert not integrity.distance_spike_evidence
    assert not integrity.corrupted_intervals


def test_incomplete_speed_stream_blocks_distance_repair(tmp_path: Path) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path, missing_speed_index=200))
    integrity = analyze_integrity(activity)

    assert 300 not in {item.record_index for item in integrity.distance_spike_evidence}
    assert all(item.start_record_index != 300 for item in integrity.corrupted_intervals)


def test_distance_evidence_retention_can_be_disabled(tmp_path: Path) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path))
    integrity = analyze_integrity(
        activity,
        replace(IntegrityConfig.running(), distance_spike_max_evidence=0),
    )

    assert not integrity.distance_spike_evidence
    assert not integrity.corrupted_intervals


def test_writer_revalidates_distance_proof_before_editing(tmp_path: Path) -> None:
    activity = read_fit(_unknown_geometry_fit(tmp_path))
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(activity, integrity)
    forged = replace(
        plan.distance_spike_repairs[0],
        replacement_increment_m=plan.distance_spike_repairs[0].replacement_increment_m + 1.0,
    )
    forged_plan = replace(plan, distance_spike_repairs=(forged,))

    with pytest.raises(FitWriteError, match="distance-spike proof"):
        write_repaired_fit(
            activity,
            forged_plan,
            tmp_path / "forged.fit",
            minimum_confidence=IntegrityConfidence.MEDIUM,
        )
    assert not (tmp_path / "forged.fit").exists()
