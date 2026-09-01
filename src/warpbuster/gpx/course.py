"""Read GPX reference geometry without treating it as an activity."""

from __future__ import annotations

from collections.abc import Iterator
from math import isfinite
from pathlib import Path
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from warpbuster.geo import geodesic_distance_m
from warpbuster.models.reconstruction import CourseData, CoursePoint, CourseSegment

_FORBIDDEN_XML_DECLARATIONS = (b"<!DOCTYPE", b"<!ENTITY")


class GpxCourseReadError(ValueError):
    """Raised when a GPX file cannot provide a usable reference polyline."""


def read_gpx_course(path: str | Path) -> CourseData:
    """Parse GPX tracks and routes as independent continuous course segments."""
    source_path = Path(path)
    raw_bytes = source_path.read_bytes()
    _reject_unsafe_xml(source_path, raw_bytes)
    try:
        root = ElementTree.fromstring(raw_bytes)
    except ElementTree.ParseError as error:
        raise GpxCourseReadError(f"cannot decode GPX course {source_path}: {error}") from error
    if _local_name(root.tag) != "gpx":
        raise GpxCourseReadError(f"cannot decode GPX course {source_path}: root element is not gpx")

    raw_segments: list[tuple[Element, ...]] = []
    for track in _children(root, "trk"):
        raw_segments.extend(
            tuple(_children(segment, "trkpt")) for segment in _children(track, "trkseg")
        )
    raw_segments.extend(tuple(_children(route, "rtept")) for route in _children(root, "rte"))

    segments: list[CourseSegment] = []
    global_point_index = 0
    for raw_points in raw_segments:
        if len(raw_points) < 2:
            continue
        segment, global_point_index = _segment(
            len(segments),
            global_point_index,
            raw_points,
            source_path,
        )
        segments.append(segment)
    if not segments:
        raise GpxCourseReadError(
            f"cannot decode GPX course {source_path}: no segment with at least two points"
        )

    return CourseData(
        source_path=source_path,
        raw_bytes=raw_bytes,
        version=_attribute(root, "version"),
        creator=_attribute(root, "creator"),
        segments=tuple(segments),
        point_count=sum(len(segment.points) for segment in segments),
        total_distance_m=sum(segment.length_m for segment in segments),
    )


def _segment(
    segment_index: int,
    global_point_index: int,
    raw_points: tuple[Element, ...],
    source_path: Path,
) -> tuple[CourseSegment, int]:
    points: list[CoursePoint] = []
    cumulative_distance_m = 0.0
    previous: tuple[float, float] | None = None
    for point_index, element in enumerate(raw_points):
        latitude = _coordinate(element, "lat", -90.0, 90.0, source_path)
        longitude = _coordinate(element, "lon", -180.0, 180.0, source_path)
        if previous is not None:
            cumulative_distance_m += geodesic_distance_m(
                previous[0],
                previous[1],
                latitude,
                longitude,
            )
        points.append(
            CoursePoint(
                index=global_point_index,
                segment_index=segment_index,
                point_index=point_index,
                latitude=latitude,
                longitude=longitude,
                elevation_m=_optional_float(_child_text(element, "ele"), source_path),
                cumulative_distance_m=cumulative_distance_m,
            )
        )
        global_point_index += 1
        previous = latitude, longitude
    return (
        CourseSegment(
            index=segment_index,
            points=tuple(points),
            length_m=cumulative_distance_m,
        ),
        global_point_index,
    )


def _reject_unsafe_xml(source_path: Path, raw_bytes: bytes) -> None:
    upper_bytes = raw_bytes.upper()
    if any(declaration in upper_bytes for declaration in _FORBIDDEN_XML_DECLARATIONS):
        raise GpxCourseReadError(
            f"cannot decode GPX course {source_path}: DTD and entity declarations are not supported"
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
        raise GpxCourseReadError(f"cannot decode GPX course {source_path}: point has no {name}")
    try:
        value = float(raw_value)
    except ValueError as error:
        raise GpxCourseReadError(
            f"cannot decode GPX course {source_path}: invalid {name} {raw_value!r}"
        ) from error
    if not isfinite(value) or not minimum <= value <= maximum:
        raise GpxCourseReadError(f"cannot decode GPX course {source_path}: {name} out of range")
    return value


def _optional_float(value: str | None, source_path: Path) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise GpxCourseReadError(
            f"cannot decode GPX course {source_path}: invalid elevation"
        ) from error
    if not isfinite(number):
        raise GpxCourseReadError(f"cannot decode GPX course {source_path}: non-finite elevation")
    return number


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
