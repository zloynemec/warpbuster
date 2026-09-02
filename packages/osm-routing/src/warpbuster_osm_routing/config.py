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
        return self

    def limits_dict(self) -> dict[str, int | float]:
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if item.name != "cache_directory"
        }
