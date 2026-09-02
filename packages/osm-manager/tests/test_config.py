"""Named configuration defaults and validation."""

import tomllib
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, cast

import pytest

from warpbuster_osm_manager.config import OsmManagerConfig

PACKAGE_ROOT = Path(__file__).parents[1]


def test_every_operational_setting_is_named_in_one_config_model(
    manager_config: OsmManagerConfig,
) -> None:
    names = {field.name for field in fields(OsmManagerConfig)}
    assert {
        "gpx_corridor_buffer_m",
        "cache_grid_zoom",
        "default_max_age_seconds",
        "maximum_requested_area_km2",
        "maximum_ensure_cells",
        "maximum_download_bytes",
        "maximum_ensure_download_bytes",
        "network_timeout_seconds",
        "maximum_retry_count",
        "cache_lock_timeout_seconds",
    } <= names
    assert manager_config.gpx_corridor_buffer_m == 1_000.0
    assert manager_config.default_max_age_seconds == 2_592_000


def test_example_toml_documents_every_configurable_setting() -> None:
    document = tomllib.loads(
        (PACKAGE_ROOT / "osm-manager.example.toml").read_text(encoding="utf-8")
    )
    assert set(document["osm_manager"]) == {field.name for field in fields(OsmManagerConfig)}


def test_explicit_toml_overrides_known_settings(tmp_path: Path) -> None:
    path = tmp_path / "manager.toml"
    path.write_text(
        """
[osm_manager]
cache_directory = "/tmp/custom-osm-cache"
gpx_corridor_buffer_m = 750.0
maximum_retry_count = 4
""",
        encoding="utf-8",
    )
    config = OsmManagerConfig.from_toml(path)
    assert config.cache_directory == Path("/tmp/custom-osm-cache")
    assert config.gpx_corridor_buffer_m == 750.0
    assert config.maximum_retry_count == 4


def test_toml_rejects_unknown_or_incorrect_settings(tmp_path: Path) -> None:
    unknown = tmp_path / "unknown.toml"
    unknown.write_text("mystery = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown OSM Manager config keys"):
        OsmManagerConfig.from_toml(unknown)

    incorrect = tmp_path / "incorrect.toml"
    incorrect.write_text('maximum_retry_count = "many"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="maximum_retry_count must be numeric"):
        OsmManagerConfig.from_toml(incorrect)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("gpx_corridor_buffer_m", 0, "must be greater than zero"),
        ("maximum_retry_count", -1, "must not be negative"),
        ("cache_grid_zoom", 21, "must be between"),
        ("overpass_url", "http://unsafe.test", "must use https"),
        ("overpass_url", "https://user:secret@unsafe.test/api", "must not contain"),
        ("overpass_url", "https://overpass.test/api?token=secret", "must not contain"),
    ],
)
def test_config_rejects_unsafe_values(
    manager_config: OsmManagerConfig, name: str, value: object, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(manager_config, **cast(Any, {name: value}))
