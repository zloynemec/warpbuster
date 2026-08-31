"""Optional acceptance tests for ignored private GPX activities."""

from pathlib import Path

import pytest

from warpbuster.gpx.reader import read_gpx
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import GeometryWarningKind, IntegrityStatus

_ORION_ARTYOM = Path("tests/private/tracks/Orion_Artyom.gpx")


@pytest.mark.private
@pytest.mark.skipif(
    not _ORION_ARTYOM.exists(),
    reason=f"private GPX is unavailable: {_ORION_ARTYOM}",
)
def test_private_orion_exposes_interpolated_geometry_without_claiming_corruption() -> None:
    """The Strava route has a long synthetic chord but no temporal evidence."""
    report = analyze_integrity(read_gpx(_ORION_ARTYOM))

    warning = max(report.geometry_warnings, key=lambda candidate: candidate.chord_distance_m)
    assert report.status is IntegrityStatus.UNKNOWN
    assert report.corrupted_intervals == ()
    assert warning.kind is GeometryWarningKind.POSSIBLE_INTERPOLATED_GNSS_GAP
    assert 1_150 <= warning.start_record_index <= 1_250
    assert 2_100 <= warning.end_record_index <= 2_200
    assert warning.chord_distance_m == pytest.approx(2_182.0, abs=20.0)
    assert warning.max_cross_track_deviation_m < 0.5
    assert warning.timestamps_available is False
