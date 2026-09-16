"""Bounded one-degree Skadi tile planning for WGS84 polylines."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number
from warpbuster_osm_routing.models import GeoPoint


@dataclass(frozen=True, slots=True)
class DemCoveragePlan:
    tile_names: tuple[str, ...]
    point_count: int
    buffer_m: float

    def as_dict(self) -> dict[str, object]:
        return {
            "tile_names": list(self.tile_names),
            "point_count": self.point_count,
            "buffer_m": self.buffer_m,
        }


def tile_name(latitude: int, longitude: int) -> str:
    if not -90 <= latitude < 90 or not -180 <= longitude < 180:
        raise RoutingError("INVALID_REQUEST", "DEM tile coordinates are outside WGS84")
    return f"{'S' if latitude < 0 else 'N'}{abs(latitude):02d}{'W' if longitude < 0 else 'E'}{abs(longitude):03d}"


def plan_coverage(
    points: Sequence[GeoPoint], *, buffer_m: float, maximum_points: int, maximum_tiles: int
) -> DemCoveragePlan:
    if not points or len(points) > maximum_points:
        raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM polyline point count is invalid")
    if not finite_number(buffer_m) or buffer_m < 0:
        raise RoutingError("INVALID_REQUEST", "DEM buffer must be finite and nonnegative")
    if maximum_points <= 0 or maximum_tiles <= 0:
        raise RoutingError("INVALID_REQUEST", "DEM planning limits must be positive")
    for point in points:
        if (
            not isinstance(point, GeoPoint)
            or not finite_number(point.latitude)
            or not finite_number(point.longitude)
            or not -90 <= point.latitude <= 90
            or not -180 <= point.longitude <= 180
        ):
            raise RoutingError("INVALID_REQUEST", "DEM polyline contains invalid WGS84")
    tiles: set[str] = set()
    segments = pairwise(points) if len(points) > 1 else ((points[0], points[0]),)
    for start, end in segments:
        if abs(end.longitude - start.longitude) > 180:
            raise RoutingError("INVALID_REQUEST", "DEM antimeridian crossing is unsupported")
        steps = max(
            1,
            math.ceil(
                max(abs(end.latitude - start.latitude), abs(end.longitude - start.longitude)) * 4
            ),
        )
        if steps > maximum_tiles * 16:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM segment exceeds planning budget")
        for index in range(steps + 1):
            fraction = index / steps
            lat = start.latitude + (end.latitude - start.latitude) * fraction
            lon = start.longitude + (end.longitude - start.longitude) * fraction
            lat_buffer = buffer_m / 111_000
            cos_lat = math.cos(math.radians(lat))
            if cos_lat < 0.01:
                raise RoutingError("INVALID_REQUEST", "DEM polar coverage is unsupported")
            lon_buffer = buffer_m / (111_000 * cos_lat)
            south, north = math.floor(lat - lat_buffer), math.floor(lat + lat_buffer)
            west, east = math.floor(lon - lon_buffer), math.floor(lon + lon_buffer)
            if south < -90 or north >= 90 or west < -180 or east >= 180:
                raise RoutingError("INVALID_REQUEST", "DEM buffered coverage crosses world bounds")
            if (north - south + 1) * (east - west + 1) > maximum_tiles:
                raise RoutingError(
                    "RESOURCE_LIMIT_EXCEEDED", "DEM buffered coverage exceeds tile limit"
                )
            for tile_lat in range(south, north + 1):
                for tile_lon in range(west, east + 1):
                    tiles.add(tile_name(tile_lat, tile_lon))
                    if len(tiles) > maximum_tiles:
                        raise RoutingError(
                            "RESOURCE_LIMIT_EXCEEDED", "DEM coverage exceeds tile limit"
                        )
    return DemCoveragePlan(tuple(sorted(tiles)), len(points), buffer_m)
