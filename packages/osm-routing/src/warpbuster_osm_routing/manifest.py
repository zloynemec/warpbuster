"""Read and verify the public OSM Manager protocol v1 manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.models import Snapshot, SnapshotDataFile

PROTOCOL_VERSION = 1
MANIFEST_VERSION = 1
DATASET_PROFILE = "pedestrian-routing-v1"
SUPPORTED_MEDIA_TYPES = {
    "application/vnd.openstreetmap.data+xml",
    "application/vnd.openstreetmap.data+xml+gzip",
    "application/vnd.openstreetmap.data+pbf",
}


def load_snapshot(path: Path, config: RoutingCacheConfig | None = None) -> Snapshot:
    """Validate the bounded manifest and every referenced immutable data file."""
    effective = config or RoutingCacheConfig.defaults()
    manifest_path = path.expanduser().resolve()
    try:
        size = manifest_path.stat().st_size
    except OSError as error:
        raise RoutingSpikeError("manifest_unreadable", f"cannot read manifest: {error}") from error
    if size > effective.maximum_manifest_bytes:
        raise RoutingSpikeError(
            "manifest_too_large",
            "manifest exceeds maximum size",
            {"size_bytes": size, "limit_bytes": effective.maximum_manifest_bytes},
        )
    raw = manifest_path.read_bytes()
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoutingSpikeError(
            "manifest_invalid", f"manifest is not valid JSON: {error}"
        ) from error
    if not isinstance(document, dict):
        raise RoutingSpikeError("manifest_invalid", "manifest root must be an object")
    _require_equal(document, "protocol_version", PROTOCOL_VERSION)
    _require_equal(document, "manifest_version", MANIFEST_VERSION)
    _require_equal(document, "dataset_profile", DATASET_PROFILE)
    snapshot_id = _required_string(document, "snapshot_id")
    manager_version = _required_string(document, "manager_version")
    files = document.get("data_files")
    if not isinstance(files, list) or not files:
        raise RoutingSpikeError("manifest_invalid", "data_files must be a non-empty array")
    if len(files) > effective.maximum_data_files:
        raise RoutingSpikeError(
            "resource_limit",
            "snapshot has too many data files",
            {"count": len(files), "limit": effective.maximum_data_files},
        )
    verified: list[SnapshotDataFile] = []
    total_size = 0
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            raise RoutingSpikeError("manifest_invalid", f"data_files[{index}] must be an object")
        data_path = Path(_required_string(item, "path"))
        if not data_path.is_absolute():
            raise RoutingSpikeError(
                "manifest_invalid", f"data_files[{index}].path must be absolute"
            )
        media_type = _required_string(item, "media_type")
        if media_type not in SUPPORTED_MEDIA_TYPES:
            raise RoutingSpikeError(
                "unsupported_media_type",
                f"unsupported OSM media type: {media_type}",
                {"index": index},
            )
        declared_size = item.get("size_bytes")
        if not isinstance(declared_size, int) or declared_size < 0:
            raise RoutingSpikeError(
                "manifest_invalid", f"data_files[{index}].size_bytes must be non-negative"
            )
        declared_hash = _required_string(item, "sha256")
        actual_size, actual_hash = _hash_file(data_path, effective.io_chunk_bytes)
        if actual_size != declared_size:
            raise RoutingSpikeError(
                "data_size_mismatch",
                f"data file size mismatch: {data_path}",
                {"declared": declared_size, "actual": actual_size},
            )
        if actual_hash != declared_hash:
            raise RoutingSpikeError(
                "data_hash_mismatch",
                f"data file hash mismatch: {data_path}",
                {"declared": declared_hash, "actual": actual_hash},
            )
        total_size += actual_size
        if total_size > effective.maximum_total_source_bytes:
            raise RoutingSpikeError(
                "resource_limit",
                "snapshot data exceeds maximum total size",
                {"size_bytes": total_size, "limit_bytes": effective.maximum_total_source_bytes},
            )
        verified.append(
            SnapshotDataFile(
                path=data_path.resolve(),
                media_type=media_type,
                sha256=declared_hash,
                size_bytes=declared_size,
            )
        )
    timestamp = document.get("osm_base_timestamp")
    if timestamp is not None and not isinstance(timestamp, str):
        raise RoutingSpikeError("manifest_invalid", "osm_base_timestamp must be a string or null")
    return Snapshot(
        manifest_path=manifest_path,
        snapshot_id=snapshot_id,
        dataset_profile=DATASET_PROFILE,
        manager_version=manager_version,
        osm_base_timestamp=timestamp,
        data_files=tuple(sorted(verified, key=lambda item: (item.sha256, str(item.path)))),
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        attribution=_optional_string(document, "attribution", "OpenStreetMap contributors"),
        copyright_url=_optional_string(
            document, "copyright_url", "https://www.openstreetmap.org/copyright"
        ),
        license_url=_optional_string(
            document, "license_url", "https://opendatacommons.org/licenses/odbl/1-0/"
        ),
    )


def _require_equal(document: dict[str, Any], key: str, expected: object) -> None:
    actual = document.get(key)
    if actual != expected:
        raise RoutingSpikeError(
            "unsupported_manifest",
            f"unsupported {key}: {actual!r}",
            {"expected": expected},
        )


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise RoutingSpikeError("manifest_invalid", f"{key} must be a non-empty string")
    return value


def _optional_string(document: dict[str, Any], key: str, default: str) -> str:
    value = document.get(key, default)
    if not isinstance(value, str) or not value:
        raise RoutingSpikeError("manifest_invalid", f"{key} must be a non-empty string")
    return value


def _hash_file(path: Path, chunk_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_bytes):
                size += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise RoutingSpikeError(
            "data_unreadable", f"cannot read data file {path}: {error}"
        ) from error
    return size, digest.hexdigest()
