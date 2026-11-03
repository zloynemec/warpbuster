"""Optional smoke test; the recorder CSV and generated artifacts stay ignored."""

from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree

import pytest

from warpbuster.gps import analyze_gps, trusted_gpx

_LOCATIONS = Path("tests/private/trail-companion/locations.csv")


@pytest.mark.private
@pytest.mark.skipif(not _LOCATIONS.exists(), reason="private Trail Companion CSV unavailable")
def test_private_recording_is_analyzed_without_changing_source() -> None:
    original = sha256(_LOCATIONS.read_bytes()).digest()
    result = analyze_gps(_LOCATIONS)
    assert len(result.points) == len({point.seq for point in result.points}) == 754
    assert {point.status for point in result.points} == {"trusted", "rejected", "uncertain"}
    assert max(t.elapsed_seconds for t in result.integrity.transitions) == pytest.approx(149, abs=1)
    gpx = ElementTree.fromstring(trusted_gpx(result))
    assert len(gpx.findall(".//{*}trkpt")) == sum(p.status == "trusted" for p in result.points)
    assert len(gpx.findall(".//{*}trkseg")) > 1
    assert sha256(_LOCATIONS.read_bytes()).digest() == original
