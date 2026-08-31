"""Geographic utility tests."""

import pytest

from warpbuster.geo import geodesic_distance_m


def test_geodesic_distance_uses_great_circle_geometry() -> None:
    """One longitude degree at the equator has the expected geodesic length."""
    assert geodesic_distance_m(0.0, 0.0, 0.0, 1.0) == pytest.approx(111_195.08, abs=0.1)
    assert geodesic_distance_m(55.0, 37.0, 55.0, 37.0) == 0.0


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
)
def test_geodesic_distance_rejects_invalid_coordinates(
    latitude: float,
    longitude: float,
) -> None:
    """Invalid WGS84 coordinates cannot silently enter detector calculations."""
    with pytest.raises(ValueError):
        geodesic_distance_m(latitude, longitude, 0.0, 0.0)
