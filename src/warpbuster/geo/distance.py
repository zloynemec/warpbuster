"""Geodesic distance calculations."""

from __future__ import annotations

from math import asin, cos, radians, sin, sqrt

# IUGG mean Earth radius. This is a physical model constant, not a detector threshold.
_MEAN_EARTH_RADIUS_M = 6_371_008.8


def geodesic_distance_m(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    """Return the great-circle distance between two WGS84 coordinates in metres."""
    _validate_coordinate(latitude_a, longitude_a)
    _validate_coordinate(latitude_b, longitude_b)

    latitude_a_rad = radians(latitude_a)
    latitude_b_rad = radians(latitude_b)
    latitude_delta = latitude_b_rad - latitude_a_rad
    longitude_delta = radians(longitude_b - longitude_a)
    haversine = sin(latitude_delta / 2.0) ** 2 + (
        cos(latitude_a_rad) * cos(latitude_b_rad) * sin(longitude_delta / 2.0) ** 2
    )
    angular_distance = 2.0 * asin(min(1.0, sqrt(haversine)))
    return _MEAN_EARTH_RADIUS_M * angular_distance


def _validate_coordinate(latitude: float, longitude: float) -> None:
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude outside [-90, 90]: {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude outside [-180, 180]: {longitude}")
