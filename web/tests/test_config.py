"""Deployment configuration defaults and environment overrides."""

import pytest
from warpbuster_web.config import WebConfig

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
