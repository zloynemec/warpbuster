"""Typed Task 010B configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from warpbuster_osm_routing.config import RoutingCacheConfig


def test_toml_overrides_named_limits(tmp_path: Path) -> None:
    path = tmp_path / "routing.toml"
    path.write_text(
        f'''[osm_routing]
cache_directory = "{tmp_path / "graphs"}"
maximum_osm_objects = 1234
build_timeout_seconds = 12.5
''',
        encoding="utf-8",
    )

    config = RoutingCacheConfig.from_toml(path)

    assert config.cache_directory == tmp_path / "graphs"
    assert config.maximum_osm_objects == 1234
    assert config.build_timeout_seconds == 12.5


def test_unknown_or_non_positive_config_is_rejected(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("unknown_limit = 1", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        RoutingCacheConfig.from_toml(unknown)

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("maximum_osm_objects = 0", encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        RoutingCacheConfig.from_toml(invalid)
