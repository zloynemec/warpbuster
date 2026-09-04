"""Optional private regression: exact proof scopes supersede old corridor goldens."""

from pathlib import Path

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import CoordinateState, GapRepairPlan
from warpbuster.reconstruction import build_repair_plan, select_repair_intervals
from warpbuster.report.fit import write_result_report

_ORIGINAL = Path("tests/private/tracks/Andromeda_Taras.fit")
_COURSE = Path("tests/private/tracks/Andromeda_2026.gpx")
pytestmark = [
    pytest.mark.private,
    pytest.mark.skipif(
        not (_ORIGINAL.exists() and _COURSE.exists()),
        reason="private original/course fixtures are unavailable",
    ),
]


def test_private_one_sided_proof_does_not_authorize_course_corridor_expansion() -> None:
    """The detector proof remains valid, but unrelated drift is no longer overwritten."""
    activity = read_fit(_ORIGINAL)
    integrity = analyze_integrity(activity)
    interval = next(i for i in integrity.corrupted_intervals if i.start_record_index == 3627)
    assert interval.end_record_index == 3700
    assert interval.confidence is IntegrityConfidence.MEDIUM
    cluster = next(
        c
        for c in integrity.one_sided_search_diagnostics.retained_clusters
        if c.start_record_index == 3627
    )
    assert cluster.reconstructable and cluster.tainted_positioned_component_count == 2
    assert cluster.bridge is not None
    assert cluster.bridge.apparent_speed_mps == pytest.approx(2.496, abs=0.01)
    course = read_gpx_course(_COURSE)
    default = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    opted_in = build_repair_plan(
        activity,
        integrity,
        course,
        fill_missing_from_course=True,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    assert default.coordinate_mask[3627].state is CoordinateState.PRESERVED
    assert opted_in.coordinate_mask[3627].state is CoordinateState.INVALIDATED
    assert all(
        item.state is CoordinateState.PRESERVED for item in opted_in.coordinate_mask[3582:3627]
    )
    assert all(
        item.state is CoordinateState.PRESERVED for item in opted_in.coordinate_mask[3701:3742]
    )
    gap = next(g for g in opted_in.gaps if g.start_record_index == 3627)
    assert gap.end_record_index == 3700
    # Immediate anchors cannot safely match. The old output expanded to 3582..3741
    # using GPX alone; that golden violates Task 011's immutable edit mask.
    assert gap in [g.interval for g in opted_in.unresolved_gaps]


def test_private_mixed_main_path_requires_missing_opt_in_and_medium(tmp_path: Path) -> None:
    """Mixed originally missing data caps path confidence independently of HIGH proof."""
    activity = read_fit(_ORIGINAL)
    integrity = analyze_integrity(activity)
    course = read_gpx_course(_COURSE)
    disabled = build_repair_plan(activity, integrity, course)
    assert not disabled.interval_plans
    plan = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    main = next(c for c in plan.interval_plans if c.interval.start_record_index == 1794)
    assert isinstance(main, GapRepairPlan)
    assert main.interval.end_record_index == 3254
    assert main.interval.original_missing_count > 0 and main.interval.invalidated_count > 0
    assert main.confidence is IntegrityConfidence.MEDIUM
    assert main.interval.invalidation_confidence is IntegrityConfidence.HIGH
    assert not select_repair_intervals(plan).selected_interval_plans
    assert select_repair_intervals(plan).invalidations
    result = write_repaired_fit(
        activity,
        plan,
        tmp_path / "main.fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert main in result.selection.selected_interval_plans
    fixed = read_fit(result.output_path)
    for update in main.coordinate_updates:
        record = fixed.records[update.record_index]
        assert record.latitude == pytest.approx(update.candidate_latitude, abs=1e-7)
        assert record.longitude == pytest.approx(update.candidate_longitude, abs=1e-7)
        assert record.timestamp == update.timestamp
    assert result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0


def test_private_partial_output_corrects_proven_distance_and_preserves_unproven_geometry(
    tmp_path: Path,
) -> None:
    """Correct only reconstructed spans; keep increments in unresolved spans."""
    activity = read_fit(_ORIGINAL)
    source_bytes = _ORIGINAL.read_bytes()
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(_COURSE),
        fill_missing_from_course=True,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    result = write_repaired_fit(
        activity,
        plan,
        tmp_path / "partial.fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    fixed = read_fit(result.output_path)
    assert result.selection.applied_interval_count >= 1
    assert result.selection.unresolved_invalidated_indices
    assert write_result_report(result)["distance"]["quality"] == "uncertain"
    assert result.distance_field_change_count > 0 and result.summary_field_change_count > 0
    assert fixed.recorded_distance_m < activity.recorded_distance_m
    assert result.post_write_verified
    corrected = {
        u.record_index
        for candidate in result.selection.selected_interval_plans
        if not candidate.preserve_recorded_distance
        for u in candidate.coordinate_updates
    }
    for index in range(1, len(fixed.records)):
        if index in corrected or index - 1 in corrected:
            continue
        assert fixed.records[index].distance - fixed.records[index - 1].distance == pytest.approx(
            activity.records[index].distance - activity.records[index - 1].distance, abs=0.02
        )
    assert [r.timestamp for r in fixed.records] == [r.timestamp for r in activity.records]
    for item in plan.coordinate_mask:
        if item.state is CoordinateState.PRESERVED:
            original = activity.records[item.record_index]
            cleaned = fixed.records[item.record_index]
            assert (original.latitude, original.longitude) == (cleaned.latitude, cleaned.longitude)
    for index in result.selection.unresolved_invalidated_indices:
        assert fixed.records[index].latitude is None and fixed.records[index].longitude is None
    assert result.diff.timestamps.percentage == 100
    assert result.diff.sensors.percentage == 100
    assert result.diff.developer_fields.percentage == 100
    assert result.diff.unknown_fields.percentage == 100
    assert result.diff.unexpected_changed_field_count == 0
    assert _ORIGINAL.read_bytes() == source_bytes
