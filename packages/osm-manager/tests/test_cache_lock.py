"""Bounded cache locking and stale-owner recovery."""

import json
import os
import time
from dataclasses import replace

import pytest

from warpbuster_osm_manager.cache import CacheStore
from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError


def _write_old_lock(cache: CacheStore, *, pid: int, token: str) -> None:
    lock = cache.locks_directory / "ensure.lock"
    lock.write_text(json.dumps({"pid": pid, "owner_token": token}), encoding="utf-8")
    old = time.time() - cache.config.stale_lock_seconds - 1
    os.utime(lock, (old, old))


def test_old_lock_for_live_process_is_not_broken(manager_config: OsmManagerConfig) -> None:
    config = replace(manager_config, cache_lock_timeout_seconds=0.01)
    cache = CacheStore(config)
    _write_old_lock(cache, pid=os.getpid(), token="active")
    with pytest.raises(OsmManagerError) as raised, cache.ensure_lock():
        pass
    assert raised.value.code is ErrorCode.CACHE_LOCK_TIMEOUT
    assert (cache.locks_directory / "ensure.lock").is_file()


def test_old_lock_for_dead_process_is_recovered(manager_config: OsmManagerConfig) -> None:
    cache = CacheStore(manager_config)
    _write_old_lock(cache, pid=999_999_999, token="abandoned")
    with cache.ensure_lock():
        owner = json.loads((cache.locks_directory / "ensure.lock").read_text(encoding="utf-8"))
        assert owner["owner_token"] != "abandoned"
        assert owner["process_started_at"]
    assert not (cache.locks_directory / "ensure.lock").exists()


def test_writer_does_not_remove_a_replaced_lock(manager_config: OsmManagerConfig) -> None:
    cache = CacheStore(manager_config)
    lock = cache.locks_directory / "ensure.lock"
    with cache.ensure_lock():
        lock.write_text(
            json.dumps({"pid": os.getpid(), "owner_token": "replacement"}),
            encoding="utf-8",
        )
    assert lock.is_file()
