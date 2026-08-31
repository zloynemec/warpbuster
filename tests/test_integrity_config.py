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
        "island_search_max_elapsed_seconds": 3_600.0,
        "island_search_max_exit_candidates": 64,
        "bridge_max_speed_mps": None,
        "bridge_speed_floor_mps": 5.0,
        "bridge_baseline_multiplier": 3.0,
        "diagnostic_max_candidate_details": 100,
        "geometry_min_chord_distance_m": 1_000.0,
        "geometry_min_position_count": 100,
        "geometry_max_cross_track_deviation_m": 0.5,
        "geometry_max_path_to_chord_ratio": 1.0005,
        "geometry_scan_max_window_records": 512,
        "geometry_scan_stride_records": 16,
        "geometry_max_bearing_change_degrees": 2.0,
        "geometry_max_warnings": 100,
    }


def test_running_profile_has_an_explicit_physical_ceiling() -> None:
    """Running activities use the documented running-specific threshold profile."""
    config = IntegrityConfig.for_sport("running")

    assert config.profile is IntegrityProfile.RUNNING
    assert config.absolute_impossible_speed_mps == 25.0
    assert config.bridge_max_speed_mps == 12.0
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
        ("island_search_max_elapsed_seconds", 0.0),
        ("island_search_max_exit_candidates", 0),
        ("bridge_max_speed_mps", 0.0),
        ("bridge_speed_floor_mps", 0.0),
        ("bridge_baseline_multiplier", 0.0),
        ("geometry_min_chord_distance_m", 0.0),
        ("geometry_min_position_count", 2),
        ("geometry_max_cross_track_deviation_m", 0.0),
        ("geometry_max_path_to_chord_ratio", 0.99),
        ("geometry_scan_stride_records", 0),
        ("geometry_max_bearing_change_degrees", 0.0),
    ],
)
def test_integrity_thresholds_reject_non_positive_values(name: str, value: float) -> None:
    """Invalid profiles fail at construction instead of changing classifications."""
    with pytest.raises(ValueError):
        IntegrityConfig(**{name: value})  # type: ignore[arg-type]


def test_bridge_floor_cannot_exceed_bridge_ceiling() -> None:
    """A contradictory bridge profile is rejected at construction."""
    with pytest.raises(ValueError, match="bridge_speed_floor_mps"):
        IntegrityConfig(bridge_max_speed_mps=4.0, bridge_speed_floor_mps=5.0)


def test_diagnostic_detail_limit_can_be_zero_but_not_negative() -> None:
    """Diagnostics may retain no details while preserving aggregate counters."""
    assert IntegrityConfig(diagnostic_max_candidate_details=0).diagnostic_max_candidate_details == 0
    with pytest.raises(ValueError, match="diagnostic_max_candidate_details"):
        IntegrityConfig(diagnostic_max_candidate_details=-1)


def test_geometry_bounds_reject_contradictory_or_unbounded_values() -> None:
    """Geometry scan work and angular bounds remain explicit and finite."""
    with pytest.raises(ValueError, match="geometry_scan_max_window_records"):
        IntegrityConfig(geometry_scan_max_window_records=99)
    with pytest.raises(ValueError, match="geometry_max_bearing_change_degrees"):
        IntegrityConfig(geometry_max_bearing_change_degrees=181.0)
    assert IntegrityConfig(geometry_max_warnings=0).geometry_max_warnings == 0
    with pytest.raises(ValueError, match="geometry_max_warnings"):
        IntegrityConfig(geometry_max_warnings=-1)
