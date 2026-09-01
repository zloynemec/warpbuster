"""Conservative bounded detection of missing-exit GNSS failure clusters."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.integrity.islands import bridge_speed_limit
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    BaselineStats,
    BridgeResult,
    CorruptedInterval,
    IntegrityConfidence,
    IntervalDetectionKind,
    IntervalReason,
    OneSidedClusterDiagnostic,
    OneSidedClusterReason,
    OneSidedSearchDiagnostics,
    TransitionClassification,
    TransitionResult,
)

_ONE_SIDED_INTERVAL_REASONS = (
    IntervalReason.IMPOSSIBLE_TRANSITION_IN,
    IntervalReason.MISSING_EXIT_BOUNDARY,
    IntervalReason.STABLE_OUTER_ANCHORS,
    IntervalReason.TAINTED_POSITION_COMPONENTS,
    IntervalReason.PLAUSIBLE_BRIDGE,
)


@dataclass(frozen=True, slots=True)
class OneSidedDetectionResult:
    """Intervals and diagnostics from the bounded one-sided search."""

    intervals: tuple[CorruptedInterval, ...]
    diagnostics: OneSidedSearchDiagnostics


def detect_one_sided_clusters(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
    baseline: BaselineStats,
    config: IntegrityConfig,
    classic_intervals: tuple[CorruptedInterval, ...],
) -> OneSidedDetectionResult:
    """Find missing-terminated clusters without using course geometry.

    A reconstructable cluster needs an impossible adjacent entry, missing-position
    evidence, stable positioned anchors on both sides, a plausible direct bridge, and
    suspicious/impossible evidence touching every positioned component inside the
    proposed interval. The proof is deliberately capped at MEDIUM confidence.
    """
    maximum_bridge_speed = bridge_speed_limit(baseline, config)
    impossible = tuple(
        transition
        for transition in transitions
        if transition.classification is TransitionClassification.IMPOSSIBLE
    )
    missing_indices = tuple(
        record.index for record in activity.records if not _has_position(record)
    )
    if maximum_bridge_speed is None:
        return _empty_result(enabled=False)

    transition_by_pair = {
        (transition.from_record_index, transition.to_record_index): transition
        for transition in transitions
    }
    abnormal = tuple(
        transition
        for transition in transitions
        if transition.classification
        in (TransitionClassification.SUSPICIOUS, TransitionClassification.IMPOSSIBLE)
    )
    abnormal_by_record: dict[int, list[TransitionResult]] = {}
    for transition in abnormal:
        abnormal_by_record.setdefault(transition.from_record_index, []).append(transition)
        abnormal_by_record.setdefault(transition.to_record_index, []).append(transition)
    missing_index_set = set(missing_indices)
    intervals: list[CorruptedInterval] = []
    diagnostics: list[OneSidedClusterDiagnostic] = []
    considered = 0
    classic_skipped = 0
    with_missing = 0
    reconstructable_count = 0
    unresolved_count = 0
    records_scanned = 0
    total_diagnostics = 0
    consumed_through = -1

    for entry in impossible:
        if _belongs_to_classic_interval(entry, classic_intervals):
            classic_skipped += 1
            continue
        if entry.from_record_index <= consumed_through:
            continue
        considered += 1
        window_end = min(
            len(activity.records) - 1,
            entry.to_record_index + config.one_sided_search_max_records - 1,
        )
        records_scanned += max(0, window_end - entry.to_record_index + 1)
        first_missing_position = bisect_left(missing_indices, entry.to_record_index)
        nearby_missing = (
            first_missing_position < len(missing_indices)
            and missing_indices[first_missing_position] <= window_end
        )
        if not nearby_missing:
            diagnostic = _early_diagnostic(
                entry,
                config,
                maximum_bridge_speed,
                OneSidedClusterReason.NO_NEARBY_MISSING_POSITION,
            )
            total_diagnostics += 1
            unresolved_count += 1
            _retain(diagnostics, diagnostic, config)
            continue
        with_missing += 1

        diagnostic, interval = _evaluate_candidate(
            activity,
            entry,
            abnormal_by_record,
            missing_index_set,
            transition_by_pair,
            window_end,
            maximum_bridge_speed,
            config,
        )
        total_diagnostics += 1
        _retain(diagnostics, diagnostic, config)
        if interval is None:
            unresolved_count += 1
            continue
        reconstructable_count += 1
        intervals.append(interval)
        consumed_through = interval.end_record_index

    return OneSidedDetectionResult(
        intervals=tuple(intervals),
        diagnostics=OneSidedSearchDiagnostics(
            enabled=True,
            impossible_entries_considered=considered,
            classic_interval_entries_skipped=classic_skipped,
            candidates_with_missing_evidence=with_missing,
            reconstructable_cluster_count=reconstructable_count,
            unresolved_cluster_count=unresolved_count,
            records_scanned=records_scanned,
            retained_clusters=tuple(diagnostics),
            clusters_truncated_count=total_diagnostics - len(diagnostics),
        ),
    )


def _evaluate_candidate(
    activity: ActivityData,
    entry: TransitionResult,
    abnormal_by_record: dict[int, list[TransitionResult]],
    missing_indices: set[int],
    transition_by_pair: dict[tuple[int, int], TransitionResult],
    window_end: int,
    maximum_bridge_speed: float,
    config: IntegrityConfig,
) -> tuple[OneSidedClusterDiagnostic, CorruptedInterval | None]:
    start = entry.to_record_index
    reasons = [OneSidedClusterReason.IMPOSSIBLE_ENTRY]
    if entry.to_record_index != entry.from_record_index + 1:
        reasons.append(OneSidedClusterReason.ENTRY_NOT_ADJACENT)

    evidence = {start}
    candidate_abnormal: dict[tuple[int, int], TransitionResult] = {}
    for index in range(start, window_end + 1):
        if index in missing_indices:
            evidence.add(index)
        for transition in abnormal_by_record.get(index, ()):
            candidate_abnormal[(transition.from_record_index, transition.to_record_index)] = (
                transition
            )
            evidence.add(max(start, transition.from_record_index))
            evidence.add(min(window_end, transition.to_record_index))
    ordered_evidence = sorted(evidence)
    last_evidence = start
    for index in ordered_evidence[1:]:
        if index - last_evidence - 1 > config.one_sided_max_clean_gap_records:
            break
        last_evidence = index

    if _has_position(activity.records[last_evidence]):
        reasons.append(OneSidedClusterReason.CLUSTER_NOT_MISSING_TERMINATED)
        end: int | None = last_evidence
    else:
        end = last_evidence
        reasons.extend(
            (
                OneSidedClusterReason.MISSING_POSITION_EVIDENCE,
                OneSidedClusterReason.MISSING_EXIT_BOUNDARY,
            )
        )

    after_index = end + 1 if end is not None else None
    before = _record(activity, entry.from_record_index)
    after = _record(activity, after_index)
    anchor_available = (
        before is not None
        and after is not None
        and _has_complete_observation(before)
        and _has_complete_observation(after)
    )
    if not anchor_available:
        reasons.append(OneSidedClusterReason.TRUSTED_ANCHOR_UNAVAILABLE)

    same_continuity = (
        before is not None and after is not None and before.continuity_id == after.continuity_id
    )
    if anchor_available and not same_continuity:
        reasons.append(OneSidedClusterReason.CONTINUITY_BOUNDARY)

    before_normal = _normal_context_count(
        transition_by_pair,
        entry.from_record_index,
        direction=-1,
        scan_limit=config.one_sided_anchor_scan_max_records,
    )
    after_normal = (
        _normal_context_count(
            transition_by_pair,
            after_index,
            direction=1,
            scan_limit=config.one_sided_anchor_scan_max_records,
        )
        if after_index is not None
        else 0
    )
    if before_normal < config.one_sided_anchor_min_normal_transitions:
        reasons.append(OneSidedClusterReason.ANCHOR_BEFORE_UNSTABLE)
    if after_normal < config.one_sided_anchor_min_normal_transitions:
        reasons.append(OneSidedClusterReason.ANCHOR_AFTER_UNSTABLE)
    if (
        before_normal >= config.one_sided_anchor_min_normal_transitions
        and after_normal >= config.one_sided_anchor_min_normal_transitions
    ):
        reasons.append(OneSidedClusterReason.STABLE_OUTER_ANCHORS)

    measured_bridge = (
        _bridge(before, after, maximum_bridge_speed)
        if anchor_available and same_continuity
        else None
    )
    if measured_bridge is None:
        reasons.append(OneSidedClusterReason.BRIDGE_UNAVAILABLE)
        bridge = None
    elif measured_bridge.apparent_speed_mps > maximum_bridge_speed:
        reasons.append(OneSidedClusterReason.BRIDGE_TOO_FAST)
        bridge = None
    else:
        reasons.append(OneSidedClusterReason.PLAUSIBLE_OUTER_BRIDGE)
        bridge = measured_bridge

    candidate_end = end if end is not None else start
    candidate_transitions = tuple(
        transition
        for transition in candidate_abnormal.values()
        if transition.to_record_index >= start and transition.from_record_index <= candidate_end
    )
    components = _positioned_components(activity, start, candidate_end)
    tainted_components = sum(
        _component_is_tainted(component, candidate_transitions) for component in components
    )
    if components and tainted_components == len(components):
        reasons.append(OneSidedClusterReason.ALL_POSITION_COMPONENTS_TAINTED)
    else:
        reasons.append(OneSidedClusterReason.UNTAINTED_POSITION_COMPONENT)

    failure_reasons = {
        OneSidedClusterReason.ENTRY_NOT_ADJACENT,
        OneSidedClusterReason.CLUSTER_NOT_MISSING_TERMINATED,
        OneSidedClusterReason.TRUSTED_ANCHOR_UNAVAILABLE,
        OneSidedClusterReason.CONTINUITY_BOUNDARY,
        OneSidedClusterReason.ANCHOR_BEFORE_UNSTABLE,
        OneSidedClusterReason.ANCHOR_AFTER_UNSTABLE,
        OneSidedClusterReason.BRIDGE_UNAVAILABLE,
        OneSidedClusterReason.BRIDGE_TOO_FAST,
        OneSidedClusterReason.UNTAINTED_POSITION_COMPONENT,
    }
    reconstructable = (
        end is not None and bridge is not None and not failure_reasons.intersection(reasons)
    )
    confidence = IntegrityConfidence.MEDIUM if reconstructable else IntegrityConfidence.LOW
    missing_count = sum(
        not _has_position(record) for record in activity.records[start : candidate_end + 1]
    )
    diagnostic = OneSidedClusterDiagnostic(
        start_record_index=start,
        end_record_index=end,
        trusted_before_record_index=entry.from_record_index,
        trusted_after_record_index=after_index,
        missing_position_record_count=missing_count,
        impossible_transition_count=sum(
            transition.classification is TransitionClassification.IMPOSSIBLE
            for transition in candidate_transitions
        ),
        suspicious_transition_count=sum(
            transition.classification is TransitionClassification.SUSPICIOUS
            for transition in candidate_transitions
        ),
        positioned_component_count=len(components),
        tainted_positioned_component_count=tainted_components,
        anchor_before_normal_transition_count=before_normal,
        anchor_after_normal_transition_count=after_normal,
        anchor_required_normal_transition_count=config.one_sided_anchor_min_normal_transitions,
        bridge=measured_bridge,
        bridge_speed_limit_mps=maximum_bridge_speed,
        confidence=confidence,
        reconstructable=reconstructable,
        reasons=tuple(reasons),
    )
    if not reconstructable or end is None or after_index is None or bridge is None:
        return diagnostic, None
    start_record = activity.records[start]
    end_record = activity.records[end]
    return diagnostic, CorruptedInterval(
        start_record_index=start,
        end_record_index=end,
        start_timestamp=start_record.timestamp,
        end_timestamp=end_record.timestamp,
        trusted_before_record_index=entry.from_record_index,
        trusted_after_record_index=after_index,
        entry_transition=entry,
        exit_transition=None,
        bridge=bridge,
        confidence=IntegrityConfidence.MEDIUM,
        reasons=_ONE_SIDED_INTERVAL_REASONS,
        detection_kind=IntervalDetectionKind.ONE_SIDED_CLUSTER,
    )


def _normal_context_count(
    transition_by_pair: dict[tuple[int, int], TransitionResult],
    anchor_index: int | None,
    *,
    direction: int,
    scan_limit: int,
) -> int:
    if anchor_index is None:
        return 0
    count = 0
    current = anchor_index
    for _ in range(scan_limit):
        pair = (current - 1, current) if direction < 0 else (current, current + 1)
        transition = transition_by_pair.get(pair)
        if transition is None or transition.classification is not TransitionClassification.NORMAL:
            break
        count += 1
        current += direction
    return count


def _positioned_components(
    activity: ActivityData,
    start: int,
    end: int,
) -> tuple[tuple[int, int], ...]:
    components: list[tuple[int, int]] = []
    component_start: int | None = None
    for index in range(start, end + 1):
        if _has_position(activity.records[index]):
            if component_start is None:
                component_start = index
        elif component_start is not None:
            components.append((component_start, index - 1))
            component_start = None
    if component_start is not None:
        components.append((component_start, end))
    return tuple(components)


def _component_is_tainted(
    component: tuple[int, int],
    abnormal: tuple[TransitionResult, ...],
) -> bool:
    start, end = component
    return any(
        transition.to_record_index == transition.from_record_index + 1
        and (
            start <= transition.from_record_index <= end
            or start <= transition.to_record_index <= end
        )
        for transition in abnormal
    )


def _bridge(
    before: ActivityRecord | None,
    after: ActivityRecord | None,
    maximum_speed_mps: float,
) -> BridgeResult | None:
    if before is None or after is None or not _has_complete_observation(before):
        return None
    if not _has_complete_observation(after):
        return None
    assert before.timestamp is not None and after.timestamp is not None
    assert before.latitude is not None and before.longitude is not None
    assert after.latitude is not None and after.longitude is not None
    elapsed_seconds = (after.timestamp - before.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return None
    distance_m = geodesic_distance_m(
        before.latitude,
        before.longitude,
        after.latitude,
        after.longitude,
    )
    return BridgeResult(
        from_record_index=before.index,
        to_record_index=after.index,
        elapsed_seconds=elapsed_seconds,
        distance_m=distance_m,
        apparent_speed_mps=distance_m / elapsed_seconds,
        maximum_plausible_speed_mps=maximum_speed_mps,
    )


def _belongs_to_classic_interval(
    entry: TransitionResult,
    intervals: tuple[CorruptedInterval, ...],
) -> bool:
    return any(
        interval.trusted_before_record_index <= entry.from_record_index
        and entry.to_record_index <= interval.trusted_after_record_index
        for interval in intervals
    )


def _record(activity: ActivityData, index: int | None) -> ActivityRecord | None:
    if index is None or not 0 <= index < len(activity.records):
        return None
    return activity.records[index]


def _has_position(record: ActivityRecord) -> bool:
    return record.latitude is not None and record.longitude is not None


def _has_complete_observation(record: ActivityRecord) -> bool:
    return record.timestamp is not None and _has_position(record)


def _early_diagnostic(
    entry: TransitionResult,
    config: IntegrityConfig,
    bridge_limit: float,
    reason: OneSidedClusterReason,
) -> OneSidedClusterDiagnostic:
    return OneSidedClusterDiagnostic(
        start_record_index=entry.to_record_index,
        end_record_index=None,
        trusted_before_record_index=entry.from_record_index,
        trusted_after_record_index=None,
        missing_position_record_count=0,
        impossible_transition_count=1,
        suspicious_transition_count=0,
        positioned_component_count=0,
        tainted_positioned_component_count=0,
        anchor_before_normal_transition_count=0,
        anchor_after_normal_transition_count=0,
        anchor_required_normal_transition_count=config.one_sided_anchor_min_normal_transitions,
        bridge=None,
        bridge_speed_limit_mps=bridge_limit,
        confidence=IntegrityConfidence.LOW,
        reconstructable=False,
        reasons=(OneSidedClusterReason.IMPOSSIBLE_ENTRY, reason),
    )


def _retain(
    retained: list[OneSidedClusterDiagnostic],
    diagnostic: OneSidedClusterDiagnostic,
    config: IntegrityConfig,
) -> None:
    if len(retained) < config.one_sided_max_diagnostics:
        retained.append(diagnostic)


def _empty_result(*, enabled: bool) -> OneSidedDetectionResult:
    return OneSidedDetectionResult(
        intervals=(),
        diagnostics=OneSidedSearchDiagnostics(
            enabled=enabled,
            impossible_entries_considered=0,
            classic_interval_entries_skipped=0,
            candidates_with_missing_evidence=0,
            reconstructable_cluster_count=0,
            unresolved_cluster_count=0,
            records_scanned=0,
            retained_clusters=(),
            clusters_truncated_count=0,
        ),
    )
