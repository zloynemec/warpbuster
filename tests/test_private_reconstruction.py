"""Optional Task 006 acceptance against ignored private Andromeda data."""

from pathlib import Path
from statistics import median
from typing import cast

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.geo import geodesic_distance_m
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntervalDetectionKind,
    TransitionClassification,
)
from warpbuster.models.reconstruction import (
    AllocationMethod,
    GnssComponentState,
    RepairIntervalAction,
    RepairPlanStatus,
)
from warpbuster.reconstruction import build_course_repair_plan, select_repair_intervals
from warpbuster.report.fit import write_result_report

_ORIGINAL = Path("tests/private/tracks/Andromeda_Taras.fit")
_REFERENCE_FIXED = Path("tests/private/tracks/Andromeda_Taras_FIXED.fit")
_COURSE = Path("tests/private/tracks/Andromeda_2026.gpx")
_PRIVATE_FILES = (_ORIGINAL, _REFERENCE_FIXED, _COURSE)


@pytest.mark.private
@pytest.mark.skipif(
    not (_ORIGINAL.exists() and _COURSE.exists()),
    reason="private Andromeda original/course fixtures are unavailable",
)
def test_private_andromeda_one_sided_cluster_is_explicit_medium_candidate() -> None:
    """Task 006B proves the residual cluster and keeps application explicitly opt-in."""
    activity = read_fit(_ORIGINAL)
    integrity = analyze_integrity(activity)

    interval = next(
        interval
        for interval in integrity.corrupted_intervals
        if interval.start_record_index == 3_627
    )
    assert interval.end_record_index == 3_700
    assert interval.confidence is IntegrityConfidence.MEDIUM
    cluster = next(
        cluster
        for cluster in integrity.one_sided_search_diagnostics.retained_clusters
        if cluster.start_record_index == 3_627
    )
    assert cluster.reconstructable is True
    assert cluster.positioned_component_count == 2
    assert cluster.tainted_positioned_component_count == 2
    assert cluster.bridge is not None
    assert cluster.bridge.apparent_speed_mps == pytest.approx(2.496, abs=0.01)

    plan = build_course_repair_plan(activity, integrity, read_gpx_course(_COURSE))
    candidate = next(
        candidate
        for candidate in plan.interval_plans
        if candidate.boundary_refinement is not None
        and candidate.boundary_refinement.detected_start_record_index == 3_627
    )
    assert (candidate.interval.start_record_index, candidate.interval.end_record_index) == (
        3_582,
        3_741,
    )
    assert (
        candidate.interval.trusted_before_record_index,
        candidate.interval.trusted_after_record_index,
    ) == (3_581, 3_742)
    assert candidate.confidence is IntegrityConfidence.MEDIUM
    assert candidate.repair_eligible is False
    assert candidate.anchor_before.anchor_distance_m == pytest.approx(12.98, abs=0.1)
    assert candidate.anchor_after.anchor_distance_m == pytest.approx(14.91, abs=0.1)
    assert candidate.anchor_connector_distance_m == pytest.approx(27.89, abs=0.2)
    assert candidate.reconstruction_path_distance_m == pytest.approx(235.73, abs=0.3)
    assert select_repair_intervals(plan).decisions[1].action is RepairIntervalAction.SKIPPED
    assert (
        select_repair_intervals(plan, IntegrityConfidence.MEDIUM).decisions[1].action
        is RepairIntervalAction.APPLIED
    )


