"""Local physical-transition detector tests."""

import pytest

from tests.activity_factory import eastward_observations, make_activity
from warpbuster.config import IntegrityConfig, IntegrityProfile
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityStatus,
    TransitionClassification,
    TransitionReason,
)


def test_clean_run_is_clean() -> None:
    """A steady synthetic run contains no local integrity findings."""
    activity = make_activity(
        eastward_observations(list(range(10)), [index * 3 for index in range(10)])
    )

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.CLEAN
    assert report.confidence is IntegrityConfidence.HIGH
    assert report.config.profile is IntegrityProfile.RUNNING
    assert report.count(TransitionClassification.NORMAL) == 9
    assert report.count(TransitionClassification.SUSPICIOUS) == 0
    assert report.count(TransitionClassification.IMPOSSIBLE) == 0
    assert report.baseline.median_speed_mps == pytest.approx(3.0, abs=0.01)


def test_one_kilometre_per_second_is_impossible() -> None:
    """A huge spike and its return are absolute physical impossibilities."""
    activity = make_activity(
        eastward_observations(
            list(range(8)),
            [0.0, 3.0, 6.0, 1_006.0, 9.0, 12.0, 15.0, 18.0],
        )
    )

    report = analyze_integrity(activity)
    impossible = [
        transition
        for transition in report.transitions
        if transition.classification is TransitionClassification.IMPOSSIBLE
    ]

    assert report.status is IntegrityStatus.CORRUPTED
    assert report.confidence is IntegrityConfidence.HIGH
    assert len(impossible) == 2
    assert impossible[0].apparent_speed_mps == pytest.approx(1_000.0, rel=0.01)
    assert impossible[0].reasons == (TransitionReason.ABSOLUTE_SPEED_AND_DISTANCE_EXCEEDED,)


def test_irregular_sampling_uses_elapsed_time() -> None:
    """Long sampling gaps do not turn normal movement into a teleport."""
    activity = make_activity(
        eastward_observations(
            [0.0, 1.0, 6.0, 26.0, 27.0, 32.0],
            [0.0, 3.0, 18.0, 78.0, 81.0, 96.0],
        )
    )

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.CLEAN
    assert {transition.elapsed_seconds for transition in report.transitions} == {1.0, 5.0, 20.0}
    assert all(
        transition.classification is TransitionClassification.NORMAL
        for transition in report.transitions
    )


def test_missing_position_is_skipped_without_losing_elapsed_time() -> None:
    """Valid observations around missing GNSS use their full timestamp interval."""
    activity = make_activity(
        [
            (0.0, 55.0, 37.0),
            (1.0, None, None),
            eastward_observations([2.0], [6.0])[0],
        ]
    )

    report = analyze_integrity(activity)

    assert report.missing_position_record_count == 1
    assert report.status is IntegrityStatus.UNKNOWN
    assert report.confidence is IntegrityConfidence.LOW
    assert len(report.transitions) == 1
    assert report.transitions[0].from_record_index == 0
    assert report.transitions[0].to_record_index == 2
    assert report.transitions[0].elapsed_seconds == 2.0
    assert report.transitions[0].classification is TransitionClassification.NORMAL


def test_fast_but_plausible_segment_is_not_corrupted() -> None:
    """A short 18 m/s segment remains below conservative evidence thresholds."""
    activity = make_activity(
        eastward_observations(
            list(range(8)),
            [0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 33.0, 36.0],
        )
    )

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.CLEAN
    assert report.transitions[5].apparent_speed_mps == pytest.approx(18.0, abs=0.1)
    assert report.transitions[5].classification is TransitionClassification.NORMAL


def test_relative_outlier_is_suspicious_but_not_impossible() -> None:
    """Relative evidence alone cannot prove coordinate corruption."""
    activity = make_activity(
        eastward_observations(
            list(range(8)),
            [0.0, 3.0, 6.0, 9.0, 12.0, 15.0, 45.0, 48.0],
        )
    )

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.SUSPICIOUS
    assert report.confidence is IntegrityConfidence.LOW
    assert report.transitions[5].classification is TransitionClassification.SUSPICIOUS
    assert report.transitions[5].reasons == (TransitionReason.RELATIVE_SPEED_OUTLIER,)


def test_unknown_sport_does_not_claim_speed_only_impossibility() -> None:
    """Generic activities remain suspicious when no sport ceiling is justified."""
    activity = make_activity(
        eastward_observations([0.0, 1.0], [0.0, 1_000.0]),
        sport=None,
    )

    report = analyze_integrity(activity)

    assert report.config.profile is IntegrityProfile.GENERIC
    assert report.config.absolute_impossible_speed_mps is None
    assert report.status is IntegrityStatus.SUSPICIOUS
    assert report.confidence is IntegrityConfidence.LOW
    assert report.transitions[0].classification is TransitionClassification.SUSPICIOUS


def test_missing_timestamp_is_unknown() -> None:
    """The detector does not invent speed when timestamps are missing."""
    activity = make_activity(
        [
            *eastward_observations([0.0], [0.0]),
            (None, 55.0, eastward_observations([1.0], [3.0])[0][2]),
            *eastward_observations([0.0], [6.0]),
        ]
    )

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.UNKNOWN
    assert [transition.classification for transition in report.transitions] == [
        TransitionClassification.UNKNOWN,
        TransitionClassification.UNKNOWN,
    ]
    assert report.transitions[0].reasons == (TransitionReason.MISSING_TIMESTAMP,)
    assert report.transitions[1].reasons == (TransitionReason.MISSING_TIMESTAMP,)


def test_non_positive_time_delta_is_unknown() -> None:
    """Duplicate or decreasing timestamps cannot produce an apparent speed."""
    activity = make_activity(eastward_observations([0.0, 0.0, -1.0], [0.0, 3.0, 6.0]))

    report = analyze_integrity(activity)

    assert report.status is IntegrityStatus.UNKNOWN
    assert [transition.elapsed_seconds for transition in report.transitions] == [0.0, -1.0]
    assert all(
        transition.reasons == (TransitionReason.NON_POSITIVE_TIME_DELTA,)
        for transition in report.transitions
    )


def test_custom_absolute_threshold_changes_classification() -> None:
    """Classification depends on explicit config rather than hidden constants."""
    activity = make_activity(eastward_observations([0.0, 1.0], [0.0, 10.0]))
    config = IntegrityConfig(
        absolute_impossible_speed_mps=5.0,
        absolute_impossible_distance_m=5.0,
    )

    report = analyze_integrity(activity, config)

    assert report.transitions[0].classification is TransitionClassification.IMPOSSIBLE
