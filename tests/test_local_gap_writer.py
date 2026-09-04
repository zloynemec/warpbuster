"""End-to-end local reconstruction and independent raw FIT invalidation."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.local_reconstruction_factory import local_fixture
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import CoordinateState
from warpbuster.reconstruction import build_repair_plan
from warpbuster.report.fit import write_result_report


def test_invalidation_without_course_preserves_every_non_position_field(tmp_path: Path) -> None:
    activity, _course = local_fixture(tmp_path, missing=(), spikes=(200,))
    original_bytes = activity.preservation.raw_bytes
    plan = build_repair_plan(activity, analyze_integrity(activity))
    assert plan.coordinate_mask[200].state is CoordinateState.INVALIDATED
    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)
    assert fixed.records[200].latitude is None and fixed.records[200].longitude is None
    assert not result.selection.selected_interval_plans
    assert result.coordinate_field_change_count == 2
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    assert result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.diff.unknown_fields.percentage == result.diff.developer_fields.percentage == 100
    for a, b in zip(activity.records, fixed.records, strict=True):
        assert (a.distance, a.speed, a.timestamp, a.altitude) == (
            b.distance,
            b.speed,
            b.timestamp,
            b.altitude,
        )
        if a.index != 200:
            assert (a.latitude, a.longitude) == (b.latitude, b.longitude)
    report = write_result_report(result)
    assert report["coordinate_coverage"]["invalidated"] == 1
    assert report["coordinate_coverage"]["unresolved"] == 1
    assert report["distance"]["quality"] == "uncertain"
    assert report["gap_inventory"][0]["invalidation_action"] == "applied"
    assert activity.preservation.source_path.read_bytes() == original_bytes


@pytest.mark.parametrize("count", [500, 800])
def test_all_gap_kinds_and_mixed_corruption_write_losslessly(tmp_path: Path, count: int) -> None:
    activity, course = local_fixture(
        tmp_path,
        count=count,
        missing=((0, 19), (151, 175), (300, 320), (count - 20, count - 1)),
        spikes=(150,),
        detour=(350, 450, 80.0),
    )
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.gaps) == len(plan.interval_plans) == 4
    assert plan.gaps[1].origin.value == "mixed"
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert all(r.latitude is not None and r.longitude is not None for r in fixed.records)
    assert not analyze_integrity(fixed).corrupted_intervals
    assert result.validation.crc_valid and result.diff.unexpected_changed_field_count == 0
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    assert [r.distance for r in fixed.records] == [r.distance for r in activity.records]
    assert fixed.records[-1].distance > course.total_distance_m + 25
    assert [r.timestamp for r in fixed.records] == [r.timestamp for r in activity.records]
    for item in plan.coordinate_mask:
        if item.state is CoordinateState.PRESERVED:
            a, b = activity.records[item.record_index], fixed.records[item.record_index]
            assert (a.latitude, a.longitude) == (b.latitude, b.longitude)


def test_unresolved_invalidation_does_not_block_other_gap_or_invent_distance(
    tmp_path: Path,
) -> None:
    # Adjacent short trusted component prevents matching the second spike, not the first.
    activity, course = local_fixture(tmp_path, missing=((351, 355),), spikes=(150, 350))
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    assert len(plan.interval_plans) == 1  # mixed hole remains opt-in, independent HIGH fills
    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)
    assert fixed.records[150].latitude is not None
    assert fixed.records[350].latitude is None
    assert result.selection.unresolved_invalidated_indices == frozenset({350})
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    assert [r.distance for r in fixed.records] == [r.distance for r in activity.records]
    assert write_result_report(result)["distance"]["correction_skipped"] is True


def test_low_path_is_never_applied_even_with_low_threshold(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=(), spikes=(200,))
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    plan = replace(
        plan, interval_plans=(replace(plan.interval_plans[0], confidence=IntegrityConfidence.LOW),)
    )
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.LOW)
    assert result.selection.applied_interval_count == 0
    assert read_fit(result.output_path).records[200].latitude is None


@pytest.mark.parametrize("fault", ["outside_mask", "unsafe_edge", "missing_mask", "stale_mask"])
def test_writer_revalidates_scope_snapshot_and_actual_edges_before_publication(
    tmp_path: Path,
    fault: str,
) -> None:
    activity, course = local_fixture(tmp_path, missing=(), spikes=(200,))
    plan = build_repair_plan(activity, analyze_integrity(activity), course)
    candidate = plan.interval_plans[0]
    update = candidate.coordinate_updates[0]
    if fault == "outside_mask":
        candidate = replace(candidate, coordinate_updates=(replace(update, record_index=199),))
    elif fault == "unsafe_edge":
        candidate = replace(
            candidate, coordinate_updates=(replace(update, candidate_latitude=60.0),)
        )
    elif fault == "missing_mask":
        plan = replace(plan, coordinate_mask=())
    else:
        mask = list(plan.coordinate_mask)
        mask[0] = replace(mask[0], original_latitude=60.0)
        plan = replace(plan, coordinate_mask=tuple(mask))
    plan = replace(plan, interval_plans=(candidate,))
    output = tmp_path / "unsafe.fit"
    with pytest.raises(FitWriteError):
        write_repaired_fit(activity, plan, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_absent_coordinate_fields_are_added_for_all_gap_kinds(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, position_fields=False)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == 3
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert all(r.latitude is not None and r.longitude is not None for r in fixed.records)
    assert result.validation.valid and result.validation.crc_valid
    assert result.diff.structure_compatible and not result.diff.definitions_unchanged
    assert result.diff.added_coordinate_field_count == 180
    assert result.diff.definition_count_delta == 180
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    assert [r.distance for r in fixed.records] == [r.distance for r in activity.records]
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes
    for original, written in zip(
        activity.preservation.messages, fixed.preservation.messages, strict=True
    ):
        if "position_lat" in original.fields or original.message_type != "record":
            assert original.raw_chunk == written.raw_chunk


def test_partial_coordinate_pair_keeps_field_provenance_and_can_be_completed(
    tmp_path: Path,
) -> None:
    base, course = local_fixture(tmp_path, missing=())
    source = tmp_path / "partial-pair.fit"
    write_trajectory_activity(
        source,
        [(r.index, r.latitude, None if r.index == 200 else r.longitude) for r in base.records],
        retain_invalid_position_fields=True,
        distances_m=[r.distance for r in base.records],
        speeds_mps=[r.speed for r in base.records],
    )
    activity = read_fit(source)
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    item = plan.coordinate_mask[200]
    assert item.state is CoordinateState.ORIGINAL_MISSING
    assert item.original_latitude is not None and item.original_longitude is None
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    fixed = read_fit(result.output_path)
    assert fixed.records[200].latitude is not None and fixed.records[200].longitude is not None
    assert write_result_report(result)["coordinate_dispositions"][0]["original_longitude"] is None
