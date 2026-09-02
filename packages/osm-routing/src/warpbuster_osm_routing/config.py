"""Named Task 010B resource limits and cache configuration."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Self, get_type_hints

DEFAULT_CONFIG_FILENAME = "osm-routing.toml"
CACHE_DIRECTORY_ENVIRONMENT_VARIABLE = "WARPBUSTER_OSM_ROUTING_CACHE_DIR"
VALHALLA_MAX_LOCATION_RADIUS_M = 200.0
VALHALLA_MAX_PEDESTRIAN_DISTANCE_M = 250_000.0
VALHALLA_MAX_TRACE_SHAPE_POINTS = 16_000
QUERY_POLICY_FIELDS = frozenset(
    {
        "snap_search_radius_m",
        "maximum_snap_distance_m",
        "equivalent_snap_separation_m",
        "snap_ambiguity_distance_delta_m",
        "maximum_snap_candidates",
        "maximum_reported_candidate_groups",
        "route_endpoint_tolerance_m",
        "maximum_route_distance_m",
        "maximum_route_shape_points",
        "maximum_route_edges",
        "route_length_absolute_tolerance_m",
        "route_length_relative_tolerance",
    }
)


def default_cache_directory() -> Path:
    """Return the platform-native derived graph cache without creating it."""
    explicit = os.environ.get(CACHE_DIRECTORY_ENVIRONMENT_VARIABLE)
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "WarpBuster" / "osm-routing"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "WarpBuster" / "osm-routing" / "Cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "warpbuster" / "osm-routing"


@dataclass(frozen=True, slots=True)
class RoutingCacheConfig:
    """All operational limits used by snapshot preparation and cache maintenance."""

    cache_directory: Path
    maximum_manifest_bytes: int = 1 * 1024 * 1024
    maximum_data_files: int = 64
    maximum_total_source_bytes: int = 2 * 1024 * 1024 * 1024
    maximum_osm_objects: int = 5_000_000
    maximum_total_node_references: int = 50_000_000
    maximum_total_tag_bytes: int = 512 * 1024 * 1024
    maximum_diagnostic_items: int = 100
    maximum_output_pbf_bytes: int = 2 * 1024 * 1024 * 1024
    maximum_tile_files: int = 250_000
    maximum_total_tile_bytes: int = 8 * 1024 * 1024 * 1024
    build_timeout_seconds: float = 900.0
    cache_lock_timeout_seconds: float = 60.0
    stale_lock_seconds: float = 1_800.0
    lock_poll_seconds: float = 0.1
    io_chunk_bytes: int = 1 * 1024 * 1024
    prune_minimum_age_seconds: float = 7 * 24 * 60 * 60
    snap_search_radius_m: float = 100.0
    maximum_snap_distance_m: float = 30.0
    equivalent_snap_separation_m: float = 3.0
    snap_ambiguity_distance_delta_m: float = 10.0
    maximum_snap_candidates: int = 64
    maximum_reported_candidate_groups: int = 8
    route_endpoint_tolerance_m: float = 5.0
    maximum_route_distance_m: float = VALHALLA_MAX_PEDESTRIAN_DISTANCE_M
    maximum_route_shape_points: int = VALHALLA_MAX_TRACE_SHAPE_POINTS
    maximum_route_edges: int = VALHALLA_MAX_TRACE_SHAPE_POINTS
    route_length_absolute_tolerance_m: float = 10.0
    route_length_relative_tolerance: float = 0.01

    @classmethod
    def defaults(cls) -> Self:
        return cls(cache_directory=default_cache_directory())

    @classmethod
    def load(cls, explicit_path: Path | None = None) -> Self:
        """Load explicit or cwd-local TOML over environment-aware defaults."""
        base = cls.defaults()
        path = explicit_path
        if path is None:
            candidate = Path.cwd() / DEFAULT_CONFIG_FILENAME
            path = candidate if candidate.is_file() else None
        if path is None:
            return base.validated()
        return cls.from_toml(path, base=base)

    @classmethod
    def from_toml(cls, path: Path, *, base: Self | None = None) -> Self:
        effective = base or cls.defaults()
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"cannot read OSM routing config {path}: {error}") from error
        section = raw.get("osm_routing", raw)
        if not isinstance(section, dict):
            raise ValueError("OSM routing TOML must contain a table")
        known = {item.name for item in fields(cls)}
        unknown = sorted(set(section) - known)
        if unknown:
            raise ValueError(f"unknown OSM routing config keys: {', '.join(unknown)}")
        hints = get_type_hints(cls)
        values: dict[str, Any] = {}
        for key, value in section.items():
            if key == "cache_directory":
                if not isinstance(value, str) or not value:
                    raise ValueError("cache_directory must be a non-empty path string")
                values[key] = Path(value).expanduser()
            elif hints[key] is int:
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError(f"{key} must be an integer")
                values[key] = value
            elif hints[key] is float:
                if not isinstance(value, int | float) or isinstance(value, bool):
                    raise ValueError(f"{key} must be a number")
                values[key] = float(value)
            else:
                values[key] = value
        return replace(effective, **values).validated()

    def with_cache_directory(self, path: Path | None) -> Self:
        return replace(self, cache_directory=path.expanduser() if path else self.cache_directory)

    def validated(self) -> Self:
        for item in fields(self):
            if item.name == "cache_directory":
                continue
            value = getattr(self, item.name)
            if not isinstance(value, int | float) or value <= 0:
                raise ValueError(f"{item.name} must be positive")
        if self.maximum_snap_distance_m > self.snap_search_radius_m:
            raise ValueError("maximum_snap_distance_m must not exceed snap_search_radius_m")
        if self.snap_search_radius_m > VALHALLA_MAX_LOCATION_RADIUS_M:
            raise ValueError(
                f"snap_search_radius_m must not exceed {VALHALLA_MAX_LOCATION_RADIUS_M:g}"
            )
        if self.equivalent_snap_separation_m > self.maximum_snap_distance_m:
            raise ValueError(
                "equivalent_snap_separation_m must not exceed maximum_snap_distance_m"
            )
        if self.maximum_route_distance_m > VALHALLA_MAX_PEDESTRIAN_DISTANCE_M:
            raise ValueError(
                "maximum_route_distance_m exceeds the pinned Valhalla pedestrian limit"
            )
        if self.maximum_route_shape_points > VALHALLA_MAX_TRACE_SHAPE_POINTS:
            raise ValueError(
                "maximum_route_shape_points exceeds the pinned Valhalla trace limit"
            )
        return self

    def limits_dict(self) -> dict[str, int | float]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "cache_directory"
        }

    def build_limits_dict(self) -> dict[str, int | float]:
        """Return operational values that affected graph preparation."""
        return {
            key: value
            for key, value in self.limits_dict().items()
            if key not in QUERY_POLICY_FIELDS
        }

    def query_policy_dict(self) -> dict[str, int | float]:
        """Return the complete request-time policy for route provenance."""
        values = self.limits_dict()
        return {key: values[key] for key in sorted(QUERY_POLICY_FIELDS)}
