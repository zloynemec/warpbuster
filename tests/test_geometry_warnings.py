"""Geometry-only diagnostics for likely synthetic interpolation."""

from dataclasses import replace
from math import cos, pi, radians, sin

import pytest

from tests.activity_factory import Observation, eastward_observations, make_activity
from warpbuster.config import IntegrityConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    GeometryWarningKind,
    GeometryWarningReason,
    IntegrityConfidence,
    IntegrityStatus,
)

_LATITUDE = 55.0
_LONGITUDE = 37.0
_METRES_PER_LATITUDE_DEGREE = 111_195.0
_METRES_PER_LONGITUDE_DEGREE = _METRES_PER_LATITUDE_DEGREE * cos(radians(_LATITUDE))


def test_long_dense_perfect_chord_creates_low_warning_only() -> None:
    """A compelling interpolation fingerprint remains non-authoritative evidence."""
    observations = eastward_observations(
        [float(index) for index in range(401)],
        [float(index * 5) for index in range(401)],
    )

    report = analyze_integrity(make_activity(observations))

    assert report.status is IntegrityStatus.CLEAN
    assert report.corrupted_intervals == ()
    assert len(report.geometry_warnings) == 1
    warning = report.geometry_warnings[0]
    assert warning.kind is GeometryWarningKind.POSSIBLE_INTERPOLATED_GNSS_GAP
    assert warning.confidence is IntegrityConfidence.LOW
    assert warning.chord_distance_m >= 1_900.0
    assert warning.path_to_chord_ratio == pytest.approx(1.0, abs=1e-6)
    assert warning.max_cross_track_deviation_m < 0.01
    assert warning.timestamps_available is True
    assert warning.reasons == (
        GeometryWarningReason.LONG_NEAR_COLLINEAR_RUN,
        GeometryWarningReason.PATH_NEAR_CHORD,
        GeometryWarningReason.NARROW_CORRIDOR,
    )


def test_missing_time_keeps_status_unknown_with_geometry_warning() -> None:
    """Geometry evidence never invents speed or upgrades missing time to corruption."""
    observations: list[Observation] = [
        (None, latitude, longitude)
        for _elapsed, latitude, longitude in eastward_observations(
            [float(index) for index in range(401)],
            [float(index * 5) for index in range(401)],
        )
    ]

    report = analyze_integrity(make_activity(observations))

    assert report.status is IntegrityStatus.UNKNOWN
    assert report.confidence is IntegrityConfidence.LOW
    assert len(report.geometry_warnings) == 1
    assert report.geometry_warnings[0].timestamps_available is False
    assert report.corrupted_intervals == ()


def test_realistic_noisy_straight_does_not_create_warning() -> None:
    """Normal sub-metre GNSS variation prevents a synthetic-line warning."""
    points = [(float(index * 5), 1.5 if index % 2 else -1.5) for index in range(401)]

    report = analyze_integrity(make_activity(_xy_observations(points)))

    assert report.status is IntegrityStatus.CLEAN
    assert report.geometry_warnings == ()


def test_long_gentle_curve_does_not_create_warning() -> None:
    """A long smooth bend has physical curvature rather than an artificial chord."""
    radius_m = 1_000.0
    points = [
        (
            radius_m * sin((pi / 2.0) * index / 400.0),
            radius_m * (1.0 - cos((pi / 2.0) * index / 400.0)),
        )
        for index in range(401)
    ]

    report = analyze_integrity(make_activity(_xy_observations(points)))

    assert report.status is IntegrityStatus.CLEAN
    assert report.geometry_warnings == ()


def test_geometry_scan_does_not_join_continuity_segments() -> None:
    """Two sub-threshold straight segments cannot combine into one warning."""
    observations = eastward_observations(
        [float(index) for index in range(401)],
        [float(index * 3) for index in range(401)],
    )
    activity = make_activity(observations)
    activity = replace(
        activity,
        records=tuple(
            replace(record, continuity_id=0 if record.index < 201 else 1)
            for record in activity.records
        ),
    )

    report = analyze_integrity(activity)

    assert report.geometry_scan_diagnostics.continuity_segment_count == 2
    assert report.geometry_warnings == ()


def test_geometry_warning_retention_is_bounded() -> None:
    """Reports can omit warning details without losing aggregate counts."""
    observations = eastward_observations(
        [float(index) for index in range(802)],
        [float(index * 5) for index in range(802)],
    )
    activity = make_activity(observations)
    activity = replace(
        activity,
        records=tuple(
            replace(record, continuity_id=0 if record.index < 401 else 1)
            for record in activity.records
        ),
    )

    report = analyze_integrity(
        activity,
        replace(IntegrityConfig.running(), geometry_max_warnings=1),
    )

    assert report.geometry_scan_diagnostics.warning_count == 2
    assert report.geometry_scan_diagnostics.retained_warning_count == 1
    assert report.geometry_scan_diagnostics.warnings_truncated_count == 1
    assert len(report.geometry_warnings) == 1


def _xy_observations(points_m: list[tuple[float, float]]) -> list[Observation]:
    return [
        (
            float(index),
            _LATITUDE + north_m / _METRES_PER_LATITUDE_DEGREE,
            _LONGITUDE + east_m / _METRES_PER_LONGITUDE_DEGREE,
        )
        for index, (east_m, north_m) in enumerate(points_m)
    ]
