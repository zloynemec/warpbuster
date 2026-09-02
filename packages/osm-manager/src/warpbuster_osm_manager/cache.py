"""Immutable content-addressed OSM blobs, coverage index, and snapshots."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from warpbuster_osm_manager.config import MANIFEST_VERSION, PROTOCOL_VERSION, OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import (
    CachedCell,
    CellId,
    CoveragePlan,
    SnapshotDataFile,
    SnapshotManifest,
    format_datetime,
    parse_datetime,
    utc_now,
)

OSM_XML_GZIP_MEDIA_TYPE = "application/vnd.openstreetmap.data+xml+gzip"
OSM_PBF_MEDIA_TYPE = "application/vnd.openstreetmap.data+pbf"
SNAPSHOT_ID_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")
PROCESS_STARTED_AT = format_datetime(utc_now())


@dataclass(frozen=True, slots=True)
class PublishedBlob:
    """One validated immutable cache blob."""

    sha256: str
    path: Path
    size_bytes: int
    media_type: str


class CacheStore:
    """Filesystem and SQLite-backed cache with recoverable derived index."""

    def __init__(self, config: OsmManagerConfig) -> None:
        self.config = config
        self.root = config.cache_directory.expanduser().resolve()
        self.blobs_directory = self.root / "blobs"
        self.imports_directory = self.root / "imports"
        self.snapshots_directory = self.root / "snapshots"
        self.indexes_directory = self.root / "indexes"
        self.locks_directory = self.root / "locks"
        self.temporary_directory = self.root / "tmp"
        self.index_path = self.indexes_directory / "coverage.sqlite"
        try:
            for directory in (
                self.blobs_directory,
                self.imports_directory,
                self.snapshots_directory,
                self.indexes_directory,
                self.locks_directory,
                self.temporary_directory,
            ):
                directory.mkdir(parents=True, exist_ok=True)
            self._initialize_index()
        except (OSError, sqlite3.Error) as error:
            raise _cache_error(f"cannot initialize OSM cache at {self.root}: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.index_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_index(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coverage (
                    dataset_profile TEXT NOT NULL,
                    coverage_scheme TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    blob_sha256 TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    osm_base_timestamp TEXT,
                    source_kind TEXT NOT NULL,
                    endpoint TEXT,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    PRIMARY KEY (dataset_profile, coverage_scheme, cell_id)
                )
                """
            )

    @contextmanager
    def ensure_lock(self) -> Iterator[None]:
        """Serialize cache publication with bounded stale-lock recovery."""
        lock_path = self.locks_directory / "ensure.lock"
        started = time.monotonic()
        owner_token = secrets.token_hex(16)
        while True:
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "pid": os.getpid(),
                            "process_started_at": PROCESS_STARTED_AT,
                            "created_at": format_datetime(utc_now()),
                            "owner_token": owner_token,
                        },
                        stream,
                    )
                break
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    lock_owner = _read_lock_owner(lock_path)
                    if age > self.config.stale_lock_seconds and not _process_is_alive(
                        lock_owner.get("pid")
                    ):
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                except OSError as error:
                    raise _cache_error(f"cannot inspect cache lock: {error}") from error
                if time.monotonic() - started >= self.config.cache_lock_timeout_seconds:
                    raise OsmManagerError(
                        ErrorCode.CACHE_LOCK_TIMEOUT,
                        "timed out waiting for OSM cache lock",
                        {"lock_path": str(lock_path)},
                    ) from None
                time.sleep(self.config.lock_poll_seconds)
            except OSError as error:
                raise _cache_error(f"cannot create cache lock: {error}") from error
        try:
            yield
        finally:
            try:
                if _read_lock_owner(lock_path).get("owner_token") == owner_token:
                    lock_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise _cache_error(f"cannot remove cache lock: {error}") from error

    def new_temporary_path(self, suffix: str) -> Path:
        """Reserve a task-local temporary cache path."""
        descriptor, raw_path = tempfile.mkstemp(dir=self.temporary_directory, suffix=suffix)
        os.close(descriptor)
        return Path(raw_path)

    def current_cells(self, plan: CoveragePlan) -> dict[CellId, CachedCell]:
        """Return valid current mappings for the requested cells."""
        requested = {str(cell): cell for cell in plan.cells}
        if not requested:
            return {}
        placeholders = ",".join("?" for _ in requested)
        query = f"""
            SELECT * FROM coverage
            WHERE dataset_profile = ? AND coverage_scheme = ?
              AND cell_id IN ({placeholders})
        """
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    query,
                    (plan.dataset_profile, plan.coverage_scheme, *requested),
                ).fetchall()
        except sqlite3.Error as error:
            raise _cache_error(f"cannot read coverage index: {error}") from error
        result: dict[CellId, CachedCell] = {}
        for row in rows:
            path = Path(str(row["path"]))
            try:
                valid_size = path.is_file() and path.stat().st_size == int(row["size_bytes"])
            except OSError:
                valid_size = False
            if not valid_size:
                continue
            cell = requested[str(row["cell_id"])]
            result[cell] = CachedCell(
                cell=cell,
                blob_sha256=str(row["blob_sha256"]),
                fetched_at=parse_datetime(str(row["fetched_at"])),
                osm_base_timestamp=(
                    str(row["osm_base_timestamp"])
                    if row["osm_base_timestamp"] is not None
                    else None
                ),
                source_kind=str(row["source_kind"]),
                endpoint=str(row["endpoint"]) if row["endpoint"] is not None else None,
                size_bytes=int(row["size_bytes"]),
                media_type=str(row["media_type"]),
                path=path,
            )
        return result

    def publish_overpass_xml(self, source: Path) -> PublishedBlob:
        """Deterministically gzip and content-address one validated XML response."""
        compressed = self.new_temporary_path(".osm.xml.gz")
        try:
            with (
                source.open("rb") as input_stream,
                compressed.open("wb") as raw_output,
                gzip.GzipFile(fileobj=raw_output, mode="wb", filename="", mtime=0) as output,
            ):
                while chunk := input_stream.read(self.config.http_read_chunk_bytes):
                    output.write(chunk)
            return self._publish_file(
                compressed, self.blobs_directory, ".osm.xml.gz", OSM_XML_GZIP_MEDIA_TYPE
            )
        finally:
            compressed.unlink(missing_ok=True)

    def publish_import(self, source: Path) -> PublishedBlob:
        """Copy one validated imported OSM file into content-addressed storage."""
        lower = source.name.casefold()
        if lower.endswith(".osm.pbf") or lower.endswith(".pbf"):
            suffix = ".osm.pbf"
            media_type = OSM_PBF_MEDIA_TYPE
        elif lower.endswith(".osm.gz"):
            suffix = ".osm.gz"
            media_type = OSM_XML_GZIP_MEDIA_TYPE
        else:
            suffix = ".osm"
            media_type = "application/vnd.openstreetmap.data+xml"
        temporary = self.new_temporary_path(suffix)
        try:
            with source.open("rb") as input_stream, temporary.open("wb") as output:
                while chunk := input_stream.read(self.config.http_read_chunk_bytes):
                    output.write(chunk)
            return self._publish_file(temporary, self.imports_directory, suffix, media_type)
        finally:
            temporary.unlink(missing_ok=True)

    def _publish_file(
        self, source: Path, directory: Path, suffix: str, media_type: str
    ) -> PublishedBlob:
        digest, size = hash_file(source, self.config.http_read_chunk_bytes)
        destination = directory / f"{digest}{suffix}"
        try:
            if not destination.exists():
                os.replace(source, destination)
            return PublishedBlob(digest, destination.resolve(), size, media_type)
        except OSError as error:
            raise _cache_error(f"cannot publish cache blob: {error}") from error

    def commit_cells(self, cells: Iterable[CachedCell], plan: CoveragePlan) -> None:
        """Atomically replace current mappings after all blobs are available."""
        rows = tuple(cells)
        try:
            with self._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO coverage (
                        dataset_profile, coverage_scheme, cell_id, blob_sha256,
                        fetched_at, osm_base_timestamp, source_kind, endpoint,
                        size_bytes, media_type, path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dataset_profile, coverage_scheme, cell_id) DO UPDATE SET
                        blob_sha256=excluded.blob_sha256,
                        fetched_at=excluded.fetched_at,
                        osm_base_timestamp=excluded.osm_base_timestamp,
                        source_kind=excluded.source_kind,
                        endpoint=excluded.endpoint,
                        size_bytes=excluded.size_bytes,
                        media_type=excluded.media_type,
                        path=excluded.path
                    """,
                    (
                        (
                            plan.dataset_profile,
                            plan.coverage_scheme,
                            str(cell.cell),
                            cell.blob_sha256,
                            format_datetime(cell.fetched_at),
                            cell.osm_base_timestamp,
                            cell.source_kind,
                            cell.endpoint,
                            cell.size_bytes,
                            cell.media_type,
                            str(cell.path),
                        )
                        for cell in rows
                    ),
                )
        except sqlite3.Error as error:
            raise _cache_error(f"cannot update coverage index: {error}") from error

    def create_snapshot(
        self,
        plan: CoveragePlan,
        cells: dict[CellId, CachedCell],
        *,
        now: datetime,
        stale: bool,
        manager_version: str,
    ) -> tuple[SnapshotManifest, Path]:
        """Create or reuse an immutable manifest for an exact cell/blob mapping."""
        missing = set(plan.cells) - set(cells)
        if missing:
            raise _cache_error("cannot create a snapshot with incomplete coverage")
        identity = json.dumps(
            {
                "dataset_profile": plan.dataset_profile,
                "coverage_scheme": plan.coverage_scheme,
                "request_fingerprint": plan.request_fingerprint,
                "source_kind": plan.source_kind,
                "manager_settings": self.config.manifest_settings(),
                "cells": [[str(cell), cells[cell].blob_sha256] for cell in sorted(plan.cells)],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        digest = hashlib.sha256(identity).hexdigest()
        snapshot_id = f"sha256:{digest}"
        directory = self.snapshots_directory / digest
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            document = self.read_manifest(snapshot_id)
            return _manifest_from_document(document), manifest_path.resolve()

        by_blob: dict[str, CachedCell] = {}
        for cell in cells.values():
            by_blob.setdefault(cell.blob_sha256, cell)
        data_files = tuple(
            SnapshotDataFile(
                path=cell.path.resolve(),
                media_type=cell.media_type,
                sha256=cell.blob_sha256,
                size_bytes=cell.size_bytes,
                source_kind=cell.source_kind,
                endpoint=cell.endpoint,
                osm_base_timestamp=cell.osm_base_timestamp,
            )
            for _, cell in sorted(by_blob.items())
        )
        timestamps = sorted(
            {cell.osm_base_timestamp for cell in cells.values() if cell.osm_base_timestamp}
        )
        manifest = SnapshotManifest(
            protocol_version=PROTOCOL_VERSION,
            manifest_version=MANIFEST_VERSION,
            manager_version=manager_version,
            snapshot_id=snapshot_id,
            dataset_profile=plan.dataset_profile,
            created_at=now,
            osm_base_timestamp=timestamps[0] if timestamps else None,
            request_fingerprint=plan.request_fingerprint,
            coverage_scheme=plan.coverage_scheme,
            source_kind=plan.source_kind,
            data_files=data_files,
            stale=stale,
            cell_ids=tuple(str(cell) for cell in sorted(plan.cells)),
            cell_blob_sha256=tuple(
                (str(cell), cells[cell].blob_sha256) for cell in sorted(plan.cells)
            ),
            cell_fetched_at=tuple(
                (str(cell), format_datetime(cells[cell].fetched_at)) for cell in sorted(plan.cells)
            ),
            requested_buffer_m=plan.buffer_m,
            requested_area_km2=plan.area_km2,
            manager_settings=tuple(sorted(self.config.manifest_settings().items())),
        )
        temporary_directory = self.snapshots_directory / f".{digest}.{os.getpid()}.tmp"
        try:
            temporary_directory.mkdir(parents=False, exist_ok=False)
            temporary_manifest = temporary_directory / "manifest.json"
            temporary_manifest.write_text(
                json.dumps(manifest.as_dict(), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.replace(temporary_directory, directory)
            except OSError:
                if not manifest_path.exists():
                    raise
                shutil.rmtree(temporary_directory, ignore_errors=True)
            return manifest, manifest_path.resolve()
        except OSError as error:
            shutil.rmtree(temporary_directory, ignore_errors=True)
            raise _cache_error(f"cannot publish snapshot manifest: {error}") from error

    def read_manifest(self, snapshot_id: str) -> dict[str, Any]:
        """Read one exact snapshot manifest after validating its identifier."""
        directory = self._snapshot_directory(snapshot_id)
        path = directory / "manifest.json"
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise _cache_error(f"cannot read snapshot {snapshot_id}: {error}") from error
        if document.get("snapshot_id") != snapshot_id:
            raise _cache_error("snapshot manifest identifier mismatch")
        if not isinstance(document, dict):
            raise _cache_error("snapshot manifest must be a JSON object")
        return cast(dict[str, Any], document)

    def list_manifests(self) -> tuple[dict[str, Any], ...]:
        """Return every readable snapshot manifest in creation order."""
        documents: list[dict[str, Any]] = []
        for path in sorted(self.snapshots_directory.glob("*/manifest.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except OSError, UnicodeError, json.JSONDecodeError:
                continue
            if isinstance(document, dict):
                documents.append(document)
        documents.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return tuple(documents)

    def verify_manifest(self, snapshot_id: str) -> dict[str, Any]:
        """Verify every referenced data file hash and size."""
        document = self.read_manifest(snapshot_id)
        checked = 0
        for item in document.get("data_files", []):
            path = Path(str(item["path"]))
            digest, size = hash_file(path, self.config.http_read_chunk_bytes)
            if digest != item.get("sha256") or size != item.get("size_bytes"):
                raise _cache_error(f"snapshot data file verification failed: {path}")
            checked += 1
        return {"manifest": document, "verified_data_file_count": checked}

    def remove_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Remove exactly one manifest directory; shared blobs remain untouched."""
        directory = self._snapshot_directory(snapshot_id)
        document = self.read_manifest(snapshot_id)
        try:
            shutil.rmtree(directory)
        except OSError as error:
            raise _cache_error(f"cannot remove snapshot {snapshot_id}: {error}") from error
        return {
            "snapshot_id": snapshot_id,
            "removed_manifest": True,
            "referenced_data_file_count": len(document.get("data_files", [])),
        }

    def prune(self, *, apply: bool, now: datetime) -> dict[str, Any]:
        """Find or remove old unreferenced blobs without deleting snapshots."""
        referenced = self._referenced_paths()
        candidates: list[Path] = []
        for directory in (self.blobs_directory, self.imports_directory):
            for path in directory.iterdir():
                if not path.is_file() or path.resolve() in referenced:
                    continue
                age = now.timestamp() - path.stat().st_mtime
                if age >= self.config.prune_minimum_age_seconds:
                    candidates.append(path)
        total = sum(path.stat().st_size for path in candidates)
        if apply:
            for path in candidates:
                path.unlink(missing_ok=True)
        return {
            "applied": apply,
            "candidate_count": len(candidates),
            "size_bytes": total,
            "paths": [str(path.resolve()) for path in sorted(candidates)],
        }

    def rebuild_index(self) -> int:
        """Rebuild the derived coverage index from immutable snapshot manifests."""
        restored = 0
        try:
            with self._connect() as connection:
                connection.execute("DELETE FROM coverage")
                documents = sorted(
                    self.list_manifests(), key=lambda item: str(item.get("created_at", ""))
                )
                for document in documents:
                    coverage = document.get("coverage")
                    if not isinstance(coverage, dict):
                        continue
                    cell_blobs = coverage.get("cell_blobs")
                    if not isinstance(cell_blobs, dict):
                        continue
                    cell_fetched_at = coverage.get("cell_fetched_at")
                    if not isinstance(cell_fetched_at, dict):
                        continue
                    data_files = document.get("data_files")
                    if not isinstance(data_files, list):
                        continue
                    files = {
                        item.get("sha256"): item
                        for item in data_files
                        if isinstance(item, dict) and Path(str(item.get("path", ""))).is_file()
                    }
                    for cell_id, blob_sha256 in cell_blobs.items():
                        item = files.get(blob_sha256)
                        if item is None or cell_id not in cell_fetched_at:
                            continue
                        connection.execute(
                            """
                            INSERT INTO coverage (
                                dataset_profile, coverage_scheme, cell_id, blob_sha256,
                                fetched_at, osm_base_timestamp, source_kind, endpoint,
                                size_bytes, media_type, path
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(dataset_profile, coverage_scheme, cell_id) DO UPDATE SET
                                blob_sha256=excluded.blob_sha256,
                                fetched_at=excluded.fetched_at,
                                osm_base_timestamp=excluded.osm_base_timestamp,
                                source_kind=excluded.source_kind,
                                endpoint=excluded.endpoint,
                                size_bytes=excluded.size_bytes,
                                media_type=excluded.media_type,
                                path=excluded.path
                            """,
                            (
                                document["dataset_profile"],
                                coverage["scheme"],
                                cell_id,
                                blob_sha256,
                                cell_fetched_at[cell_id],
                                item.get("osm_base_timestamp"),
                                item["source_kind"],
                                item.get("endpoint"),
                                item["size_bytes"],
                                item["media_type"],
                                item["path"],
                            ),
                        )
                        restored += 1
        except sqlite3.Error as error:
            raise _cache_error(f"cannot rebuild coverage index: {error}") from error
        return restored

    def _referenced_paths(self) -> set[Path]:
        referenced: set[Path] = set()
        for document in self.list_manifests():
            referenced.update(Path(str(item["path"])).resolve() for item in document["data_files"])
        try:
            with self._connect() as connection:
                referenced.update(
                    Path(str(row[0])).resolve()
                    for row in connection.execute("SELECT path FROM coverage")
                )
        except sqlite3.Error as error:
            raise _cache_error(f"cannot inspect coverage index: {error}") from error
        return referenced

    def _snapshot_directory(self, snapshot_id: str) -> Path:
        match = SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id)
        if match is None:
            raise OsmManagerError(
                ErrorCode.INVALID_INPUT,
                "snapshot ID must have the form sha256:<64 lowercase hex characters>",
            )
        return self.snapshots_directory / match.group(1)


