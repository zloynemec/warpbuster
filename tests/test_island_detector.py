"""Long spoofing-island and bridge plausibility tests."""

from dataclasses import replace
from time import perf_counter

import pytest

from tests.activity_factory import Observation, eastward_observations, make_activity
from warpbuster.config import IntegrityConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntervalReason,
    TransitionClassification,
)


def test_long_smooth_spoofing_island_is_one_corrupted_interval() -> None:
    """Impossible entry/exit plus a plausible bridge captures the full fake island."""
    activity = make_activity(_spoof_island_observations())

    report = analyze_integrity(activity)

    assert report.count(TransitionClassification.IMPOSSIBLE) == 2
    assert len(report.corrupted_intervals) == 1
    interval = report.corrupted_intervals[0]
    assert interval.start_record_index == 1
    assert interval.end_record_index == 1_201
    assert interval.record_count == 1_201
    assert interval.trusted_before_record_index == 0
    assert interval.trusted_after_record_index == 1_202
    assert interval.confidence is IntegrityConfidence.HIGH
    assert interval.reasons == (
        IntervalReason.IMPOSSIBLE_TRANSITION_IN,
        IntervalReason.IMPOSSIBLE_TRANSITION_OUT,
        IntervalReason.PLAUSIBLE_BRIDGE,
    )
    assert interval.bridge.apparent_speed_mps == pytest.approx(3.0, abs=0.05)
    assert interval.start_timestamp is not None
    assert interval.end_timestamp is not None
    assert (interval.end_timestamp - interval.start_timestamp).total_seconds() == 1_200.0


def test_single_spike_is_grouped_as_one_record_interval() -> None:
    """The same strong structure also gives precise boundaries for a short spike."""
    activity = make_activity(
        [
            *eastward_observations([0.0], [0.0]),
            (1.0, 56.0, 37.0),
            *eastward_observations([2.0], [6.0]),
        ]
    )

    report = analyze_integrity(activity)

    assert len(report.corrupted_intervals) == 1
    assert report.corrupted_intervals[0].start_record_index == 1
    assert report.corrupted_intervals[0].end_record_index == 1


def test_implausible_bridge_does_not_create_an_interval() -> None:
    """Two impossible edges are insufficient when trusted anchors are unreachable."""
    activity = make_activity(
        [
            (0.0, 55.0, 37.0),
            (1.0, 56.0, 37.0),
            (9.0, 56.0, 37.0001),
            (10.0, 55.0, 38.0),
        ]
    )

    report = analyze_integrity(activity)

    assert report.count(TransitionClassification.IMPOSSIBLE) == 2
    assert report.corrupted_intervals == ()


def test_island_search_respects_elapsed_time_bound() -> None:
    """An exit outside the configured reachability window is not inspected."""
    activity = make_activity(_spoof_island_observations())
    config = replace(
        IntegrityConfig.running(),
        island_search_max_elapsed_seconds=600.0,
    )

    report = analyze_integrity(activity, config)

    assert report.corrupted_intervals == ()


def test_island_search_respects_exit_candidate_bound() -> None:
    """Candidate pruning prevents unbounded scans through impossible transitions."""
    activity = make_activity(
        [
            (0.0, 55.0, 37.0),
            (1.0, 56.0, 37.0),
            (2.0, 57.0, 37.0),
            *eastward_observations([3.0], [9.0]),
        ]
    )
    unbounded_report = analyze_integrity(activity)
    bounded_config = replace(
        IntegrityConfig.running(),
        island_search_max_exit_candidates=1,
    )

    bounded_report = analyze_integrity(activity, bounded_config)

    assert len(unbounded_report.corrupted_intervals) == 1
    assert bounded_report.corrupted_intervals == ()


def test_local_and_island_analysis_of_20k_records_is_bounded() -> None:
    """A long clean activity stays comfortably within the MVP performance target."""
    count = 20_000
    observations = eastward_observations(
        [float(index) for index in range(count)],
        [float(index * 3) for index in range(count)],
    )
    activity = make_activity(observations)

    started = perf_counter()
    report = analyze_integrity(activity)
    elapsed_seconds = perf_counter() - started

    assert report.corrupted_intervals == ()
    assert elapsed_seconds < 5.0


def test_many_impossible_edges_keep_diagnostics_bounded() -> None:
    """Worst-case local findings do not create unbounded diagnostic storage."""
    count = 20_000
    base_track = eastward_observations(
        [float(index) for index in range(count)],
        [float(index * 3) for index in range(count)],
    )
    observations = [
        observation if index % 2 == 0 else (observation[0], 56.0, observation[2])
        for index, observation in enumerate(base_track)
    ]
    activity = make_activity(observations)

    started = perf_counter()
    report = analyze_integrity(activity)
    elapsed_seconds = perf_counter() - started

    diagnostics = report.island_search_diagnostics
    assert diagnostics.candidates_considered >= diagnostics.accepted_interval_count
    assert len(diagnostics.retained_candidate_details) == 100
    assert diagnostics.candidate_details_truncated_count > 0
    assert elapsed_seconds < 5.0


def _spoof_island_observations() -> list[Observation]:
    observations: list[Observation] = [eastward_observations([0.0], [0.0])[0]]
    observations.extend(
        eastward_observations(
            [float(second) for second in range(1, 1_202)],
            [float((second - 1) * 3) for second in range(1, 1_202)],
            latitude=56.0,
        )
    )
    observations.extend(eastward_observations([1_202.0], [3_606.0]))
    return observations
