"""Synthetic regressions for locally consistent geometry and distance repair."""

import json
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit import writer
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.reconstruction import build_repair_plan
from warpbuster.report.fit import write_result_report
from warpbuster.report.html import write_repair_html


def _jump_fixture(tmp_path: Path, *, origin: str, other_gaps: bool):
    spikes = () if origin == "missing" else (150,)
    if other_gaps:
        spikes += (350, 365)
    base, course = local_fixture(
        tmp_path,
        missing=((150 if origin == "missing" else 151, 165),) if origin != "invalidated" else (),
        spikes=spikes,
    )
    source = tmp_path / "distance-jumps.fit"
    write_trajectory_activity(
        source,
        [(r.index, r.latitude, r.longitude) for r in base.records],
        retain_invalid_position_fields=True,
        distances_m=[r.distance + (20000 if r.index >= 150 else 0) for r in base.records],
        speeds_mps=[r.speed for r in base.records],
        altitudes_m=[r.altitude for r in base.records],
    )
    return read_fit(source), course


@pytest.mark.parametrize("origin", ["invalidated", "mixed", "missing"])
@pytest.mark.parametrize("other_gaps", [False, True])
def test_distance_correction_is_local_and_independent_of_gap_origin(
    tmp_path: Path, origin: str, other_gaps: bool
) -> None:
    activity, course = _jump_fixture(tmp_path, origin=origin, other_gaps=other_gaps)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 1
    assert not plan.interval_plans[0].preserve_recorded_distance
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert fixed.records[-1].distance == pytest.approx(1198, abs=0.1)
    assert fixed.sessions[0].fields["total_distance"] == pytest.approx(1198, abs=0.1)
    assert result.distance_field_change_count > 0
    for original, written in zip(activity.records, fixed.records, strict=True):
        assert (original.timestamp, original.speed, original.altitude) == (
            written.timestamp,
            written.speed,
            written.altitude,
        )
    end = plan.interval_plans[0].interval.anchor_after_record_index
    for index in range(end + 1, len(fixed.records)):
        assert fixed.records[index].distance - fixed.records[index - 1].distance == pytest.approx(
            activity.records[index].distance - activity.records[index - 1].distance, abs=0.02
        )
    audit = write_result_report(result)
    assert audit["distance"]["policy"] == "coordinate_dependent_correction"
    assert audit["distance"]["correction_skipped"] is False
    assert audit["post_write_verified"] is True
    if other_gaps:
        assert fixed.records[350].latitude is None
        assert fixed.records[365].latitude is None
        assert audit["distance"]["quality"] == "uncertain"
        assert audit["distance"]["status"] == "partially_corrected"
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes


def test_short_gap_can_use_consistent_speed_after_distance_disagreement(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 154),))
    activity = replace(
        activity,
        records=tuple(
            replace(
                r,
                latitude=r.latitude + 6 / 111195
                if r.latitude is not None and r.index >= 155
                else r.latitude,
                speed=2.6 if 149 <= r.index <= 155 else r.speed,
            )
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    assert candidate.provenance.allocation_method.value == "recorded_speed"
    assert candidate.preserve_recorded_distance  # disagreement is not corruption proof
    assert "distance_path_mismatch" in candidate.provenance.signal_diagnostics


def test_physical_distance_jump_in_original_missing_can_use_speed(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 165),))
    activity = replace(
        activity,
        records=tuple(
            replace(r, distance=r.distance + (20000 if r.index >= 155 else 0))
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 1
    assert not plan.interval_plans[0].preserve_recorded_distance
    assert plan.interval_plans[0].provenance.allocation_method.value == "recorded_speed"


def test_plausible_conflicting_signals_cannot_be_ignored_for_time(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    activity = replace(
        activity,
        records=tuple(
            replace(r, distance=r.distance + max(0, min(r.index - 149, 31)) * 2, speed=4.0)
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        CourseReconstructionConfig(anchor_match_tolerance_m=5, high_confidence_anchor_distance_m=5),
        fill_missing_from_course=True,
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "local_distance_inconsistent"


def test_plausible_distance_is_preserved_despite_corrupted_coordinates(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=(), spikes=(150,))
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    assert plan.interval_plans[0].preserve_recorded_distance
    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)
    assert [r.distance for r in fixed.records] == [r.distance for r in activity.records]
    assert all(b.distance >= a.distance for a, b in pairwise(fixed.records))


def test_post_write_verification_rejects_dropped_metric_patch_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activity, course = _jump_fixture(tmp_path, origin="mixed", other_gaps=True)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    original_patch = writer._patch_fit_bytes
    calls = 0

    def drop_first_metric_patch(raw, requests):
        nonlocal calls
        calls += 1
        if calls == 1:
            requests = tuple(r for r in requests if r.category == "coordinate")
        return original_patch(raw, requests)

    monkeypatch.setattr(writer, "_patch_fit_bytes", drop_first_metric_patch)
    output = tmp_path / "must-not-publish.fit"
    with pytest.raises(FitWriteError, match="written fields disagree"):
        write_repaired_fit(activity, plan, output, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert calls == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".must-not-publish.fit.*.tmp"))
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes


def test_html_separates_input_output_geometry_and_distance_status(tmp_path: Path) -> None:
    activity, course = _jump_fixture(tmp_path, origin="mixed", other_gaps=True)
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    report = write_repair_html(
        activity,
        integrity,
        course,
        plan,
        CourseReconstructionConfig(),
        tmp_path / "report.html",
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fixed_activity=fixed,
        write_result=result,
    )
    rendered = report.read_text()
    payload = json.loads(
        rendered.split('<script id="warpbuster-report-data" type="application/json">', 1)[1].split(
            "</script>", 1
        )[0]
    )
    assert payload["output_summary"]["recorded_distance_m"] == fixed.recorded_distance_m
    assert payload["original_performance"]["distance_m"] == activity.recorded_distance_m
    assert (
        payload["original_performance"]["timer_duration_seconds"]
        == payload["repaired_performance"]["timer_duration_seconds"]
    )
    assert (
        payload["original_performance"]["average_pace_seconds_per_km"]
        < payload["repaired_performance"]["average_pace_seconds_per_km"]
    )
    assert payload["output_summary"]["missing_position_count"] == 2
    assert payload["repair"]["geometry_status"] == "partial"
    assert payload["repair"]["distance"]["status"] == "partially_corrected"
    assert payload["repair"]["gap_inventory"][0]["distance_action"] == "corrected"
    assert payload["coordinate_update_ranges"] == [[150, 165]]
    assert payload["write_result"]["post_write_verified"] is True
    assert 'addOverlay("Preserved coordinates"' in rendered
    assert 'addOverlay("Reconstructed coordinates (including joins)"' in rendered
    assert "const label = String(bucket);" in rendered
    assert '? "?" : ""' not in rendered
    assert "distance uncertainty is shown in the summary and marker popups" in rendered
    assert 'Quality: ${repair?.distance?.quality || "source_unverified"}' in rendered
