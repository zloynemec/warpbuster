"""Real offline Valhalla alternatives, never a mocked pathfinding replacement."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest
import valhalla

from tests.helpers import forked_osm, make_manifest
from warpbuster_osm_routing.alternatives import geometry_weights
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.coverage import parse_coverage
from warpbuster_osm_routing.geometry import haversine_m
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest, RouteRequest
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, apply_profile
from warpbuster_osm_routing.route_service import RouteService
from warpbuster_osm_routing.snapping import audit_snap

START = GeoPoint(44.0, 33.0005)
END = GeoPoint(44.0, 33.0095)


@pytest.mark.integration
def test_native_alternatives_probe_with_partial_edges(tmp_path: Path) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(make_manifest(tmp_path, [forked_osm()]))
    actor = valhalla.Actor(json.loads((graph.manifest_path.parent / "valhalla.json").read_text()))
    coverage = parse_coverage(graph.document["source"]["coverage"])
    start = audit_snap(actor, START, coverage, config)
    end = audit_snap(actor, END, coverage, config)
    assert start.status == end.status == "ACCEPTED"
    assert start.selected is not None and end.selected is not None
    request = {
        "locations": [START.as_valhalla(), END.as_valhalla()],
        "alternates": 2,
        "units": "kilometers",
        "directions_type": "none",
    }
    apply_profile(request, TRAIL_RUNNING_V1)
    response = json.loads(actor.route(json.dumps(request)))
    trips = [response["trip"], *(item["trip"] for item in response.get("alternates", []))]
    assert len(trips) >= 2
    audited = [
        RouteService(config)._audit_trip(actor, trip, start.selected, end.selected)
        for trip in trips
    ]
    for route in audited:
        edges = route["edges"]
        assert edges[0]["begin_shape_index"] == 0
        assert edges[-1]["end_shape_index"] == route["geometry"]["point_count"] - 1
        assert all(a["end_shape_index"] == b["begin_shape_index"] for a, b in pairwise(edges))
        assert edges[0]["way_id"] == 101
        assert edges[-1]["way_id"] == 104
    assert {102, 103} <= {e["way_id"] for r in audited for e in r["edges"]}


@pytest.mark.integration
def test_production_alternatives_repeatable_and_partial_weights(tmp_path: Path) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(make_manifest(tmp_path, [forked_osm()]))
    service = RouteService(config)
    request = RouteAlternativesRequest(graph.graph_id, START, END, 2)
    result = service.alternatives(request)
    assert result.status == "READY"
    assert result.exit_code == 0
    assert result.as_dict() == service.alternatives(request).as_dict()
    document = result.as_dict()
    assert document["operation"] == "route_alternatives"
    assert document["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    assert document["search"]["exhaustive"] is False
    assert len(result.candidates) == len(document["routes"]) >= 2
    pair = document["comparisons"][0]
    shared = haversine_m(START, GeoPoint(44, 33.002)) + haversine_m(GeoPoint(44, 33.008), END)
    assert pair["shared_edge_weight_m"] == pytest.approx(shared, abs=0.01)
    assert 0 < pair["overlap_a"] < 1
    for candidate in result.candidates:
        assert candidate.coordinates[0] == START
        assert candidate.coordinates[-1] == END
        mutable = candidate.as_dict()
        mutable["edges"].clear()
        assert candidate.as_dict()["edges"]
    document["routes"].clear()
    assert result.as_dict()["routes"]
    reverse = service.alternatives(RouteAlternativesRequest(graph.graph_id, END, START, 2))
    forward_edges = {e for r in result.candidates for e in geometry_weights(r.as_dict()).weights}
    reverse_edges = {e for r in reverse.candidates for e in geometry_weights(r.as_dict()).weights}
    assert not forward_edges & reverse_edges
    # Neither the graph nor the legacy single-route document depends on new policy.
    other = replace(config, minimum_diversity_ratio=0.5, maximum_requested_alternates=1)
    cached = GraphCache(other).prepare(make_manifest(tmp_path, [forked_osm()]))
    assert cached.status == "CACHED"
    assert cached.graph_id == graph.graph_id
    old = RouteRequest(graph.graph_id, START, END)
    assert RouteService(other).route(old).as_dict() == service.route(old).as_dict()


@pytest.mark.integration
@pytest.mark.parametrize(
    "tags",
    [
        '<tag k="foot" v="no"/>',
        '<tag k="sac_scale" v="alpine_hiking"/>',
        '<tag k="impassable" v="yes"/>',
    ],
)
def test_forbidden_bypass_not_used_for_alternatives(tmp_path: Path, tags: str) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(make_manifest(tmp_path, [forked_osm(tags)]))
    result = RouteService(config).alternatives(
        RouteAlternativesRequest(graph.graph_id, START, END, 2)
    )
    assert result.status == "READY"
    assert result.document["route_choice"]["status"] == "SINGLE_CANDIDATE"
    assert result.document["search"]["reasons"] == ["NO_ALTERNATIVES_RETURNED"]
    assert 103 not in {e["way_id"] for r in result.candidates for e in r.as_dict()["edges"]}


@pytest.mark.integration
def test_real_cli_json_and_actor_call_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(make_manifest(tmp_path, [forked_osm()]))
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "warpbuster_osm_routing",
            "route",
            graph.graph_id,
            "--from",
            "44,33.0005",
            "--to",
            "44,33.0095",
            "--alternates",
            "2",
            "--cache-dir",
            str(config.cache_directory),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    document = json.loads(completed.stdout)
    assert document["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    native_actor = valhalla.Actor
    calls = {"locate": 0, "route": 0, "trace_attributes": 0}

    class CountedActor:
        def __init__(self, actor_config: dict) -> None:
            self.actor = native_actor(actor_config)

        def locate(self, request: str) -> str:
            calls["locate"] += 1
            return self.actor.locate(request)

        def route(self, request: str) -> str:
            calls["route"] += 1
            return self.actor.route(request)

        def trace_attributes(self, request: str) -> str:
            calls["trace_attributes"] += 1
            return self.actor.trace_attributes(request)

    monkeypatch.setattr(valhalla, "Actor", CountedActor)
    result = RouteService(config).alternatives(
        RouteAlternativesRequest(graph.graph_id, START, END, 2)
    )
    assert calls == {
        "locate": 2,
        "route": 1,
        "trace_attributes": result.document["search"]["engine_returned_routes"],
    }


@pytest.mark.integration
@pytest.mark.parametrize(
    ("offset", "start", "end", "status"),
    [
        (0.002, GeoPoint(44, 33.001), GeoPoint(44.002, 33.001), "NO_ROUTE"),
        (0.00005, GeoPoint(44.000025, 33.001), GeoPoint(44.000025, 33.002), "AMBIGUOUS_SNAP"),
        (0.002, GeoPoint(45, 34), GeoPoint(44, 33.001), "OUTSIDE_COVERAGE"),
        (0.002, GeoPoint(44.005, 33.001), GeoPoint(44, 33.001), "NO_SNAP"),
    ],
)
def test_real_negative_outcomes(
    tmp_path: Path, offset: float, start: GeoPoint, end: GeoPoint, status: str
) -> None:
    osm = f'''<osm version="0.6">
<node id="1" lat="44" lon="33" version="1"/><node id="2" lat="44" lon="33.003" version="1"/>
<node id="3" lat="{44 + offset}" lon="33" version="1"/>
<node id="4" lat="{44 + offset}" lon="33.003" version="1"/>
<way id="101" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>
<way id="102" version="1"><nd ref="3"/><nd ref="4"/><tag k="highway" v="path"/></way>
</osm>'''.encode()
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(make_manifest(tmp_path, [osm]))
    result = RouteService(config).alternatives(
        RouteAlternativesRequest(graph.graph_id, start, end, 2)
    )
    assert result.status == status
    assert result.exit_code == 1
    assert result.candidates == ()
