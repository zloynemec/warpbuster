"""Validated content-addressed cache for Mapzen/Tilezen Skadi HGT tiles."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, BinaryIO, Literal, Self, get_type_hints

from warpbuster_osm_routing.dem_coverage import DemCoveragePlan
from warpbuster_osm_routing.dem_profile import MAPZEN_SKADI_EGM96_V1
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number

DemMode = Literal["auto", "offline", "disabled"]
TILE_PATTERN = re.compile(r"[NS]\d{2}[EW]\d{3}\Z")
SNAPSHOT_PATTERN = re.compile(r"sha256:([0-9a-f]{64})\Z")
SNAPSHOT_SCHEMA = 1
ResponseFactory = Callable[[str, float], BinaryIO]


def default_dem_cache_directory() -> Path:
    override = os.environ.get("WARPBUSTER_DEM_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    from warpbuster_osm_routing.config import default_cache_directory

    return default_cache_directory().parent / "dem"


@dataclass(frozen=True, slots=True)
class DemCacheConfig:
    cache_directory: Path
    maximum_points: int = 20_000
    maximum_tiles: int = 16
    buffer_m: float = 30.0
    maximum_compressed_tile_bytes: int = 16 * 1024 * 1024
    maximum_total_cache_bytes: int = 512 * 1024 * 1024
    maximum_manifest_bytes: int = 64 * 1024
    connect_read_timeout_seconds: float = 15.0
    total_deadline_seconds: float = 120.0
    lock_poll_seconds: float = 0.1
    lock_timeout_seconds: float = 120.0
    stale_lock_seconds: float = 1_800.0
    io_chunk_bytes: int = 64 * 1024
    prune_minimum_age_seconds: float = 7 * 24 * 60 * 60

    @classmethod
    def defaults(cls) -> Self:
        return cls(cache_directory=default_dem_cache_directory())

    def validated(self) -> Self:
        hints = get_type_hints(type(self))
        for item in fields(self):
            name, value = item.name, getattr(self, item.name)
            if name == "cache_directory":
                if not isinstance(value, Path):
                    raise ValueError("cache_directory must be a path")
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or (hints[name] is int and not isinstance(value, int))
            ):
                raise ValueError(f"{name} must be finite numeric")
            if name == "buffer_m":
                if value < 0:
                    raise ValueError("buffer_m must be nonnegative")
            elif value <= 0:
                raise ValueError(f"{name} must be positive")
        return self


@dataclass(frozen=True, slots=True)
class DemSnapshot:
    status: str
    snapshot_id: str | None
    manifest_path: Path | None
    document: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dem_snapshot_id": self.snapshot_id,
            "manifest_path": str(self.manifest_path) if self.manifest_path else None,
            "snapshot": self.document,
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _open_http(url: str, timeout: float) -> BinaryIO:
    opener = urllib.request.build_opener(_NoRedirect)
    return opener.open(url, timeout=timeout)  # type: ignore[no-any-return]


class DemCache:
    """Acquire complete verified tiles; publish only complete immutable snapshots."""

    def __init__(
        self, config: DemCacheConfig, *, response_factory: ResponseFactory = _open_http
    ) -> None:
        self.config = config.validated()
        self.root = config.cache_directory.expanduser().resolve()
        if self.root in {Path(self.root.anchor), Path.home().resolve()}:
            raise RoutingError("INVALID_REQUEST", "unsafe broad DEM cache root")
        self.objects = self.root / "objects" / "sha256"
        self.snapshots = self.root / "snapshots"
        self.index = self.root / "index" / MAPZEN_SKADI_EGM96_V1.profile_id
        self.staging = self.root / "staging"
        self.locks = self.root / "locks"
        self.response_factory = response_factory

    def ensure(self, plan: DemCoveragePlan | None, mode: DemMode) -> DemSnapshot:
        if mode == "disabled":
            return DemSnapshot("DISABLED", None, None, {})
        if (
            mode not in {"auto", "offline"}
            or plan is None
            or not plan.tile_names
            or len(plan.tile_names) > self.config.maximum_tiles
            or plan.tile_names != tuple(sorted(set(plan.tile_names)))
            or isinstance(plan.point_count, bool)
            or not 0 < plan.point_count <= self.config.maximum_points
            or not finite_number(plan.buffer_m)
            or plan.buffer_m < 0
        ):
            raise RoutingError("INVALID_REQUEST", "invalid DEM mode or coverage plan")
        deadline = time.monotonic() + self.config.total_deadline_seconds
        self._directories()
        with _Lock(self.locks / "maintenance.lock", self.config, deadline):
            return self._ensure_locked(plan, mode, deadline)

    def _ensure_locked(self, plan: DemCoveragePlan, mode: DemMode, deadline: float) -> DemSnapshot:
        entries: list[dict[str, Any]] = []
        for name in plan.tile_names:
            if not TILE_PATTERN.fullmatch(name):
                raise RoutingError("INVALID_REQUEST", "invalid DEM tile name")
            entries.append(self._ensure_tile(name, mode, deadline))
        key = self._cache_key(entries)
        digest = hashlib.sha256(_canonical(key)).hexdigest()
        snapshot_id = f"sha256:{digest}"
        target = self.snapshots / digest
        with _Lock(self.locks / f"snapshot-{digest}.lock", self.config, deadline):
            if target.exists():
                document = self._verify_snapshot(target, snapshot_id)
                return DemSnapshot("CACHED", snapshot_id, target / "manifest.json", document)
            document = {
                "status": "READY",
                "dem_snapshot_id": snapshot_id,
                "cache_key": key,
                "tiles": entries,
                "attribution_bundle_version": 1,
            }
            stage = Path(tempfile.mkdtemp(prefix="snapshot-", dir=self.staging))
            try:
                self._write_json(stage / "manifest.json", document)
                stage.replace(target)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
            verified = self._verify_snapshot(target, snapshot_id)
        return DemSnapshot("READY", snapshot_id, target / "manifest.json", verified)

    @staticmethod
    def _cache_key(entries: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA,
            "dataset_profile_id": MAPZEN_SKADI_EGM96_V1.profile_id,
            "dataset_profile_sha256": MAPZEN_SKADI_EGM96_V1.sha256(),
            "horizontal_crs": "EPSG:4326",
            "vertical_datum": "WGS84/EGM96 geoid",
            "elevation_unit": "metre",
            "artifact_format": "hgt.gz",
            "tiles": [{"name": entry["name"], "sha256": entry["sha256"]} for entry in entries],
        }

    def inspect(self, snapshot_id: str) -> DemSnapshot:
        match = SNAPSHOT_PATTERN.fullmatch(snapshot_id)
        if not match:
            raise RoutingError(
                "INVALID_REQUEST", "DEM snapshot ID must be sha256:<64 lowercase hex>"
            )
        target = self.snapshots / match.group(1)
        if not target.is_dir():
            raise RoutingError("DEM_SNAPSHOT_NOT_FOUND", "DEM snapshot does not exist")
        document = self._verify_snapshot(target, snapshot_id)
        return DemSnapshot("READY", snapshot_id, target / "manifest.json", document)

    @contextmanager
    def lease(self, snapshot_id: str) -> Iterator[DemSnapshot]:
        """Protect a verified snapshot and its referenced tiles during a future sampler call."""
        match = SNAPSHOT_PATTERN.fullmatch(snapshot_id)
        if match is None:
            raise RoutingError("INVALID_REQUEST", "invalid DEM snapshot ID")
        self._directories()
        deadline = time.monotonic() + self.config.lock_timeout_seconds
        with _Lock(self.locks / f"lease-{match.group(1)}.lock", self.config, deadline):
            yield self.inspect(snapshot_id)

    def prune(self, *, apply: bool = False) -> dict[str, Any]:
        """Age-based cache cleanup; never touch leased snapshots or referenced objects."""
        if not self.root.exists():
            return {
                "operation": "dem_prune",
                "status": "APPLIED" if apply else "DRY_RUN",
                "snapshots": [],
                "index": [],
                "objects": [],
            }
        self._directories()
        deadline = time.monotonic() + self.config.total_deadline_seconds
        with _Lock(self.locks / "maintenance.lock", self.config, deadline):
            now = time.time()
            old = self.config.prune_minimum_age_seconds
            snapshots: list[Path] = []
            for path in sorted(self.snapshots.iterdir()):
                if not path.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", path.name):
                    continue
                if (self.locks / f"lease-{path.name}.lock").exists():
                    continue
                if now - path.stat().st_mtime < old:
                    continue
                self._verify_snapshot(path, f"sha256:{path.name}")
                snapshots.append(path)
            index_paths: list[Path] = []
            for path in sorted(self.index.glob("*/*.json")):
                if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                    raise RoutingError("DEM_CACHE_CORRUPT", "DEM index inventory is unsafe")
                if (self.locks / f"tile-{path.stem}.lock").exists():
                    continue
                if now - path.stat().st_mtime >= old:
                    index_paths.append(path)
            remaining_hashes: set[str] = set()
            for path in self.snapshots.iterdir():
                if path in snapshots or not path.is_dir():
                    continue
                document = self._verify_snapshot(path, f"sha256:{path.name}")
                remaining_hashes.update(tile["sha256"] for tile in document["tiles"])
            for path in self.index.glob("*/*.json"):
                if path in index_paths:
                    continue
                entry = self._load_json(path)
                self._verify_entry(entry, expected_name=path.stem)
                remaining_hashes.add(entry["sha256"])
            objects: list[Path] = []
            for path in sorted(self.objects.glob("*/*.hgt.gz")):
                if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                    raise RoutingError("DEM_CACHE_CORRUPT", "DEM object inventory is unsafe")
                digest = path.name.removesuffix(".hgt.gz")
                if digest not in remaining_hashes and now - path.stat().st_mtime >= old:
                    objects.append(path)
            result = {
                "operation": "dem_prune",
                "status": "APPLIED" if apply else "DRY_RUN",
                "snapshots": [f"sha256:{path.name}" for path in snapshots],
                "index": [path.stem for path in index_paths],
                "objects": [path.name for path in objects],
            }
            if apply:
                for path in snapshots:
                    shutil.rmtree(path)
                for path in index_paths:
                    path.unlink()
                for path in objects:
                    path.unlink()
            return result

    def _directories(self) -> None:
        for path in (self.objects, self.snapshots, self.index, self.staging, self.locks):
            if path.is_symlink():
                raise RoutingError("DEM_CACHE_CORRUPT", "DEM cache directory is a symlink")
            path.mkdir(parents=True, exist_ok=True)

    def _ensure_tile(self, name: str, mode: DemMode, deadline: float) -> dict[str, Any]:
        with _Lock(self.locks / f"tile-{name}.lock", self.config, deadline):
            index_path = self.index / name[:3] / f"{name}.json"
            if index_path.exists():
                entry = self._load_json(index_path)
                self._verify_entry(entry, expected_name=name)
                return entry
            if mode == "offline":
                raise RoutingError("DEM_TILE_MISSING", "DEM tile is not cached", {"tile": name})
            return self._download_tile(name, index_path, deadline)

    def _download_tile(self, name: str, index_path: Path, deadline: float) -> dict[str, Any]:
        url = f"{MAPZEN_SKADI_EGM96_V1.canonical_document()['source_origin']}/skadi/{name[:3]}/{name}.hgt.gz"
        path = self.staging / f"tile-{name}-{os.getpid()}-{time.time_ns()}"
        digest = hashlib.sha256()
        size = 0
        headers: dict[str, str | None] = {}
        try:
            try:
                response = self.response_factory(
                    url, min(self.config.connect_read_timeout_seconds, self._remaining(deadline))
                )
            except TimeoutError as error:
                raise RoutingError(
                    "DEM_NETWORK_TIMEOUT", "DEM tile request timed out", {"tile": name}
                ) from error
            except (OSError, urllib.error.URLError) as error:
                raise RoutingError(
                    "DEM_DOWNLOAD_FAILED", "DEM tile request failed", {"tile": name}
                ) from error
            with response:
                status = getattr(response, "status", 200)
                final_url = response.geturl() if hasattr(response, "geturl") else url
                if status != 200 or final_url != url:
                    raise RoutingError(
                        "DEM_DOWNLOAD_FAILED",
                        "DEM source returned an invalid status or redirect",
                        {"tile": name},
                    )
                response_headers = getattr(response, "headers", {})
                headers = {
                    "etag": response_headers.get("ETag"),
                    "last_modified": response_headers.get("Last-Modified"),
                }
                declared = response_headers.get("Content-Length")
                if declared is not None:
                    try:
                        declared_size = int(declared)
                    except ValueError as error:
                        raise RoutingError(
                            "DEM_DOWNLOAD_FAILED", "invalid DEM content length"
                        ) from error
                    if (
                        declared_size < 1
                        or declared_size > self.config.maximum_compressed_tile_bytes
                    ):
                        raise RoutingError(
                            "RESOURCE_LIMIT_EXCEEDED", "DEM response exceeds compressed byte limit"
                        )
                else:
                    declared_size = None
                with path.open("xb") as target:
                    while True:
                        self._remaining(deadline)
                        chunk = response.read(self.config.io_chunk_bytes)
                        self._remaining(deadline)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.config.maximum_compressed_tile_bytes:
                            raise RoutingError(
                                "RESOURCE_LIMIT_EXCEEDED",
                                "DEM response exceeds compressed byte limit",
                            )
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if declared_size is not None and declared_size != size:
                    raise RoutingError("DEM_TILE_CORRUPT", "DEM content length mismatch")
            self._validate_gzip(path)
            object_path = self._object_path(digest.hexdigest())
            with _Lock(self.locks / "quota.lock", self.config, deadline):
                object_path.parent.mkdir(parents=True, exist_ok=True)
                if object_path.parent.is_symlink():
                    raise RoutingError("DEM_CACHE_CORRUPT", "DEM object directory is unsafe")
                if object_path.exists():
                    self._verify_object(object_path, size, digest.hexdigest())
                else:
                    if self._stored_object_bytes() + size > self.config.maximum_total_cache_bytes:
                        raise RoutingError(
                            "RESOURCE_LIMIT_EXCEEDED", "DEM object cache quota exceeded"
                        )
                    path.replace(object_path)
            entry = {
                "name": name,
                "sha256": digest.hexdigest(),
                "size_bytes": size,
                "source_url": url,
                **headers,
            }
            index_path.parent.mkdir(parents=True, exist_ok=True)
            if index_path.parent.is_symlink():
                raise RoutingError("DEM_CACHE_CORRUPT", "DEM index directory is unsafe")
            self._write_json(index_path, entry)
            return entry
        except TimeoutError as error:
            raise RoutingError(
                "DEM_NETWORK_TIMEOUT", "DEM deadline exceeded", {"tile": name}
            ) from error
        except RoutingError:
            raise
        except (OSError, zlib.error, EOFError) as error:
            raise RoutingError(
                "DEM_DOWNLOAD_FAILED", "DEM tile download or validation failed", {"tile": name}
            ) from error
        finally:
            path.unlink(missing_ok=True)

    def _verify_entry(self, entry: dict[str, Any], *, expected_name: str) -> None:
        if entry.get("name") != expected_name or not TILE_PATTERN.fullmatch(expected_name):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM tile index has invalid identity")
        digest = entry.get("sha256")
        size = entry.get("size_bytes")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM tile index has invalid hash")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 < size <= self.config.maximum_compressed_tile_bytes
        ):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM tile index has invalid size")
        self._verify_object(self._object_path(digest), size, digest)

    def _verify_object(self, path: Path, expected_size: int, expected_sha: str) -> None:
        if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM object is missing or unsafe")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(self.config.io_chunk_bytes):
                size += len(chunk)
                if size > self.config.maximum_compressed_tile_bytes:
                    raise RoutingError("DEM_CACHE_CORRUPT", "DEM object exceeds byte limit")
                digest.update(chunk)
        if size != expected_size or digest.hexdigest() != expected_sha:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM object does not match manifest")
        try:
            self._validate_gzip(path)
        except RoutingError as error:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM object gzip is invalid") from error

    def _stored_object_bytes(self) -> int:
        total = 0
        for path in self.objects.glob("*/*.hgt.gz"):
            if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
                raise RoutingError("DEM_CACHE_CORRUPT", "DEM object inventory is unsafe")
            total += path.stat().st_size
            if total > self.config.maximum_total_cache_bytes:
                raise RoutingError("DEM_CACHE_CORRUPT", "DEM object cache already exceeds quota")
        return total

    def _validate_gzip(self, path: Path) -> None:
        expected = 25_934_402
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        count = 0
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(self.config.io_chunk_bytes):
                    output = decoder.decompress(chunk, expected + 1 - count)
                    count += len(output)
                    if count > expected or decoder.unconsumed_tail or decoder.unused_data:
                        raise RoutingError("DEM_TILE_CORRUPT", "DEM raster has extra data")
            if not decoder.eof or count != expected:
                raise RoutingError("DEM_TILE_CORRUPT", "DEM raster size or gzip CRC is invalid")
        except (OSError, zlib.error) as error:
            raise RoutingError("DEM_TILE_CORRUPT", "DEM gzip is invalid") from error

    def _verify_snapshot(self, target: Path, expected_id: str) -> dict[str, Any]:
        if target.is_symlink() or not target.is_dir():
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot directory is unsafe")
        document = self._load_json(target / "manifest.json")
        key = document.get("cache_key")
        if not isinstance(key, dict) or key.get("schema_version") != SNAPSHOT_SCHEMA:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot schema is invalid")
        digest = hashlib.sha256(_canonical(key)).hexdigest()
        if document.get("dem_snapshot_id") != expected_id or f"sha256:{digest}" != expected_id:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot identity mismatch")
        tiles = document.get("tiles")
        if not isinstance(tiles, list) or not 0 < len(tiles) <= self.config.maximum_tiles:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot tile inventory is invalid")
        if not all(isinstance(tile, dict) and isinstance(tile.get("name"), str) for tile in tiles):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot tile inventory is invalid")
        names = [tile["name"] for tile in tiles]
        if names != sorted(set(names)) or key != self._cache_key(tiles):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM snapshot key is not canonical")
        for tile in tiles:
            self._verify_entry(tile, expected_name=tile["name"])
        return document

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / f"{digest}.hgt.gz"

    def _load_json(self, path: Path) -> dict[str, Any]:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > self.config.maximum_manifest_bytes
        ):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM manifest is missing, unsafe or oversized")
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM manifest is unreadable") from error
        if not isinstance(document, dict):
            raise RoutingError("DEM_CACHE_CORRUPT", "DEM manifest must be an object")
        return document

    def _write_json(self, path: Path, document: dict[str, Any]) -> None:
        encoded = _canonical(document)
        if len(encoded) > self.config.maximum_manifest_bytes:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM manifest exceeds byte limit")
        stage = self.staging / f"index-{os.getpid()}-{time.time_ns()}"
        with stage.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        stage.replace(path)

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DEM deadline expired")
        return remaining


class _Lock(AbstractContextManager["_Lock"]):
    def __init__(self, path: Path, config: DemCacheConfig, deadline: float) -> None:
        self.path = path
        self.config = config
        self.deadline = min(deadline, time.monotonic() + config.lock_timeout_seconds)

    def __enter__(self) -> Self:
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.monotonic() >= self.deadline:
                    age = time.time() - self.path.stat().st_mtime
                    raise RoutingError(
                        "DEM_LOCK_TIMEOUT",
                        "DEM cache lock wait expired",
                        {"stale": age >= self.config.stale_lock_seconds},
                    ) from None
                time.sleep(self.config.lock_poll_seconds)
                continue
            try:
                os.write(descriptor, str(os.getpid()).encode())
            finally:
                os.close(descriptor)
            return self

    def __exit__(self, *args: object) -> None:
        self.path.unlink(missing_ok=True)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
