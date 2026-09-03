"""Atomic graph cache tests for Task 010B."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.identity import graph_id_for_key


def _fake_builder(config_path: Path, _pbf: Path, _timeout: float | None) -> str:
    config = json.loads(config_path.read_text())
    tiles = Path(config["mjolnir"]["tile_dir"])
    (tiles / "2").mkdir(parents=True, exist_ok=True)
    (tiles / "2" / "000.tile").write_bytes(b"synthetic-valhalla-tile")
    return "synthetic build"


def _config(
    tmp_path: Path,
    *,
    lock_poll_seconds: float | None = None,
    cache_lock_timeout_seconds: float | None = None,
    stale_lock_seconds: float | None = None,
    prune_minimum_age_seconds: float | None = None,
) -> RoutingCacheConfig:
    base = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    return replace(
        base,
        lock_poll_seconds=lock_poll_seconds or base.lock_poll_seconds,
        cache_lock_timeout_seconds=cache_lock_timeout_seconds or base.cache_lock_timeout_seconds,
        stale_lock_seconds=stale_lock_seconds or base.stale_lock_seconds,
        prune_minimum_age_seconds=prune_minimum_age_seconds or base.prune_minimum_age_seconds,
    )


def test_first_prepare_is_ready_and_second_is_cached(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    cache = GraphCache(_config(tmp_path), tile_builder=_fake_builder)

    ready = cache.prepare(snapshot_manifest)

    assert ready.status == "READY"
    assert ready.manifest_path.is_file()

    def should_not_build(*_args: object) -> str:
        raise AssertionError("cache hit rebuilt Valhalla")

    cached = GraphCache(_config(tmp_path), tile_builder=should_not_build).prepare(snapshot_manifest)
    assert cached.status == "CACHED"
    assert cached.graph_id == ready.graph_id


@pytest.mark.parametrize("artifact", ["pbf", "config", "tile"])
def test_cache_corruption_requires_explicit_rebuild(
    artifact: str, snapshot_manifest: Path, tmp_path: Path
) -> None:
    cache = GraphCache(_config(tmp_path), tile_builder=_fake_builder)
    ready = cache.prepare(snapshot_manifest)
    if artifact == "tile":
        relative = ready.document["artifacts"]["tiles"]["files"][0]["path"]
        path = ready.manifest_path.parent / "tiles" / relative
    else:
        path = ready.manifest_path.parent / ready.document["artifacts"][artifact]["path"]
    path.write_bytes(b"corrupt")

    with pytest.raises(RoutingError) as caught:
        cache.prepare(snapshot_manifest)

    assert caught.value.code == "CACHE_CORRUPT"
    rebuilt = cache.prepare(snapshot_manifest, rebuild=True)
    assert rebuilt.status == "READY"


def test_failed_rebuild_preserves_existing_graph(snapshot_manifest: Path, tmp_path: Path) -> None:
    config = _config(tmp_path)
    ready = GraphCache(config, tile_builder=_fake_builder).prepare(snapshot_manifest)
    before = ready.manifest_path.read_bytes()

    def fail(*_args: object) -> str:
        raise RuntimeError("replacement failed")

    with pytest.raises(RoutingError):
        GraphCache(config, tile_builder=fail).prepare(snapshot_manifest, rebuild=True)

    assert ready.manifest_path.read_bytes() == before
    assert GraphCache(config, tile_builder=_fake_builder).inspect(ready.graph_id).status == "READY"


def test_build_failure_publishes_no_graph(snapshot_manifest: Path, tmp_path: Path) -> None:
    def fail(*_args: object) -> str:
        raise RuntimeError("builder exploded")

    cache = GraphCache(_config(tmp_path), tile_builder=fail)

    with pytest.raises(RoutingError) as caught:
        cache.prepare(snapshot_manifest)

    assert caught.value.code == "VALHALLA_BUILD_FAILED"
    assert list(cache.graphs.glob("*")) == []
    assert list(cache.staging.glob("*")) == []


def test_build_timeout_publishes_no_graph(snapshot_manifest: Path, tmp_path: Path) -> None:
    def timeout(*_args: object) -> str:
        raise RoutingError("BUILD_TIMEOUT", "synthetic timeout")

    cache = GraphCache(_config(tmp_path), tile_builder=timeout)

    with pytest.raises(RoutingError) as caught:
        cache.prepare(snapshot_manifest)

    assert caught.value.code == "BUILD_TIMEOUT"
    assert list(cache.graphs.glob("*")) == []


def test_concurrent_prepare_builds_once(snapshot_manifest: Path, tmp_path: Path) -> None:
    calls = 0

    def slow_builder(config_path: Path, pbf: Path, timeout: float | None) -> str:
        nonlocal calls
        calls += 1
        time.sleep(0.1)
        return _fake_builder(config_path, pbf, timeout)

    config = _config(tmp_path, lock_poll_seconds=0.01)

    def prepare() -> str:
        return GraphCache(config, tile_builder=slow_builder).prepare(snapshot_manifest).status

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: prepare(), range(2)))

    assert statuses == ["CACHED", "READY"]
    assert calls == 1


def test_lock_timeout_has_stale_diagnostics(snapshot_manifest: Path, tmp_path: Path) -> None:
    cache = GraphCache(
        _config(
            tmp_path,
            cache_lock_timeout_seconds=0.02,
            lock_poll_seconds=0.01,
            stale_lock_seconds=0.01,
        ),
        tile_builder=_fake_builder,
    )
    cache._ensure_directories()
    from warpbuster_osm_routing.identity import graph_cache_key
    from warpbuster_osm_routing.manifest import load_snapshot

    _, digest = graph_cache_key(load_snapshot(snapshot_manifest, cache.config))
    lock = cache.locks / f"{digest}.lock"
    lock.write_text("locked")
    old = time.time() - 1
    os.utime(lock, (old, old))

    with pytest.raises(RoutingError) as caught:
        cache.prepare(snapshot_manifest)

    assert caught.value.code == "LOCK_TIMEOUT"
    assert caught.value.details["stale"] is True


def test_list_inspect_remove_and_prune(snapshot_manifest: Path, tmp_path: Path) -> None:
    cache = GraphCache(
        _config(tmp_path, prune_minimum_age_seconds=0.01), tile_builder=_fake_builder
    )
    ready = cache.prepare(snapshot_manifest)

    assert cache.list_graphs()[0]["status"] == "READY"
    assert cache.inspect(ready.graph_id).graph_id == ready.graph_id
    old = time.time() - 1
    os.utime(ready.manifest_path.parent, (old, old))
    dry_run = cache.prune()
    assert dry_run["status"] == "DRY_RUN"
    assert ready.manifest_path.exists()
    applied = cache.prune(apply=True)
    assert applied["removed"] == [ready.graph_id]
    assert not ready.manifest_path.exists()


def test_legacy_v1_graph_is_inspectable_but_identified_as_legacy(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    cache = GraphCache(_config(tmp_path), tile_builder=_fake_builder)
    current = cache.prepare(snapshot_manifest)
    document = json.loads(current.manifest_path.read_text())
    old_key = dict(document["cache_key"])
    old_key.pop("coverage")
    old_key["cache_key_schema"] = "graph-cache-key-v1"
    old_graph_id = graph_id_for_key(old_key)
    old_target = cache.graphs / old_graph_id.removeprefix("sha256:")
    shutil.copytree(current.manifest_path.parent, old_target)

    config_path = old_target / "valhalla.json"
    config = json.loads(config_path.read_text())
    config["mjolnir"]["tile_dir"] = str((old_target / "tiles").resolve())
    config_bytes = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    config_path.write_bytes(config_bytes)

    document["manifest_version"] = 1
    document["cache_key_schema"] = "graph-cache-key-v1"
    document["graph_id"] = old_graph_id
    document["cache_key"] = old_key
    document["source"].pop("coverage")
    document["artifacts"]["config"]["size_bytes"] = len(config_bytes)
    document["artifacts"]["config"]["sha256"] = hashlib.sha256(config_bytes).hexdigest()
    (old_target / "manifest.json").write_text(json.dumps(document))

    inspected = cache.inspect(old_graph_id)

    assert inspected.status == "LEGACY_READY"
    assert {item["status"] for item in cache.list_graphs()} == {"READY", "LEGACY_READY"}


def test_graph_v2_requires_coverage_provenance(snapshot_manifest: Path, tmp_path: Path) -> None:
    cache = GraphCache(_config(tmp_path), tile_builder=_fake_builder)
    ready = cache.prepare(snapshot_manifest)
    document = json.loads(ready.manifest_path.read_text())
    document["source"].pop("coverage")
    ready.manifest_path.write_text(json.dumps(document))

    with pytest.raises(RoutingError) as caught:
        cache.inspect(ready.graph_id)

    assert caught.value.code == "CACHE_CORRUPT"


def test_remove_never_deletes_a_locked_graph(snapshot_manifest: Path, tmp_path: Path) -> None:
    config = _config(tmp_path, cache_lock_timeout_seconds=0.02, lock_poll_seconds=0.01)
    cache = GraphCache(config, tile_builder=_fake_builder)
    ready = cache.prepare(snapshot_manifest)
    digest = ready.graph_id.removeprefix("sha256:")
    (cache.locks / f"{digest}.lock").write_text("active")

    with pytest.raises(RoutingError) as caught:
        cache.remove(ready.graph_id)

    assert caught.value.code == "LOCK_TIMEOUT"
    assert ready.manifest_path.exists()


def test_broad_cache_targets_are_rejected() -> None:
    with pytest.raises(RoutingError) as caught:
        GraphCache(RoutingCacheConfig.defaults().with_cache_directory(Path("/")))
    assert caught.value.code == "UNSAFE_CACHE_TARGET"


@pytest.mark.integration
def test_published_graph_config_is_loadable_at_final_path(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    import valhalla

    ready = GraphCache(_config(tmp_path)).prepare(snapshot_manifest)
    config = json.loads((ready.manifest_path.parent / "valhalla.json").read_text())

    assert Path(config["mjolnir"]["tile_dir"]) == ready.manifest_path.parent / "tiles"
    actor = valhalla.Actor(config)
    located = json.loads(
        actor.locate(
            json.dumps({"locations": [{"lat": 44.0, "lon": 33.0}], "costing": "pedestrian"})
        )
    )
    assert located[0]["edges"]
