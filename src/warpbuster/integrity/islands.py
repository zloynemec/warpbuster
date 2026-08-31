"""Bounded spoofing-island and bridge plausibility detection."""

from __future__ import annotations

from dataclasses import dataclass

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    BaselineStats,
    BridgeCandidateDiagnostic,
    BridgeCandidateOutcome,
    BridgeResult,
    CorruptedInterval,
    IntegrityConfidence,
    IntervalReason,
    IslandSearchDiagnostics,
    TransitionClassification,
    TransitionResult,
)

_STRONG_ISLAND_REASONS = (
    IntervalReason.IMPOSSIBLE_TRANSITION_IN,
    IntervalReason.IMPOSSIBLE_TRANSITION_OUT,
    IntervalReason.PLAUSIBLE_BRIDGE,
)


@dataclass(frozen=True, slots=True)
class IslandDetectionResult:
    """Intervals plus diagnostics from one bounded island-search pass."""

    intervals: tuple[CorruptedInterval, ...]
    diagnostics: IslandSearchDiagnostics


def detect_spoofing_islands(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
    baseline: BaselineStats,
    config: IntegrityConfig,
) -> IslandDetectionResult:
    """Find strong A→X ... Y→B islands using a bounded impossible-edge search."""
    impossible = tuple(
        transition
        for transition in transitions
        if transition.classification is TransitionClassification.IMPOSSIBLE
    )
    bridge_speed_limit_mps = _bridge_speed_limit(baseline, config)
    if bridge_speed_limit_mps is None:
        return IslandDetectionResult(
            intervals=(),
            diagnostics=_diagnostics(enabled=False, impossible_count=len(impossible)),
        )

    intervals: list[CorruptedInterval] = []
    details: list[BridgeCandidateDiagnostic] = []
    consumed_through_record_index = -1
    entries_considered = 0
    consumed_entries_skipped = 0
    candidates_considered = 0
    candidate_limit_pruned_count = 0
    time_window_pruned_count = 0
    invalid_candidate_count = 0
    implausible_bridge_count = 0

    for entry_position, entry in enumerate(impossible):
        if entry.from_record_index <= consumed_through_record_index:
            consumed_entries_skipped += 1
            continue
        entries_considered += 1
        candidate_start = entry_position + 1
        available_candidates = len(impossible) - candidate_start
        retained_candidates = min(
            available_candidates,
            config.island_search_max_exit_candidates,
        )
        candidate_limit_pruned_count += available_candidates - retained_candidates
        candidate_end = candidate_start + retained_candidates

        for exit_position in range(candidate_start, candidate_end):
            exit_transition = impossible[exit_position]
            if exit_transition.from_record_index < entry.to_record_index:
                continue
            candidates_considered += 1
            search_elapsed = _search_elapsed_seconds(entry, exit_transition)
            if search_elapsed is None:
                invalid_candidate_count += 1
                _retain_detail(
                    details,
                    _candidate_detail(
                        entry,
                        exit_transition,
                        BridgeCandidateOutcome.INVALID_ELAPSED_TIME,
                        bridge_speed_limit_mps,
                    ),
                    config,
                )
                continue
            if search_elapsed > config.island_search_max_elapsed_seconds:
                time_window_pruned_count += candidate_end - exit_position
                _retain_detail(
                    details,
                    _candidate_detail(
                        entry,
                        exit_transition,
                        BridgeCandidateOutcome.OUTSIDE_SEARCH_WINDOW,
                        bridge_speed_limit_mps,
                        search_elapsed_seconds=search_elapsed,
                    ),
                    config,
                )
                break

            bridge, outcome, bridge_distance_m, bridge_speed_mps = _evaluate_bridge(
                activity,
                entry,
                exit_transition,
                bridge_speed_limit_mps,
            )
            if outcome is BridgeCandidateOutcome.UNUSABLE_ANCHORS:
                invalid_candidate_count += 1
            elif outcome is BridgeCandidateOutcome.BRIDGE_TOO_FAST:
                implausible_bridge_count += 1
            if bridge is None:
                _retain_detail(
                    details,
                    _candidate_detail(
                        entry,
                        exit_transition,
                        outcome,
                        bridge_speed_limit_mps,
                        search_elapsed_seconds=search_elapsed,
                        bridge_distance_m=bridge_distance_m,
                        bridge_speed_mps=bridge_speed_mps,
                    ),
                    config,
                )
                continue

            interval = _interval(activity, entry, exit_transition, bridge)
            if interval is None:
                invalid_candidate_count += 1
                _retain_detail(
                    details,
                    _candidate_detail(
                        entry,
                        exit_transition,
                        BridgeCandidateOutcome.EMPTY_INTERVAL,
                        bridge_speed_limit_mps,
                        search_elapsed_seconds=search_elapsed,
                        bridge_distance_m=bridge.distance_m,
                        bridge_speed_mps=bridge.apparent_speed_mps,
                    ),
                    config,
                )
                continue

            intervals.append(interval)
            consumed_through_record_index = interval.end_record_index
            _retain_detail(
                details,
                _candidate_detail(
                    entry,
                    exit_transition,
                    BridgeCandidateOutcome.ACCEPTED,
                    bridge_speed_limit_mps,
                    search_elapsed_seconds=search_elapsed,
                    bridge_distance_m=bridge.distance_m,
                    bridge_speed_mps=bridge.apparent_speed_mps,
                ),
                config,
            )
            break

    return IslandDetectionResult(
        intervals=tuple(intervals),
        diagnostics=IslandSearchDiagnostics(
            enabled=True,
            bridge_speed_limit_mps=bridge_speed_limit_mps,
            impossible_transition_count=len(impossible),
            entries_considered=entries_considered,
            consumed_entries_skipped=consumed_entries_skipped,
            candidates_considered=candidates_considered,
            candidate_limit_pruned_count=candidate_limit_pruned_count,
            time_window_pruned_count=time_window_pruned_count,
            invalid_candidate_count=invalid_candidate_count,
            implausible_bridge_count=implausible_bridge_count,
            accepted_interval_count=len(intervals),
            retained_candidate_details=tuple(details),
            candidate_details_truncated_count=candidates_considered - len(details),
        ),
    )


