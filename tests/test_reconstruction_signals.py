"""Course-independent signal qualification and allocation tolerance boundaries."""

from dataclasses import replace

import pytest

from tests.activity_factory import eastward_observations, make_activity
from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.models.reconstruction import AllocationMethod, ReconstructionReason
from warpbuster.reconstruction.local import _fractions
from warpbuster.reconstruction.signals import qualify_distance, qualify_speed


def _records(distance: float | None, *, speed: float | None = None, elapsed: float = 1):
    records = make_activity(eastward_observations([0, elapsed], [0, 2])).records
    return tuple(
        replace(r, distance=(distance * r.index if distance is not None else None), speed=speed)
        for r in records
    )


@pytest.mark.parametrize(
    ("distance", "elapsed", "status"),
    [
        (50, 1, "plausible"),
        (50.01, 1, "implausible"),
        (100, 4, "plausible"),
        (100, 3.99, "implausible"),
        (0, 1, "zero"),
        (None, 1, "unavailable"),
        (float("nan"), 1, "unavailable"),
        (float("inf"), 1, "unavailable"),
    ],
)
def test_distance_proof_uses_existing_profile_boundaries(distance, elapsed, status) -> None:
    signal = qualify_distance(_records(distance, elapsed=elapsed), IntegrityConfig.running())
    assert signal.status == status
    assert signal.correction_supported is (status == "implausible")


def test_unknown_profile_and_distance_reset_do_not_authorize_correction() -> None:
    config = replace(IntegrityConfig.running(), absolute_impossible_speed_mps=None)
    assert not qualify_distance(_records(20000), config).correction_supported
    a, b = _records(10)
    reset = qualify_distance((replace(a, distance=100), b), IntegrityConfig.running())
    assert reset.status == "non_monotonic"
    assert not reset.correction_supported


@pytest.mark.parametrize(
    ("length", "delta", "accepted"),
    [
        (10, 3, True),
        (10, -3, True),
        (10, 3.001, False),
        (10, -3.001, False),
        (100, 15, True),
        (100, -15, True),
        (100, 15.001, False),
    ],
)
def test_absolute_and_relative_signal_error_boundaries(length, delta, accepted) -> None:
    result = _fractions(
        _records(length + delta, elapsed=100),
        length,
        CourseReconstructionConfig(),
        IntegrityConfig.running(),
    )
    if accepted:
        assert result[0] is AllocationMethod.RECORDED_DISTANCE
    else:
        assert result is ReconstructionReason.LOCAL_DISTANCE_INCONSISTENT


def test_zero_measurements_do_not_allow_time_only_override() -> None:
    result = _fractions(
        _records(0, speed=0), 2, CourseReconstructionConfig(), IntegrityConfig.running()
    )
    assert result is ReconstructionReason.LOCAL_DISTANCE_INCONSISTENT


def test_unusable_signals_allow_explicit_estimated_time_allocation() -> None:
    result = _fractions(
        _records(20000, speed=100), 2, CourseReconstructionConfig(), IntegrityConfig.running()
    )
    assert result == (
        AllocationMethod.TIMESTAMPS,
        (0.0, 1.0),
        ("distance_implausible", "speed_implausible"),
    )


@pytest.mark.parametrize(
    ("speed", "status"),
    [
        (25, "plausible"),
        (25.001, "implausible"),
        (float("nan"), "unavailable"),
        (-1, "unavailable"),
    ],
)
def test_speed_qualification_boundaries(speed, status) -> None:
    assert qualify_speed(_records(None, speed=speed), IntegrityConfig.running()).status == status
