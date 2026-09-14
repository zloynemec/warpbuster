"""Unverified GNSS telemetry cannot veto an otherwise usable gap path."""

from dataclasses import replace

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.local_reconstruction_factory import local_fixture
from tests.test_automatic_osm import discover
from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import AllocationMethod
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction import apply_automatic_osm_routes, build_repair_plan
from warpbuster.reconstruction.local import _Path, _score_path_signals
from warpbuster.reconstruction.signals import allocate_signal_progress
from warpbuster.reconstruction.timing import allocation_clock
from warpbuster.report.fit import write_result_report


def undercount_fixture(tmp_path):
    activity, course = local_fixture(tmp_path, missing=((150, 371),))
    distances = [2 * i - 0.7 * min(223, max(0, i - 149)) for i in range(600)]
    write_trajectory_activity(
        activity.preservation.source_path,
        [(r.index, r.latitude, r.longitude) for r in activity.records],
        retain_invalid_position_fields=True,
        distances_m=distances,
        speeds_mps=[1.3 if 149 <= i <= 372 else 2.0 for i in range(600)],
        altitudes_m=[100.0] * 600,
    )
    return read_fit(activity.preservation.source_path), course


@pytest.mark.parametrize("provider", ["gpx", "osm"])
def test_undercounted_telemetry_uses_active_time_and_writes_losslessly(tmp_path, provider):
    activity, course = undercount_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(
        activity, integrity, course if provider == "gpx" else None, fill_missing_from_course=True
    )
    if provider == "osm":
        plan = apply_automatic_osm_routes(activity, integrity, plan, discover(activity, plan))
    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    provenance = candidate.provenance or candidate.osm_provenance
    assert candidate.confidence is IntegrityConfidence.MEDIUM
    assert provenance.allocation_method is AllocationMethod.TIMESTAMPS
    assert {"distance_path_mismatch", "speed_path_mismatch", "active_time_estimated"} <= set(
        provenance.signal_diagnostics
    )
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert result.validation.valid and result.post_write_verified
    assert result.diff.unexpected_changed_field_count == 0
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    assert write_result_report(result)["distance"]["quality"] == "uncertain"
    fixed = read_fit(result.output_path)
    assert [(r.timestamp, r.distance, r.speed, r.altitude) for r in fixed.records] == [
        (r.timestamp, r.distance, r.speed, r.altitude) for r in activity.records
    ]
    for a, b in zip(activity.records, fixed.records, strict=True):
        if not 150 <= a.index <= 371:
            assert (a.latitude, a.longitude) == (b.latitude, b.longitude)


def test_disagreement_penalty_is_bounded_and_not_two_independent_votes(tmp_path):
    activity, course = undercount_fixture(tmp_path)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    p = plan.interval_plans[0].provenance
    path = _Path(p.anchor_before, p.anchor_after, p.direction, 0, ())
    config = CourseReconstructionConfig()
    assert config.signal_path_score_penalty_m == 10
    assert _score_path_signals(path, (path.length,), config).score == 0
    one = _score_path_signals(path, (path.length * 0.65,), config)
    two = _score_path_signals(path, (path.length * 0.65, path.length * 0.65), config)
    assert one.score == two.score > 0
    assert _score_path_signals(path, (path.length * 100,), config).score == 10
    assert p.signal_path_score_penalty_m > 0
    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            CourseReconstructionConfig(signal_path_score_penalty_m=value)


def test_bad_progress_profile_falls_back_but_impossible_active_time_still_fails(tmp_path):
    activity, _ = undercount_fixture(tmp_path)
    records = activity.records[149:373]
    records = tuple(
        replace(r, speed=None, distance=0.0 if n == 0 else 400.0) for n, r in enumerate(records)
    )
    clock = allocation_clock(records, ())
    method, fractions, diagnostics = allocate_signal_progress(
        records, 400, IntegrityConfig.running(), clock, error_budget_m=100, maximum_speed_mps=10
    )
    assert method is AllocationMethod.TIMESTAMPS
    # The discontinuous profile is either independently implausible or unsuitable for allocation.
    assert any(item.startswith("distance_") for item in diagnostics)
    assert fractions[-1] == 1
    assert (
        allocate_signal_progress(
            records,
            3000,
            IntegrityConfig.running(),
            clock,
            error_budget_m=100,
            maximum_speed_mps=10,
        )
        is Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    )


def test_accepted_gpx_with_disagreement_keeps_priority_over_osm(tmp_path):
    activity, course = undercount_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    final = apply_automatic_osm_routes(activity, integrity, base, discover(activity, base))
    assert final.interval_plans == base.interval_plans
    assert final.interval_plans[0].provenance is not None
