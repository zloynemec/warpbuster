"""Direction, ambiguity, local search bounds and qualified signal regressions."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.gpx_factory import write_gpx_activity
from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import SourceMessage
from warpbuster.models.reconstruction import CourseDirection, GapRepairPlan
from warpbuster.reconstruction import build_repair_plan, local


def test_reverse_course_changes_direction_not_coordinate_mask(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path)
    points = [(p.latitude, p.longitude, None, None) for p in course.segments[0].points]
    path = tmp_path / "reverse.gpx"
    write_gpx_activity(path, [list(reversed(points))])
    integrity = analyze_integrity(activity)
    forward = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    reverse = build_repair_plan(
        activity, integrity, read_gpx_course(path), fill_missing_from_course=True
    )
    assert forward.coordinate_mask == reverse.coordinate_mask
    assert len(reverse.interval_plans) == 3
    for candidate in reverse.interval_plans:
        assert isinstance(candidate, GapRepairPlan) and candidate.provenance is not None
        assert candidate.provenance.direction is CourseDirection.REVERSE
        for update in candidate.coordinate_updates:
            p = course.segments[0].points[update.record_index]
            assert update.candidate_latitude == pytest.approx(p.latitude, abs=1e-6)
            assert update.candidate_longitude == pytest.approx(p.longitude, abs=1e-6)


@pytest.mark.parametrize("shape", ["duplicate_segments", "repeated_loop", "segment_break"])
def test_ambiguous_paths_and_course_breaks_remain_local_failures(
    tmp_path: Path, shape: str
) -> None:
    activity, course = local_fixture(
        tmp_path, missing=((150, 279 if shape == "segment_break" else 179),)
    )
    points = [(p.latitude, p.longitude, None, None) for p in course.segments[0].points]
    segments = (
        [points, points]
        if shape == "duplicate_segments"
        else [points + list(reversed(points)) + points]
        if shape == "repeated_loop"
        else [points[:215], points[215:]]
    )
    path = tmp_path / "branches.gpx"
    write_gpx_activity(path, segments)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), read_gpx_course(path), fill_missing_from_course=True
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == (
        "local_course_match_not_found"
        if shape == "segment_break"
        else "local_course_match_ambiguous"
    )


@pytest.mark.parametrize(
    "bound", ["maximum_anchor_candidates", "local_alignment_max_path_evaluations"]
)
def test_truncated_branch_search_never_claims_uniqueness(tmp_path: Path, bound: str) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    points = [(p.latitude, p.longitude, None, None) for p in course.segments[0].points]
    path = tmp_path / "duplicates.gpx"
    write_gpx_activity(path, [points, points])
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(path),
        CourseReconstructionConfig(**{bound: 1}),
        fill_missing_from_course=True,
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "search_limit_reached"


@pytest.mark.parametrize(
    "records, seconds, succeeds", [(29, 300, False), (30, 28, False), (30, 29, True)]
)
def test_endpoint_context_caps_include_exact_boundary(
    tmp_path: Path,
    records: int,
    seconds: float,
    succeeds: bool,
) -> None:
    activity, course = local_fixture(tmp_path, missing=((0, 19),))
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        CourseReconstructionConfig(
            local_alignment_max_context_records=records, local_alignment_max_context_seconds=seconds
        ),
        fill_missing_from_course=True,
    )
    assert bool(plan.interval_plans) is succeeds
    if not succeeds:
        assert plan.unresolved_gaps[0].reasons[0].value == "no_trusted_local_anchor"


@pytest.mark.parametrize("signal", ["zero", "reset", "nan", "unavailable"])
def test_unusable_distance_falls_back_to_qualified_speed(tmp_path: Path, signal: str) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    records = list(activity.records)
    for i in range(150, 180):
        distance = 0.0 if signal in {"zero", "reset"} else float("nan") if signal == "nan" else None
        records[i] = replace(records[i], distance=distance)
    if signal == "zero":
        records[149] = replace(records[149], distance=0.0)
        records[180] = replace(records[180], distance=0.0)
    changed = replace(activity, records=tuple(records))
    plan = build_repair_plan(
        changed, analyze_integrity(changed), course, fill_missing_from_course=True
    )
    candidate = plan.interval_plans[0]
    assert isinstance(candidate, GapRepairPlan) and candidate.provenance is not None
    assert candidate.provenance.allocation_method.value == "recorded_speed"
    assert candidate.provenance.signal_diagnostics
    assert candidate.provenance.signal_quality == "source_unverified"


@pytest.mark.parametrize("bad_time", ["missing", "duplicate", "backwards"])
def test_missing_or_non_monotonic_time_never_uses_record_order(
    tmp_path: Path, bad_time: str
) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    records = list(activity.records)
    records[160] = replace(
        records[160],
        timestamp=(
            None
            if bad_time == "missing"
            else records[159 if bad_time == "duplicate" else 158].timestamp
        ),
    )
    changed = replace(activity, records=tuple(records))
    plan = build_repair_plan(
        changed, analyze_integrity(changed), course, fill_missing_from_course=True
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "timing_unusable"


def test_no_observed_anchors_remains_visible_and_unresolved(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((0, 599),))
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.gaps) == len(plan.unresolved_gaps) == 1
    assert plan.unresolved_gaps[0].reasons[0].value == "no_trusted_local_anchor"


def test_prefix_cannot_use_an_intermediate_segment_as_course_start(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((0, 19),))
    points = [(p.latitude, p.longitude, None, None) for p in course.segments[0].points]
    path = tmp_path / "disconnected-start.gpx"
    write_gpx_activity(path, [[(56.0, 37.0, None, None), (56.0, 37.01, None, None)], points])
    plan = build_repair_plan(
        activity, analyze_integrity(activity), read_gpx_course(path), fill_missing_from_course=True
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "local_course_match_not_found"


def test_timestamp_allocation_is_explicitly_estimated_and_record_cap_is_audited(
    tmp_path: Path,
) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    activity = replace(
        activity,
        records=tuple(
            replace(r, distance=None, speed=None) if 149 <= r.index <= 180 else r
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    provenance = plan.interval_plans[0].provenance
    assert provenance.allocation_method.value == "timestamps"
    assert provenance.signal_quality == "estimated"
    assert provenance.signal_diagnostics == ("distance_unavailable", "speed_unavailable")
    limited = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        CourseReconstructionConfig(missing_completion_max_run_records=29),
        fill_missing_from_course=True,
    )
    assert limited.unresolved_gaps[0].reasons[0].value == "search_limit_reached"


def test_gap_planning_order_does_not_create_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activity, course = local_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    original = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    inventory = local.inventory_gaps
    monkeypatch.setattr(local, "inventory_gaps", lambda a, m: tuple(reversed(inventory(a, m))))
    reordered = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    assert sorted(original.interval_plans, key=lambda p: p.interval.gap_id) == sorted(
        reordered.interval_plans, key=lambda p: p.interval.gap_id
    )


def test_projection_error_budget_keeps_short_lateral_context_usable(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=())
    # 30 m recorded movement but 15 m projected progression, with measured
    # 20 m and 5 m lateral errors: strict relative-only matching loses the branch.
    base = activity.records[100]
    assert base.latitude is not None and base.longitude is not None
    context = tuple(
        replace(
            activity.records[100 + i],
            latitude=base.latitude + (20 - i) / 111195,
            longitude=base.longitude + i * (activity.records[101].longitude - base.longitude) / 2,
            distance=float(i * 2),
        )
        for i in range(16)
    )
    config = CourseReconstructionConfig()
    options, _reason = local._options(context, 1, local._CourseIndex(course, config), config)
    assert any(o.direction is CourseDirection.FORWARD for o in options)


@pytest.mark.parametrize("stop_kind", ["stop", "stop_all", "stop_disable", "stop_disable_all"])
def test_distance_movement_during_timer_pause_blocks_only_affected_gap(
    tmp_path: Path, stop_kind: str
) -> None:
    activity, course = local_fixture(tmp_path)
    events = tuple(
        SourceMessage(
            index,
            index,
            0,
            21,
            "event",
            index,
            {
                "event": "timer",
                "event_type": kind,
                "event_group": 0,
                "timestamp": activity.records[record].timestamp,
            },
            b"",
        )
        for index, (record, kind) in enumerate([(0, "start"), (160, stop_kind), (170, "start")])
    )
    activity = replace(activity, events=events)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 2
    assert plan.unresolved_gaps[0].interval.gap_id == "gap-150-179"
    assert [r.value for r in plan.unresolved_gaps[0].reasons] == ["pause_distance_conflict"]
