"""False-positive regressions for physically plausible running trajectories."""

from __future__ import annotations

from dataclasses import replace
from inspect import signature
from math import cos, pi, radians, sin

from tests.activity_factory import Observation, eastward_observations, make_activity
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport, IntegrityStatus

_LATITUDE = 55.0
_LONGITUDE = 37.0
_METRES_PER_LATITUDE_DEGREE = 111_195.0
_METRES_PER_LONGITUDE_DEGREE = _METRES_PER_LATITUDE_DEGREE * cos(radians(_LATITUDE))


def test_gradual_wrong_turn_over_kilometres_is_clean() -> None:
    """A long continuous departure from a hypothetical route is real movement."""
    points = [(float(index * 3), 0.0) for index in range(1_001)]
    points.extend((3_000.0, float(index * 3)) for index in range(1, 1_001))

    report = analyze_integrity(make_activity(_xy_observations(points)))

    assert report.status is IntegrityStatus.CLEAN
    assert report.corrupted_intervals == ()


def test_out_and_back_is_clean() -> None:
    """Retracing a path does not resemble a teleport when every step is continuous."""
    distances = [float(index * 3) for index in range(501)]
    distances.extend(float(index * 3) for index in range(499, -1, -1))

    report = analyze_integrity(
        make_activity(eastward_observations(list(map(float, range(len(distances)))), distances))
    )

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_closed_loop_is_clean() -> None:
    """Returning near the start through a smooth loop is not corruption."""
    radius_m = 100.0
    points = [
        (
            radius_m * cos(2.0 * pi * index / 360.0),
            radius_m * sin(2.0 * pi * index / 360.0),
        )
        for index in range(361)
    ]

    report = analyze_integrity(make_activity(_xy_observations(points)))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_tight_switchbacks_are_clean() -> None:
    """Frequent sharp direction changes remain physically continuous."""
    points: list[tuple[float, float]] = []
    for index in range(301):
        phase = index % 20
        east_m = float(phase * 2 if phase <= 10 else (20 - phase) * 2)
        points.append((east_m, float(index)))

    report = analyze_integrity(make_activity(_xy_observations(points)))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_fast_downhill_is_clean() -> None:
    """A fast but physically plausible continuous descent remains clean."""
    activity = make_activity(
        eastward_observations(
            [float(index) for index in range(101)],
            [float(index * 12) for index in range(101)],
        )
    )
    activity = replace(
        activity,
        records=tuple(
            replace(record, altitude=1_000.0 - record.index * 5.0) for record in activity.records
        ),
    )

    report = analyze_integrity(activity)

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_stop_and_restart_is_clean() -> None:
    """A pause followed by normal movement uses timestamps rather than record cadence."""
    observations = eastward_observations(
        [0.0, 1.0, 2.0, 122.0, 123.0, 124.0],
        [0.0, 3.0, 6.0, 6.0, 9.0, 12.0],
    )

    report = analyze_integrity(make_activity(observations))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_irregular_sampling_is_clean() -> None:
    """Mixed sampling intervals preserve a constant physical speed."""
    observations = eastward_observations(
        [0.0, 1.0, 6.0, 26.0, 27.0, 47.0],
        [0.0, 3.0, 18.0, 78.0, 81.0, 141.0],
    )

    report = analyze_integrity(make_activity(observations))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_long_gps_dropout_is_unknown_not_corrupted() -> None:
    """Missing GNSS over a long plausible bridge remains explicitly unknown."""
    observations: list[Observation] = [eastward_observations([0.0], [0.0])[0]]
    observations.extend((float(second), None, None) for second in range(1, 301))
    observations.extend(eastward_observations([301.0, 302.0], [903.0, 906.0]))

    report = analyze_integrity(make_activity(observations))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.UNKNOWN
    assert report.missing_position_record_count == 300


def test_short_noisy_drift_is_clean() -> None:
    """A small reversible GNSS wobble stays below suspicious evidence floors."""
    observations = eastward_observations(
        [0.0, 1.0, 2.0, 3.0, 4.0, 5.0],
        [0.0, 3.0, 6.0, 21.0, 12.0, 15.0],
    )

    report = analyze_integrity(make_activity(observations))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_several_legitimate_pace_regimes_are_clean() -> None:
    """Warm-up, tempo, and recovery changes do not imply broken GNSS."""
    distances = [0.0]
    for speed_mps in (2.0, 6.0, 1.0):
        for _ in range(100):
            distances.append(distances[-1] + speed_mps)
    observations = eastward_observations(
        [float(index) for index in range(len(distances))],
        distances,
    )

    report = analyze_integrity(make_activity(observations))

    _assert_not_high_corrupted(report)
    assert report.status is IntegrityStatus.CLEAN


def test_detector_api_has_no_course_input() -> None:
    """Integrity detection remains structurally independent of GPX/course data."""
    assert tuple(signature(analyze_integrity).parameters) == ("activity", "config")


def _xy_observations(points_m: list[tuple[float, float]]) -> list[Observation]:
    return [
        (
            float(index),
            _LATITUDE + north_m / _METRES_PER_LATITUDE_DEGREE,
            _LONGITUDE + east_m / _METRES_PER_LONGITUDE_DEGREE,
        )
        for index, (east_m, north_m) in enumerate(points_m)
    ]


def _assert_not_high_corrupted(report: IntegrityReport) -> None:
    assert not (
        report.status is IntegrityStatus.CORRUPTED and report.confidence is IntegrityConfidence.HIGH
    )
    assert report.corrupted_intervals == ()