def _diagnostics(*, enabled: bool, impossible_count: int) -> IslandSearchDiagnostics:
    return IslandSearchDiagnostics(
        enabled=enabled,
        bridge_speed_limit_mps=None,
        impossible_transition_count=impossible_count,
        entries_considered=0,
        consumed_entries_skipped=0,
        candidates_considered=0,
        candidate_limit_pruned_count=0,
        time_window_pruned_count=0,
        invalid_candidate_count=0,
        implausible_bridge_count=0,
        accepted_interval_count=0,
        retained_candidate_details=(),
        candidate_details_truncated_count=0,
    )


def _retain_detail(
    details: list[BridgeCandidateDiagnostic],
    detail: BridgeCandidateDiagnostic,
    config: IntegrityConfig,
) -> None:
    if len(details) < config.diagnostic_max_candidate_details:
        details.append(detail)


def _candidate_detail(
    entry: TransitionResult,
    exit_transition: TransitionResult,
    outcome: BridgeCandidateOutcome,
    bridge_speed_limit_mps: float,
    *,
    search_elapsed_seconds: float | None = None,
    bridge_distance_m: float | None = None,
    bridge_speed_mps: float | None = None,
) -> BridgeCandidateDiagnostic:
    return BridgeCandidateDiagnostic(
        entry_from_record_index=entry.from_record_index,
        entry_to_record_index=entry.to_record_index,
        exit_from_record_index=exit_transition.from_record_index,
        exit_to_record_index=exit_transition.to_record_index,
        search_elapsed_seconds=search_elapsed_seconds,
        bridge_distance_m=bridge_distance_m,
        bridge_speed_mps=bridge_speed_mps,
        bridge_speed_limit_mps=bridge_speed_limit_mps,
        outcome=outcome,
    )


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


def _evaluate_bridge(
    activity: ActivityData,
    entry: TransitionResult,
    exit_transition: TransitionResult,
    maximum_speed_mps: float,
) -> tuple[BridgeResult | None, BridgeCandidateOutcome, float | None, float | None]:
    if not _valid_record_index(activity, entry.from_record_index) or not _valid_record_index(
        activity, exit_transition.to_record_index
    ):
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    before = activity.records[entry.from_record_index]
    after = activity.records[exit_transition.to_record_index]
    if not _has_complete_observation(before) or not _has_complete_observation(after):
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    if before.timestamp is None or after.timestamp is None:
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    elapsed_seconds = (after.timestamp - before.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    if before.latitude is None or before.longitude is None:
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    if after.latitude is None or after.longitude is None:
        return None, BridgeCandidateOutcome.UNUSABLE_ANCHORS, None, None
    distance_m = geodesic_distance_m(
        before.latitude,
        before.longitude,
        after.latitude,
        after.longitude,
    )
    apparent_speed_mps = distance_m / elapsed_seconds
    if apparent_speed_mps > maximum_speed_mps:
        return (
            None,
            BridgeCandidateOutcome.BRIDGE_TOO_FAST,
            distance_m,
            apparent_speed_mps,
        )
    return (
        BridgeResult(
            from_record_index=before.index,
            to_record_index=after.index,
            elapsed_seconds=elapsed_seconds,
            distance_m=distance_m,
            apparent_speed_mps=apparent_speed_mps,
            maximum_plausible_speed_mps=maximum_speed_mps,
        ),
        BridgeCandidateOutcome.ACCEPTED,
        distance_m,
        apparent_speed_mps,
    )


def _valid_record_index(activity: ActivityData, index: int) -> bool:
    return 0 <= index < len(activity.records)


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
    if (
        start_record_index > end_record_index
        or not _valid_record_index(activity, start_record_index)
        or not _valid_record_index(activity, end_record_index)
    ):
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
