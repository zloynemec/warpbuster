"""Read GPX tracks into the vendor-neutral activity model."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from warpbuster.models.activity import (
    ActivityData,
    ActivityRecord,
    CoordinateBounds,
    GpxPreservationData,
    SourceRecordRef,
)

_FORBIDDEN_XML_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")
_RUNNING_TYPES = frozenset({"run", "running"})
_TRAIL_RUNNING_TYPES = frozenset({"trail run", "trail running"})


class GpxReadError(ValueError):
    """Raised when an input cannot be decoded as a supported GPX activity."""


def read_gpx(path: str | Path) -> ActivityData:
    """Parse standard GPX tracks without interpreting them as a reference course."""
    source_path = Path(path)
    raw_bytes = source_path.read_bytes()
    _reject_unsafe_xml(source_path, raw_bytes)
    try:
        root = ElementTree.fromstring(raw_bytes)
    except ElementTree.ParseError as error:
        raise GpxReadError(f"cannot decode GPX file {source_path}: {error}") from error
    if _local_name(root.tag) != "gpx":
        raise GpxReadError(f"cannot decode GPX file {source_path}: root element is not gpx")

    tracks = tuple(_children(root, "trk"))
    records: list[ActivityRecord] = []
    segment_count = 0
    for track in tracks:
        for segment in _children(track, "trkseg"):
            continuity_id = segment_count
            segment_count += 1
            for point in _children(segment, "trkpt"):
                records.append(_record(len(records), continuity_id, point, source_path))
    if not records:
        raise GpxReadError(f"cannot decode GPX file {source_path}: no track points")

    normalized_records = tuple(records)
    timestamps = tuple(
        record.timestamp for record in normalized_records if record.timestamp is not None
    )
    sport, sub_sport = _activity_type(tracks)
    return ActivityData(
        records=normalized_records,
        laps=(),
        sessions=(),
        events=(),
        manufacturer=None,
        product=None,
        sport=sport,
        sub_sport=sub_sport,
        start_time=min(timestamps) if timestamps else None,
        duration_seconds=(
            (max(timestamps) - min(timestamps)).total_seconds()
            if len(timestamps) >= 2
            else (0.0 if timestamps else None)
        ),
        recorded_distance_m=None,
        coordinate_bounds=_coordinate_bounds(normalized_records),
        available_fields=_available_fields(normalized_records),
        message_counts=MappingProxyType(
            {
                "track": len(tracks),
                "track_point": len(normalized_records),
                "track_segment": segment_count,
            }
        ),
        developer_fields=(),
        unknown_fields=(),
        preservation=GpxPreservationData(
            source_path=source_path,
            raw_bytes=raw_bytes,
            version=_attribute(root, "version"),
            creator=_attribute(root, "creator"),
            track_count=len(tracks),
            segment_count=segment_count,
        ),
    )


def _reject_unsafe_xml(source_path: Path, raw_bytes: bytes) -> None:
    upper_bytes = raw_bytes.upper()
    if any(declaration in upper_bytes for declaration in _FORBIDDEN_XML_DECLARATIONS):
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: DTD and entity declarations are not supported"
        )


def _record(index: int, continuity_id: int, point: Element, source_path: Path) -> ActivityRecord:
    latitude = _coordinate(point, "lat", -90.0, 90.0, source_path)
    longitude = _coordinate(point, "lon", -180.0, 180.0, source_path)
    return ActivityRecord(
        index=index,
        timestamp=_timestamp(_child_text(point, "time"), source_path, index),
        latitude=latitude,
        longitude=longitude,
        altitude=_optional_float(_child_text(point, "ele"), "elevation", source_path, index),
        distance=None,
        speed=None,
        heart_rate=None,
        cadence=None,
        power=None,
        temperature=None,
        source=SourceRecordRef(message_index=index, occurrence_index=index),
        continuity_id=continuity_id,
    )


def _coordinate(
    point: Element,
    name: str,
    minimum: float,
    maximum: float,
    source_path: Path,
) -> float:
    raw_value = point.attrib.get(name)
    if raw_value is None:
        raise GpxReadError(f"cannot decode GPX file {source_path}: track point has no {name}")
    try:
        value = float(raw_value)
    except ValueError as error:
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: invalid {name} {raw_value!r}"
        ) from error
    if not isfinite(value) or not minimum <= value <= maximum:
        raise GpxReadError(f"cannot decode GPX file {source_path}: {name} out of range")
    return value


def _timestamp(value: str | None, source_path: Path, record_index: int) -> datetime | None:
    if value is None:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: invalid time at track point {record_index}"
        ) from error
    if timestamp.utcoffset() is None:
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: time has no timezone at track point "
            f"{record_index}"
        )
    return timestamp


def _optional_float(
    value: str | None,
    field_name: str,
    source_path: Path,
    record_index: int,
) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: invalid {field_name} at track point "
            f"{record_index}"
        ) from error
    if not isfinite(number):
        raise GpxReadError(
            f"cannot decode GPX file {source_path}: non-finite {field_name} at track point "
            f"{record_index}"
        )
    return number


def _activity_type(tracks: tuple[Element, ...]) -> tuple[str | None, str | None]:
    raw_types = tuple(_child_text(track, "type") for track in tracks)
    if not raw_types or any(value is None for value in raw_types):
        return None, None
    normalized_types = tuple(_normalize_type(value) for value in raw_types if value is not None)
    if any(value is None for value in normalized_types):
        return None, None
    resolved = {value for value in normalized_types if value is not None}
    return resolved.pop() if len(resolved) == 1 else (None, None)


def _normalize_type(value: str) -> tuple[str, str | None] | None:
    normalized = " ".join(value.casefold().replace("_", " ").replace("-", " ").split())
    if normalized in _RUNNING_TYPES:
        return "running", None
    if normalized in _TRAIL_RUNNING_TYPES:
        return "running", "trail"
    return None


def _coordinate_bounds(records: tuple[ActivityRecord, ...]) -> CoordinateBounds:
    latitudes = [record.latitude for record in records if record.latitude is not None]
    longitudes = [record.longitude for record in records if record.longitude is not None]
    return CoordinateBounds(
        min_latitude=min(latitudes),
        max_latitude=max(latitudes),
        min_longitude=min(longitudes),
        max_longitude=max(longitudes),
    )


def _available_fields(records: tuple[ActivityRecord, ...]) -> frozenset[str]:
    fields = {"position"}
    if any(record.timestamp is not None for record in records):
        fields.add("timestamp")
    if any(record.altitude is not None for record in records):
        fields.add("altitude")
    return frozenset(fields)


def _children(element: Element, name: str) -> Iterator[Element]:
    return (child for child in element if _local_name(child.tag) == name)


def _child_text(element: Element, name: str) -> str | None:
    child = next(_children(element, name), None)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _attribute(element: Element, name: str) -> str | None:
    value = element.attrib.get(name)
    return value.strip() if value is not None and value.strip() else None


def _local_name(tag: str) -> str:
    return tag.rpartition("}")[2]
