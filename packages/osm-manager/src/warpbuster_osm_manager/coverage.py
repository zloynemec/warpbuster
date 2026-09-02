"""Safe GPX/GeoJSON/bbox parsing and bounded geographic cell planning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from math import (
    asinh,
    atan,
    atan2,
    ceil,
    cos,
    degrees,
    floor,
    isfinite,
    pi,
    radians,
    sin,
    sinh,
    tan,
)
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from warpbuster_osm_manager.config import COVERAGE_SCHEME_VERSION, OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, InvalidInputError, RequestLimitError
from warpbuster_osm_manager.models import BoundingBox, CellBatch, CellId, CoveragePlan, GeoPoint

EARTH_RADIUS_M = 6_371_008.8
WEB_MERCATOR_MAX_LATITUDE = 85.05112878
DEGREES_PER_HALF_TURN = 180.0
FULL_LONGITUDE_DEGREES = 360.0
OSM_COORDINATE_TOLERANCE_DEGREES = 0.000001


@dataclass(frozen=True, slots=True)
class ParsedGeometry:
    """Continuous lines and filled polygon rings from one local source."""

    lines: tuple[tuple[GeoPoint, ...], ...]
    polygons: tuple[tuple[tuple[GeoPoint, ...], ...], ...] = ()


def _read_bounded(path: Path, maximum_bytes: int) -> bytes:
    try:
        size = path.stat().st_size
        if size > maximum_bytes:
            raise RequestLimitError(
                "input geometry file exceeds maximum_input_file_bytes",
                details={"path": str(path), "size_bytes": size, "limit_bytes": maximum_bytes},
            )
        return path.read_bytes()
    except RequestLimitError:
        raise
    except OSError as error:
        raise InvalidInputError(f"cannot read input geometry {path}: {error}") from error


def _reject_unsafe_xml(raw: bytes) -> None:
    uppercase = raw[: min(len(raw), 65_536)].upper()
    if b"<!DOCTYPE" in uppercase or b"<!ENTITY" in uppercase:
        raise InvalidInputError(
            "GPX document types and entities are not supported",
            code=ErrorCode.INVALID_GPX,
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _point(longitude: Any, latitude: Any, *, gpx: bool = False) -> GeoPoint:
    error_code = ErrorCode.INVALID_GPX if gpx else ErrorCode.INVALID_INPUT
    try:
        lon = float(longitude)
        lat = float(latitude)
    except (TypeError, ValueError) as error:
        raise InvalidInputError("coordinates must be finite numbers", code=error_code) from error
    if not isfinite(lon) or not isfinite(lat):
        raise InvalidInputError("coordinates must be finite numbers", code=error_code)
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise InvalidInputError(
            "coordinates are outside valid WGS84 bounds",
            code=error_code,
            details={"longitude": lon, "latitude": lat},
        )
    if abs(lat) > WEB_MERCATOR_MAX_LATITUDE:
        raise InvalidInputError(
            "coordinates exceed the supported Web Mercator latitude range",
            code=error_code,
            details={"latitude": lat, "limit": WEB_MERCATOR_MAX_LATITUDE},
        )
    return GeoPoint(longitude=lon, latitude=lat)


def parse_gpx(path: Path, config: OsmManagerConfig) -> ParsedGeometry:
    """Read only trk/rte line geometry from a bounded local GPX document."""
    raw = _read_bounded(path, config.maximum_input_file_bytes)
    _reject_unsafe_xml(raw)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise InvalidInputError(
            f"cannot parse GPX {path}: {error}", code=ErrorCode.INVALID_GPX
        ) from error
    if _local_name(root.tag) != "gpx":
        raise InvalidInputError("input is not a GPX document", code=ErrorCode.INVALID_GPX)

    lines: list[tuple[GeoPoint, ...]] = []
    point_count = 0
    for element in root.iter():
        kind = _local_name(element.tag)
        if kind not in {"trkseg", "rte"}:
            continue
        expected_child = "trkpt" if kind == "trkseg" else "rtept"
        points = tuple(
            _point(child.get("lon"), child.get("lat"), gpx=True)
            for child in element
            if _local_name(child.tag) == expected_child
        )
        point_count += len(points)
        if len(points) >= 2:
            lines.append(points)
        if point_count > config.maximum_gpx_points:
            raise RequestLimitError(
                "GPX exceeds maximum_gpx_points",
                details={"point_count": point_count, "limit": config.maximum_gpx_points},
            )
    if not lines:
        raise InvalidInputError(
            "GPX must contain a trkseg or rte with at least two coordinates",
            code=ErrorCode.INVALID_GPX,
        )
    _validate_line_lengths(tuple(lines), config, gpx=True)
    return ParsedGeometry(lines=tuple(lines))


def parse_geojson(path: Path, config: OsmManagerConfig) -> ParsedGeometry:
    """Parse supported WGS84 GeoJSON geometries without retaining properties."""
    raw = _read_bounded(path, config.maximum_input_file_bytes)
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidInputError(f"cannot parse GeoJSON {path}: {error}") from error
    lines: list[tuple[GeoPoint, ...]] = []
    polygons: list[tuple[tuple[GeoPoint, ...], ...]] = []

    def consume_geometry(geometry: Any) -> None:
        if not isinstance(geometry, dict):
            raise InvalidInputError("GeoJSON geometry must be an object")
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if kind == "Point":
            lines.append((_geojson_point(coordinates),))
        elif kind == "MultiPoint":
            values = _expect_sequence(coordinates, "MultiPoint coordinates")
            lines.extend((_geojson_point(value),) for value in values)
        elif kind == "LineString":
            lines.append(_geojson_line(coordinates))
        elif kind == "MultiLineString":
            values = _expect_sequence(coordinates, "MultiLineString coordinates")
            lines.extend(_geojson_line(value) for value in values)
        elif kind == "Polygon":
            polygons.append(_geojson_polygon(coordinates))
        elif kind == "MultiPolygon":
            values = _expect_sequence(coordinates, "MultiPolygon coordinates")
            polygons.extend(_geojson_polygon(value) for value in values)
        else:
            raise InvalidInputError(f"unsupported GeoJSON geometry type: {kind!r}")

    if not isinstance(document, dict):
        raise InvalidInputError("GeoJSON root must be an object")
    root_kind = document.get("type")
    if root_kind == "FeatureCollection":
        features = _expect_sequence(document.get("features"), "FeatureCollection features")
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise InvalidInputError("FeatureCollection contains an invalid feature")
            consume_geometry(feature.get("geometry"))
    elif root_kind == "Feature":
        consume_geometry(document.get("geometry"))
    else:
        consume_geometry(document)
    if not lines and not polygons:
        raise InvalidInputError("GeoJSON contains no supported geometry")
    polygon_rings = tuple(ring for polygon in polygons for ring in polygon)
    _validate_line_lengths(
        (*tuple(line for line in lines if len(line) >= 2), *polygon_rings),
        config,
        gpx=False,
    )
    return ParsedGeometry(lines=tuple(lines), polygons=tuple(polygons))


def _expect_sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidInputError(f"{label} must be an array")
    return value


def _geojson_point(value: Any) -> GeoPoint:
    if not isinstance(value, list) or len(value) < 2:
        raise InvalidInputError("GeoJSON coordinate must contain longitude and latitude")
    return _point(value[0], value[1])


def _geojson_line(value: Any) -> tuple[GeoPoint, ...]:
    _expect_sequence(value, "LineString coordinates")
    points = tuple(_geojson_point(item) for item in value)
    if len(points) < 2:
        raise InvalidInputError("LineString must contain at least two coordinates")
    return points


def _geojson_polygon(value: Any) -> tuple[tuple[GeoPoint, ...], ...]:
    _expect_sequence(value, "Polygon coordinates")
    rings = tuple(_geojson_line(ring) for ring in value)
    if not rings:
        raise InvalidInputError("Polygon must contain at least one ring")
    if any(len(ring) < 4 or ring[0] != ring[-1] for ring in rings):
        raise InvalidInputError("Polygon rings must be closed and contain at least four points")
    return rings


def validated_point(longitude: Any, latitude: Any) -> GeoPoint:
    """Create one validated WGS84/Web-Mercator point for protocol geometry."""
    return _point(longitude, latitude)


def parse_bbox(value: str) -> BoundingBox:
    """Parse WEST,SOUTH,EAST,NORTH, allowing explicit antimeridian crossing."""
    try:
        west, south, east, north = (float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise InvalidInputError("bbox must be WEST,SOUTH,EAST,NORTH") from error
    _point(west, south)
    _point(east, north)
    if south >= north:
        raise InvalidInputError("bbox south must be less than north")
    return BoundingBox(west=west, south=south, east=east, north=north)


def plan_from_gpx(
    path: Path, config: OsmManagerConfig, *, buffer_m: float | None = None
) -> CoveragePlan:
    """Build a bounded coverage plan from GPX track/route geometry."""
    return plan_from_geometry(
        parse_gpx(path, config),
        config,
        source_kind="gpx",
        buffer_m=config.gpx_corridor_buffer_m if buffer_m is None else buffer_m,
    )


def plan_from_geojson(
    path: Path, config: OsmManagerConfig, *, buffer_m: float | None = None
) -> CoveragePlan:
    """Build coverage from GeoJSON; polygons have zero implicit buffer."""
    geometry = parse_geojson(path, config)
    implicit = config.gpx_corridor_buffer_m if geometry.lines and not geometry.polygons else 0.0
    return plan_from_geometry(
        geometry,
        config,
        source_kind="geojson",
        buffer_m=implicit if buffer_m is None else buffer_m,
    )


def plan_from_bbox(bounds: BoundingBox, config: OsmManagerConfig) -> CoveragePlan:
    """Build coverage for an explicit bbox."""
    _point(bounds.west, bounds.south)
    _point(bounds.east, bounds.north)
    if bounds.south >= bounds.north:
        raise InvalidInputError("bbox south must be less than north")
    cells = cells_for_bbox(bounds, config.cache_grid_zoom, config.maximum_ensure_cells)
    return _finalize_plan("bbox", cells, 0.0, config)


def plan_from_import_bounds(bounds: BoundingBox, config: OsmManagerConfig) -> CoveragePlan:
    """Index only cache cells wholly covered by a declared extract rectangle."""
    intersecting = plan_from_bbox(bounds, config)
    contained = {
        cell for cell in intersecting.cells if _bounds_contains(bounds, bounds_for_cell(cell))
    }
    if not contained:
        raise InvalidInputError(
            "imported extract bounds do not fully contain one cache cell",
            details={"cache_grid_zoom": config.cache_grid_zoom},
        )
    return _finalize_plan("import", contained, 0.0, config)


def _bounds_contains(container: BoundingBox, candidate: BoundingBox) -> bool:
    return (
        container.west <= candidate.west + OSM_COORDINATE_TOLERANCE_DEGREES
        and container.south <= candidate.south + OSM_COORDINATE_TOLERANCE_DEGREES
        and container.east + OSM_COORDINATE_TOLERANCE_DEGREES >= candidate.east
        and container.north + OSM_COORDINATE_TOLERANCE_DEGREES >= candidate.north
    )


def plan_from_geometry(
    geometry: ParsedGeometry,
    config: OsmManagerConfig,
    *,
    source_kind: str,
    buffer_m: float,
) -> CoveragePlan:
    """Convert parsed geometry to a stable bounded set of cache cells."""
    if buffer_m < 0:
        raise InvalidInputError("buffer_m must not be negative")
    polygon_rings = tuple(ring for polygon in geometry.polygons for ring in polygon)
    _validate_line_lengths(
        (*tuple(line for line in geometry.lines if len(line) >= 2), *polygon_rings),
        config,
        gpx=source_kind == "gpx",
    )
    cells: set[CellId] = set()
    for line in geometry.lines:
        cells.update(_cells_for_line(line, buffer_m, config))
        _check_cell_count(cells, config)
    for polygon in geometry.polygons:
        cells.update(_cells_for_polygon(polygon, buffer_m, config))
        _check_cell_count(cells, config)
    if not cells:
        raise InvalidInputError("coverage geometry produced no cache cells")
    return _finalize_plan(source_kind, cells, buffer_m, config)


def _validate_line_lengths(
    lines: tuple[tuple[GeoPoint, ...], ...], config: OsmManagerConfig, *, gpx: bool
) -> None:
    total = 0.0
    for line in lines:
        for before, after in pairwise(line):
            distance = geodesic_distance_m(before, after)
            if distance > config.maximum_gpx_segment_length_m:
                raise RequestLimitError(
                    "geometry segment exceeds maximum_gpx_segment_length_m",
                    details={
                        "distance_m": distance,
                        "limit_m": config.maximum_gpx_segment_length_m,
                        "input_kind": "gpx" if gpx else "geojson",
                    },
                )
            total += distance
    if total > config.maximum_gpx_total_length_m:
        raise RequestLimitError(
            "geometry exceeds maximum_gpx_total_length_m",
            details={"length_m": total, "limit_m": config.maximum_gpx_total_length_m},
        )


def _cells_for_line(
    line: Sequence[GeoPoint], buffer_m: float, config: OsmManagerConfig
) -> set[CellId]:
    if len(line) == 1:
        return _cells_near_point(line[0], buffer_m, config)
    result: set[CellId] = set()
    for before, after in pairwise(line):
        distance = geodesic_distance_m(before, after)
        midpoint = _interpolate(before, after, 0.5)
        sample_cell = cell_for_point(midpoint, config.cache_grid_zoom)
        width, height = cell_dimensions_m(sample_cell)
        step = min(width, height) * config.coverage_sample_cell_fraction
        sample_count = max(1, ceil(distance / step))
        for index in range(sample_count + 1):
            sample = _interpolate(before, after, index / sample_count)
            result.update(_cells_near_point(sample, buffer_m, config))
    return result


def _cells_near_point(point: GeoPoint, buffer_m: float, config: OsmManagerConfig) -> set[CellId]:
    if not buffer_m:
        return {cell_for_point(point, config.cache_grid_zoom)}
    latitude_radius = degrees(buffer_m / EARTH_RADIUS_M)
    longitude_radius = min(
        DEGREES_PER_HALF_TURN,
        latitude_radius / max(cos(radians(point.latitude)), 1e-12),
    )
    south = max(-WEB_MERCATOR_MAX_LATITUDE, point.latitude - latitude_radius)
    north = min(WEB_MERCATOR_MAX_LATITUDE, point.latitude + latitude_radius)
    west = _normalize_longitude(point.longitude - longitude_radius)
    east = _normalize_longitude(point.longitude + longitude_radius)
    return cells_for_bbox(
        BoundingBox(west=west, south=south, east=east, north=north),
        config.cache_grid_zoom,
        config.maximum_ensure_cells,
    )


def _normalize_longitude(longitude: float) -> float:
    if longitude == DEGREES_PER_HALF_TURN:
        return longitude
    return (longitude + DEGREES_PER_HALF_TURN) % FULL_LONGITUDE_DEGREES - (DEGREES_PER_HALF_TURN)


def _cells_for_polygon(
    polygon: tuple[tuple[GeoPoint, ...], ...], buffer_m: float, config: OsmManagerConfig
) -> set[CellId]:
    outer = polygon[0]
    result = _cells_for_line(outer, buffer_m, config)
    longitudes = [point.longitude for point in outer]
    latitudes = [point.latitude for point in outer]
    bounds = BoundingBox(min(longitudes), min(latitudes), max(longitudes), max(latitudes))
    candidates = cells_for_bbox(bounds, config.cache_grid_zoom, config.maximum_ensure_cells)
    for cell in candidates:
        cell_bounds = bounds_for_cell(cell)
        centre = GeoPoint(
            longitude=(cell_bounds.west + cell_bounds.east) / 2,
            latitude=(cell_bounds.south + cell_bounds.north) / 2,
        )
        if _point_in_ring(centre, outer) and not any(
            _point_in_ring(centre, hole) for hole in polygon[1:]
        ):
            result.add(cell)
    return result


def _point_in_ring(point: GeoPoint, ring: Sequence[GeoPoint]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        crosses = (current.latitude > point.latitude) != (previous.latitude > point.latitude)
        if crosses:
            denominator = previous.latitude - current.latitude
            longitude = (previous.longitude - current.longitude) * (
                point.latitude - current.latitude
            ) / denominator + current.longitude
            if point.longitude < longitude:
                inside = not inside
        previous = current
    return inside


def cells_for_bbox(bounds: BoundingBox, zoom: int, maximum_cells: int) -> set[CellId]:
    """Enumerate bounded cells for a bbox, including explicit dateline crossing."""
    dimension = 1 << zoom
    north_y = cell_for_point(GeoPoint(bounds.west, bounds.north), zoom).y
    south_y = cell_for_point(GeoPoint(bounds.west, bounds.south), zoom).y
    west_x = cell_for_point(GeoPoint(bounds.west, bounds.south), zoom).x
    east_x = cell_for_point(GeoPoint(bounds.east, bounds.south), zoom).x
    if bounds.west <= bounds.east:
        x_values: Iterable[int] = range(west_x, east_x + 1)
    else:
        x_values = (*range(west_x, dimension), *range(0, east_x + 1))
    x_tuple = tuple(x_values)
    count = len(x_tuple) * (south_y - north_y + 1)
    if count > maximum_cells:
        raise RequestLimitError(
            "bbox exceeds maximum_ensure_cells",
            details={"cell_count": count, "limit": maximum_cells},
        )
    return {CellId(zoom=zoom, x=x, y=y) for y in range(north_y, south_y + 1) for x in x_tuple}


def cell_for_point(point: GeoPoint, zoom: int) -> CellId:
    """Return the Web Mercator cell containing a validated point."""
    dimension = 1 << zoom
    x_float = (point.longitude + DEGREES_PER_HALF_TURN) / FULL_LONGITUDE_DEGREES
    latitude_rad = radians(point.latitude)
    y_float = (1 - asinh(tan(latitude_rad)) / pi) / 2
    x = min(dimension - 1, max(0, floor(x_float * dimension)))
    y = min(dimension - 1, max(0, floor(y_float * dimension)))
    return CellId(zoom=zoom, x=x, y=y)


def bounds_for_cell(cell: CellId) -> BoundingBox:
    """Return exact WGS84 bounds of a Web Mercator coverage cell."""
    dimension = 1 << cell.zoom
    west = cell.x / dimension * FULL_LONGITUDE_DEGREES - DEGREES_PER_HALF_TURN
    east = (cell.x + 1) / dimension * FULL_LONGITUDE_DEGREES - DEGREES_PER_HALF_TURN
    north = degrees(atan(sinh(pi * (1 - 2 * cell.y / dimension))))
    south = degrees(atan(sinh(pi * (1 - 2 * (cell.y + 1) / dimension))))
    return BoundingBox(west=west, south=south, east=east, north=north)


def cell_dimensions_m(cell: CellId) -> tuple[float, float]:
    """Return approximate east-west and north-south dimensions in metres."""
    bounds = bounds_for_cell(cell)
    midpoint_latitude = (bounds.south + bounds.north) / 2
    width = geodesic_distance_m(
        GeoPoint(bounds.west, midpoint_latitude), GeoPoint(bounds.east, midpoint_latitude)
    )
    height = geodesic_distance_m(
        GeoPoint(bounds.west, bounds.south), GeoPoint(bounds.west, bounds.north)
    )
    return width, height


def geodesic_distance_m(before: GeoPoint, after: GeoPoint) -> float:
    """Compute spherical geodesic distance with antimeridian-safe longitude delta."""
    latitude_1 = radians(before.latitude)
    latitude_2 = radians(after.latitude)
    delta_latitude = latitude_2 - latitude_1
    delta_longitude_degrees = _longitude_delta(before.longitude, after.longitude)
    delta_longitude = radians(delta_longitude_degrees)
    haversine = sin(delta_latitude / 2) ** 2 + (
        cos(latitude_1) * cos(latitude_2) * sin(delta_longitude / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * atan2(haversine**0.5, max(0.0, 1 - haversine) ** 0.5)


def _longitude_delta(before: float, after: float) -> float:
    return (after - before + DEGREES_PER_HALF_TURN) % FULL_LONGITUDE_DEGREES - (
        DEGREES_PER_HALF_TURN
    )


def _interpolate(before: GeoPoint, after: GeoPoint, fraction: float) -> GeoPoint:
    delta_longitude = _longitude_delta(before.longitude, after.longitude)
    longitude = before.longitude + delta_longitude * fraction
    if longitude > DEGREES_PER_HALF_TURN:
        longitude -= FULL_LONGITUDE_DEGREES
    if longitude < -DEGREES_PER_HALF_TURN:
        longitude += FULL_LONGITUDE_DEGREES
    return GeoPoint(
        longitude=longitude,
        latitude=before.latitude + (after.latitude - before.latitude) * fraction,
    )


def _check_cell_count(cells: set[CellId], config: OsmManagerConfig) -> None:
    if len(cells) > config.maximum_ensure_cells:
        raise RequestLimitError(
            "coverage exceeds maximum_ensure_cells",
            details={"cell_count": len(cells), "limit": config.maximum_ensure_cells},
        )


def _finalize_plan(
    source_kind: str,
    cells: Iterable[CellId],
    buffer_m: float,
    config: OsmManagerConfig,
) -> CoveragePlan:
    ordered = tuple(sorted(set(cells)))
    _check_cell_count(set(ordered), config)
    area_km2 = sum(cell_area_km2(cell) for cell in ordered)
    if area_km2 > config.maximum_requested_area_km2:
        raise RequestLimitError(
            "coverage exceeds maximum_requested_area_km2",
            details={"area_km2": area_km2, "limit_km2": config.maximum_requested_area_km2},
        )
    canonical = json.dumps(
        {
            "coverage_scheme": COVERAGE_SCHEME_VERSION,
            "source_kind": source_kind,
            "buffer_m": buffer_m,
            "cells": [str(cell) for cell in ordered],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return CoveragePlan(
        source_kind=source_kind,
        cells=ordered,
        buffer_m=buffer_m,
        area_km2=area_km2,
        request_fingerprint=hashlib.sha256(canonical).hexdigest(),
    )


def cell_area_km2(cell: CellId) -> float:
    """Compute spherical surface area for one cell."""
    bounds = bounds_for_cell(cell)
    longitude_span = radians(bounds.east - bounds.west)
    sine_span = abs(sin(radians(bounds.north)) - sin(radians(bounds.south)))
    return EARTH_RADIUS_M**2 * longitude_span * sine_span / 1_000_000


def batch_cells(cells: Iterable[CellId], config: OsmManagerConfig) -> tuple[CellBatch, ...]:
    """Merge horizontal neighboring cells into bounded exact rectangular requests."""
    by_row: dict[tuple[int, int], list[int]] = {}
    for cell in cells:
        by_row.setdefault((cell.zoom, cell.y), []).append(cell.x)
    batches: list[CellBatch] = []
    maximum = config.maximum_cells_per_overpass_request
    for (zoom, y), values in sorted(by_row.items()):
        unique = sorted(set(values))
        start = 0
        while start < len(unique):
            run = [unique[start]]
            index = start + 1
            while index < len(unique) and unique[index] == run[-1] + 1 and len(run) < maximum:
                run.append(unique[index])
                index += 1
            batch_items = tuple(CellId(zoom=zoom, x=x, y=y) for x in run)
            first = bounds_for_cell(batch_items[0])
            last = bounds_for_cell(batch_items[-1])
            batches.append(
                CellBatch(
                    cells=batch_items,
                    bounds=BoundingBox(first.west, first.south, last.east, first.north),
                )
            )
            start = index
    if len(batches) > config.maximum_overpass_requests:
        raise RequestLimitError(
            "coverage exceeds maximum_overpass_requests",
            details={"request_count": len(batches), "limit": config.maximum_overpass_requests},
        )
    return tuple(batches)
