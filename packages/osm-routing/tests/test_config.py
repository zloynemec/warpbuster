"""Typed Task 010B configuration tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from warpbuster_osm_routing.config import ALTERNATIVES_POLICY_FIELDS, RoutingCacheConfig


def test_toml_overrides_named_limits(tmp_path: Path) -> None:
    path = tmp_path / "routing.toml"
    path.write_text(
        f'''[osm_routing]
cache_directory = "{tmp_path / "graphs"}"
maximum_osm_objects = 1234
build_timeout_seconds = 12.5
maximum_snap_distance_m = 22.0
''',
        encoding="utf-8",
    )

    config = RoutingCacheConfig.from_toml(path)

    assert config.cache_directory == tmp_path / "graphs"
    assert config.maximum_osm_objects == 1234
    assert config.build_timeout_seconds == 12.5
    assert config.maximum_snap_distance_m == 22.0
    assert "maximum_snap_distance_m" not in config.build_limits_dict()
    assert config.query_policy_dict()["maximum_snap_distance_m"] == 22.0


def test_unknown_or_non_positive_config_is_rejected(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("unknown_limit = 1", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown"):
        RoutingCacheConfig.from_toml(unknown)

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("maximum_osm_objects = 0", encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        RoutingCacheConfig.from_toml(invalid)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("maximum_snap_distance_m", 101),
        ("snap_search_radius_m", 201),
        ("equivalent_snap_separation_m", 31),
        ("maximum_route_distance_m", 250001),
        ("maximum_route_shape_points", 16001),
    ],
)
def test_query_policy_cross_limits_are_rejected(setting: str, value: int, tmp_path: Path) -> None:
    path = tmp_path / "invalid-query.toml"
    path.write_text(f"{setting} = {value}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        RoutingCacheConfig.from_toml(path)


@pytest.mark.parametrize("key", sorted(ALTERNATIVES_POLICY_FIELDS))
@pytest.mark.parametrize("value", [True, False, "1", float("nan"), float("inf"), -1])
def test_alternatives_config_rejects_invalid_numbers(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        replace(RoutingCacheConfig.defaults(), **{key: value}).validated()


@pytest.mark.parametrize(
    "key",
    sorted(
        ALTERNATIVES_POLICY_FIELDS
        - {
            "minimum_diversity_ratio",
            "detour_warning_ratio",
        }
    ),
)
@pytest.mark.parametrize("value", [0, 1.5])
def test_alternatives_counts_are_positive_integers(key: str, value: Any) -> None:
    with pytest.raises(ValueError):
        replace(RoutingCacheConfig.defaults(), **{key: value}).validated()


def test_alternatives_policy_defaults_boundaries_hash_and_build_separation(tmp_path: Path) -> None:
    base = RoutingCacheConfig.defaults()
    defaults = base.alternatives_policy_dict()
    assert defaults["maximum_requested_alternates"] == 2
    assert defaults["maximum_alternatives_response_bytes"] == 8388608
    assert defaults["maximum_total_route_shape_points"] == 48000
    assert defaults["maximum_total_route_edges"] == 48000
    assert defaults["minimum_diversity_ratio"] == 0.10
    assert defaults["detour_warning_ratio"] == 1.50
    for key in sorted(ALTERNATIVES_POLICY_FIELDS):
        value = (
            1
            if key == "maximum_requested_alternates"
            else 0.5
            if key == "minimum_diversity_ratio"
            else 2
        )
        path = tmp_path / "policy.toml"
        path.write_text(f"[osm_routing]\n{key} = {value}\n")
        changed = RoutingCacheConfig.from_toml(path)
        assert changed.alternatives_policy_dict()[key] == value
        assert changed.alternatives_policy_dict()["policy_sha256"] != defaults["policy_sha256"]
        assert changed.build_limits_dict() == base.build_limits_dict()
        assert changed.query_policy_dict() == base.query_policy_dict()
    for diversity in (0, 1):
        replace(base, minimum_diversity_ratio=diversity, detour_warning_ratio=1).validated()
    for changes in (
        {"minimum_diversity_ratio": 1.01},
        {"detour_warning_ratio": 0.99},
        {"maximum_requested_alternates": 3},
    ):
        with pytest.raises(ValueError):
            replace(base, **changes).validated()
    example = RoutingCacheConfig.from_toml(Path(__file__).parents[1] / "osm-routing.example.toml")
    assert example.alternatives_policy_dict() == defaults


def test_canonical_threshold_hash_and_huge_integers(tmp_path: Path) -> None:
    base = RoutingCacheConfig.defaults()
    first = replace(base, minimum_diversity_ratio=1, detour_warning_ratio=1)
    second = replace(base, minimum_diversity_ratio=1.0, detour_warning_ratio=1.0)
    assert first.alternatives_policy_dict() == second.alternatives_policy_dict()
    with pytest.raises(ValueError):
        replace(base, maximum_requested_alternates=10**500).validated()
    path = tmp_path / "overflow.toml"
    path.write_text(f"detour_warning_ratio = {10**500}")
    with pytest.raises(ValueError):
        RoutingCacheConfig.from_toml(path)
