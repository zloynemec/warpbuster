"""Real offline Valhalla graph -> Core candidate-only reconstruction contract."""

from __future__ import annotations

import hashlib
import json
from math import cos, radians
from pathlib import Path

import pytest

from tests.fit_factory import write_trajectory_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.osm import OSMReconstructionProvider, ValhallaRoutingClient
from warpbuster.report.html import write_repair_html
from warpbuster.report.osm import osm_reconstruction_report

pytestmark = pytest.mark.integration


def _forked_osm() -> bytes:
    """Return two route choices with common stems around the FIT gap anchors."""
    return b"""<osm version="0.6">
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
<tag k="highway" v="path"/></way>
<way id="104" version="1"><nd ref="4"/><nd ref="5"/>
<tag k="highway" v="path"/></way>
</osm>"""


def _manifest(tmp_path: Path, source: bytes) -> Path:
    source_path = tmp_path / "source.osm"
    source_path.write_bytes(source)
    document = {
        "protocol_version": 1,
        "manifest_version": 1,
        "manager_version": "test",
        "snapshot_id": "sha256:native-core-test",
        "dataset_profile": "pedestrian-routing-v1",
        "osm_base_timestamp": "2026-09-04T00:00:00Z",
        "attribution": "OpenStreetMap contributors",
        "copyright_url": "https://www.openstreetmap.org/copyright",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "coverage": {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000.0,
            "area_km2": 1.0,
        },
        "data_files": [
            {
                "path": str(source_path.resolve()),
                "media_type": "application/vnd.openstreetmap.data+xml",
                "sha256": hashlib.sha256(source).hexdigest(),
                "size_bytes": len(source),
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_native_valhalla_candidates_are_advisory_and_repeatable(tmp_path: Path) -> None:
    routing = pytest.importorskip("warpbuster_osm_routing")
    metres_per_longitude_degree = 111_195.0 * cos(radians(44.0))
    observations = [
        (float(index), 44.0, 33.0 + index * 2.0 / metres_per_longitude_degree)
        for index in range(401)
    ]
    observations = [
        (elapsed, None, None) if 21 <= index <= 379 else (elapsed, latitude, longitude)
        for index, (elapsed, latitude, longitude) in enumerate(observations)
    ]
    fit_path = tmp_path / "activity.fit"
    write_trajectory_activity(
        fit_path,
        observations,
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
        altitudes_m=[100.0] * 401,
    )
    activity = read_fit(fit_path)
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(activity, integrity)
    cache_directory = tmp_path / "cache"
    routing_config = routing.RoutingCacheConfig.defaults().with_cache_directory(cache_directory)
    graph = routing.GraphCache(routing_config).prepare(_manifest(tmp_path, _forked_osm()))

    provider = OSMReconstructionProvider(ValhallaRoutingClient(cache_directory=cache_directory))
    first = provider.discover(activity, plan, graph.graph_id)
    second = provider.discover(activity, plan, graph.graph_id)
    first_report = osm_reconstruction_report(first)

    assert first_report == osm_reconstruction_report(second)
    assert first.candidate_count >= 2
    assert first_report["application_allowed"] is False
    assert first_report["graph"]["graph_id"] == graph.graph_id
    assert first_report["graph"]["snapshot_id"] == "sha256:native-core-test"
    assert first_report["profile"]["profile_id"] == "warpbuster-trail-running-v1"
    candidates = first_report["gap_evaluations"][0]["candidates"]
    assert {candidate["role"] for candidate in candidates} >= {"primary", "alternative"}
    assert all(candidate["allocated_to_records"] is False for candidate in candidates)
    assert plan.interval_plans == ()
    assert not fit_path.with_suffix(".fixed.fit").exists()
    html_path = tmp_path / "osm-dry-run.html"
    write_repair_html(
        activity,
        integrity,
        None,
        plan,
        CourseReconstructionConfig(),
        html_path,
        osm_result=first,
    )
    html = html_path.read_text(encoding="utf-8")
    assert "OSM reconstruction candidates" in html
    assert "OSM primary candidates" in html and "OSM alternative candidates" in html
