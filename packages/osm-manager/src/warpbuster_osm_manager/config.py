"""All named OSM Manager settings, units, defaults, and validation."""

from __future__ import annotations

import os
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Self, get_type_hints

PROTOCOL_VERSION = 1
MANIFEST_VERSION = 1
DATASET_PROFILE = "pedestrian-routing-v1"
COVERAGE_SCHEME_VERSION = "web-mercator-v1"
DEFAULT_CONFIG_FILENAME = "osm-manager.toml"

DEFAULT_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_USER_AGENT = "warpbuster-osm-manager/0.1 (+https://github.com/zloynemec/warpbuster)"
DEFAULT_GPX_CORRIDOR_BUFFER_M = 1_000.0
DEFAULT_CACHE_GRID_ZOOM = 12
DEFAULT_COVERAGE_SAMPLE_CELL_FRACTION = 0.5
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_MAXIMUM_REQUESTED_AREA_KM2 = 2_000.0
DEFAULT_MAXIMUM_ENSURE_CELLS = 512
DEFAULT_MAXIMUM_CELLS_PER_OVERPASS_REQUEST = 32
DEFAULT_MAXIMUM_OVERPASS_REQUESTS = 128
DEFAULT_MAXIMUM_DOWNLOAD_BYTES = 256 * 1024 * 1024
DEFAULT_MAXIMUM_ENSURE_DOWNLOAD_BYTES = 512 * 1024 * 1024
DEFAULT_NETWORK_TIMEOUT_SECONDS = 180.0
DEFAULT_MAXIMUM_RETRY_COUNT = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_RETRY_JITTER_SECONDS = 0.25
DEFAULT_CACHE_LOCK_TIMEOUT_SECONDS = 60.0
DEFAULT_STALE_LOCK_SECONDS = 15 * 60.0
DEFAULT_LOCK_POLL_SECONDS = 0.1
DEFAULT_HTTP_READ_CHUNK_BYTES = 64 * 1024
DEFAULT_MAXIMUM_INPUT_FILE_BYTES = 32 * 1024 * 1024
DEFAULT_MAXIMUM_IMPORT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAXIMUM_GPX_POINTS = 1_000_000
DEFAULT_MAXIMUM_GPX_SEGMENT_LENGTH_M = 100_000.0
DEFAULT_MAXIMUM_GPX_TOTAL_LENGTH_M = 5_000_000.0
DEFAULT_MAXIMUM_OSM_OBJECTS = 5_000_000
DEFAULT_PRUNE_MINIMUM_AGE_SECONDS = 24 * 60 * 60

CACHE_DIRECTORY_ENVIRONMENT_VARIABLE = "WARPBUSTER_OSM_CACHE_DIR"
OVERPASS_URL_ENVIRONMENT_VARIABLE = "WARPBUSTER_OSM_OVERPASS_URL"


def default_cache_directory() -> Path:
    """Return a platform-native cache directory without touching the filesystem."""
    explicit = os.environ.get(CACHE_DIRECTORY_ENVIRONMENT_VARIABLE)
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "WarpBuster" / "osm-manager"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "WarpBuster" / "osm-manager" / "Cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "warpbuster" / "osm-manager"


