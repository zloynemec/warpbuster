"""Valhalla adapter contract tests."""

from __future__ import annotations

from pathlib import Path

from warpbuster_osm_routing.valhalla_backend import build_config


def test_config_hash_excludes_derived_output_location(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_config, first_hash = build_config(first)
    second_config, second_hash = build_config(second)

    assert first_config["mjolnir"]["tile_dir"] != second_config["mjolnir"]["tile_dir"]
    assert first_hash == second_hash
