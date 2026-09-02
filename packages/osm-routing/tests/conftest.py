"""Deterministic fixtures for the isolated routing spike."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def osm_xml() -> bytes:
    """A small connected pedestrian square with two possible paths."""
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="warpbuster-test">
  <bounds minlat="44.0000" minlon="33.0000" maxlat="44.0020" maxlon="33.0020"/>
  <node id="1" version="1" lat="44.0000" lon="33.0000"/>
  <node id="2" version="1" lat="44.0000" lon="33.0010"/>
  <node id="3" version="1" lat="44.0000" lon="33.0020"/>
  <node id="4" version="1" lat="44.0010" lon="33.0020"/>
  <node id="5" version="1" lat="44.0020" lon="33.0020"/>
  <node id="6" version="1" lat="44.0020" lon="33.0010"/>
  <node id="7" version="1" lat="44.0020" lon="33.0000"/>
  <node id="8" version="1" lat="44.0010" lon="33.0000"/>
  <node id="9" version="1" lat="44.0010" lon="33.0010"/>
  <way id="101" version="1">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="5"/>
    <tag k="highway" v="path"/><tag k="surface" v="ground"/>
  </way>
  <way id="102" version="1">
    <nd ref="1"/><nd ref="8"/><nd ref="7"/><nd ref="6"/><nd ref="5"/>
    <tag k="highway" v="path"/><tag k="surface" v="gravel"/>
  </way>
  <way id="103" version="1">
    <nd ref="2"/><nd ref="9"/><nd ref="6"/>
    <tag k="highway" v="footway"/>
  </way>
</osm>
"""


@pytest.fixture
def snapshot_manifest(tmp_path: Path, osm_xml: bytes) -> Path:
    data = tmp_path / "source.osm"
    data.write_bytes(osm_xml)
    digest = hashlib.sha256(osm_xml).hexdigest()
    manifest = {
        "protocol_version": 1,
        "manifest_version": 1,
        "manager_version": "test",
        "snapshot_id": f"sha256:{digest}",
        "dataset_profile": "pedestrian-routing-v1",
        "osm_base_timestamp": None,
        "coverage": {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000.0,
            "area_km2": 1.0,
        },
        "data_files": [
            {
                "path": str(data.resolve()),
                "media_type": "application/vnd.openstreetmap.data+xml",
                "sha256": digest,
                "size_bytes": len(osm_xml),
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path
