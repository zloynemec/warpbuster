"""Shared deterministic OSM Manager test fixtures."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import osmium
import pytest

from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.models import BoundingBox
from warpbuster_osm_manager.osm_validation import validate_osm_xml
from warpbuster_osm_manager.overpass import DownloadedOsm


def write_gpx(
    path: Path,
    lines: list[list[tuple[float, float]]],
    *,
    route: bool = False,
    version: str = "1.1",
) -> Path:
    """Write a small namespaced GPX track or route."""
    namespace = f"http://www.topografix.com/GPX/{version}"
    if route:
        body = (
            "<rte>"
            + "".join(f'<rtept lat="{lat}" lon="{lon}" />' for lon, lat in lines[0])
            + "</rte>"
        )
    else:
        body = (
            "<trk>"
            + "".join(
                "<trkseg>"
                + "".join(f'<trkpt lat="{lat}" lon="{lon}" />' for lon, lat in line)
                + "</trkseg>"
                for line in lines
            )
            + "</trk>"
        )
    path.write_text(
        f'<?xml version="1.0"?><gpx xmlns="{namespace}" version="{version}">{body}</gpx>',
        encoding="utf-8",
    )
    return path


def osm_xml(bounds: BoundingBox, *, variant: int = 0) -> str:
    """Return reference-complete OSM XML inside the supplied bounds."""
    latitude = (bounds.south + bounds.north) / 2
    longitude = (bounds.west + bounds.east) / 2
    delta = 0.0001 + variant * 0.000001
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<osm version="0.6" generator="tests">'
        '<meta osm_base="2026-09-02T00:00:00Z" />'
        f'<bounds minlat="{bounds.south}" minlon="{bounds.west}" '
        f'maxlat="{bounds.north}" maxlon="{bounds.east}" />'
        f'<node id="{variant * 10 + 1}" lat="{latitude}" lon="{longitude}" version="1" />'
        f'<node id="{variant * 10 + 2}" lat="{latitude + delta}" '
        f'lon="{longitude + delta}" version="1" />'
        f'<way id="{variant + 100}" version="1">'
        f'<nd ref="{variant * 10 + 1}"/><nd ref="{variant * 10 + 2}"/>'
        '<tag k="highway" v="path"/></way></osm>'
    )


def write_pbf(path: Path, bounds: BoundingBox) -> Path:
    """Write a tiny reference-complete PBF with a declared header box."""
    header = osmium.io.Header()
    header.add_box(osmium.osm.Box(bounds.west, bounds.south, bounds.east, bounds.north))
    with osmium.SimpleWriter(path, header=header) as writer:
        writer.add_node(osmium.osm.mutable.Node(id=1, location=(33.6, 44.4)))
        writer.add_node(osmium.osm.mutable.Node(id=2, location=(33.7, 44.5)))
        writer.add_way(osmium.osm.mutable.Way(id=3, nodes=[1, 2], tags={"highway": "path"}))
    return path


class FakeOverpassClient:
    """Write deterministic valid responses while recording requested boxes."""

    def __init__(self, config: OsmManagerConfig) -> None:
        self.config = config
        self.calls: list[BoundingBox] = []
        self.variant = 0

    def fetch(self, bounds: BoundingBox, destination: Path) -> DownloadedOsm:
        self.calls.append(bounds)
        self.variant += 1
        destination.write_text(osm_xml(bounds, variant=self.variant), encoding="utf-8")
        validation = validate_osm_xml(destination, self.config)
        return DownloadedOsm(
            path=destination,
            size_bytes=destination.stat().st_size,
            validation=validation,
        )


@pytest.fixture
def manager_config(tmp_path: Path) -> OsmManagerConfig:
    """Return small fast bounds with an isolated cache."""
    return replace(
        OsmManagerConfig.defaults(),
        cache_directory=tmp_path / "cache",
        overpass_url="https://overpass.test/api/interpreter",
        maximum_retry_count=0,
        retry_backoff_seconds=0,
        retry_jitter_seconds=0,
        cache_lock_timeout_seconds=0.05,
        stale_lock_seconds=0.05,
        lock_poll_seconds=0.005,
    )
