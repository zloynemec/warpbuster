"""Task 020B cache and coverage tests with synthetic, offline Skadi HGT."""

from __future__ import annotations

import gzip
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig
from warpbuster_osm_routing.dem_coverage import DemCoveragePlan, plan_coverage, tile_name
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, data: bytes, url: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(data)
        self.url = url
        self.headers = headers or {"Content-Length": str(len(data)), "ETag": '"synthetic"'}

    def geturl(self) -> str:
        return self.url


@pytest.fixture(scope="module")
def hgt_gzip() -> bytes:
    return gzip.compress(bytes(25_934_402), mtime=0)


def _cache(tmp_path: Path, data: bytes, calls: list[str]) -> DemCache:
    def respond(url: str, timeout: float) -> FakeResponse:
        assert timeout > 0
        calls.append(url)
        return FakeResponse(data, url)

    return DemCache(DemCacheConfig(cache_directory=tmp_path / "dem"), response_factory=respond)


def _plan(name: str = "N44E033") -> DemCoveragePlan:
    return DemCoveragePlan((name,), 2, 30.0)


def test_coverage_names_boundaries_and_resource_limit() -> None:
    assert tile_name(-1, -1) == "S01W001"
    assert tile_name(0, 0) == "N00E000"
    assert tile_name(44, 33) == "N44E033"
    plan = plan_coverage(
        (GeoPoint(44.5, 33.5), GeoPoint(44.6, 33.6)),
        buffer_m=30,
        maximum_points=20,
        maximum_tiles=4,
    )
    assert plan.tile_names == ("N44E033",)
    boundary = plan_coverage((GeoPoint(0, 0),), buffer_m=30, maximum_points=20, maximum_tiles=4)
    assert boundary.tile_names == ("N00E000", "N00W001", "S01E000", "S01W001")
    with pytest.raises(RoutingError, match="tile limit"):
        plan_coverage((GeoPoint(44.5, 33.5),), buffer_m=250_000, maximum_points=20, maximum_tiles=4)
    with pytest.raises(RoutingError, match="antimeridian"):
        plan_coverage(
            (GeoPoint(1, 179), GeoPoint(1, -179)),
            buffer_m=0,
            maximum_points=20,
            maximum_tiles=8,
        )
    with pytest.raises(RoutingError) as invalid:
        plan_coverage(
            (GeoPoint(44.5, "bad"),),  # type: ignore[arg-type]
            buffer_m=0,
            maximum_points=20,
            maximum_tiles=8,
        )
    assert invalid.value.code == "INVALID_REQUEST"


def test_auto_then_offline_reuses_verified_snapshot(tmp_path: Path, hgt_gzip: bytes) -> None:
    calls: list[str] = []
    cache = _cache(tmp_path, hgt_gzip, calls)
    first = cache.ensure(_plan(), "auto")
    assert first.status == "READY"
    assert len(calls) == 1
    second = cache.ensure(_plan(), "offline")
    assert second.status == "CACHED"
    assert second.snapshot_id == first.snapshot_id
    assert len(calls) == 1
    inspected = cache.inspect(first.snapshot_id or "")
    assert inspected.document["tiles"][0]["size_bytes"] == len(hgt_gzip)
    assert cache.ensure(None, "disabled").status == "DISABLED"


def test_offline_miss_and_corrupt_hit_do_not_download(tmp_path: Path, hgt_gzip: bytes) -> None:
    calls: list[str] = []
    cache = _cache(tmp_path, hgt_gzip, calls)
    with pytest.raises(RoutingError) as missing:
        cache.ensure(_plan(), "offline")
    assert missing.value.code == "DEM_TILE_MISSING"
    ready = cache.ensure(_plan(), "auto")
    assert len(calls) == 1
    entry = ready.document["tiles"][0]
    object_path = cache._object_path(entry["sha256"])
    object_path.write_bytes(b"corrupt")
    with pytest.raises(RoutingError) as corrupt:
        cache.ensure(_plan(), "auto")
    assert corrupt.value.code == "DEM_CACHE_CORRUPT"
    assert len(calls) == 1
    with pytest.raises(RoutingError):
        cache.inspect(ready.snapshot_id or "")


