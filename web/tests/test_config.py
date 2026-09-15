"""Deployment configuration defaults and environment overrides."""

import pytest
from warpbuster_web.config import OSMMode, WebConfig

LIMIT_ENVIRONMENT = {
    "WARPBUSTER_WEB_MAX_JOBS": "1200",
    "WARPBUSTER_WEB_MAX_OWNER_JOBS": "125",
    "WARPBUSTER_WEB_MAX_PENDING_JOBS": "60",
}


def test_capacity_defaults_match_public_deployment_policy(monkeypatch):
    for name in LIMIT_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    config = WebConfig.from_environment()

    assert config.max_jobs == 1_000
    assert config.max_owner_jobs == 100
    assert config.max_pending_jobs == 50
    assert config.process_timeout_seconds == 600
    assert config.osm_mode is OSMMode.AUTO
    assert config.osm_total_timeout_seconds == 360
    assert config.publish_reserve_seconds == 60
    assert config.osm_maximum_cells == 64


def test_capacity_limits_can_be_overridden_from_environment(monkeypatch):
    for name, value in LIMIT_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)

    config = WebConfig.from_environment()

    assert config.max_jobs == 1_200
    assert config.max_owner_jobs == 125
    assert config.max_pending_jobs == 60


@pytest.mark.parametrize("value", ["", "zero", "0", "-1", "1.5"])
@pytest.mark.parametrize("name", LIMIT_ENVIRONMENT)
def test_invalid_environment_capacity_limit_stops_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=rf"^{name} must be a positive integer$"):
        WebConfig.from_environment()


def test_osm_operator_mode_and_limits_are_environment_controlled(monkeypatch):
    monkeypatch.setenv("WARPBUSTER_WEB_OSM_MODE", "offline")
    monkeypatch.setenv("WARPBUSTER_WEB_OSM_MAXIMUM_CELLS", "12")
    monkeypatch.setenv("WARPBUSTER_WEB_OSM_MAXIMUM_AREA_KM2", "42.5")
    config = WebConfig.from_environment()
    assert config.osm_mode is OSMMode.OFFLINE
    assert config.osm_maximum_cells == 12
    assert config.osm_maximum_area_km2 == 42.5


@pytest.mark.parametrize("value", ["", "enabled", "AUTO"])
def test_invalid_osm_mode_stops_startup(monkeypatch, value):
    monkeypatch.setenv("WARPBUSTER_WEB_OSM_MODE", value)
    with pytest.raises(ValueError):
        WebConfig.from_environment()


def test_processor_environment_preserves_operator_osm_policy(monkeypatch, tmp_path):
    expected = WebConfig(
        data_dir=tmp_path / "private",
        osm_mode=OSMMode.OFFLINE,
        osm_maximum_cells=17,
        osm_maximum_area_km2=81.5,
        osm_child_cpu_seconds=123,
    )
    for name, value in expected.processor_environment().items():
        monkeypatch.setenv(name, value)
    actual = WebConfig.from_environment()
    assert actual.data_dir == expected.data_dir
    assert actual.osm_mode is OSMMode.OFFLINE
    assert actual.osm_maximum_cells == 17
    assert actual.osm_maximum_area_km2 == 81.5
    assert actual.osm_child_cpu_seconds == 123
