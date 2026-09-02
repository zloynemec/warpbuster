"""End-to-end cache resolution with deterministic fake Overpass data."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread
from time import sleep

import pytest
from conftest import FakeOverpassClient, osm_xml, write_gpx, write_pbf

from warpbuster_osm_manager.cache import hash_file
from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.coverage import bounds_for_cell, plan_from_bbox, plan_from_gpx
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import BoundingBox, CoveragePlan
from warpbuster_osm_manager.overpass import DownloadedOsm
from warpbuster_osm_manager.service import OsmManager


def _plan(config: OsmManagerConfig) -> CoveragePlan:
    return plan_from_bbox(BoundingBox(33.60, 44.40, 33.61, 44.41), config)


def _declared_bounds(plan: CoveragePlan) -> BoundingBox:
    bounds = [bounds_for_cell(cell) for cell in plan.cells]
    return BoundingBox(
        west=min(item.west for item in bounds),
        south=min(item.south for item in bounds),
        east=max(item.east for item in bounds),
        north=max(item.north for item in bounds),
    )


def test_first_ensure_downloads_and_second_ensure_uses_cache(
    manager_config: OsmManagerConfig,
) -> None:
    client = FakeOverpassClient(manager_config)
    manager = OsmManager(manager_config, overpass_client=client)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    plan = _plan(manager_config)

    first = manager.ensure(plan, now=now)
    call_count = len(client.calls)
    second = manager.ensure(plan, now=now + timedelta(hours=1))

    assert first.downloaded is True
    assert second.downloaded is False
    assert len(client.calls) == call_count
    assert second.manifest.snapshot_id == first.manifest.snapshot_id
    assert first.manifest_path.is_file()
    assert first.manifest.cell_blob_sha256
    assert dict(first.manifest.manager_settings)["maximum_download_bytes"] == (
        manager_config.maximum_download_bytes
    )
    for data_file in first.manifest.data_files:
        digest, size = hash_file(data_file.path, manager_config.http_read_chunk_bytes)
        assert digest == data_file.sha256
        assert size == data_file.size_bytes


def test_offline_hit_and_miss_have_explicit_semantics(
    manager_config: OsmManagerConfig,
) -> None:
    client = FakeOverpassClient(manager_config)
    manager = OsmManager(manager_config, overpass_client=client)
    plan = _plan(manager_config)
    now = datetime(2026, 9, 2, tzinfo=UTC)

    with pytest.raises(OsmManagerError) as raised:
        manager.ensure(plan, offline=True, now=now)
    assert raised.value.code is ErrorCode.OFFLINE_CACHE_MISS

    manager.ensure(plan, now=now)
    hit = manager.ensure(plan, offline=True, now=now + timedelta(days=1))
    assert hit.downloaded is False
    assert hit.manifest.stale is False


def test_stale_cache_refresh_and_network_fallback(manager_config: OsmManagerConfig) -> None:
    client = FakeOverpassClient(manager_config)
    manager = OsmManager(manager_config, overpass_client=client)
    plan = _plan(manager_config)
    initial = datetime(2026, 1, 1, tzinfo=UTC)
    original = manager.ensure(plan, now=initial)

    class FailingClient:
        def fetch(self, _bounds: BoundingBox, _destination: Path) -> DownloadedOsm:
            raise OsmManagerError(ErrorCode.OVERPASS_UNAVAILABLE, "offline")

    manager.overpass = FailingClient()
    fallback = manager.ensure(plan, now=initial + timedelta(days=31))
    assert fallback.manifest.snapshot_id == original.manifest.snapshot_id
    assert fallback.stale is True
    assert fallback.warnings

    with pytest.raises(OsmManagerError) as raised:
        manager.ensure(plan, now=initial + timedelta(days=31), require_fresh=True)
    assert raised.value.code is ErrorCode.OVERPASS_UNAVAILABLE


def test_refresh_creates_new_snapshot_without_deleting_old(
    manager_config: OsmManagerConfig,
) -> None:
    client = FakeOverpassClient(manager_config)
    manager = OsmManager(manager_config, overpass_client=client)
    plan = _plan(manager_config)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    first = manager.ensure(plan, now=now)
    second = manager.ensure(plan, refresh=True, now=now + timedelta(minutes=1))
    assert first.manifest.snapshot_id != second.manifest.snapshot_id
    assert first.manifest_path.is_file()
    assert second.manifest_path.is_file()
    assert len(manager.list_snapshots()) == 2


def test_overlapping_request_downloads_only_missing_cells(
    manager_config: OsmManagerConfig,
) -> None:
    client = FakeOverpassClient(manager_config)
    manager = OsmManager(manager_config, overpass_client=client)
    first = plan_from_bbox(BoundingBox(33.60, 44.40, 33.61, 44.41), manager_config)
    second = plan_from_bbox(BoundingBox(33.60, 44.40, 33.70, 44.41), manager_config)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    manager.ensure(first, now=now)
    first_calls = len(client.calls)
    manager.ensure(second, now=now)
    additional_calls = len(client.calls) - first_calls
    assert set(first.cells) < set(second.cells)
    assert 0 < additional_calls < len(second.cells)


def test_import_xml_is_content_addressed_and_available_offline(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    source = tmp_path / "region.osm"
    bounds = _declared_bounds(_plan(manager_config))
    source.write_text(osm_xml(bounds), encoding="utf-8")
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    imported = manager.import_file(source, now=datetime(2026, 9, 2, tzinfo=UTC))
    assert imported.manifest.data_files[0].source_kind == "import"
    assert imported.manifest.data_files[0].path != source
    assert imported.manifest.data_files[0].path.is_file()
    offline = manager.ensure(_plan(manager_config), offline=True)
    assert offline.manifest.data_files[0].source_kind == "import"


def test_import_requires_declared_bounds_and_one_fully_covered_cell(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    without_bounds = tmp_path / "without-bounds.osm"
    without_bounds.write_text(
        '<osm version="0.6"><node id="1" lat="44.4" lon="33.6" /></osm>',
        encoding="utf-8",
    )
    with pytest.raises(OsmManagerError) as raised:
        manager.import_file(without_bounds)
    assert raised.value.code is ErrorCode.OSM_DATA_INVALID

    partial_cell = tmp_path / "partial-cell.osm"
    partial_cell.write_text(osm_xml(BoundingBox(33.600, 44.400, 33.601, 44.401)), encoding="utf-8")
    with pytest.raises(OsmManagerError, match="fully contain one cache cell"):
        manager.import_file(partial_cell)


def test_import_pbf_is_published_with_pbf_media_type(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    plan = _plan(manager_config)
    source = write_pbf(tmp_path / "region.osm.pbf", _declared_bounds(plan))
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    imported = manager.import_file(source)
    assert imported.manifest.data_files[0].media_type.endswith("data+pbf")
    assert manager.ensure(plan, offline=True).downloaded is False


def test_inspect_remove_and_prune_preserve_referenced_blobs(
    manager_config: OsmManagerConfig,
) -> None:
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    result = manager.ensure(_plan(manager_config), now=datetime(2026, 9, 2, tzinfo=UTC))
    inspected = manager.inspect_snapshot(result.manifest.snapshot_id)
    assert inspected["verified_data_file_count"] == len(result.manifest.data_files)
    assert manager.prune(apply=False)["candidate_count"] == 0
    removed = manager.remove_snapshot(result.manifest.snapshot_id)
    assert removed["removed_manifest"] is True
    assert result.manifest.data_files[0].path.is_file()


def test_doctor_rebuilds_coverage_index_from_manifests(
    manager_config: OsmManagerConfig,
) -> None:
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    plan = _plan(manager_config)
    manager.ensure(plan, now=datetime(2026, 9, 2, tzinfo=UTC))
    with manager.cache._connect() as connection:
        connection.execute("DELETE FROM coverage")
    assert not manager.cache.current_cells(plan)
    doctor = manager.doctor()
    assert doctor["coverage_index_rows_restored"] == len(plan.cells)
    assert len(manager.cache.current_cells(plan)) == len(plan.cells)


def test_gpx_path_and_filename_are_absent_from_manifest(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    secret_name = "private-athlete-secret-course.gpx"
    path = write_gpx(tmp_path / secret_name, [[(33.6, 44.4), (33.61, 44.41)]])
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    result = manager.ensure(plan_from_gpx(path, manager_config))
    rendered = result.manifest_path.read_text(encoding="utf-8")
    assert secret_name not in rendered
    assert str(path) not in rendered


def test_configured_download_total_is_enforced(manager_config: OsmManagerConfig) -> None:
    constrained = replace(
        manager_config,
        maximum_download_bytes=1,
        maximum_ensure_download_bytes=1,
    )
    manager = OsmManager(constrained, overpass_client=FakeOverpassClient(constrained))
    with pytest.raises(OsmManagerError) as raised:
        manager.ensure(_plan(constrained))
    assert raised.value.code is ErrorCode.RESPONSE_LIMIT_EXCEEDED


def test_stale_fallback_does_not_hide_integrity_or_limit_errors(
    manager_config: OsmManagerConfig,
) -> None:
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    plan = _plan(manager_config)
    manager.ensure(plan)

    class OversizedClient:
        def fetch(self, _bounds: BoundingBox, _destination: Path) -> DownloadedOsm:
            raise OsmManagerError(ErrorCode.RESPONSE_LIMIT_EXCEEDED, "too large")

    manager.overpass = OversizedClient()
    with pytest.raises(OsmManagerError) as raised:
        manager.ensure(plan, refresh=True)
    assert raised.value.code is ErrorCode.RESPONSE_LIMIT_EXCEEDED


def test_concurrent_identical_ensure_downloads_each_batch_once(
    manager_config: OsmManagerConfig,
) -> None:
    config = replace(manager_config, cache_lock_timeout_seconds=1.0)

    class SlowClient(FakeOverpassClient):
        def fetch(self, bounds: BoundingBox, destination: Path) -> DownloadedOsm:
            sleep(0.02)
            return super().fetch(bounds, destination)

    client = SlowClient(config)
    manager = OsmManager(config, overpass_client=client)
    plan = _plan(config)
    results = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            results.append(manager.ensure(plan))
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    threads = [Thread(target=run), Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(results) == 2
    assert results[0].manifest.snapshot_id == results[1].manifest.snapshot_id
    assert sum(result.downloaded for result in results) == 1


def test_snapshot_identity_preserves_request_provenance(
    manager_config: OsmManagerConfig,
) -> None:
    manager = OsmManager(manager_config, overpass_client=FakeOverpassClient(manager_config))
    plan = _plan(manager_config)
    first = manager.ensure(plan)
    alternate = replace(
        plan,
        source_kind="protocol_geometry",
        request_fingerprint="a" * 64,
        buffer_m=123.0,
    )
    second = manager.ensure(alternate, offline=True)
    assert first.manifest.snapshot_id != second.manifest.snapshot_id
    assert second.manifest.request_fingerprint == alternate.request_fingerprint
    assert second.manifest.source_kind == "protocol_geometry"
