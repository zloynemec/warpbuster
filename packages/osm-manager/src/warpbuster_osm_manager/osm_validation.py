"""Bounded validation and metadata extraction for raw OSM files."""

from __future__ import annotations

import gzip
from dataclasses import dataclass
from pathlib import Path
from typing import IO, cast
from xml.etree import ElementTree

from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import BoundingBox


@dataclass(frozen=True, slots=True)
class OsmValidationResult:
    """Validated raw OSM metadata used by cache publication."""

    object_count: int
    node_count: int
    way_count: int
    osm_base_timestamp: str | None
    bounds: BoundingBox | None
    bounds_are_declared: bool


def validate_osm_xml(path: Path, config: OsmManagerConfig) -> OsmValidationResult:
    """Validate a plain or gzipped OSM XML file with bounded object retention."""
    try:
        with _open_binary(path) as stream:
            prefix = stream.read(min(config.http_read_chunk_bytes, 65_536)).upper()
            if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
                raise _invalid("OSM XML document types and entities are not supported")
        with _open_binary(path) as stream:
            return _parse_osm_xml(stream, config)
    except OsmManagerError:
        raise
    except (OSError, ElementTree.ParseError, UnicodeError, ValueError) as error:
        raise _invalid(f"cannot validate OSM XML: {error}") from error


def _open_binary(path: Path) -> IO[bytes]:
    if path.suffix.casefold() == ".gz":
        return cast(IO[bytes], gzip.open(path, "rb"))
    return path.open("rb")


def _parse_osm_xml(stream: IO[bytes], config: OsmManagerConfig) -> OsmValidationResult:
    root_seen = False
    osm_base_timestamp: str | None = None
    node_ids: set[int] = set()
    referenced_node_ids: set[int] = set()
    node_count = 0
    way_count = 0
    object_count = 0
    west = 180.0
    east = -180.0
    south = 90.0
    north = -90.0
    explicit_bounds: BoundingBox | None = None

    for event, element in ElementTree.iterparse(stream, events=("start", "end")):
        tag = _local_name(element.tag)
        if event == "start" and not root_seen:
            if tag != "osm":
                raise _invalid("OSM XML root must be <osm>")
            root_seen = True
            continue
        if event != "end":
            continue
        if tag == "meta":
            osm_base_timestamp = element.get("osm_base") or osm_base_timestamp
        elif tag == "remark" and (element.text or "").strip():
            raise _invalid("Overpass returned an error remark instead of OSM data")
        elif tag == "bounds":
            explicit_bounds = BoundingBox(
                west=float(_required_attribute(element, "minlon")),
                south=float(_required_attribute(element, "minlat")),
                east=float(_required_attribute(element, "maxlon")),
                north=float(_required_attribute(element, "maxlat")),
            )
            _validate_declared_bounds(explicit_bounds)
        elif tag == "node":
            identifier = int(_required_attribute(element, "id"))
            longitude = float(_required_attribute(element, "lon"))
            latitude = float(_required_attribute(element, "lat"))
            if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
                raise _invalid("OSM node coordinate is outside WGS84 bounds")
            node_ids.add(identifier)
            node_count += 1
            object_count += 1
            west = min(west, longitude)
            east = max(east, longitude)
            south = min(south, latitude)
            north = max(north, latitude)
        elif tag == "way":
            _required_attribute(element, "id")
            referenced_node_ids.update(
                int(_required_attribute(child, "ref"))
                for child in element
                if _local_name(child.tag) == "nd"
            )
            way_count += 1
            object_count += 1
        elif tag == "relation":
            object_count += 1
        if object_count > config.maximum_osm_objects:
            raise OsmManagerError(
                ErrorCode.RESPONSE_LIMIT_EXCEEDED,
                "OSM data exceeds maximum_osm_objects",
                {"object_count": object_count, "limit": config.maximum_osm_objects},
            )
        if tag in {"node", "way", "relation", "bounds", "meta"}:
            element.clear()
    if not root_seen:
        raise _invalid("OSM XML is empty")
    missing = referenced_node_ids - node_ids
    if missing:
        raise _invalid(
            "OSM XML is not reference complete",
            details={"missing_node_reference_count": len(missing)},
        )
    inferred = BoundingBox(west=west, south=south, east=east, north=north) if node_count else None
    return OsmValidationResult(
        object_count=object_count,
        node_count=node_count,
        way_count=way_count,
        osm_base_timestamp=osm_base_timestamp,
        bounds=explicit_bounds or inferred,
        bounds_are_declared=explicit_bounds is not None,
    )


