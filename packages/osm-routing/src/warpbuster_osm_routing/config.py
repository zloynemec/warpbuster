"""Named Task 010B resource limits and cache configuration."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Self, get_type_hints

from warpbuster_osm_routing.geometry import finite_number

DEFAULT_CONFIG_FILENAME = "osm-routing.toml"
CACHE_DIRECTORY_ENVIRONMENT_VARIABLE = "WARPBUSTER_OSM_ROUTING_CACHE_DIR"
VALHALLA_MAX_LOCATION_RADIUS_M = 200.0
VALHALLA_MAX_PEDESTRIAN_DISTANCE_M = 250_000.0
VALHALLA_MAX_TRACE_SHAPE_POINTS = 16_000
VALHALLA_MAX_ALTERNATES = 2
ALTERNATIVES_POLICY_FIELDS = frozenset(
    {
        "maximum_requested_alternates",
        "maximum_alternatives_response_bytes",
        "maximum_total_route_shape_points",
        "maximum_total_route_edges",
        "minimum_diversity_ratio",
        "detour_warning_ratio",
    }
)
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
    # Request-time bounds; none of these affect immutable graph identity.
    maximum_requested_alternates: int = VALHALLA_MAX_ALTERNATES
    maximum_alternatives_response_bytes: int = 8 * 1024 * 1024
    maximum_total_route_shape_points: int = 48_000
    maximum_total_route_edges: int = 48_000
    minimum_diversity_ratio: float = 0.10
    detour_warning_ratio: float = 1.50

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
                if not finite_number(value):
                    raise ValueError(f"{key} must be finite")
                values[key] = float(value)
            else:
                values[key] = value
        return replace(effective, **values).validated()

    def with_cache_directory(self, path: Path | None) -> Self:
        return replace(self, cache_directory=path.expanduser() if path else self.cache_directory)

    def validated(self) -> Self:
        hints = get_type_hints(type(self))
        for item in fields(self):
            if item.name == "cache_directory":
                continue
            value = getattr(self, item.name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or (hints[item.name] is int and not isinstance(value, int))
                or not finite_number(value)
            ):
                raise ValueError(
                    f"{item.name} must have a finite numeric value of the correct type"
                )
            if item.name == "minimum_diversity_ratio":
                if not 0 <= value <= 1:
                    raise ValueError("minimum_diversity_ratio must be in [0, 1]")
                continue
            if value <= 0:
                raise ValueError(f"{item.name} must be positive")
        if self.maximum_requested_alternates > VALHALLA_MAX_ALTERNATES:
            raise ValueError("maximum_requested_alternates exceeds the pinned engine limit of 2")
        if self.detour_warning_ratio < 1:
            raise ValueError("detour_warning_ratio must be >= 1")
        if self.maximum_snap_distance_m > self.snap_search_radius_m:
            raise ValueError("maximum_snap_distance_m must not exceed snap_search_radius_m")
        if self.snap_search_radius_m > VALHALLA_MAX_LOCATION_RADIUS_M:
            raise ValueError(
                f"snap_search_radius_m must not exceed {VALHALLA_MAX_LOCATION_RADIUS_M:g}"
            )
        if self.equivalent_snap_separation_m > self.maximum_snap_distance_m:
            raise ValueError("equivalent_snap_separation_m must not exceed maximum_snap_distance_m")
        if self.maximum_route_distance_m > VALHALLA_MAX_PEDESTRIAN_DISTANCE_M:
            raise ValueError(
                "maximum_route_distance_m exceeds the pinned Valhalla pedestrian limit"
            )
        if self.maximum_route_shape_points > VALHALLA_MAX_TRACE_SHAPE_POINTS:
            raise ValueError("maximum_route_shape_points exceeds the pinned Valhalla trace limit")
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
            if key not in QUERY_POLICY_FIELDS | ALTERNATIVES_POLICY_FIELDS
        }

    def query_policy_dict(self) -> dict[str, int | float]:
        """Return the complete request-time policy for route provenance."""
        values = self.limits_dict()
        return {key: values[key] for key in sorted(QUERY_POLICY_FIELDS)}

    def alternatives_policy_dict(self) -> dict[str, Any]:
        """Versioned interpretation and limits, separate from the 010D contract."""
        policy: dict[str, Any] = {
            "policy_version": 1,
            "metric": "directed_edge_weighted_v1",
            "metric_description": (
                "Directed edge-weight similarity, not exact spatial intersection. "
                "Disjoint partial spans on one edge may overestimate shared weight; "
                "repeated traversals preserve weight but not order. "
                "Ratios do not prove path identity, uniqueness or repair confidence."
            ),
            "ordering": "engine primary first; alternatives by length rounded to mm, then route_id",
            "identity_schema": "audited-route-v1",
            **{
                key: (
                    float(getattr(self, key))
                    if key in {"minimum_diversity_ratio", "detour_warning_ratio"}
                    else getattr(self, key)
                )
                for key in sorted(ALTERNATIVES_POLICY_FIELDS)
            },
        }
        encoded = json.dumps(policy, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return {**policy, "policy_sha256": hashlib.sha256(encoded.encode()).hexdigest()}
