"""Course-independent scope, provenance, continuity and invalidation regressions."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, IntervalDetectionKind
from warpbuster.models.reconstruction import CoordinateState, GapOrigin, ReconstructionReason
from warpbuster.reconstruction.gaps import coordinate_mask, inventory_gaps, masked_activity


def test_mask_keeps_original_snapshot_and_mixed_provenance(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=((201, 210),), spikes=(200,))
    integrity = analyze_integrity(activity)
    assert integrity.corrupted_intervals
    mask = coordinate_mask(activity, integrity)
    gaps = inventory_gaps(activity, mask)
    assert len(gaps) == 1
    assert gaps[0].origin is GapOrigin.MIXED
    assert gaps[0].original_missing_count == 10
    assert gaps[0].invalidated_count == 1
    assert mask[200].state is CoordinateState.INVALIDATED
    assert mask[201].state is CoordinateState.ORIGINAL_MISSING
    assert activity.records[200].latitude == pytest.approx(56.0)
    view = masked_activity(activity, mask)
    assert view.records[200].latitude is None
    assert view.records[200].timestamp == activity.records[200].timestamp
    assert view.preservation is activity.preservation


def test_preserved_component_and_continuity_split_gaps(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=((0, 9), (11, 30)))
    activity = replace(
        activity,
        records=tuple(replace(r, continuity_id=int(r.index >= 20)) for r in activity.records),
    )
    mask = coordinate_mask(activity, analyze_integrity(activity))
    gaps = inventory_gaps(activity, mask)
    assert [(g.start_record_index, g.end_record_index) for g in gaps] == [
        (0, 9),
        (11, 19),
        (20, 30),
    ]
    assert gaps[0].anchor_after_record_index == 10
    assert ReconstructionReason.CONTINUITY_BREAK in gaps[1].reasons
    assert gaps[2].anchor_before_record_index is None


def test_low_or_envelope_evidence_does_not_authorize_invalidation(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=(), spikes=(200,))
    integrity = analyze_integrity(activity)
    original = integrity.corrupted_intervals[0]
    for interval in (
        replace(original, confidence=IntegrityConfidence.LOW),
        replace(original, detection_kind=IntervalDetectionKind.COMPOSITE_REGION),
    ):
        mask = coordinate_mask(activity, replace(integrity, corrupted_intervals=(interval,)))
        assert mask[200].state is CoordinateState.PRESERVED
        assert not mask[200].anchor_eligible
        assert ReconstructionReason.INSUFFICIENT_CORRUPTION_PROOF in mask[200].reasons
    # An impossible transition with no record-level proof is not sufficient either.
    assert all(
        item.state is CoordinateState.PRESERVED
        for item in coordinate_mask(activity, replace(integrity, corrupted_intervals=()))
    )


def test_invalidation_confidence_is_separate_and_low_is_forbidden(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=(), spikes=(200,))
    integrity = analyze_integrity(activity)
    integrity = replace(
        integrity,
        corrupted_intervals=(
            replace(integrity.corrupted_intervals[0], confidence=IntegrityConfidence.MEDIUM),
        ),
    )
    assert coordinate_mask(activity, integrity)[200].state is CoordinateState.PRESERVED
    assert (
        coordinate_mask(activity, integrity, IntegrityConfidence.MEDIUM)[200].state
        is CoordinateState.INVALIDATED
    )
    with pytest.raises(ValueError, match="high or medium"):
        coordinate_mask(activity, integrity, IntegrityConfidence.LOW)
