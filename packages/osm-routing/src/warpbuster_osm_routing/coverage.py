"""Coverage validation and point lookup for OSM Manager snapshots."""

from __future__ import annotations

import math
import re
from typing import Any

from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.models import GeoPoint, SnapshotCoverage

COVERAGE_SCHEME = "web-mercator-v1"
COVERAGE_ZOOM = 12
MAX_WEB_MERCATOR_LATITUDE = 85.05112878
_CELL_PATTERN = re.compile(r"12/(\d{1,4})/(\d{1,4})\Z")


def parse_coverage(value: object) -> SnapshotCoverage:
    if not isinstance(value, dict):
        raise RoutingSpikeError("manifest_invalid", "coverage must be an object")
    if value.get("scheme") != COVERAGE_SCHEME:
        raise RoutingSpikeError(
            "unsupported_manifest",
            f"unsupported coverage scheme: {value.get('scheme')!r}",
            {"expected": COVERAGE_SCHEME},
        )
    raw_ids = value.get("cell_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise RoutingSpikeError("manifest_invalid", "coverage.cell_ids must be non-empty")
    cell_ids: list[str] = []
    numeric_ids: list[tuple[int, int]] = []
    cell_limit = 1 << COVERAGE_ZOOM
    for index, raw_id in enumerate(raw_ids):
        match = _CELL_PATTERN.fullmatch(raw_id) if isinstance(raw_id, str) else None
        if match is None:
            raise RoutingSpikeError(
                "manifest_invalid", f"coverage.cell_ids[{index}] is invalid"
            )
        x, y = (int(part) for part in match.groups())
        if x >= cell_limit or y >= cell_limit:
            raise RoutingSpikeError(
                "manifest_invalid", f"coverage.cell_ids[{index}] is out of range"
            )
        cell_ids.append(raw_id)
        numeric_ids.append((x, y))
    if len(cell_ids) != len(set(cell_ids)) or numeric_ids != sorted(numeric_ids):
        raise RoutingSpikeError(
            "manifest_invalid", "coverage.cell_ids must be unique and sorted"
        )
    buffer_m = _number(value, "buffer_m", allow_zero=True)
    area_km2 = _number(value, "area_km2", allow_zero=False)
    return SnapshotCoverage(COVERAGE_SCHEME, tuple(cell_ids), buffer_m, area_km2)


def contains(coverage: SnapshotCoverage, point: GeoPoint) -> bool:
    cell_id = cell_id_for_point(point)
    return cell_id is not None and cell_id in coverage.cell_ids


def cell_id_for_point(point: GeoPoint) -> str | None:
    if not (-90.0 <= point.latitude <= 90.0 and -180.0 <= point.longitude <= 180.0):
        return None
    if abs(point.latitude) > MAX_WEB_MERCATOR_LATITUDE:
        return None
    size = 1 << COVERAGE_ZOOM
    x = int((point.longitude + 180.0) / 360.0 * size)
    latitude_radians = math.radians(point.latitude)
    y = int(
        (1.0 - math.asinh(math.tan(latitude_radians)) / math.pi) / 2.0 * size
    )
    x = min(size - 1, max(0, x))
    y = min(size - 1, max(0, y))
    return f"{COVERAGE_ZOOM}/{x}/{y}"


def _number(document: dict[str, Any], key: str, *, allow_zero: bool) -> float:
    value = document.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
        raise RoutingSpikeError("manifest_invalid", f"coverage.{key} must be a number")
    result = float(value)
    if result < 0 or (not allow_zero and result == 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise RoutingSpikeError("manifest_invalid", f"coverage.{key} must be {qualifier}")
    return result
