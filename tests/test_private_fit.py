"""Optional smoke test for ignored private activity data."""

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
