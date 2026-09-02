"""Path-independent graph key tests."""

from __future__ import annotations

from pathlib import Path

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.identity import graph_cache_key
from warpbuster_osm_routing.manifest import load_snapshot

from .helpers import make_manifest


def test_graph_id_is_independent_of_source_and_cache_paths(tmp_path: Path) -> None:
    source = b'<osm version="0.6"><node id="1" version="1" lat="44" lon="33"/></osm>'
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    config = RoutingCacheConfig.defaults()

    _, first_digest = graph_cache_key(load_snapshot(make_manifest(first, [source]), config))
    _, second_digest = graph_cache_key(load_snapshot(make_manifest(second, [source]), config))

    assert first_digest == second_digest
