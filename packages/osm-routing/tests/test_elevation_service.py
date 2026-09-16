"""Task 020C: deterministic offline DEM sampling and native height contract."""

from __future__ import annotations

import gzip
import io
import json
import struct
import time
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig
from warpbuster_osm_routing.dem_coverage import DemCoveragePlan
from warpbuster_osm_routing.elevation_backend import ValhallaElevationBackend
from warpbuster_osm_routing.elevation_service import (
    ElevationSamplingConfig,
    ElevationService,
    densify,
)
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint


class Response(io.BytesIO):
    status = 200

    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self.url = url

    def geturl(self) -> str:
        return self.url


def _cache(tmp_path: Path, raster: bytes) -> tuple[DemCache, str]:
    data = gzip.compress(raster, mtime=0)
    cache = DemCache(
        DemCacheConfig(cache_directory=tmp_path / "dem"),
        response_factory=lambda url, _: Response(data, url),
    )
    snapshot = cache.ensure(DemCoveragePlan(("N44E033",), 2, 30.0), "auto")
    assert snapshot.snapshot_id is not None
    return cache, snapshot.snapshot_id


def test_densification_preserves_vertices_and_bounds_before_backend() -> None:
    points = (GeoPoint(44.5, 33.5), GeoPoint(44.5, 33.501), GeoPoint(44.501, 33.501))
    config = ElevationSamplingConfig(maximum_spacing_m=20)
    samples = densify(points, config)
    assert samples[0] == (points[0], 0.0, 0)
    assert [sample[0] for sample in samples if sample[2] is not None] == list(points)
    assert samples[-1][2] == 2
    assert all(b[1] > a[1] for a, b in pairwise(samples))
    assert samples == densify(points, config)
    with pytest.raises(RoutingError) as limit:
        densify(points, replace(config, maximum_samples=3, maximum_batch_samples=3))
    assert limit.value.code == "RESOURCE_LIMIT_EXCEEDED"


def test_native_bilinear_and_offline_identity(tmp_path: Path) -> None:
    raster = bytearray(25_934_402)
    # 44.5 N / 33.5 E is the 1800th sample from the north/west tile edges.
    for row, col, value in ((1800, 1800, 10), (1800, 1801, 20), (1801, 1800, 30), (1801, 1801, 40)):
        struct.pack_into(">h", raster, 2 * (row * 3601 + col), value)
    cache, snapshot_id = _cache(tmp_path, bytes(raster))
    service = ElevationService(cache, ElevationSamplingConfig(maximum_spacing_m=100))
    point = GeoPoint(44.5 - 1 / 7200, 33.5 + 1 / 7200)
    first = service.sample(snapshot_id, (point,))
    second = service.sample(snapshot_id, (point,))
    assert first == second
    assert first.status == "READY"
    assert first.samples[0].raw_elevation_m == pytest.approx(25.0)
    assert first.samples[0].missing_reason is None
    assert not list(cache.staging.iterdir())


def test_partial_distinguishes_missing_tile_from_void(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path, bytes(25_934_402))
    service = ElevationService(cache, ElevationSamplingConfig(maximum_spacing_m=200_000))
    profile = service.sample(snapshot_id, (GeoPoint(44.5, 33.5), GeoPoint(44.5, 34.5)))
    assert profile.status == "PARTIAL"
    assert profile.samples[0].raw_elevation_m == 0.0
    assert profile.samples[-1].missing_reason == "TILE_NOT_IN_SNAPSHOT"
    assert profile.samples[-1].raw_elevation_m is None


def test_invalid_backend_response_is_typed(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path, bytes(25_934_402))

    class BadBackend:
        def heights(self, points: tuple[GeoPoint, ...]) -> tuple[float | None, ...]:
            return (float("nan"),)

    service = ElevationService(cache, backend_factory=lambda _path, _limit: BadBackend())
    with pytest.raises(RoutingError) as failure:
        service.sample(snapshot_id, (GeoPoint(44.5, 33.5),))
    assert failure.value.code == "DEM_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        '{"shape":[{"lat":44.5,"lon":33.5}],"height":[0,1]}',
        '{"shape":[{"lat":44.5,"lon":33.5}],"height":[NaN]}',
        '{"shape":[{"lat":44.5,"lon":33.5}],"height":[0],"extra":1}',
    ],
)
def test_native_adapter_rejects_malformed_or_extra_values(response: str) -> None:
    class Actor:
        def height(self, request: str) -> str:
            return response

    backend = object.__new__(ValhallaElevationBackend)
    backend._actor = Actor()  # type: ignore[assignment]
    backend.maximum_response_bytes = 1024
    with pytest.raises(RoutingError) as failure:
        backend.heights((GeoPoint(44.5, 33.5),))
    assert failure.value.code == "DEM_RESPONSE_INVALID"


def test_void_is_not_replaced_with_zero(tmp_path: Path) -> None:
    raster = bytearray(25_934_402)
    struct.pack_into(">h", raster, 2 * (1800 * 3601 + 1800), -32768)
    cache, snapshot_id = _cache(tmp_path, bytes(raster))
    profile = ElevationService(cache).sample(snapshot_id, (GeoPoint(44.5, 33.5),))
    assert profile.status == "PARTIAL"
    assert profile.samples[0].raw_elevation_m is None
    assert profile.samples[0].missing_reason == "VOID"


@pytest.mark.performance
def test_densify_20k_samples_within_five_seconds() -> None:
    points = tuple(GeoPoint(44.5 + index / 100_000, 33.5) for index in range(20_000))
    started = time.monotonic()
    result = densify(points, ElevationSamplingConfig())
    assert len(result) == 20_000
    assert time.monotonic() - started < 5


def test_cli_samples_existing_snapshot_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache, snapshot_id = _cache(tmp_path, bytes(25_934_402))
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"points": [{"latitude": 44.5, "longitude": 33.5}]}))
    assert (
        main(["dem", "sample", snapshot_id, str(route), "--cache-dir", str(cache.root), "--json"])
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "READY"
    assert document["samples"][0]["raw_elevation_m"] == 0.0


@pytest.mark.performance
def test_native_20k_samples_within_five_seconds(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path, bytes(25_934_402))
    points = tuple(GeoPoint(44.5 + index / 100_000, 33.5) for index in range(20_000))
    started = time.monotonic()
    profile = ElevationService(cache).sample(snapshot_id, points)
    assert profile.status == "READY"
    assert len(profile.samples) == 20_000
    assert time.monotonic() - started < 5