@pytest.mark.parametrize(
    "invalid",
    [
        b"not gzip",
        gzip.compress(b"short", mtime=0),
        gzip.compress(bytes(25_934_402), mtime=0) + b"extra",
    ],
)
def test_invalid_hgt_is_never_published(tmp_path: Path, invalid: bytes) -> None:
    cache = _cache(tmp_path, invalid, [])
    with pytest.raises(RoutingError):
        cache.ensure(_plan(), "auto")
    assert not list(cache.index.rglob("*.json"))
    assert not list(cache.snapshots.iterdir())


def test_concurrent_calls_download_once(tmp_path: Path, hgt_gzip: bytes) -> None:
    calls: list[str] = []
    cache = _cache(tmp_path, hgt_gzip, calls)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cache.ensure(_plan(), "auto"), range(2)))
    assert len(calls) == 1
    assert results[0].snapshot_id == results[1].snapshot_id


def test_manifest_tampering_detected(tmp_path: Path, hgt_gzip: bytes) -> None:
    cache = _cache(tmp_path, hgt_gzip, [])
    result = cache.ensure(_plan(), "auto")
    manifest = result.manifest_path
    assert manifest is not None
    document = json.loads(manifest.read_text())
    document["cache_key"]["vertical_datum"] = "wrong"
    manifest.write_text(json.dumps(document))
    with pytest.raises(RoutingError) as error:
        cache.inspect(result.snapshot_id or "")
    assert error.value.code == "DEM_CACHE_CORRUPT"