def validate_osm_pbf(path: Path, config: OsmManagerConfig) -> OsmValidationResult:
    """Stream a PBF with pyosmium and require valid referenced node locations."""
    try:
        import osmium
    except ImportError as error:
        raise _invalid("PBF import requires the osmium runtime dependency") from error

    class _LimitExceeded(Exception):
        pass

    class _Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.object_count = 0
            self.node_count = 0
            self.way_count = 0
            self.west = 180.0
            self.east = -180.0
            self.south = 90.0
            self.north = -90.0

        def _count(self) -> None:
            self.object_count += 1
            if self.object_count > config.maximum_osm_objects:
                raise _LimitExceeded

        def node(self, node: object) -> None:
            self._count()
            self.node_count += 1
            longitude = float(node.location.lon)  # type: ignore[attr-defined]
            latitude = float(node.location.lat)  # type: ignore[attr-defined]
            self.west = min(self.west, longitude)
            self.east = max(self.east, longitude)
            self.south = min(self.south, latitude)
            self.north = max(self.north, latitude)

        def way(self, way: object) -> None:
            self._count()
            self.way_count += 1
            if any(not node.location.valid() for node in way.nodes):  # type: ignore[attr-defined]
                raise ValueError("PBF way references a node without a valid location")

        def relation(self, _relation: object) -> None:
            self._count()

    declared_bounds: BoundingBox | None = None
    try:
        with osmium.io.Reader(str(path)) as reader:
            header_box = reader.header().box()
            if header_box.valid():
                declared_bounds = BoundingBox(
                    west=float(header_box.bottom_left.lon),
                    south=float(header_box.bottom_left.lat),
                    east=float(header_box.top_right.lon),
                    north=float(header_box.top_right.lat),
                )
                _validate_declared_bounds(declared_bounds)
    except (OSError, RuntimeError, ValueError) as error:
        raise _invalid(f"cannot read OSM PBF header: {error}") from error

    handler = _Handler()
    try:
        handler.apply_file(str(path), locations=True)
    except _LimitExceeded as error:
        raise OsmManagerError(
            ErrorCode.RESPONSE_LIMIT_EXCEEDED,
            "OSM data exceeds maximum_osm_objects",
            {"object_count": handler.object_count, "limit": config.maximum_osm_objects},
        ) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise _invalid(f"cannot validate OSM PBF: {error}") from error
    inferred_bounds = (
        BoundingBox(handler.west, handler.south, handler.east, handler.north)
        if handler.node_count
        else None
    )
    return OsmValidationResult(
        object_count=handler.object_count,
        node_count=handler.node_count,
        way_count=handler.way_count,
        osm_base_timestamp=None,
        bounds=declared_bounds or inferred_bounds,
        bounds_are_declared=declared_bounds is not None,
    )


def validate_osm_file(path: Path, config: OsmManagerConfig) -> OsmValidationResult:
    """Dispatch supported raw OSM formats."""
    lower = path.name.casefold()
    if lower.endswith(".osm.pbf") or lower.endswith(".pbf"):
        return validate_osm_pbf(path, config)
    if lower.endswith(".osm") or lower.endswith(".xml") or lower.endswith(".osm.gz"):
        return validate_osm_xml(path, config)
    raise _invalid("supported OSM formats are .osm, .osm.gz, and .osm.pbf")


def _required_attribute(element: ElementTree.Element, name: str) -> str:
    value = element.get(name)
    if value is None:
        raise ValueError(f"<{_local_name(element.tag)}> is missing {name}")
    return value


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _validate_declared_bounds(bounds: BoundingBox) -> None:
    if not all(-180.0 <= value <= 180.0 for value in (bounds.west, bounds.east)):
        raise ValueError("declared longitude bounds are outside WGS84")
    if not all(-90.0 <= value <= 90.0 for value in (bounds.south, bounds.north)):
        raise ValueError("declared latitude bounds are outside WGS84")
    if bounds.west >= bounds.east or bounds.south >= bounds.north:
        raise ValueError("declared OSM bounds must have increasing coordinates")


def _invalid(message: str, *, details: dict[str, object] | None = None) -> OsmManagerError:
    return OsmManagerError(ErrorCode.OSM_DATA_INVALID, message, details)
