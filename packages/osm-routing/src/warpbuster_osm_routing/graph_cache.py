"""Atomic content-addressed Valhalla graph cache for Task 010B."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.coverage import parse_coverage
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.identity import (
    CACHE_KEY_SCHEMA_VERSION,
    graph_cache_key,
    graph_id_for_key,
)
from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.models import GraphResult, MaterializationResult, Snapshot
from warpbuster_osm_routing.normalize import normalize_snapshot
from warpbuster_osm_routing.valhalla_backend import build_config, build_tiles

GRAPH_MANIFEST_VERSION = 2
LEGACY_GRAPH_MANIFEST_VERSION = 1
LEGACY_CACHE_KEY_SCHEMA_VERSION = "graph-cache-key-v1"
GRAPH_ID_PATTERN = re.compile(r"sha256:([0-9a-f]{64})\Z")
TileBuilder = Callable[[Path, Path, float | None], str]
Normalizer = Callable[[Snapshot, Path, Path, RoutingCacheConfig], MaterializationResult]


class GraphCache:
    """Prepare, verify and maintain derived graphs without touching source snapshots."""

    def __init__(
        self,
        config: RoutingCacheConfig,
        *,
        tile_builder: TileBuilder = build_tiles,
        normalizer: Normalizer = normalize_snapshot,
    ) -> None:
        self.config = config.validated()
        self.root = _safe_cache_root(config.cache_directory)
        self.graphs = self.root / "graphs"
        self.locks = self.root / "locks"
        self.staging = self.root / "staging"
        self.tile_builder = tile_builder
        self.normalizer = normalizer

    def prepare(self, manifest_path: Path, *, rebuild: bool = False) -> GraphResult:
        started = time.monotonic()
        snapshot = self._load_production_snapshot(manifest_path)
        key, digest = graph_cache_key(snapshot)
        graph_id = f"sha256:{digest}"
        target = self.graphs / digest
        self._ensure_directories()
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise RoutingError("OUTPUT_EXISTS", f"graph target is not a safe directory: {target}")

        if target.exists() and not rebuild:
            try:
                document = self._verify_directory(target, expected_graph_id=graph_id)
            except RoutingError:
                if not (self.locks / f"{digest}.lock").exists():
                    raise
            else:
                return GraphResult("CACHED", graph_id, target / "manifest.json", document)

        with _GraphLock(self, digest):
            if target.exists() and not rebuild:
                document = self._verify_directory(target, expected_graph_id=graph_id)
                return GraphResult("CACHED", graph_id, target / "manifest.json", document)
            staging = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=self.staging))
            try:
                document = self._build(staging, target, snapshot, key, graph_id, started)
                self._publish(staging, target, replace_existing=rebuild)
                document = self._verify_directory(target, expected_graph_id=graph_id)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise
        return GraphResult("READY", graph_id, target / "manifest.json", document)

    def inspect(self, graph_id: str) -> GraphResult:
        digest = _graph_digest(graph_id)
        target = self.graphs / digest
        if not target.is_dir():
            raise RoutingError("CACHE_NOT_FOUND", f"graph does not exist: {graph_id}")
        document = self._verify_directory(target, expected_graph_id=graph_id)
        status = self._graph_status(document)
        return GraphResult(status, graph_id, target / "manifest.json", document)

    def list_graphs(self) -> list[dict[str, Any]]:
        if not self.graphs.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for target in sorted(self.graphs.iterdir()):
            if not target.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", target.name):
                continue
            graph_id = f"sha256:{target.name}"
            try:
                document = self._verify_directory(target, expected_graph_id=graph_id)
            except RoutingError as error:
                results.append(
                    {"graph_id": graph_id, "status": "CORRUPT", "error": error.as_dict()}
                )
            else:
                results.append(
                    {
                        "graph_id": graph_id,
                        "status": self._graph_status(document),
                        "snapshot_id": document["source"]["snapshot_id"],
                        "created_at": document["build"]["created_at"],
                        "manifest_path": str(target / "manifest.json"),
                    }
                )
        return results

    def remove(self, graph_id: str) -> dict[str, Any]:
        digest = _graph_digest(graph_id)
        target = self.graphs / digest
        self._ensure_directories()
        with _GraphLock(self, digest):
            if not target.is_dir():
                raise RoutingError("CACHE_NOT_FOUND", f"graph does not exist: {graph_id}")
            self._verify_directory(target, expected_graph_id=graph_id)
            shutil.rmtree(target)
        return {"operation": "remove", "status": "REMOVED", "graph_id": graph_id}

    def prune(self, *, apply: bool = False) -> dict[str, Any]:
        now = time.time()
        candidates: list[dict[str, Any]] = []
        if self.graphs.is_dir():
            for target in sorted(self.graphs.iterdir()):
                if not target.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", target.name):
                    continue
                lock = self.locks / f"{target.name}.lock"
                age = now - target.stat().st_mtime
                if lock.exists() or age < self.config.prune_minimum_age_seconds:
                    continue
                candidates.append(
                    {"graph_id": f"sha256:{target.name}", "age_seconds": round(age, 3)}
                )
        removed: list[str] = []
        if apply:
            for item in candidates:
                graph_id = str(item["graph_id"])
                self.remove(graph_id)
                removed.append(graph_id)
        return {
            "operation": "prune",
            "status": "APPLIED" if apply else "DRY_RUN",
            "candidates": candidates,
            "removed": removed,
        }

    def _ensure_directories(self) -> None:
        for path in (self.graphs, self.locks, self.staging):
            path.mkdir(parents=True, exist_ok=True)

    def _load_production_snapshot(self, manifest_path: Path) -> Snapshot:
        try:
            return load_snapshot(manifest_path, self.config)
        except RoutingError as error:
            mapping = {
                "manifest_unreadable": "MANIFEST_INVALID",
                "manifest_too_large": "RESOURCE_LIMIT_EXCEEDED",
                "manifest_invalid": "MANIFEST_INVALID",
                "resource_limit": "RESOURCE_LIMIT_EXCEEDED",
                "unsupported_media_type": "UNSUPPORTED_OSM_FORMAT",
                "data_unreadable": "SOURCE_MISSING",
                "data_size_mismatch": "SOURCE_SIZE_MISMATCH",
                "data_hash_mismatch": "SOURCE_HASH_MISMATCH",
            }
            code = mapping.get(error.code, error.code)
            if error.code == "unsupported_manifest":
                code = (
                    "UNSUPPORTED_DATASET_PROFILE"
                    if "dataset_profile" in error.message
                    else "UNSUPPORTED_PROTOCOL"
                )
            raise RoutingError(code, error.message, error.details) from error

    def _build(
        self,
        staging: Path,
        target: Path,
        snapshot: Snapshot,
        key: dict[str, Any],
        graph_id: str,
        started: float,
    ) -> dict[str, Any]:
        normalized_at = time.monotonic()
        pbf = staging / "source.osm.pbf"
        materialized = self.normalizer(snapshot, pbf, staging / "normalization.sqlite", self.config)
        (staging / "normalization.sqlite").unlink(missing_ok=True)
        normalize_seconds = time.monotonic() - normalized_at

        tiles = staging / "tiles"
        tiles.mkdir()
        valhalla_config, config_hash = build_config(tiles)
        if config_hash != key["build_config_sha256"]:
            raise RoutingError(
                "CACHE_CORRUPT", "Valhalla semantic config hash changed during build"
            )
        config_path = staging / "valhalla.json"
        config_path.write_text(
            json.dumps(valhalla_config, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )
        build_at = time.monotonic()
        try:
            build_log = self.tile_builder(
                config_path, materialized.path, self.config.build_timeout_seconds
            )
        except RoutingError as error:
            if error.code in {"BUILD_TIMEOUT", "VALHALLA_BUILD_FAILED"}:
                raise
            raise RoutingError("VALHALLA_BUILD_FAILED", error.message, error.details) from error
        except Exception as error:
            raise RoutingError(
                "VALHALLA_BUILD_FAILED", f"Valhalla tile build failed: {error}"
            ) from error
        build_seconds = time.monotonic() - build_at

        # The builder needs the staging path, while consumers need the final path
        # after the directory is atomically published.
        valhalla_config["mjolnir"]["tile_dir"] = str((target / "tiles").resolve())
        config_path.write_text(
            json.dumps(valhalla_config, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )

        tile_tree = self._inventory_tiles(tiles)
        config_size, config_sha = _hash_file(config_path, self.config.io_chunk_bytes)
        warnings = []
        if materialized.statistics.unresolved_relation_members:
            warnings.append(
                {
                    "code": "UNRESOLVED_RELATION_MEMBERS",
                    "count": materialized.statistics.unresolved_relation_members,
                }
            )
        document: dict[str, Any] = {
            "protocol_version": 1,
            "manifest_version": GRAPH_MANIFEST_VERSION,
            "cache_key_schema": CACHE_KEY_SCHEMA_VERSION,
            "status": "READY",
            "graph_id": graph_id,
            "cache_key": key,
            "source": {
                "snapshot_id": snapshot.snapshot_id,
                "dataset_profile": snapshot.dataset_profile,
                "manager_version": snapshot.manager_version,
                "manifest_sha256": snapshot.manifest_sha256,
                "osm_base_timestamp": snapshot.osm_base_timestamp,
                "files": [
                    {
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "media_type": item.media_type,
                    }
                    for item in snapshot.data_files
                ],
                "attribution": snapshot.attribution,
                "copyright_url": snapshot.copyright_url,
                "license_url": snapshot.license_url,
                "coverage": snapshot.coverage.as_dict(),
            },
            "materialization": {
                "schema": key["materializer_schema"],
                "semantic_object_sha256": materialized.semantic_object_sha256,
                "statistics": materialized.statistics.as_dict(),
            },
            "engine": {
                "name": "Valhalla",
                "version": key["runtime"]["valhalla"],
                "build_profile": key["build_profile"],
                "build_config_sha256": config_hash,
            },
            "artifacts": {
                "pbf": {
                    "path": "source.osm.pbf",
                    "size_bytes": materialized.size_bytes,
                    "sha256": materialized.sha256,
                },
                "config": {
                    "path": "valhalla.json",
                    "size_bytes": config_size,
                    "sha256": config_sha,
                },
                "tiles": tile_tree,
            },
            "build": {
                "created_at": datetime.now(UTC).isoformat(),
                "timings_seconds": {
                    "normalization": round(normalize_seconds, 6),
                    "valhalla": round(build_seconds, 6),
                    "total": round(time.monotonic() - started, 6),
                },
                "warnings": warnings,
                "log_tail": build_log[-4000:],
            },
            "applied_limits": self.config.build_limits_dict(),
        }
        (staging / "manifest.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self._verify_directory(
            staging, expected_graph_id=graph_id, configured_graph_directory=target
        )
        return document

    def _inventory_tiles(
        self, tiles: Path, *, empty_error_code: str = "VALHALLA_BUILD_FAILED"
    ) -> dict[str, Any]:
        files: list[dict[str, Any]] = []
        total = 0
        for path in sorted(item for item in tiles.rglob("*") if item.is_file()):
            relative = path.relative_to(tiles).as_posix()
            size, digest = _hash_file(path, self.config.io_chunk_bytes)
            total += size
            files.append({"path": relative, "size_bytes": size, "sha256": digest})
            if len(files) > self.config.maximum_tile_files:
                raise _resource_limit(
                    "maximum_tile_files", len(files), self.config.maximum_tile_files
                )
            if total > self.config.maximum_total_tile_bytes:
                raise _resource_limit(
                    "maximum_total_tile_bytes", total, self.config.maximum_total_tile_bytes
                )
        if not files:
            raise RoutingError(empty_error_code, "Valhalla tile tree is empty or missing")
        canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        return {
            "path": "tiles",
            "file_count": len(files),
            "size_bytes": total,
            "tree_sha256": hashlib.sha256(canonical).hexdigest(),
            "files": files,
        }

    def _verify_directory(
        self,
        target: Path,
        *,
        expected_graph_id: str,
        configured_graph_directory: Path | None = None,
    ) -> dict[str, Any]:
        if target.is_symlink() or not target.is_dir():
            raise RoutingError("CACHE_CORRUPT", "graph artifact is not a regular directory")
        manifest = target / "manifest.json"
        try:
            raw = manifest.read_bytes()
            document = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RoutingError("CACHE_CORRUPT", f"cannot read graph manifest: {error}") from error
        if not isinstance(document, dict):
            raise RoutingError("CACHE_CORRUPT", "graph manifest root is not an object")
        expected = {
            "protocol_version": 1,
            "status": "READY",
            "graph_id": expected_graph_id,
        }
        for field, value in expected.items():
            if document.get(field) != value:
                raise RoutingError(
                    "CACHE_CORRUPT",
                    f"graph manifest has invalid {field}",
                    {"expected": value, "actual": document.get(field)},
                )
        version_pair = (document.get("manifest_version"), document.get("cache_key_schema"))
        supported_pairs = {
            (GRAPH_MANIFEST_VERSION, CACHE_KEY_SCHEMA_VERSION),
            (LEGACY_GRAPH_MANIFEST_VERSION, LEGACY_CACHE_KEY_SCHEMA_VERSION),
        }
        if version_pair not in supported_pairs:
            raise RoutingError(
                "CACHE_CORRUPT",
                "graph manifest has an unsupported version/schema pair",
                {"actual": list(version_pair)},
            )
        key = document.get("cache_key")
        if not isinstance(key, dict) or graph_id_for_key(key) != expected_graph_id:
            raise RoutingError("CACHE_CORRUPT", "graph manifest cache key does not match graph ID")
        if key.get("cache_key_schema") != document.get("cache_key_schema"):
            raise RoutingError("CACHE_CORRUPT", "graph cache key schema provenance is inconsistent")
        artifacts = document.get("artifacts")
        if not isinstance(artifacts, dict):
            raise RoutingError("CACHE_CORRUPT", "graph manifest has no artifacts object")
        self._verify_file(target, artifacts.get("pbf"), "PBF", "source.osm.pbf")
        self._verify_file(target, artifacts.get("config"), "config", "valhalla.json")
        self._verify_provenance(document, key, target, configured_graph_directory or target)
        recorded_tiles = artifacts.get("tiles")
        if not isinstance(recorded_tiles, dict) or recorded_tiles.get("path") != "tiles":
            raise RoutingError("CACHE_CORRUPT", "graph manifest has invalid tile inventory")
        try:
            actual_tiles = self._inventory_tiles(target / "tiles", empty_error_code="CACHE_CORRUPT")
        except RoutingError as error:
            if error.code == "CACHE_CORRUPT":
                raise
            raise RoutingError("CACHE_CORRUPT", error.message, error.details) from error
        if actual_tiles != recorded_tiles:
            raise RoutingError("CACHE_CORRUPT", "Valhalla tile tree does not match graph manifest")
        return document

    def _verify_provenance(
        self,
        document: dict[str, Any],
        key: dict[str, Any],
        target: Path,
        configured_graph_directory: Path,
    ) -> None:
        source = document.get("source")
        if not isinstance(source, dict):
            raise RoutingError("CACHE_CORRUPT", "graph manifest has no source provenance")
        files = source.get("files")
        if not isinstance(files, list):
            raise RoutingError("CACHE_CORRUPT", "graph manifest has invalid source provenance")
        source_hashes: list[str] = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
                raise RoutingError("CACHE_CORRUPT", "invalid source hash provenance")
            source_hashes.append(item["sha256"])
        source_hashes.sort()
        if (
            source.get("snapshot_id") != key.get("snapshot_id")
            or source.get("dataset_profile") != key.get("dataset_profile")
            or source_hashes != key.get("source_sha256")
        ):
            raise RoutingError("CACHE_CORRUPT", "source provenance does not match graph cache key")
        if document.get("manifest_version") == GRAPH_MANIFEST_VERSION:
            coverage = source.get("coverage")
            if not isinstance(coverage, dict) or coverage != key.get("coverage"):
                raise RoutingError("CACHE_CORRUPT", "coverage provenance does not match cache key")
            try:
                parse_coverage(coverage)
            except RoutingError as error:
                raise RoutingError(
                    "CACHE_CORRUPT", f"graph has invalid coverage provenance: {error.message}"
                ) from error
        try:
            config_document = json.loads((target / "valhalla.json").read_text(encoding="utf-8"))
            tile_dir = Path(config_document["mjolnir"]["tile_dir"]).resolve()
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise RoutingError("CACHE_CORRUPT", f"invalid Valhalla config: {error}") from error
        if tile_dir != (configured_graph_directory / "tiles").resolve():
            raise RoutingError(
                "CACHE_CORRUPT", "Valhalla config points outside its graph directory"
            )
        config_document["mjolnir"]["tile_dir"] = "<DERIVED_TILE_DIRECTORY>"
        canonical = json.dumps(config_document, sort_keys=True, separators=(",", ":")).encode()
        if hashlib.sha256(canonical).hexdigest() != key.get("build_config_sha256"):
            raise RoutingError("CACHE_CORRUPT", "Valhalla semantic config does not match cache key")

    @staticmethod
    def _graph_status(document: dict[str, Any]) -> str:
        return (
            "LEGACY_READY"
            if document.get("manifest_version") == LEGACY_GRAPH_MANIFEST_VERSION
            else "READY"
        )

    def _verify_file(self, target: Path, artifact: object, label: str, expected_path: str) -> None:
        if not isinstance(artifact, dict):
            raise RoutingError("CACHE_CORRUPT", f"graph manifest has no {label} artifact")
        relative = artifact.get("path")
        if relative != expected_path:
            raise RoutingError("CACHE_CORRUPT", f"invalid {label} artifact path")
        assert isinstance(relative, str)
        path = _contained_path(target, relative)
        size, digest = _hash_file(path, self.config.io_chunk_bytes)
        if size != artifact.get("size_bytes") or digest != artifact.get("sha256"):
            raise RoutingError("CACHE_CORRUPT", f"{label} artifact does not match graph manifest")

    def _publish(self, staging: Path, target: Path, *, replace_existing: bool) -> None:
        if not target.exists():
            staging.replace(target)
            return
        if not replace_existing:
            raise RoutingError("OUTPUT_EXISTS", f"graph directory already exists: {target}")
        backup = self.staging / f".{target.name}.backup-{os.getpid()}-{time.time_ns()}"
        target.replace(backup)
        try:
            staging.replace(target)
        except Exception:
            backup.replace(target)
            raise
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


class _GraphLock(AbstractContextManager["_GraphLock"]):
    def __init__(self, cache: GraphCache, digest: str) -> None:
        self.cache = cache
        self.path = cache.locks / f"{digest}.lock"

    def __enter__(self) -> Self:
        deadline = time.monotonic() + self.cache.config.cache_lock_timeout_seconds
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= deadline:
                    age = _age_seconds(self.path)
                    raise RoutingError(
                        "LOCK_TIMEOUT",
                        "timed out waiting for graph build lock",
                        {
                            "lock_path": str(self.path),
                            "age_seconds": age,
                            "stale": age is not None
                            and age >= self.cache.config.stale_lock_seconds,
                        },
                    ) from None
                time.sleep(self.cache.config.lock_poll_seconds)
                continue
            payload = json.dumps(
                {"pid": os.getpid(), "created_at": datetime.now(UTC).isoformat()}
            ).encode()
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            return self

    def __exit__(self, *args: object) -> None:
        self.path.unlink(missing_ok=True)


def _safe_cache_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise RoutingError("UNSAFE_CACHE_TARGET", f"unsafe broad cache directory: {root}")
    return root


def _graph_digest(graph_id: str) -> str:
    match = GRAPH_ID_PATTERN.fullmatch(graph_id)
    if match is None:
        raise RoutingError("INVALID_GRAPH_ID", "graph ID must be sha256:<64 lowercase hex>")
    return match.group(1)


def _contained_path(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RoutingError("CACHE_CORRUPT", "artifact path escapes graph directory")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise RoutingError("CACHE_CORRUPT", "artifact path escapes graph directory")
    return resolved


def _hash_file(path: Path, chunk_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_bytes):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise RoutingError(
            "CACHE_CORRUPT", f"cannot read graph artifact {path}: {error}"
        ) from error
    return size, digest.hexdigest()


def _resource_limit(name: str, actual: int, limit: int) -> RoutingError:
    return RoutingError(
        "RESOURCE_LIMIT_EXCEEDED",
        f"derived graph exceeds {name}",
        {"limit_name": name, "actual": actual, "limit": limit},
    )


def _age_seconds(path: Path) -> float | None:
    try:
        return round(time.time() - path.stat().st_mtime, 3)
    except OSError:
        return None
