"""Stable internal and protocol models for OSM snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from warpbuster_osm_manager.config import (
    COVERAGE_SCHEME_VERSION,
    DATASET_PROFILE,
    MANIFEST_VERSION,
    PROTOCOL_VERSION,
)


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def format_datetime(value: datetime) -> str:
    """Serialize an aware datetime in stable UTC form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_datetime(value: str) -> datetime:
    """Parse a protocol timestamp."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


@dataclass(frozen=True, slots=True, order=True)
class CellId:
    """One Web Mercator coverage cell."""

    zoom: int
    x: int
    y: int

    def __str__(self) -> str:
        return f"{self.zoom}/{self.x}/{self.y}"

    @classmethod
    def parse(cls, value: str) -> CellId:
        zoom, x, y = (int(part) for part in value.split("/"))
        return cls(zoom=zoom, x=x, y=y)


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """One WGS84 longitude/latitude point."""

    longitude: float
    latitude: float


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """A non-antimeridian-crossing WGS84 bounding box."""

    west: float
    south: float
    east: float
    north: float

    def as_overpass(self) -> str:
        return f"{self.south:.7f},{self.west:.7f},{self.north:.7f},{self.east:.7f}"

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CoveragePlan:
    """Validated, bounded, source-independent cache coverage request."""

    source_kind: str
    cells: tuple[CellId, ...]
    buffer_m: float
    area_km2: float
    request_fingerprint: str
    dataset_profile: str = DATASET_PROFILE
    coverage_scheme: str = COVERAGE_SCHEME_VERSION


@dataclass(frozen=True, slots=True)
class CellBatch:
    """Neighboring requested cells fetched by one bounded Overpass request."""

    cells: tuple[CellId, ...]
    bounds: BoundingBox


@dataclass(frozen=True, slots=True)
class CachedCell:
    """Current cache mapping for one coverage cell."""

    cell: CellId
    blob_sha256: str
    fetched_at: datetime
    osm_base_timestamp: str | None
    source_kind: str
    endpoint: str | None
    size_bytes: int
    media_type: str
    path: Path


@dataclass(frozen=True, slots=True)
class SnapshotDataFile:
    """One immutable raw data file referenced by a snapshot."""

    path: Path
    media_type: str
    sha256: str
    size_bytes: int
    source_kind: str
    endpoint: str | None
    osm_base_timestamp: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path.resolve()),
            "media_type": self.media_type,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_kind": self.source_kind,
            "endpoint": self.endpoint,
            "osm_base_timestamp": self.osm_base_timestamp,
        }


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """Immutable auditable description of one complete coverage snapshot."""

    manager_version: str
    snapshot_id: str
    dataset_profile: str
    created_at: datetime
    osm_base_timestamp: str | None
    request_fingerprint: str
    source_kind: str
    coverage_scheme: str
    cell_ids: tuple[str, ...]
    cell_blob_sha256: tuple[tuple[str, str], ...]
    cell_fetched_at: tuple[tuple[str, str], ...]
    requested_buffer_m: float
    requested_area_km2: float
    manager_settings: tuple[tuple[str, int | float | str], ...]
    data_files: tuple[SnapshotDataFile, ...]
    stale: bool
    protocol_version: int = PROTOCOL_VERSION
    manifest_version: int = MANIFEST_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": self.protocol_version,
            "manifest_version": self.manifest_version,
            "manager_version": self.manager_version,
            "snapshot_id": self.snapshot_id,
            "dataset_profile": self.dataset_profile,
            "created_at": format_datetime(self.created_at),
            "osm_base_timestamp": self.osm_base_timestamp,
            "request_fingerprint": self.request_fingerprint,
            "source_kind": self.source_kind,
            "coverage": {
                "scheme": self.coverage_scheme,
                "cell_ids": list(self.cell_ids),
                "cell_blobs": dict(self.cell_blob_sha256),
                "cell_fetched_at": dict(self.cell_fetched_at),
                "buffer_m": self.requested_buffer_m,
                "area_km2": self.requested_area_km2,
            },
            "data_files": [item.as_dict() for item in self.data_files],
            "manager_settings": dict(self.manager_settings),
            "stale": self.stale,
            "attribution": "OpenStreetMap contributors",
            "copyright_url": "https://www.openstreetmap.org/copyright",
            "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        }


@dataclass(frozen=True, slots=True)
class EnsureResult:
    """Protocol result returned by a successful ensure operation."""

    manifest: SnapshotManifest
    manifest_path: Path
    downloaded: bool
    stale: bool
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "ensure",
            "status": "ready",
            "snapshot_id": self.manifest.snapshot_id,
            "manifest_path": str(self.manifest_path.resolve()),
            "data_files": [item.as_dict() for item in self.manifest.data_files],
            "downloaded": self.downloaded,
            "stale": self.stale,
            "warnings": list(self.warnings),
        }
