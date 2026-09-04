"""Task 011B: pauses are normal, but not evidence permitting hidden movement."""

import json
from dataclasses import replace
from datetime import timedelta
from time import perf_counter

import pytest

from tests.activity_factory import eastward_observations, make_activity
from tests.fit_factory import write_trajectory_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.cli import main
from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import SourceMessage
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import AllocationMethod
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.local import _fractions
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.reconstruction.signals import qualify_speed
from warpbuster.reconstruction.timing import activity_clock, allocation_clock, timer_pauses
from warpbuster.report.gaps import gap_audit


def _events(activity, items):
    first = activity.records[0].timestamp
    return replace(
        activity,
        events=tuple(
            SourceMessage(
                i,
                i,
                0,
                21,
                "event",
                i,
                {
                    "timestamp": first + timedelta(seconds=second),
                    "event": "timer",
                    "event_type": kind,
                    "event_group": group,
                },
                b"",
            )
            for i, (second, kind, group) in enumerate(items)
        ),
    )


def _fixture(tmp_path, shape="internal", method="distance", records_in_pause=False):
    missing, stop = {
        "prefix": ((0, 19), 5),
        "internal": ((150, 179), 160),
        "suffix": ((560, 599), 575),
    }[shape]
    points = eastward_observations(list(range(600)), [i * 2 for i in range(600)])
    observations, distances, speeds = [], [], []
    for i, (_, lat, lon) in enumerate(points):
        absent = missing[0] <= i <= missing[1]
        observations.append(
            (i + (300 if i > stop else 0), None if absent else lat, None if absent else lon)
        )
        distances.append(i * 2.0)
        speeds.append(2.0)
        if records_in_pause and i == stop:
            for second in range(stop + 1, stop + 301):
                observations.append((second, None, None))
                distances.append(i * 2.0)
                speeds.append(0.0 if second < stop + 300 else 2.0)
    path = tmp_path / "paused.fit"
    write_trajectory_activity(
        path,
        observations,
        retain_invalid_position_fields=True,
        distances_m=distances if method == "distance" else None,
        speeds_mps=speeds if method != "time" else None,
        timer_events=[(stop, "stop_all"), (stop + 300, "start"), (899, "stop_all")],
    )
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(course_path, [[(lat, lon, None, None) for _, lat, lon in points]])
    return read_fit(path), read_gpx_course(course_path)


@pytest.mark.parametrize("shape", ["internal", "prefix", "suffix"])
@pytest.mark.parametrize("method", ["distance", "speed", "time"])
def test_paused_gaps_reconstruct_and_write_losslessly(tmp_path, shape, method):
    activity, course = _fixture(tmp_path, shape, method)
    raw = activity.preservation.raw_bytes
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    assert len(plan.interval_plans) == 1 and not plan.unresolved_gaps
    candidate = plan.interval_plans[0]
    provenance = candidate.provenance
    assert provenance.timing.paused_seconds == 300
    assert provenance.timing.active_seconds == provenance.timing.elapsed_seconds - 300
    assert provenance.timing.pause_count == 1
    assert not provenance.timing.open_pause
    assert (
        provenance.allocation_method
        is {
            "distance": AllocationMethod.RECORDED_DISTANCE,
            "speed": AllocationMethod.RECORDED_SPEED,
            "time": AllocationMethod.TIMESTAMPS,
        }[method]
    )
    if method == "time":
        assert "active_time_estimated" in provenance.signal_diagnostics
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert result.post_write_verified and result.validation.valid and result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.definitions_unchanged
    assert result.diff.timestamps.percentage == 100
    assert result.diff.sensors.percentage == 100
    fixed = read_fit(result.output_path)
    assert fixed.events == activity.events
    assert len(fixed.records) == len(activity.records)
    assert activity.preservation.source_path.read_bytes() == raw
    for old, new in zip(activity.records, fixed.records, strict=True):
        assert old.timestamp == new.timestamp
        if old.latitude is not None:
            assert (old.latitude, old.longitude) == (new.latitude, new.longitude)
    assert analyze_integrity(activity) == integrity


