"""Small deterministic WGS84/polyline helpers used by route auditing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint

EARTH_RADIUS_M = 6_371_008.8
POLYLINE_PRECISION = 1_000_000


def haversine_m(first: GeoPoint, second: GeoPoint) -> float:
    lat1, lat2 = math.radians(first.latitude), math.radians(second.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(second.longitude - first.longitude)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(value)))


def decode_polyline6(encoded: str, *, maximum_points: int | None = None) -> tuple[GeoPoint, ...]:
    """Decode Valhalla's latitude-first, six-decimal polyline."""
    if not isinstance(encoded, str) or not encoded:
        raise RoutingError("ROUTE_AUDIT_FAILED", "encoded route geometry is empty")
    values: list[int] = []
    index = 0
    while index < len(encoded):
        if maximum_points is not None and len(values) >= maximum_points * 2:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "decoded route point count exceeds bounds",
                {"limit": maximum_points},
            )
        result = 0
        shift = 0
        while True:
            if index >= len(encoded) or shift > 60:
                raise RoutingError("ROUTE_AUDIT_FAILED", "encoded route geometry is invalid")
            byte = ord(encoded[index]) - 63
            index += 1
            if not 0 <= byte <= 63:
                raise RoutingError("ROUTE_AUDIT_FAILED", "encoded route geometry is invalid")
            result |= (byte & 0x1F) << shift
            shift += 5
            if byte < 0x20:
                break
        values.append(~(result >> 1) if result & 1 else result >> 1)
    if len(values) % 2:
        raise RoutingError("ROUTE_AUDIT_FAILED", "encoded route geometry has an odd ordinate")
    latitude = longitude = 0
    points: list[GeoPoint] = []
    for offset in range(0, len(values), 2):
        latitude += values[offset]
        longitude += values[offset + 1]
        point = GeoPoint(latitude / POLYLINE_PRECISION, longitude / POLYLINE_PRECISION)
        if not valid_wgs84(point):
            raise RoutingError("ROUTE_AUDIT_FAILED", "route geometry contains invalid WGS84")
        points.append(point)
    return tuple(points)


def path_length_m(points: Sequence[GeoPoint]) -> float:
    return sum(haversine_m(first, second) for first, second in pairwise(points))


def bounds(points: Sequence[GeoPoint]) -> dict[str, float]:
    return {
        "south": min(point.latitude for point in points),
        "west": min(point.longitude for point in points),
        "north": max(point.latitude for point in points),
        "east": max(point.longitude for point in points),
    }


def valid_wgs84(point: GeoPoint) -> bool:
    return (
        isinstance(point, GeoPoint)
        and finite_number(point.latitude)
        and finite_number(point.longitude)
        and -90.0 <= point.latitude <= 90.0
        and -180.0 <= point.longitude <= 180.0
    )


def finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
