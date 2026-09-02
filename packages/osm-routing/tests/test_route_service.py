from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import make_manifest
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import GeoPoint, GraphResult, RouteRequest
from warpbuster_osm_routing.route_service import RouteService


@pytest.mark.integration
def test_audited_route_is_ready(snapshot_manifest: Path, tmp_path: Path) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(snapshot_manifest)

    result = RouteService(config).route(
        RouteRequest(
            graph.graph_id,
            GeoPoint(44.0, 33.0004),
            GeoPoint(44.0016, 33.002),
        )
    )

    assert result.status == "READY"
    assert result.document["route"]["summary"]["length_m"] > 0
    assert result.document["route"]["audit"]["status"] == "PASS"
    assert result.document["route"]["edges"]
    assert result.document["profile"]["profile_id"] == "warpbuster-trail-running-v1"


@pytest.mark.integration
def test_outside_coverage_is_domain_outcome(snapshot_manifest: Path, tmp_path: Path) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(snapshot_manifest)

    result = RouteService(config).route(
        RouteRequest(graph.graph_id, GeoPoint(45.0, 34.0), GeoPoint(44.0, 33.0))
    )

    assert result.status == "OUTSIDE_COVERAGE"
    assert result.exit_code == 1
    assert result.document["route"] is None


@pytest.mark.integration
def test_disconnected_components_are_no_route(tmp_path: Path) -> None:
    osm = b"""<osm version="0.6">
<node id="1" lat="44.0000" lon="33.0000" version="1"/>
<node id="2" lat="44.0000" lon="33.0010" version="1"/>
<node id="3" lat="44.0020" lon="33.0000" version="1"/>
<node id="4" lat="44.0020" lon="33.0010" version="1"/>
<way id="101" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>
<way id="102" version="1"><nd ref="3"/><nd ref="4"/><tag k="highway" v="path"/></way>
</osm>"""
    manifest = make_manifest(tmp_path, [osm])
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(manifest)

    result = RouteService(config).route(
        RouteRequest(
            graph.graph_id,
            GeoPoint(44.0, 33.0005),
            GeoPoint(44.002, 33.0005),
        )
    )

    assert result.status == "NO_ROUTE"
    assert result.document["route"] is None


@pytest.mark.integration
def test_inside_coverage_but_beyond_30_metres_is_no_snap(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(snapshot_manifest)

    result = RouteService(config).route(
        RouteRequest(graph.graph_id, GeoPoint(44.0025, 33.002), GeoPoint(44.0, 33.0004))
    )

    assert result.status == "NO_SNAP"
    assert result.document["snapping"]["start"]["status"] == "NO_SNAP"


def test_legacy_graph_is_not_route_capable(tmp_path: Path) -> None:
    class LegacyCache:
        def inspect(self, graph_id: str) -> GraphResult:
            return GraphResult(
                "LEGACY_READY",
                graph_id,
                tmp_path / "manifest.json",
                {"manifest_version": 1},
            )

    service = RouteService(
        RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    )
    service.cache = LegacyCache()  # type: ignore[assignment]

    with pytest.raises(RoutingError) as caught:
        service.route(
            RouteRequest(
                "sha256:" + "0" * 64,
                GeoPoint(44.0, 33.0),
                GeoPoint(44.001, 33.001),
            )
        )

    assert caught.value.code == "GRAPH_CAPABILITY_MISSING"


@pytest.mark.integration
def test_ferry_only_route_is_ready_with_warning(tmp_path: Path) -> None:
    osm = b"""<osm version="0.6">
<node id="1" lat="44.0000" lon="33.0000" version="1"/>
<node id="2" lat="44.0000" lon="33.0020" version="1"/>
<way id="101" version="1"><nd ref="1"/><nd ref="2"/>
<tag k="route" v="ferry"/><tag k="foot" v="yes"/></way>
</osm>"""
    manifest = make_manifest(tmp_path, [osm])
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(config).prepare(manifest)

    result = RouteService(config).route(
        RouteRequest(
            graph.graph_id,
            GeoPoint(44.0, 33.0001),
            GeoPoint(44.0, 33.0019),
        )
    )

    assert result.status == "READY"
    assert result.document["route"]["audit"]["status"] == "WARN"
    assert {"code": "FERRY_USED"} in result.document["route"]["warnings"]
