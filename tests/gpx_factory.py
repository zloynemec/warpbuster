"""Synthetic GPX fixture generation for public tests."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from xml.etree import ElementTree

_GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"

type GpxPoint = tuple[float, float, str | None, float | None]


def write_gpx_activity(
    path: Path,
    segments: list[list[GpxPoint]],
    *,
    activity_type: str | None = "running",
) -> bytes:
    """Write a compact GPX 1.1 track with caller-supplied segments."""
    ElementTree.register_namespace("", _GPX_NAMESPACE)
    root = ElementTree.Element(
        _qualified("gpx"),
        {"version": "1.1", "creator": "WarpBuster tests"},
    )
    track = ElementTree.SubElement(root, _qualified("trk"))
    if activity_type is not None:
        ElementTree.SubElement(track, _qualified("type")).text = activity_type
    for points in segments:
        segment = ElementTree.SubElement(track, _qualified("trkseg"))
        for latitude, longitude, timestamp, elevation in points:
            point = ElementTree.SubElement(
                segment,
                _qualified("trkpt"),
                {"lat": str(latitude), "lon": str(longitude)},
            )
            if elevation is not None:
                ElementTree.SubElement(point, _qualified("ele")).text = str(elevation)
            if timestamp is not None:
                ElementTree.SubElement(point, _qualified("time")).text = timestamp
    raw_bytes = cast(bytes, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))
    path.write_bytes(raw_bytes)
    return raw_bytes


def _qualified(name: str) -> str:
    return f"{{{_GPX_NAMESPACE}}}{name}"