def test_cli_offline_error_and_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"points": [{"latitude": 44.5, "longitude": 33.5}]}))
    args = ["dem", "prepare", str(route), "--cache-dir", str(tmp_path / "cache"), "--json"]
    assert main([*args, "--mode", "offline"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "DEM_TILE_MISSING"
    assert main([*args, "--mode", "disabled"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DISABLED"
    assert main(["dem", "prune", "--cache-dir", str(tmp_path / "cache"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DRY_RUN"


def test_cli_inspect_verified_snapshot(
    tmp_path: Path, hgt_gzip: bytes, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = _cache(tmp_path, hgt_gzip, [])
    ready = cache.ensure(_plan(), "auto")
    assert ready.snapshot_id is not None
    assert (
        main(["dem", "inspect", ready.snapshot_id, "--cache-dir", str(cache.root), "--json"]) == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["dem_snapshot_id"] == ready.snapshot_id
    assert document["snapshot"]["tiles"][0]["name"] == "N44E033"


def test_config_bounds(tmp_path: Path) -> None:
    default = DemCacheConfig(cache_directory=tmp_path).validated()
    with pytest.raises(ValueError):
        replace(default, maximum_tiles=0).validated()
    with pytest.raises(ValueError):
        replace(default, buffer_m=-1).validated()
    with pytest.raises(ValueError):
        replace(default, maximum_tiles=1.5).validated()  # type: ignore[arg-type]


def test_prune_is_dry_run_and_respects_snapshot_lease(tmp_path: Path, hgt_gzip: bytes) -> None:
    base = _cache(tmp_path, hgt_gzip, [])
    cache = DemCache(
        replace(base.config, prune_minimum_age_seconds=1), response_factory=base.response_factory
    )
    ready = cache.ensure(_plan(), "auto")
    assert ready.manifest_path is not None and ready.snapshot_id is not None
    entry = ready.document["tiles"][0]
    paths = (
        ready.manifest_path.parent,
        cache.index / "N44" / "N44E033.json",
        cache._object_path(entry["sha256"]),
    )
    old = time.time() - 10
    for path in paths:
        os.utime(path, (old, old))
    with cache.lease(ready.snapshot_id):
        preview = cache.prune()
        assert preview["snapshots"] == []
        assert preview["index"] == ["N44E033"]
        assert preview["objects"] == []
        applied = cache.prune(apply=True)
        assert applied["snapshots"] == []
        assert ready.manifest_path.is_file()
    preview = cache.prune()
    assert preview["snapshots"] == [ready.snapshot_id]
    assert preview["objects"] == [f"{entry['sha256']}.hgt.gz"]
    assert ready.manifest_path.is_file()
    cache.prune(apply=True)
    assert not ready.manifest_path.exists()
    assert not cache._object_path(entry["sha256"]).exists()


def test_http_metadata_and_truncation_fail_before_publication(
    tmp_path: Path, hgt_gzip: bytes
) -> None:
    def bad_length(url: str, timeout: float) -> FakeResponse:
        return FakeResponse(hgt_gzip, url, {"Content-Length": str(len(hgt_gzip) + 1)})

    cache = DemCache(
        DemCacheConfig(cache_directory=tmp_path / "bad-length"), response_factory=bad_length
    )
    with pytest.raises(RoutingError) as error:
        cache.ensure(_plan(), "auto")
    assert error.value.code == "DEM_TILE_CORRUPT"
    assert not list(cache.index.rglob("*.json"))

    def redirect(url: str, timeout: float) -> FakeResponse:
        return FakeResponse(hgt_gzip, "https://untrusted.example/other.hgt.gz")

    cache = DemCache(
        DemCacheConfig(cache_directory=tmp_path / "redirect"), response_factory=redirect
    )
    with pytest.raises(RoutingError) as error:
        cache.ensure(_plan(), "auto")
    assert error.value.code == "DEM_DOWNLOAD_FAILED"


def test_interrupted_download_leaves_no_published_tile(tmp_path: Path, hgt_gzip: bytes) -> None:
    class InterruptedResponse(FakeResponse):
        reads = 0

        def read(self, size: int = -1) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return super().read(10)
            raise OSError("synthetic interrupted stream")

    def respond(url: str, timeout: float) -> InterruptedResponse:
        return InterruptedResponse(hgt_gzip, url)

    cache = DemCache(
        DemCacheConfig(cache_directory=tmp_path / "interrupted"), response_factory=respond
    )
    with pytest.raises(RoutingError) as error:
        cache.ensure(_plan(), "auto")
    assert error.value.code == "DEM_DOWNLOAD_FAILED"
    assert not list(cache.staging.iterdir())
    assert not list(cache.index.rglob("*.json"))
    assert not list(cache.snapshots.iterdir())


def test_timeout_and_stale_lock_are_structured(tmp_path: Path) -> None:
    def timed_out(url: str, timeout: float) -> FakeResponse:
        raise TimeoutError("synthetic timeout")

    config = DemCacheConfig(cache_directory=tmp_path / "timeout")
    cache = DemCache(config, response_factory=timed_out)
    with pytest.raises(RoutingError) as error:
        cache.ensure(_plan(), "auto")
    assert error.value.code == "DEM_NETWORK_TIMEOUT"

    cache = DemCache(replace(config, lock_timeout_seconds=0.01, lock_poll_seconds=0.01))
    cache._directories()
    lock = cache.locks / "maintenance.lock"
    lock.write_text("stale")
    old = time.time() - 2_000
    os.utime(lock, (old, old))
    with pytest.raises(RoutingError) as error:
        cache.ensure(_plan(), "offline")
    assert error.value.code == "DEM_LOCK_TIMEOUT"
    assert error.value.details["stale"] is True


def test_coverage_snapshot_identity_ignores_point_order(tmp_path: Path, hgt_gzip: bytes) -> None:
    cache = _cache(tmp_path, hgt_gzip, [])
    points = (GeoPoint(44.5, 33.5), GeoPoint(44.6, 33.6))
    plans = [
        plan_coverage(polyline, buffer_m=30, maximum_points=10, maximum_tiles=4)
        for polyline in (points, tuple(reversed(points)))
    ]
    assert plans[0].tile_names == plans[1].tile_names
    first = cache.ensure(plans[0], "auto")
    second = cache.ensure(plans[1], "offline")
    assert first.snapshot_id == second.snapshot_id


def test_total_object_quota_prevents_snapshot(tmp_path: Path, hgt_gzip: bytes) -> None:
    calls: list[str] = []
    base = _cache(tmp_path, hgt_gzip, calls)
    config = replace(base.config, maximum_total_cache_bytes=len(hgt_gzip) + 1)
    second_tile = gzip.compress(bytes([1]) * 25_934_402, mtime=0)

    def respond(url: str, timeout: float) -> FakeResponse:
        calls.append(url)
        return FakeResponse(second_tile if "E034" in url else hgt_gzip, url)

    cache = DemCache(config, response_factory=respond)
    plan = DemCoveragePlan(("N44E033", "N44E034"), 2, 30.0)
    with pytest.raises(RoutingError) as error:
        cache.ensure(plan, "auto")
    assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert len(calls) == 2
    assert not list(cache.snapshots.iterdir())
    single = cache.ensure(_plan(), "offline")
    assert single.status == "READY"
