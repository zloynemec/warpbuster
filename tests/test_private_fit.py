"""Optional smoke test for ignored private activity data."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import TransitionClassification

_ANDROMEDA_FILES = (
    Path("tests/private/tracks/Andromeda_2026.fit"),
    Path("tests/private/tracks/Andromeda_2026_FIXED.fit"),
)


@pytest.mark.private
@pytest.mark.parametrize(
    "fit_path",
    [
        pytest.param(
            fit_path,
            id=fit_path.stem,
            marks=pytest.mark.skipif(
                not fit_path.exists(),
                reason=f"private FIT is unavailable: {fit_path}",
            ),
        )
        for fit_path in _ANDROMEDA_FILES
    ],
)
def test_private_andromeda_fit_can_be_inspected(fit_path: Path) -> None:
    """Private original and reference-fixed activities decode when available."""
    activity = read_fit(fit_path)

    assert activity.records
    assert activity.manufacturer == "garmin"
    assert activity.sport == "running"
    assert activity.preservation.crc_valid is True
    assert any(record.timestamp is not None for record in activity.records)
    assert any(
        record.latitude is not None and record.longitude is not None for record in activity.records
    )


@pytest.mark.private
@pytest.mark.skipif(
    not _ANDROMEDA_FILES[0].exists(),
    reason=f"private FIT is unavailable: {_ANDROMEDA_FILES[0]}",
)
def test_private_andromeda_has_impossible_local_transition() -> None:
    """The original private incident provides Task 003 acceptance evidence."""
    activity = read_fit(_ANDROMEDA_FILES[0])

    report = analyze_integrity(activity)

    assert report.count(TransitionClassification.IMPOSSIBLE) >= 1


@pytest.mark.private
@pytest.mark.skipif(
    not _ANDROMEDA_FILES[1].exists(),
    reason=f"private FIT is unavailable: {_ANDROMEDA_FILES[1]}",
)
def test_private_fixed_andromeda_has_no_impossible_local_transition() -> None:
    """The reference-fixed private activity has no absolute local corruption."""
    activity = read_fit(_ANDROMEDA_FILES[1])

    report = analyze_integrity(activity)

    assert report.count(TransitionClassification.IMPOSSIBLE) == 0


@pytest.mark.private
@pytest.mark.skipif(
    not _ANDROMEDA_FILES[0].exists(),
    reason=f"private FIT is unavailable: {_ANDROMEDA_FILES[0]}",
)
def test_private_andromeda_main_spoofing_island_is_detected() -> None:
    """The main private incident is one HIGH interval without course input."""
    activity = read_fit(_ANDROMEDA_FILES[0])

    report = analyze_integrity(activity)
    interval = max(report.corrupted_intervals, key=lambda candidate: candidate.record_count)

    assert interval.confidence.value == "high"
    assert interval.bridge.apparent_speed_mps <= interval.bridge.maximum_plausible_speed_mps
    expected_start = datetime(2026, 8, 29, 18, 29, 13, tzinfo=UTC)
    expected_end = datetime(2026, 8, 29, 18, 53, 33, tzinfo=UTC)
    assert interval.start_timestamp is not None
    assert interval.end_timestamp is not None
    assert abs((interval.start_timestamp - expected_start).total_seconds()) <= 30
    assert abs((interval.end_timestamp - expected_end).total_seconds()) <= 30
