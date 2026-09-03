"""Shared Task 010B snapshot builders."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def forked_osm(extra_tags: str = "") -> bytes:
    """Two symmetric paths with shared stems and unambiguous partial-edge anchors."""
    return f"""<osm version="0.6">
<node id="1" lat="44.0" lon="33.0" version="1"/>
<node id="2" lat="44.0" lon="33.002" version="1"/>
<node id="3" lat="44.002" lon="33.005" version="1"/>
<node id="4" lat="44.0" lon="33.008" version="1"/>
<node id="5" lat="44.0" lon="33.01" version="1"/>
<node id="6" lat="43.998" lon="33.005" version="1"/>
<way id="101" version="1"><nd ref="1"/><nd ref="2"/>
<tag k="highway" v="path"/></way>
<way id="102" version="1"><nd ref="2"/><nd ref="3"/><nd ref="4"/>
<tag k="highway" v="path"/></way>
<way id="103" version="1"><nd ref="2"/><nd ref="6"/><nd ref="4"/>
<tag k="highway" v="path"/>{extra_tags}</way>
<way id="104" version="1"><nd ref="4"/><nd ref="5"/>
<tag k="highway" v="path"/></way>
</osm>""".encode()


def encode_polyline6(points: list[tuple[float, float]]) -> str:
    previous = [0, 0]
    output: list[str] = []
    for point in points:
        for axis, coordinate in enumerate(point):
            scaled = round(coordinate * 1_000_000)
            delta = scaled - previous[axis]
            previous[axis] = scaled
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
    return "".join(output)


def make_manifest(
    root: Path,
    sources: list[bytes],
    *,
    snapshot_id: str = "sha256:test-snapshot",
    extensions: list[str] | None = None,
    media_types: list[str] | None = None,
) -> Path:
    files = []
    for index, source in enumerate(sources):
        extension = extensions[index] if extensions else ".osm"
        path = root / f"source-{index}{extension}"
        path.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        files.append(
            {
                "path": str(path.resolve()),
                "media_type": media_types[index]
                if media_types
                else "application/vnd.openstreetmap.data+xml",
                "sha256": digest,
                "size_bytes": len(source),
            }
        )
    manifest = {
        "protocol_version": 1,
        "manifest_version": 1,
        "manager_version": "test",
        "snapshot_id": snapshot_id,
        "dataset_profile": "pedestrian-routing-v1",
        "osm_base_timestamp": "2026-08-31T00:00:00Z",
        "attribution": "OpenStreetMap contributors",
        "copyright_url": "https://www.openstreetmap.org/copyright",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "coverage": {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000.0,
            "area_km2": 1.0,
        },
        "data_files": files,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path
