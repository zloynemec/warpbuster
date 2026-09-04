"""Generic terminal-failure regressions; no private coordinates or course in proof."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from time import perf_counter

import pytest

from tests.activity_factory import eastward_observations, make_activity
from tests.local_reconstruction_factory import local_fixture
from tests.test_html_report import _embedded_payload
from warpbuster.config import IntegrityConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.integrity.one_sided import _belongs_to_classic_interval, _classic_interval_index
from warpbuster.integrity.tail import reachability_excess
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntervalDetectionKind
from warpbuster.models.reconstruction import CoordinateState, CourseData
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.gaps import coordinate_mask
from warpbuster.report.analyze import analyze_console, analyze_report
from warpbuster.report.html import write_analyze_html


def tail_fixture(tmp_path: Path, *, missing_entry: bool = False) -> tuple[ActivityData, CourseData]:
    return local_fixture(
        tmp_path,
        count=180,
        missing=((90, 109), (150, 179)) if missing_entry else ((150, 179),),
        spikes=tuple(range(110 if missing_entry else 100, 150)),
    )


@pytest.mark.parametrize("missing_entry", [False, True])
def test_smooth_unreachable_tail_has_one_anchor_proof(tmp_path: Path, missing_entry: bool) -> None:
    activity, _ = tail_fixture(tmp_path, missing_entry=missing_entry)
    integrity = analyze_integrity(activity)
    assert len(integrity.corrupted_intervals) == 1
    interval = integrity.corrupted_intervals[0]
    assert interval.detection_kind is IntervalDetectionKind.UNREACHABLE_TAIL
    assert interval.trusted_before_record_index == (89 if missing_entry else 99)
    assert interval.start_record_index == (110 if missing_entry else 100)
    assert interval.end_record_index == 149
    assert interval.bridge is interval.trusted_after_record_index is None
    assert interval.reachability.stop_reason == "end_of_recording"
    assert interval.reachability.positioned_record_count == (40 if missing_entry else 50)
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    for item in mask:
        if interval.start_record_index <= item.record_index <= 149:
            assert item.state is CoordinateState.INVALIDATED
        elif activity.records[item.record_index].latitude is not None:
            assert item.state is CoordinateState.PRESERVED
    assert all(
        item.state is not CoordinateState.INVALIDATED
        for item in coordinate_mask(activity, integrity)
    )
    report = analyze_report(activity, integrity)
    assert report["corrupted_intervals"][0]["reachability"]["positioned_record_count"] > 0
    assert report["corrupted_intervals"][0]["bridge"] is None
    assert "unreachable positions=" in analyze_console(activity, integrity, verbosity=1)


def test_reachable_after_long_gap_is_not_impossible(tmp_path: Path) -> None:
    activity, _ = tail_fixture(tmp_path, missing_entry=True)
    activity = replace(
        activity,
        records=tuple(
            replace(r, timestamp=r.timestamp + timedelta(hours=10)) if r.index >= 90 else r
            for r in activity.records
        ),
    )
    assert not analyze_integrity(activity).corrupted_intervals


def test_proof_stops_when_radius_catches_up_without_trusting_remainder(tmp_path: Path) -> None:
    activity, _ = tail_fixture(tmp_path)
    activity = replace(
        activity,
        records=tuple(
            replace(r, timestamp=r.timestamp + timedelta(hours=10)) if r.index >= 140 else r
            for r in activity.records
        ),
    )
    integrity = analyze_integrity(activity)
    interval = integrity.corrupted_intervals[0]
    assert interval.end_record_index == 139
    assert interval.reachability.stop_reason == "reachable_position_not_confirmed"
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    assert mask[139].state is CoordinateState.INVALIDATED
    assert mask[140].state is CoordinateState.PRESERVED
    assert not mask[140].anchor_eligible
    assert all(not item.anchor_eligible for item in mask[140:])


@pytest.mark.parametrize(
    "fault", ["continuity", "missing_time", "backward_time", "weak_anchor", "generic"]
)
def test_incomplete_evidence_does_not_authorize_tail_deletion(tmp_path: Path, fault: str) -> None:
    activity, _ = tail_fixture(tmp_path, missing_entry=True)
    records = list(activity.records)
    config = IntegrityConfig.running()
    if fault == "continuity":
        records = [replace(r, continuity_id=1) if r.index >= 95 else r for r in records]
    elif fault == "missing_time":
        records[95] = replace(records[95], timestamp=None)
    elif fault == "backward_time":
        records[95] = replace(records[95], timestamp=records[94].timestamp)
    elif fault == "weak_anchor":
        config = replace(config, tail_anchor_min_normal_transitions=100)
    else:
        config = IntegrityConfig()
    assert not analyze_integrity(
        replace(activity, records=tuple(records)), config
    ).corrupted_intervals


def test_real_detour_and_recovered_coordinates_are_preserved(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=(), spikes=(100,), detour=(200, 400, 200))
    integrity = analyze_integrity(activity)
    assert all(
        i.detection_kind is not IntervalDetectionKind.UNREACHABLE_TAIL
        for i in integrity.corrupted_intervals
    )
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    assert all(item.state is CoordinateState.PRESERVED for item in mask[101:])


def test_error_budget_and_speed_limit_define_physical_radius(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, count=20, missing=())
    anchor, point = activity.records[0], activity.records[10]
    excess = reachability_excess(anchor, point, 1, 0)
    assert excess == pytest.approx(10, abs=0.1)
    assert reachability_excess(anchor, point, 1, 11) < 0
    assert reachability_excess(anchor, point, 3, 0) < 0


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_error_budget_config_validation(value: float) -> None:
    with pytest.raises(ValueError, match="tail_position_error_budget"):
        replace(IntegrityConfig.running(), tail_position_error_budget_m=value)


def test_anchor_config_validation_and_defaults() -> None:
    config = IntegrityConfig.running()
    assert config.tail_anchor_min_normal_transitions == 15
    assert config.tail_position_error_budget_m == 50
    assert replace(config, tail_position_error_budget_m=0)
    with pytest.raises(ValueError, match="tail_anchor_min_normal"):
        replace(config, tail_anchor_min_normal_transitions=0)


@pytest.mark.parametrize("with_course", [False, True])
def test_tail_invalidation_and_optional_reconstruction_write_losslessly(
    tmp_path: Path, with_course: bool
) -> None:
    activity, course = tail_fixture(tmp_path, missing_entry=True)
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(
        activity,
        integrity,
        course if with_course else None,
        fill_missing_from_course=with_course,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    assert len(plan.gaps) == 1
    assert plan.gaps[0].start_record_index == 90
    assert plan.gaps[0].end_record_index == 179
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert result.post_write_verified
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.diff.unknown_fields.percentage == result.diff.developer_fields.percentage == 100
    assert len(fixed.records) == len(activity.records)
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    for old, new in zip(activity.records, fixed.records, strict=True):
        assert (old.timestamp, old.distance, old.speed, old.altitude) == (
            new.timestamp,
            new.distance,
            new.speed,
            new.altitude,
        )
        if old.index < 90:
            assert (old.latitude, old.longitude) == (new.latitude, new.longitude)
        else:
            assert (new.latitude is not None) is with_course
    assert not analyze_integrity(fixed).corrupted_intervals


def test_forged_scope_cannot_delete_reachable_coordinates(tmp_path: Path) -> None:
    activity, _ = tail_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    records = list(activity.records)
    records[120] = replace(records[120], latitude=records[99].latitude)
    mask = coordinate_mask(
        replace(activity, records=tuple(records)), integrity, IntegrityConfidence.MEDIUM
    )
    assert all(item.state is not CoordinateState.INVALIDATED for item in mask)


def test_weak_unresolved_entry_cannot_turn_real_return_into_a_bad_tail() -> None:
    full = eastward_observations([float(i) for i in range(200)], [float(i * 2) for i in range(200)])
    observations = [
        (t, 56.0, lon) if 1 <= i <= 50 or i >= 150 else (t, lat, lon)
        for i, (t, lat, lon) in enumerate(full)
    ]
    activity = make_activity(observations)
    # Force the older island search to leave the first excursion unresolved.
    config = replace(IntegrityConfig.running(), island_search_max_elapsed_seconds=0.01)
    integrity = analyze_integrity(activity, config)
    assert [(i.start_record_index, i.end_record_index) for i in integrity.corrupted_intervals] == [
        (150, 199)
    ]
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    assert all(m.state is CoordinateState.PRESERVED for m in mask[51:150])


def test_stable_reachable_run_releases_quarantine_without_extending_deletion(
    tmp_path: Path,
) -> None:
    activity, _ = tail_fixture(tmp_path)
    activity = replace(
        activity,
        records=tuple(
            replace(r, timestamp=r.timestamp + timedelta(hours=10)) if r.index >= 120 else r
            for r in activity.records
        ),
    )
    integrity = analyze_integrity(activity)
    interval = integrity.corrupted_intervals[0]
    assert interval.end_record_index == 119
    assert interval.reachability.recovered_anchor_record_index == 135
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    assert all(
        m.state is CoordinateState.PRESERVED and not m.anchor_eligible for m in mask[120:135]
    )
    assert mask[135].state is CoordinateState.PRESERVED and mask[135].anchor_eligible


def test_tail_html_has_auditable_proof_and_course_does_not_change_it(tmp_path: Path) -> None:
    activity, course = tail_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    without, with_course = tmp_path / "without.html", tmp_path / "with.html"
    write_analyze_html(activity, integrity, without)
    write_analyze_html(activity, integrity, with_course, course=course)
    first = _embedded_payload(without.read_text())
    second = _embedded_payload(with_course.read_text())
    assert first["analysis"] == second["analysis"]
    assert first["diagnostic_regions"] == second["diagnostic_regions"]
    region = next(r for r in first["diagnostic_regions"] if r["kind"] == "corrupted_interval")
    assert region["detector_stage"] == "physical_tail_reachability"
    assert region["metrics"]["bridge"] is None
    assert region["metrics"]["reachability"]["speed_limit_mps"] == 25
    assert region["metrics"]["reachability"]["minimum_excess_distance_m"] > 0


def test_20k_unreachable_tail_detection_and_mask_are_linear() -> None:
    full = eastward_observations(
        [float(i) for i in range(20_000)], [float(i * 2) for i in range(20_000)]
    )
    activity = make_activity(
        [(t, lat + 10, lon) if i >= 100 else (t, lat, lon) for i, (t, lat, lon) in enumerate(full)]
    )
    start = perf_counter()
    integrity = analyze_integrity(activity)
    mask = coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)
    elapsed = perf_counter() - start
    assert elapsed < 5
    assert sum(m.state is CoordinateState.INVALIDATED for m in mask) == 19_900
    assert len(integrity.corrupted_intervals) == 1


def test_classic_interval_index_preserves_inclusive_coverage(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=(), spikes=(100,))
    base = analyze_integrity(activity).corrupted_intervals[0]
    bounds = [(12, 20), (0, 10), (2, 6), (5, 15), (25, None)]
    intervals = tuple(
        replace(base, trusted_before_record_index=a, trusted_after_record_index=b)
        for a, b in bounds
    )
    starts, ends = _classic_interval_index(intervals)
    for left in range(26):
        for right in range(left + 1, 27):
            entry = replace(base.entry_transition, from_record_index=left, to_record_index=right)
            assert _belongs_to_classic_interval(entry, starts, ends) == any(
                a <= left and b is not None and right <= b for a, b in bounds
            )
    assert not _belongs_to_classic_interval(base.entry_transition, (), ())
