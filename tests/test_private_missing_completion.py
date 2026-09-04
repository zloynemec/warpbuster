"""Optional Task 006D acceptance against the ignored private M87 activity."""

from pathlib import Path

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, TransitionClassification
from warpbuster.models.reconstruction import (
    AllocationMethod,
    GapRepairPlan,
    MissingCourseRunKind,
)
from warpbuster.reconstruction import (
    build_course_repair_plan,
    build_missing_course_plan,
    merge_repair_plans,
)

_ORIGINAL = Path("tests/private/tracks/m87_home_run.fit")
_COURSE = Path("tests/private/tracks/m87_home_run.gpx")


@pytest.mark.private
@pytest.mark.skipif(
    not (_ORIGINAL.exists() and _COURSE.exists()),
    reason="private M87 original/course fixtures are unavailable",
)
def test_private_m87_missing_endpoints_are_completed_without_distance_changes(
    tmp_path: Path,
) -> None:
    activity = read_fit(_ORIGINAL)
    integrity = analyze_integrity(activity)
    course = read_gpx_course(_COURSE)
    plan = merge_repair_plans(
        build_course_repair_plan(activity, integrity, course),
        build_missing_course_plan(activity, integrity, course),
    )

    assert integrity.corrupted_intervals == ()
    assert len(plan.interval_plans) == 2
    prefix, suffix = plan.interval_plans
    assert isinstance(prefix, GapRepairPlan)
    assert isinstance(suffix, GapRepairPlan)
    assert prefix.interval.kind is MissingCourseRunKind.PREFIX
    assert suffix.interval.kind is MissingCourseRunKind.SUFFIX
    assert (
        prefix.interval.start_record_index,
        prefix.interval.end_record_index,
        len(prefix.coordinate_updates),
    ) == (0, 2_757, 2_758)
    assert (
        suffix.interval.start_record_index,
        suffix.interval.end_record_index,
        len(suffix.coordinate_updates),
    ) == (4_821, 4_932, 112)
    assert prefix.provenance.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert suffix.provenance.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert prefix.provenance.direction.value == "forward"
    assert prefix.provenance.endpoint_source == "course_assumption"
    assert prefix.provenance.course_span_distance_m == pytest.approx(6_521.89, abs=0.2)
    assert suffix.provenance.course_span_distance_m == pytest.approx(265.58, abs=0.2)

    output = tmp_path / "m87.fixed.fit"
    result = write_repaired_fit(
        activity,
        plan,
        output,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    repaired = read_fit(output)
    repaired_integrity = analyze_integrity(repaired)

    assert all(
        record.latitude is not None and record.longitude is not None for record in repaired.records
    )
    assert repaired.recorded_distance_m == activity.recorded_distance_m
    assert tuple(record.distance for record in repaired.records) == tuple(
        record.distance for record in activity.records
    )
    assert result.distance_field_change_count == 0
    assert result.summary_field_change_count == 0
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == 100.0
    assert result.diff.sensors.percentage == 100.0
    assert result.diff.developer_fields.percentage == 100.0
    assert result.diff.unknown_fields.percentage == 100.0
    assert all(
        transition.classification is TransitionClassification.NORMAL
        for transition in repaired_integrity.transitions
    )