@pytest.mark.private
@pytest.mark.skipif(
    not all(path.exists() for path in _PRIVATE_FILES),
    reason="private Andromeda original/fixed/course fixtures are unavailable",
)
def test_private_andromeda_main_and_composite_candidates_preserve_default_safety(
    tmp_path: Path,
) -> None:
    """The wider composite is auditable and remains skipped by default HIGH policy."""
    activity = read_fit(_ORIGINAL)
    reference = read_fit(_REFERENCE_FIXED)
    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(_COURSE),
    )

    main = max(plan.interval_plans, key=lambda candidate: candidate.interval.record_count)
    assert plan.status is RepairPlanStatus.PARTIAL
    assert main.confidence is IntegrityConfidence.HIGH
    assert main.repair_eligible is True
    assert main.interval.start_record_index == 1_794
    assert main.interval.end_record_index == 3_254
    assert len(main.coordinate_updates) == main.interval.record_count
    assert all(
        update.timestamp == activity.records[update.record_index].timestamp
        for update in main.coordinate_updates
    )
    assert main.anchor_before_stability.stable is True
    assert main.anchor_after_stability.stable is True

    residual = next(
        candidate
        for candidate in plan.interval_plans
        if candidate.boundary_refinement is not None
        and candidate.boundary_refinement.detected_start_record_index == 3_627
    )
    assert residual.confidence is IntegrityConfidence.MEDIUM
    assert residual.repair_eligible is False

    composite = next(
        candidate
        for candidate in plan.interval_plans
        if candidate.interval.detection_kind is IntervalDetectionKind.COMPOSITE_REGION
    )
    assert (composite.interval.start_record_index, composite.interval.end_record_index) == (
        8_820,
        9_580,
    )
    assert composite.interval.trusted_before_record_index == 8_819
    assert composite.interval.trusted_after_record_index == 9_581
    assert composite.confidence is IntegrityConfidence.MEDIUM
    assert composite.repair_eligible is False
    assert composite.allocation_method is AllocationMethod.TIMESTAMPS
    assert len(composite.coordinate_updates) == 761
    region = composite.composite_region
    assert region is not None
    assert (region.start_record_index, region.end_record_index) == (8_820, 9_580)
    assert (
        region.proposed_trusted_before_record_index,
        region.proposed_trusted_after_record_index,
    ) == (8_819, 9_581)
    assert region.outer_anchor_before is not None and region.outer_anchor_before.stable
    assert region.outer_anchor_after is not None and region.outer_anchor_after.stable
    assert region.bridge_plausible is True
    assert region.bridge_speed_mps == pytest.approx(1.3046, abs=0.001)
    assert region.confidence is IntegrityConfidence.MEDIUM
    assert region.repair_eligible is False
    assert region.reconstructable is True
    assert [component.state for component in region.components] == [
        GnssComponentState.TAINTED,
        GnssComponentState.MISSING,
        GnssComponentState.TAINTED,
        GnssComponentState.MISSING,
    ]
    output_path = tmp_path / "andromeda.fixed.fit"
    result = write_repaired_fit(activity, plan, output_path)
    fixed = read_fit(output_path)

    assert output_path.exists()
    assert result.selection.minimum_confidence is IntegrityConfidence.HIGH
    assert result.selection.is_partial is True
    assert result.selection.applied_interval_count == 1
    assert result.selection.skipped_interval_count == 2
    assert [decision.action for decision in result.selection.decisions] == [
        RepairIntervalAction.APPLIED,
        RepairIntervalAction.SKIPPED,
        RepairIntervalAction.SKIPPED,
    ]
    report = write_result_report(result)
    selection_report = cast(dict[str, object], report["selection"])
    interval_reports = cast(list[dict[str, object]], selection_report["intervals"])
    assert [item["action"] for item in interval_reports] == [
        "applied",
        "skipped",
        "skipped",
    ]
    assert result.validation.valid is True
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == 100.0
    assert result.diff.sensors.percentage == 100.0
    assert result.diff.developer_fields.percentage == 100.0
    assert result.diff.unknown_fields.percentage == 100.0
    assert activity.records[-1].distance is not None
    assert fixed.records[-1].distance is not None
    assert activity.records[-1].distance - fixed.records[-1].distance > 80_000.0
    assert fixed.records[-1].distance == pytest.approx(34_431.02, abs=0.05)
    assert fixed.recorded_distance_m == pytest.approx(34_431.02, abs=0.05)
    assert fixed.sessions[0].fields["total_distance"] == pytest.approx(34_431.02, abs=0.05)
    assert [lap.fields["total_distance"] for lap in fixed.laps] == pytest.approx(
        [9_121.68, 13_557.62, 11_751.72],
        abs=0.05,
    )
    assert tuple(record.timestamp for record in fixed.records) == tuple(
        record.timestamp for record in activity.records
    )
    assert all(
        (fixed_record.latitude, fixed_record.longitude)
        == (original_record.latitude, original_record.longitude)
        for original_record, fixed_record in zip(activity.records, fixed.records, strict=True)
        if not (
            main.interval.start_record_index
            <= original_record.index
            <= main.interval.end_record_index
        )
    )
    assert all(
        fixed.records[update.record_index].latitude
        == pytest.approx(update.candidate_latitude, abs=1e-7)
        and fixed.records[update.record_index].longitude
        == pytest.approx(update.candidate_longitude, abs=1e-7)
        for update in main.coordinate_updates
    )

    deviations = [
        geodesic_distance_m(
            update.candidate_latitude,
            update.candidate_longitude,
            reference_record.latitude,
            reference_record.longitude,
        )
        for update in main.coordinate_updates
        if (reference_record := reference.records[update.record_index]).latitude is not None
        and reference_record.longitude is not None
    ]
    assert median(deviations) < 20.0
    assert max(deviations) < 40.0


@pytest.mark.private
@pytest.mark.skipif(
    not (_ORIGINAL.exists() and _COURSE.exists()),
    reason="private Andromeda original/course fixtures are unavailable",
)
def test_private_andromeda_explicit_medium_writes_composite_region(tmp_path: Path) -> None:
    """Explicit MEDIUM fills both composite dropouts and passes FIT/post-transition checks."""
    activity = read_fit(_ORIGINAL)
    plan = build_course_repair_plan(
        activity,
        analyze_integrity(activity),
        read_gpx_course(_COURSE),
    )
    output_path = tmp_path / "andromeda-medium.fixed.fit"

    result = write_repaired_fit(
        activity,
        plan,
        output_path,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    fixed = read_fit(output_path)
    fixed_integrity = analyze_integrity(fixed)

    assert result.selection.applied_interval_count == 3
    assert result.selection.skipped_interval_count == 0
    assert result.validation.valid is True
    assert result.diff.unexpected_changed_field_count == 0
    assert all(
        fixed.records[index].latitude is not None and fixed.records[index].longitude is not None
        for index in range(8_820, 9_581)
    )
    assert not any(
        transition.classification is not TransitionClassification.NORMAL
        for transition in fixed_integrity.transitions
        if transition.to_record_index >= 8_819 and transition.from_record_index <= 9_581
    )
    assert tuple(record.timestamp for record in fixed.records) == tuple(
        record.timestamp for record in activity.records
    )
    assert fixed.recorded_distance_m == pytest.approx(33_495.62, abs=0.05)
