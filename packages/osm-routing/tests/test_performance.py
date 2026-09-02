"""Bounded external-storage normalization performance regression."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.normalize import normalize_snapshot

from .helpers import make_manifest


@pytest.mark.performance
def test_twenty_thousand_objects_normalize_without_quadratic_scan(tmp_path: Path) -> None:
    nodes = "".join(
        f'<node id="{index}" version="1" lat="44" lon="33"/>' for index in range(1, 20_001)
    )
    source = f'<osm version="0.6">{nodes}</osm>'.encode()
    manifest = make_manifest(tmp_path, [source])
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")

    started = time.monotonic()
    result = normalize_snapshot(
        load_snapshot(manifest, config),
        tmp_path / "large.pbf",
        tmp_path / "large.sqlite",
        config,
    )
    elapsed = time.monotonic() - started

    assert result.statistics.selected_nodes == 20_000
    assert elapsed < 5.0
