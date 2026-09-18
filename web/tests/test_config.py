"""Deployment configuration defaults and environment overrides."""

import pytest
from warpbuster_web.config import DEMMode, OSMMode, WebConfig

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
    assert config.approximate_osm is True
    assert config.dem_mode is DEMMode.AUTO
    assert config.complete_missing_altitude is True
    assert config.osm_total_timeout_seconds == 360
    assert config.publish_reserve_seconds == 60
    assert config.osm_maximum_cells == 64
    assert config.osm_maximum_area_km2 == 1_000.0


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
    assert config.dem_mode is DEMMode.OFFLINE
    assert config.osm_maximum_cells == 12
    assert config.osm_maximum_area_km2 == 42.5


def test_disabling_approximate_or_dem_derives_safe_defaults(monkeypatch):
    monkeypatch.setenv("WARPBUSTER_WEB_APPROXIMATE_OSM", "false")
    config = WebConfig.from_environment()
    assert not config.approximate_osm
    assert config.dem_mode is DEMMode.DISABLED
    assert not config.complete_missing_altitude

    monkeypatch.setenv("WARPBUSTER_WEB_APPROXIMATE_OSM", "true")
    monkeypatch.setenv("WARPBUSTER_WEB_DEM_MODE", "disabled")
    config = WebConfig.from_environment()
    assert config.approximate_osm
    assert not config.complete_missing_altitude


def test_disabling_osm_disables_entire_optional_chain(monkeypatch):
    monkeypatch.setenv("WARPBUSTER_WEB_OSM_MODE", "disabled")
    config = WebConfig.from_environment()
    assert not config.approximate_osm
    assert config.dem_mode is DEMMode.DISABLED
    assert not config.complete_missing_altitude


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


def test_approximate_dem_settings_round_trip_to_worker(monkeypatch, tmp_path):
    expected = WebConfig(
        data_dir=tmp_path / "private",
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        dem_snapshot_id="sha256:" + "a" * 64,
        dem_timeout_seconds=17,
        complete_missing_altitude=True,
    )
    for name, value in expected.processor_environment().items():
        monkeypatch.setenv(name, value)
    actual = WebConfig.from_environment()
    assert actual.pipeline_config().approximate_osm
    assert actual.dem_mode is DEMMode.OFFLINE
    assert actual.dem_snapshot_id == expected.dem_snapshot_id
    assert actual.dem_timeout_seconds == 17
    assert actual.complete_missing_altitude


def test_web_refuses_global_fit_altitude_datum():
    with pytest.raises(ValueError, match="web cannot assert FIT altitude datum"):
        WebConfig(
            approximate_osm=True,
            dem_mode=DEMMode.OFFLINE,
            complete_missing_altitude=True,
            fit_altitude_datum="WGS84/EGM96 geoid",
        )


@pytest.mark.parametrize("value", ["", "enabled", "2"])
def test_invalid_approximate_switch_fails_startup(monkeypatch, value):
    monkeypatch.setenv("WARPBUSTER_WEB_APPROXIMATE_OSM", value)
    with pytest.raises(ValueError, match="WARPBUSTER_WEB_APPROXIMATE_OSM must be boolean"):
        WebConfig.from_environment()


COVERAGE_ENVIRONMENT = {
    "minimum_observed_gps_coverage_percent": "WARPBUSTER_WEB_MINIMUM_OBSERVED_GPS_COVERAGE_PERCENT",
    "maximum_observed_gps_interval_seconds": "WARPBUSTER_WEB_MAXIMUM_OBSERVED_GPS_INTERVAL_SECONDS",
}


def test_coverage_defaults_match_core(monkeypatch):
    for name in COVERAGE_ENVIRONMENT.values():
        monkeypatch.delenv(name, raising=False)
    config = WebConfig.from_environment()
    assert config.minimum_observed_gps_coverage_percent == 51.0
    assert config.maximum_observed_gps_interval_seconds == 30.0


@pytest.mark.parametrize("name", COVERAGE_ENVIRONMENT.values())
@pytest.mark.parametrize("value", ["", "zero", "0", "-1", "nan", "inf", "-inf"])
def test_invalid_coverage_environment_fails_startup(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        WebConfig.from_environment()


def test_coverage_threshold_above_one_hundred_fails_startup(monkeypatch):
    monkeypatch.setenv(COVERAGE_ENVIRONMENT["minimum_observed_gps_coverage_percent"], "100.1")
    with pytest.raises(ValueError, match="minimum_observed_gps_coverage_percent"):
        WebConfig.from_environment()


def test_coverage_configuration_round_trips_to_worker_and_core(monkeypatch):
    expected = WebConfig(
        minimum_observed_gps_coverage_percent=72.5,
        maximum_observed_gps_interval_seconds=12.5,
    )
    for name, value in expected.processor_environment().items():
        monkeypatch.setenv(name, value)
    actual = WebConfig.from_environment()
    for field in COVERAGE_ENVIRONMENT:
        assert getattr(actual, field) == getattr(expected, field)
        assert getattr(actual.pipeline_config(), field) == getattr(expected, field)