@pytest.mark.parametrize("method", ["distance", "speed", "time"])
def test_records_during_pause_get_one_position(tmp_path, method):
    activity, course = _fixture(tmp_path, method=method, records_in_pause=True)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert not plan.unresolved_gaps
    positions = {
        (u.candidate_latitude, u.candidate_longitude)
        for u in plan.interval_plans[0].coordinate_updates
        if 160 <= u.record_index <= 460
    }
    assert len(positions) == 1


def test_timer_union_groups_duplicates_and_boundaries():
    activity = make_activity(eastward_observations(list(range(101)), list(range(101))))
    activity = _events(
        activity,
        [
            (0, "start", 0),
            (0, "start", 1),
            (10, "stop", 0),
            (12, "stop", 0),
            (15, "stop", 1),
            (20, "start", 0),
            (21, "start", 0),
            (30, "start", 1),
            (40, "stop_all", 0),
            (50, "start", 0),
            (60, "start", 1),
            (100, "stop_all", 0),
        ],
    )
    pauses = timer_pauses(activity)
    clock = allocation_clock(activity.records, pauses)
    assert clock.audit.paused_seconds == 40  # [10,30] union [40,60]
    assert clock.audit.active_seconds == 60
    assert not clock.audit.open_pause  # stop exactly at final anchor
    assert clock.audit.pause_count == 2
    assert allocation_clock(activity.records[30:41], pauses).audit.paused_seconds == 0


def test_missing_group_in_single_timer_file_is_not_a_second_timer():
    activity = make_activity(eastward_observations([0, 10, 20, 30], [0, 10, 10, 20]))
    activity = _events(activity, [(0, "start", None), (10, "stop_all", 0), (20, "start", 0)])
    assert allocation_clock(activity.records, timer_pauses(activity)).audit.active_seconds == 20


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("open", Reason.TIMER_STATE_UNRESOLVED),
        ("whole", Reason.NO_ACTIVE_TIME),
        ("conflict", Reason.PAUSE_DISTANCE_CONFLICT),
    ],
)
def test_bad_timer_state_is_local_not_a_global_veto(tmp_path, mode, reason):
    from tests.local_reconstruction_factory import local_fixture

    activity, course = local_fixture(tmp_path)
    items = {
        "open": [(560, "stop_all", 0)],
        "whole": [(149, "stop_all", 0), (180, "start", 0)],
        "conflict": [(160, "stop_all", 0), (170, "start", 0)],
    }[mode]
    activity = _events(activity, [(0, "start", 0), *items])
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 2
    assert plan.unresolved_gaps[0].reasons == (reason,)
    audit = gap_audit(plan, select_repair_intervals(plan, IntegrityConfidence.MEDIUM))
    failed = next(g for g in audit["gap_inventory"] if g["status"] == "unresolved")
    assert failed["timing"]["paused_seconds"] > 0


def test_active_speed_cannot_be_hidden_by_long_pause(tmp_path):
    activity, course = _fixture(tmp_path)
    # Retain only 0.1 active seconds for a ~62 m candidate whose wall time is 331 s.
    first = activity.records[149].timestamp
    activity = replace(
        activity,
        events=tuple(
            SourceMessage(
                i,
                i,
                0,
                21,
                "event",
                i,
                {
                    "timestamp": first + timedelta(seconds=s),
                    "event": "timer",
                    "event_type": kind,
                    "event_group": 0,
                },
                b"",
            )
            for i, (s, kind) in enumerate([(0, "start"), (0.05, "stop_all"), (330.95, "start")])
        ),
    )
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons == (Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE,)


