"""Synthetic local matching, nearest-anchor safety and course assumption tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import GapRepairPlan, ReconstructionReason
from warpbuster.reconstruction import build_repair_plan, select_repair_intervals


def test_all_gap_kinds_use_actual_neighbors_with_one_opt_in(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert not plan.unresolved_gaps
    assert len(plan.interval_plans) == 3
    assert plan.detected_interval_count == 0
    prefix, internal, suffix = plan.interval_plans
    assert isinstance(prefix, GapRepairPlan)
    assert isinstance(internal, GapRepairPlan)
    assert isinstance(suffix, GapRepairPlan)
    assert prefix.interval.anchor_after_record_index == 20
    assert internal.interval.anchor_before_record_index == 149
    assert internal.interval.anchor_after_record_index == 180
    assert suffix.interval.anchor_before_record_index == 559
    assert prefix.provenance is not None
    assert prefix.provenance.endpoint_source == "course_assumption"
    assert prefix.coordinate_updates[0].candidate_latitude == pytest.approx(
        course.segments[0].points[0].latitude
    )
    assert all(item.confidence is IntegrityConfidence.MEDIUM for item in plan.interval_plans)
    assert not select_repair_intervals(plan).has_changes
    assert (
        len(select_repair_intervals(plan, IntegrityConfidence.MEDIUM).selected_interval_plans) == 3
    )


def test_distant_detour_and_cumulative_offset_cannot_veto_prefix(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, count=1000, missing=((0, 19),))
    original = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    # Far outside the prefix context: gradual, physically possible detour.
    changed = replace(
        activity,
        records=tuple(
            replace(
                r,
                latitude=r.latitude + max(0, 100 - abs(r.index - 750)) * 0.000005
                if r.latitude is not None
                else None,
                distance=(r.distance or 0) + max(0, r.index - 600) * 0.1,
            )
            for r in activity.records
        ),
    )
    detour = build_repair_plan(
        changed, analyze_integrity(changed), course, fill_missing_from_course=True
    )
    assert original.interval_plans
    assert original.interval_plans == detour.interval_plans
    offset = replace(
        activity,
        records=tuple(replace(r, distance=(r.distance or 0) + 1200) for r in activity.records),
    )
    assert (
        build_repair_plan(
            offset, analyze_integrity(offset), course, fill_missing_from_course=True
        ).interval_plans
        == original.interval_plans
    )


def test_endpoint_failures_are_local(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((0, 19), (560, 599)))
    changed = replace(
        activity,
        records=tuple(
            replace(
                r,
                latitude=(r.latitude + 0.003)
                if r.latitude is not None and r.index > 400
                else r.latitude,
            )
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        changed, analyze_integrity(changed), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 1
    assert plan.interval_plans[0].interval.start_record_index == 0
    assert plan.unresolved_gaps[0].interval.start_record_index == 560


def test_inventory_does_not_require_course_or_completion_flag(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    no_course = build_repair_plan(activity, integrity, fill_missing_from_course=True)
    disabled = build_repair_plan(activity, integrity, course)
    assert no_course.gaps == disabled.gaps
    assert len(no_course.gaps) == 3
    assert all(g.reasons == (ReconstructionReason.NO_COURSE,) for g in no_course.unresolved_gaps)
    assert all(
        g.reasons == (ReconstructionReason.MISSING_COMPLETION_DISABLED,)
        for g in disabled.unresolved_gaps
    )


def test_impossible_distance_in_original_missing_does_not_veto_qualified_speed(
    tmp_path: Path,
) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    changed = replace(
        activity,
        records=tuple(
            replace(r, distance=(r.distance or 0) + (1000 if r.index >= 180 else 0))
            for r in activity.records
        ),
    )
    plan = build_repair_plan(
        changed, analyze_integrity(changed), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    assert candidate.provenance.allocation_method.value == "recorded_speed"
    assert candidate.provenance.distance_signal_status == "implausible"
    assert not candidate.preserve_recorded_distance


@pytest.mark.parametrize("limit", [1, 2])
def test_gap_limits_keep_complete_inventory(tmp_path: Path, limit: int) -> None:
    activity, course = local_fixture(tmp_path)
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        CourseReconstructionConfig(maximum_reconstruction_intervals=limit),
        fill_missing_from_course=True,
    )
    assert len(plan.gaps) == 3
    assert len(plan.interval_plans) == limit
    assert all(
        g.reasons == (ReconstructionReason.SEARCH_LIMIT_REACHED,) for g in plan.unresolved_gaps
    )