@dataclass(frozen=True, slots=True)
class OsmManagerConfig:
    """Validated settings for coverage, network, cache, and resource bounds.

    Field suffixes state their units. Algorithms must consume these names instead of
    embedding operational thresholds locally.
    """

    cache_directory: Path
    overpass_url: str = DEFAULT_OVERPASS_URL
    user_agent: str = DEFAULT_USER_AGENT
    gpx_corridor_buffer_m: float = DEFAULT_GPX_CORRIDOR_BUFFER_M
    cache_grid_zoom: int = DEFAULT_CACHE_GRID_ZOOM
    coverage_sample_cell_fraction: float = DEFAULT_COVERAGE_SAMPLE_CELL_FRACTION
    default_max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    maximum_requested_area_km2: float = DEFAULT_MAXIMUM_REQUESTED_AREA_KM2
    maximum_ensure_cells: int = DEFAULT_MAXIMUM_ENSURE_CELLS
    maximum_cells_per_overpass_request: int = DEFAULT_MAXIMUM_CELLS_PER_OVERPASS_REQUEST
    maximum_overpass_requests: int = DEFAULT_MAXIMUM_OVERPASS_REQUESTS
    maximum_download_bytes: int = DEFAULT_MAXIMUM_DOWNLOAD_BYTES
    maximum_ensure_download_bytes: int = DEFAULT_MAXIMUM_ENSURE_DOWNLOAD_BYTES
    network_timeout_seconds: float = DEFAULT_NETWORK_TIMEOUT_SECONDS
    maximum_retry_count: int = DEFAULT_MAXIMUM_RETRY_COUNT
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS
    retry_jitter_seconds: float = DEFAULT_RETRY_JITTER_SECONDS
    cache_lock_timeout_seconds: float = DEFAULT_CACHE_LOCK_TIMEOUT_SECONDS
    stale_lock_seconds: float = DEFAULT_STALE_LOCK_SECONDS
    lock_poll_seconds: float = DEFAULT_LOCK_POLL_SECONDS
    http_read_chunk_bytes: int = DEFAULT_HTTP_READ_CHUNK_BYTES
    maximum_input_file_bytes: int = DEFAULT_MAXIMUM_INPUT_FILE_BYTES
    maximum_import_bytes: int = DEFAULT_MAXIMUM_IMPORT_BYTES
    maximum_gpx_points: int = DEFAULT_MAXIMUM_GPX_POINTS
    maximum_gpx_segment_length_m: float = DEFAULT_MAXIMUM_GPX_SEGMENT_LENGTH_M
    maximum_gpx_total_length_m: float = DEFAULT_MAXIMUM_GPX_TOTAL_LENGTH_M
    maximum_osm_objects: int = DEFAULT_MAXIMUM_OSM_OBJECTS
    prune_minimum_age_seconds: int = DEFAULT_PRUNE_MINIMUM_AGE_SECONDS

    @classmethod
    def defaults(cls) -> Self:
        """Construct defaults, including environment-controlled location/endpoint."""
        return cls(
            cache_directory=default_cache_directory(),
            overpass_url=os.environ.get(OVERPASS_URL_ENVIRONMENT_VARIABLE, DEFAULT_OVERPASS_URL),
        )

    @classmethod
    def from_toml(cls, path: Path, *, base: Self | None = None) -> Self:
        """Load known settings from an explicit TOML file over validated defaults."""
        effective = base or cls.defaults()
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"cannot read OSM Manager config {path}: {error}") from error
        section = raw.get("osm_manager", raw)
        if not isinstance(section, dict):
            raise ValueError("OSM Manager TOML must contain a table")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(section) - known)
        if unknown:
            raise ValueError(f"unknown OSM Manager config keys: {', '.join(unknown)}")
        hints = get_type_hints(cls)
        updates: dict[str, Any] = {}
        for key, value in section.items():
            if hints[key] is Path:
                if not isinstance(value, str):
                    raise ValueError(f"{key} must be a filesystem path string")
                updates[key] = Path(value).expanduser()
            else:
                updates[key] = value
        return replace(effective, **updates)

    def with_overrides(
        self,
        *,
        cache_directory: Path | None = None,
        overpass_url: str | None = None,
    ) -> Self:
        """Apply the small set of global CLI overrides."""
        return replace(
            self,
            cache_directory=(cache_directory or self.cache_directory),
            overpass_url=(overpass_url or self.overpass_url),
        )

    def manifest_settings(self) -> dict[str, int | float | str]:
        """Return non-secret settings that influence acquisition and cache semantics."""
        excluded = {"cache_directory", "overpass_url", "user_agent"}
        return {
            item.name: value
            for item in fields(self)
            if item.name not in excluded
            and isinstance((value := getattr(self, item.name)), (int, float, str))
            and not isinstance(value, bool)
        }

    def __post_init__(self) -> None:
        """Reject unsafe, contradictory, and incorrectly typed settings."""
        if not isinstance(self.cache_directory, Path):
            raise ValueError("cache_directory must be a Path")
        if not isinstance(self.overpass_url, str):
            raise ValueError("overpass_url must be a string")
        if not isinstance(self.user_agent, str):
            raise ValueError("user_agent must be a string")
        positive_numbers = {
            "gpx_corridor_buffer_m": self.gpx_corridor_buffer_m,
            "coverage_sample_cell_fraction": self.coverage_sample_cell_fraction,
            "default_max_age_seconds": self.default_max_age_seconds,
            "maximum_requested_area_km2": self.maximum_requested_area_km2,
            "maximum_ensure_cells": self.maximum_ensure_cells,
            "maximum_cells_per_overpass_request": self.maximum_cells_per_overpass_request,
            "maximum_overpass_requests": self.maximum_overpass_requests,
            "maximum_download_bytes": self.maximum_download_bytes,
            "maximum_ensure_download_bytes": self.maximum_ensure_download_bytes,
            "network_timeout_seconds": self.network_timeout_seconds,
            "cache_lock_timeout_seconds": self.cache_lock_timeout_seconds,
            "stale_lock_seconds": self.stale_lock_seconds,
            "lock_poll_seconds": self.lock_poll_seconds,
            "http_read_chunk_bytes": self.http_read_chunk_bytes,
            "maximum_input_file_bytes": self.maximum_input_file_bytes,
            "maximum_import_bytes": self.maximum_import_bytes,
            "maximum_gpx_points": self.maximum_gpx_points,
            "maximum_gpx_segment_length_m": self.maximum_gpx_segment_length_m,
            "maximum_gpx_total_length_m": self.maximum_gpx_total_length_m,
            "maximum_osm_objects": self.maximum_osm_objects,
            "prune_minimum_age_seconds": self.prune_minimum_age_seconds,
        }
        for name, value in positive_numbers.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        non_negative_numbers = {
            "maximum_retry_count": self.maximum_retry_count,
            "retry_backoff_seconds": self.retry_backoff_seconds,
            "retry_jitter_seconds": self.retry_jitter_seconds,
        }
        for name, value in non_negative_numbers.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if value < 0:
                raise ValueError(f"{name} must not be negative")
        if isinstance(self.cache_grid_zoom, bool) or not isinstance(self.cache_grid_zoom, int):
            raise ValueError("cache_grid_zoom must be an integer")
        if not 0 <= self.cache_grid_zoom <= 20:
            raise ValueError("cache_grid_zoom must be between 0 and 20")
        if self.coverage_sample_cell_fraction > 1:
            raise ValueError("coverage_sample_cell_fraction must not exceed one")
        if self.maximum_cells_per_overpass_request > self.maximum_ensure_cells:
            raise ValueError(
                "maximum_cells_per_overpass_request must not exceed maximum_ensure_cells"
            )
        if self.maximum_download_bytes > self.maximum_ensure_download_bytes:
            raise ValueError("maximum_download_bytes must not exceed maximum_ensure_download_bytes")
        endpoint = urllib.parse.urlparse(self.overpass_url)
        if endpoint.scheme != "https" or not endpoint.hostname:
            raise ValueError("overpass_url must use https and include a host")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("overpass_url must not contain credentials, query, or fragment")
        if not self.user_agent.strip():
            raise ValueError("user_agent must not be empty")