def test_speed_ignores_paused_samples_and_time_cannot_override_distance_conflict():
    activity = make_activity(eastward_observations([0, 1, 2, 11, 12], [0, 2, 2, 2, 4]))
    activity = _events(activity, [(1, "stop_all", 0), (11, "start", 0)])
    records = tuple(
        replace(r, distance=None, speed=None if r.index == 2 else 2.0) for r in activity.records
    )
    clock = allocation_clock(records, timer_pauses(activity))
    speed = qualify_speed(records, IntegrityConfig.running(), active_deltas=clock.active_deltas)
    assert speed.cumulative == (0, 2, 2, 2, 4)
    conflict = tuple(replace(r, distance=float(r.index * 2)) for r in records)
    assert (
        _fractions(
            conflict, 8, CourseReconstructionConfig(), IntegrityConfig.running(), clock=clock
        )
        is Reason.PAUSE_DISTANCE_CONFLICT
    )
    mismatch = tuple(replace(r, distance=0.0, speed=None) for r in records)
    assert (
        _fractions(
            mismatch, 4, CourseReconstructionConfig(), IntegrityConfig.running(), clock=clock
        )
        is Reason.LOCAL_DISTANCE_INCONSISTENT
    )


def test_no_pause_fractions_stay_identical():
    activity = make_activity(eastward_observations([0, 1, 2], [0, 2, 4]))
    records = tuple(replace(r, distance=float(r.index * 2), speed=2.0) for r in activity.records)
    config, integrity = CourseReconstructionConfig(), IntegrityConfig.running()
    assert _fractions(records, 4, config, integrity) == _fractions(
        records, 4, config, integrity, clock=allocation_clock(records, ())
    )


def test_fractional_timestamps_stay_exactly_stationary_inside_pause():
    activity = make_activity(
        eastward_observations([0, 0.1, 0.15, 0.21, 0.3, 0.4], [0, 1, 1, 1, 1, 2])
    )
    activity = _events(activity, [(0.1, "stop_all", 0), (0.3, "start", 0)])
    clock = activity_clock(activity, activity.records)
    assert clock.active_cumulative[1:5] == (0.1, 0.1, 0.1, 0.1)
    assert clock.active_deltas[1:4] == (0, 0, 0)


def test_unclosed_group_does_not_poison_an_earlier_window():
    activity = make_activity(eastward_observations(list(range(31)), list(range(31))))
    activity = _events(
        activity,
        [(0, "start", 0), (0, "start", 1), (10, "stop", 0), (20, "start", 0), (15, "stop", 1)],
    )
    earlier = activity_clock(activity, activity.records[5:15])
    later = activity_clock(activity, activity.records[16:25])
    assert earlier.audit.paused_seconds == 4 and not earlier.audit.open_pause
    assert later.audit.open_pause


def test_local_active_speed_is_checked_even_when_total_speed_is_plausible(tmp_path):
    activity, course = _fixture(tmp_path)
    records = tuple(
        replace(r, distance=320.0) if 151 <= r.index <= 160 else r for r in activity.records
    )
    activity = replace(activity, records=records)
    config = replace(CourseReconstructionConfig(), missing_completion_max_connector_speed_mps=10.0)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, config, fill_missing_from_course=True
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons == (Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE,)


def test_html_and_json_include_timing_and_allocation(tmp_path, capsys):
    activity, course = _fixture(tmp_path)
    output = tmp_path / "repair.html"
    assert (
        main(
            [
                "repair",
                str(activity.preservation.source_path),
                "--course",
                str(course.source_path),
                "--fill-missing-from-course",
                "--min-confidence",
                "medium",
                "--html",
                str(output),
            ]
        )
        == 0
    )
    assert "timing=" in capsys.readouterr().out
    html = output.read_text()
    payload = json.loads(
        html.split('<script id="warpbuster-report-data" type="application/json">', 1)[1].split(
            "</script>", 1
        )[0]
    )
    gap = payload["repair"]["gap_inventory"][0]
    assert gap["timing"]["paused_seconds"] == 300
    assert gap["provenance"]["allocation_method"] == "recorded_distance"
    assert "Elapsed / paused / active" in html


def test_active_clock_is_bounded_for_long_record_stream():
    activity = make_activity(eastward_observations(list(range(20000)), list(range(20000))))
    first = activity.records[0].timestamp
    pauses = tuple(
        (first + timedelta(seconds=i), first + timedelta(seconds=i + 1)) for i in range(0, 19999, 2)
    )
    start = perf_counter()
    clock = allocation_clock(activity.records, pauses)
    assert perf_counter() - start < 1
    assert clock.audit.paused_seconds == 10000