def hash_file(path: Path, chunk_bytes: int) -> tuple[str, int]:
    """Stream a file into SHA-256 and byte count."""
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_bytes):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise _cache_error(f"cannot hash cache file {path}: {error}") from error
    return digest.hexdigest(), size


def _read_lock_owner(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeError, json.JSONDecodeError:
        return {}
    if not isinstance(document, dict):
        return {}
    return cast(dict[str, Any], document)


def _process_is_alive(value: object) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError, OSError:
        return True
    return True


def _manifest_from_document(document: dict[str, Any]) -> SnapshotManifest:
    coverage = document["coverage"]
    return SnapshotManifest(
        protocol_version=int(document["protocol_version"]),
        manifest_version=int(document["manifest_version"]),
        manager_version=str(document["manager_version"]),
        snapshot_id=str(document["snapshot_id"]),
        dataset_profile=str(document["dataset_profile"]),
        created_at=parse_datetime(str(document["created_at"])),
        osm_base_timestamp=(
            str(document["osm_base_timestamp"])
            if document.get("osm_base_timestamp") is not None
            else None
        ),
        request_fingerprint=str(document["request_fingerprint"]),
        coverage_scheme=str(coverage["scheme"]),
        source_kind=str(document["source_kind"]),
        cell_ids=tuple(str(value) for value in coverage["cell_ids"]),
        cell_blob_sha256=tuple(
            (str(cell), str(blob)) for cell, blob in coverage.get("cell_blobs", {}).items()
        ),
        cell_fetched_at=tuple(
            (str(cell), str(fetched_at))
            for cell, fetched_at in coverage.get("cell_fetched_at", {}).items()
        ),
        requested_buffer_m=float(coverage["buffer_m"]),
        requested_area_km2=float(coverage["area_km2"]),
        manager_settings=tuple(
            (str(name), value)
            for name, value in document.get("manager_settings", {}).items()
            if isinstance(value, (int, float, str)) and not isinstance(value, bool)
        ),
        data_files=tuple(
            SnapshotDataFile(
                path=Path(item["path"]),
                media_type=str(item["media_type"]),
                sha256=str(item["sha256"]),
                size_bytes=int(item["size_bytes"]),
                source_kind=str(item["source_kind"]),
                endpoint=str(item["endpoint"]) if item.get("endpoint") else None,
                osm_base_timestamp=(
                    str(item["osm_base_timestamp"]) if item.get("osm_base_timestamp") else None
                ),
            )
            for item in document["data_files"]
        ),
        stale=bool(document["stale"]),
    )


def _cache_error(message: str) -> OsmManagerError:
    return OsmManagerError(ErrorCode.CACHE_IO_ERROR, message)
