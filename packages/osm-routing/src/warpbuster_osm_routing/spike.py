"""End-to-end Valhalla feasibility workflow over one immutable snapshot."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path

import valhalla

from warpbuster_osm_routing import __version__
from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.materialize import materialize_pbf
from warpbuster_osm_routing.models import GeoPoint, SpikeResult
from warpbuster_osm_routing.valhalla_backend import (
    VALHALLA_PROFILE_ID,
    build_config,
    build_tiles,
    probe_route,
)


def run_spike(
    manifest_path: Path,
    work_directory: Path,
    start: GeoPoint,
    end: GeoPoint,
    *,
    alternates: int = 0,
    overwrite: bool = False,
) -> SpikeResult:
    """Validate, materialize, build and query one snapshot without network access."""
    _validate_point(start)
    _validate_point(end)
    if not 0 <= alternates <= 2:
        raise RoutingSpikeError("invalid_request", "alternates must be between 0 and 2")
    root = work_directory.expanduser().resolve()
    _prepare_work_directory(root, overwrite)
    started = time.perf_counter()
    snapshot = load_snapshot(manifest_path)
    verified_at = time.perf_counter()
    pbf_path = root / "snapshot.osm.pbf"
    pbf_hash, pbf_bytes = materialize_pbf(snapshot, pbf_path)
    materialized_at = time.perf_counter()
    tiles = root / "tiles"
    tiles.mkdir()
    config, config_hash = build_config(tiles)
    config_path = root / "valhalla.json"
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    build_log = build_tiles(config_path, pbf_path)
    built_at = time.perf_counter()
    probe = probe_route(config, start, end, alternates)
    routed_at = time.perf_counter()
    tile_files = tuple(sorted(path for path in tiles.rglob("*") if path.is_file()))
    tile_bytes = sum(path.stat().st_size for path in tile_files)
    semantic_key = {
        "snapshot_id": snapshot.snapshot_id,
        "valhalla_version": valhalla.__version__,
        "routing_profile": VALHALLA_PROFILE_ID,
        "config_sha256": config_hash,
    }
    way_ids_available = all(route["way_ids"] for route in probe["routes"])
    document = {
        "protocol_version": 1,
        "operation": "valhalla_feasibility_spike",
        "status": "ready",
        "verdict": "go" if way_ids_available else "conditional_go",
        "adapter_version": __version__,
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "manifest_path": str(snapshot.manifest_path),
            "manifest_sha256": snapshot.manifest_sha256,
            "dataset_profile": snapshot.dataset_profile,
            "manager_version": snapshot.manager_version,
            "osm_base_timestamp": snapshot.osm_base_timestamp,
            "data_file_count": len(snapshot.data_files),
        },
        "engine": {
            "name": "Valhalla",
            "version": valhalla.__version__,
            "routing_profile": VALHALLA_PROFILE_ID,
            "config_sha256": config_hash,
            "semantic_cache_key_sha256": hashlib.sha256(
                json.dumps(semantic_key, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "artifacts": {
            "pbf_path": str(pbf_path),
            "pbf_sha256": pbf_hash,
            "pbf_bytes": pbf_bytes,
            "config_path": str(config_path),
            "tiles_path": str(tiles),
            "tile_files": len(tile_files),
            "tile_bytes": tile_bytes,
        },
        "timings_seconds": {
            "manifest_verification": round(verified_at - started, 6),
            "pbf_materialization": round(materialized_at - verified_at, 6),
            "tile_build": round(built_at - materialized_at, 6),
            "route_probe": round(routed_at - built_at, 6),
            "total": round(routed_at - started, 6),
        },
        "probe": probe,
        "build_log_tail": build_log,
        "known_limitations": [
            "same-version conflicting OSM objects are not diagnosed by this spike adapter",
            "Valhalla binary tile byte stability is not yet part of the cache contract",
            "absence of an alternative route is not a routing failure",
        ],
    }
    return SpikeResult(document)


def _prepare_work_directory(path: Path, overwrite: bool) -> None:
    if path == Path(path.anchor) or path == Path.home().resolve():
        raise RoutingSpikeError("unsafe_work_directory", "refusing broad work directory")
    path.mkdir(parents=True, exist_ok=True)
    managed = (path / "snapshot.osm.pbf", path / "valhalla.json", path / "tiles")
    existing = [item for item in managed if item.exists()]
    if existing and not overwrite:
        raise RoutingSpikeError(
            "output_exists",
            "derived artifacts already exist; pass --overwrite to replace them",
            {"paths": [str(item) for item in existing]},
        )
    if overwrite:
        for item in existing:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()


def _validate_point(point: GeoPoint) -> None:
    if not (-90 <= point.latitude <= 90 and -180 <= point.longitude <= 180):
        raise RoutingSpikeError("invalid_request", "anchor is outside WGS84 bounds")
