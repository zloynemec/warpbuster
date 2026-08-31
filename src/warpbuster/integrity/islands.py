"""Bounded spoofing-island and bridge plausibility detection."""

from __future__ import annotations

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    BaselineStats,
    BridgeResult,
    CorruptedInterval,
    IntegrityConfidence,
    IntervalReason,
    TransitionClassification,
    TransitionResult,
)

_STRONG_ISLAND_REASONS = (
    IntervalReason.IMPOSSIBLE_TRANSITION_IN,
    IntervalReason.IMPOSSIBLE_TRANSITION_OUT,
    IntervalReason.PLAUSIBLE_BRIDGE,
)


def detect_spoofing_islands(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
    baseline: BaselineStats,
    config: IntegrityConfig,
) -> tuple[CorruptedInterval, ...]:
    """Find strong A→X ... Y→B islands using a bounded impossible-edge search."""
    if config.bridge_max_speed_mps is None:
        return ()
    bridge_speed_limit_mps = _bridge_speed_limit(baseline, config)
    if bridge_speed_limit_mps is None:
        return ()

    impossible = tuple(
        transition
        for transition in transitions
        if transition.classification is TransitionClassification.IMPOSSIBLE
    )
    intervals: list[CorruptedInterval] = []
    consumed_through_record_index = -1

    for entry_position, entry in enumerate(impossible):
        if entry.from_record_index <= consumed_through_record_index:
            continue
        candidate_exits = impossible[
            entry_position + 1 : entry_position + 1 + config.island_search_max_exit_candidates
        ]
        for exit_transition in candidate_exits:
            if exit_transition.from_record_index < entry.to_record_index:
                continue
            search_elapsed = _search_elapsed_seconds(entry, exit_transition)
            if search_elapsed is None:
                continue
            if search_elapsed > config.island_search_max_elapsed_seconds:
                break
            bridge = _bridge(activity, entry, exit_transition, bridge_speed_limit_mps)
            if bridge is None:
                continue
            interval = _interval(activity, entry, exit_transition, bridge)
            if interval is None:
                continue
            intervals.append(interval)
            consumed_through_record_index = interval.end_record_index
            break

    return tuple(intervals)


def _bridge_speed_limit(
    baseline: BaselineStats,
    config: IntegrityConfig,
) -> float | None:
    if config.bridge_max_speed_mps is None or baseline.median_speed_mps is None:
        return None
    baseline_limit = baseline.median_speed_mps * config.bridge_baseline_multiplier
    return min(
        config.bridge_max_speed_mps,
        max(config.bridge_speed_floor_mps, baseline_limit),
    )


def _search_elapsed_seconds(
    entry: TransitionResult,
    exit_transition: TransitionResult,
) -> float | None:
    if entry.from_timestamp is None or exit_transition.to_timestamp is None:
        return None
    elapsed_seconds = (exit_transition.to_timestamp - entry.from_timestamp).total_seconds()
    return elapsed_seconds if elapsed_seconds > 0 else None


def _bridge(
    activity: ActivityData,
    entry: TransitionResult,
    exit_transition: TransitionResult,
    maximum_speed_mps: float,
) -> BridgeResult | None:
    before = activity.records[entry.from_record_index]
    after = activity.records[exit_transition.to_record_index]
    if not _has_complete_observation(before) or not _has_complete_observation(after):
        return None
    if before.timestamp is None or after.timestamp is None:
        return None
    elapsed_seconds = (after.timestamp - before.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return None
    if before.latitude is None or before.longitude is None:
        return None
    if after.latitude is None or after.longitude is None:
        return None
    distance_m = geodesic_distance_m(
        before.latitude,
        before.longitude,
        after.latitude,
        after.longitude,
    )
    apparent_speed_mps = distance_m / elapsed_seconds
    if apparent_speed_mps > maximum_speed_mps:
        return None
    return BridgeResult(
        from_record_index=before.index,
        to_record_index=after.index,
        elapsed_seconds=elapsed_seconds,
        distance_m=distance_m,
        apparent_speed_mps=apparent_speed_mps,
        maximum_plausible_speed_mps=maximum_speed_mps,
    )


def _has_complete_observation(record: ActivityRecord) -> bool:
    return (
        record.timestamp is not None
        and record.latitude is not None
        and record.longitude is not None
    )


def _interval(
    activity: ActivityData,
    entry: TransitionResult,
    exit_transition: TransitionResult,
    bridge: BridgeResult,
) -> CorruptedInterval | None:
    start_record_index = entry.from_record_index + 1
    end_record_index = exit_transition.to_record_index - 1
    if start_record_index > end_record_index:
        return None
    start_record = activity.records[start_record_index]
    end_record = activity.records[end_record_index]
    return CorruptedInterval(
        start_record_index=start_record_index,
        end_record_index=end_record_index,
        start_timestamp=start_record.timestamp,
        end_timestamp=end_record.timestamp,
        trusted_before_record_index=entry.from_record_index,
        trusted_after_record_index=exit_transition.to_record_index,
        entry_transition=entry,
        exit_transition=exit_transition,
        bridge=bridge,
        confidence=IntegrityConfidence.HIGH,
        reasons=_STRONG_ISLAND_REASONS,
    )
