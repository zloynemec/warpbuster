"""Integrity threshold configuration tests."""

from dataclasses import asdict

import pytest

from warpbuster.config import IntegrityConfig, IntegrityProfile


def test_integrity_thresholds_are_named_and_serializable() -> None:
    """Every local detector threshold lives in the configuration model."""
    assert asdict(IntegrityConfig()) == {
        "profile": IntegrityProfile.GENERIC,
        "absolute_impossible_speed_mps": None,
        "absolute_impossible_distance_m": 50.0,
        "relative_suspicious_speed_floor_mps": 20.0,
        "relative_speed_multiplier": 6.0,
        "relative_mad_multiplier": 10.0,
        "relative_suspicious_distance_m": 20.0,
        "minimum_baseline_samples": 5,
    }


def test_running_profile_has_an_explicit_physical_ceiling() -> None:
    """Running activities use the documented running-specific threshold profile."""
    config = IntegrityConfig.for_sport("running")

    assert config.profile is IntegrityProfile.RUNNING
    assert config.absolute_impossible_speed_mps == 25.0
    assert IntegrityConfig.for_sport("RUNNING") == config
    assert IntegrityConfig.for_sport("cycling").profile is IntegrityProfile.GENERIC
    assert IntegrityConfig.for_sport(None).absolute_impossible_speed_mps is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("absolute_impossible_speed_mps", 0.0),
        ("absolute_impossible_distance_m", -1.0),
        ("relative_suspicious_speed_floor_mps", 0.0),
        ("relative_speed_multiplier", 0.0),
        ("relative_mad_multiplier", 0.0),
        ("relative_suspicious_distance_m", 0.0),
        ("minimum_baseline_samples", 0),
    ],
)
def test_integrity_thresholds_reject_non_positive_values(name: str, value: float) -> None:
    """Invalid profiles fail at construction instead of changing classifications."""
    with pytest.raises(ValueError):
        IntegrityConfig(**{name: value})  # type: ignore[arg-type]
