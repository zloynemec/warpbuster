"""Course-independent safety checks for proposed reconstruction anchors."""

from __future__ import annotations

from dataclasses import dataclass

from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    CorruptedInterval,
    IntegrityConfidence,
    IntegrityReport,
    TransitionClassification,
    TransitionResult,
)
from warpbuster.models.reconstruction import (
    AnchorDirection,
    AnchorStabilityDiagnostic,
    MixedGnssRegion,
    ReconstructionReason,
)


@dataclass(frozen=True, slots=True)
class IntervalSafetyAssessment:
    """Anchor diagnostics and an optional wider mixed-evidence region."""

    anchor_before: AnchorStabilityDiagnostic
    anchor_after: AnchorStabilityDiagnostic
    mixed_region: MixedGnssRegion | None

    @property
    def anchors_stable(self) -> bool:
        """Return whether both original interval anchors have sufficient context."""
        return self.anchor_before.stable and self.anchor_after.stable


def assess_interval_safety(
    activity: ActivityData,
    integrity: IntegrityReport,
    interval: CorruptedInterval,
    config: CourseReconstructionConfig,
) -> IntervalSafetyAssessment:
    """Validate original anchors and diagnose nearby mixed GNSS evidence without course."""
    transitions = {
        (transition.from_record_index, transition.to_record_index): transition
        for transition in integrity.transitions
    }
    before = _anchor_stability(
        activity,
        transitions,
        interval.trusted_before_record_index,
        AnchorDirection.BEFORE,
        config,
    )
    after = _anchor_stability(
        activity,
        transitions,
        interval.trusted_after_record_index,
        AnchorDirection.AFTER,
        config,
    )
    mixed_region = (
        None
        if before.stable and after.stable
        else _mixed_region(activity, integrity, interval, transitions, config)
    )
    return IntervalSafetyAssessment(
        anchor_before=before,
        anchor_after=after,
        mixed_region=mixed_region,
    )


def _anchor_stability(
    activity: ActivityData,
    transitions: dict[tuple[int, int], TransitionResult],
    anchor_index: int,
    direction: AnchorDirection,
    config: CourseReconstructionConfig,
) -> AnchorStabilityDiagnostic:
    step = -1 if direction is AnchorDirection.BEFORE else 1
    current_index = anchor_index
    normal_count = 0
    blocking_record_index: int | None = None
    blocking_classification: TransitionClassification | None = None
    blocking_reason: ReconstructionReason | None = None

    for _ in range(config.anchor_stability_scan_max_records):
        next_index = current_index + step
        if not 0 <= next_index < len(activity.records):
            blocking_reason = ReconstructionReason.ACTIVITY_BOUNDARY_CONTEXT
            break
        current = activity.records[current_index]
        adjacent = activity.records[next_index]
        if current.continuity_id != adjacent.continuity_id:
            blocking_record_index = next_index
            blocking_reason = ReconstructionReason.CONTINUITY_BOUNDARY_CONTEXT
            break
        if not _has_position(current) or not _has_position(adjacent):
            blocking_record_index = next_index
            blocking_reason = ReconstructionReason.MISSING_POSITION_CONTEXT
            break
        key = (
            (next_index, current_index)
            if direction is AnchorDirection.BEFORE
            else (current_index, next_index)
        )
        transition = transitions.get(key)
        if transition is None:
            blocking_record_index = next_index
            blocking_reason = ReconstructionReason.INSUFFICIENT_NORMAL_CONTEXT
            break
        if transition.classification is not TransitionClassification.NORMAL:
            blocking_record_index = next_index
            blocking_classification = transition.classification
            blocking_reason = ReconstructionReason.NON_NORMAL_TRANSITION_CONTEXT
            break
        normal_count += 1
        current_index = next_index
        if normal_count >= config.anchor_stability_min_normal_transitions:
            break

    stable = normal_count >= config.anchor_stability_min_normal_transitions
    reasons: list[ReconstructionReason] = []
    if not stable:
        reasons.append(ReconstructionReason.INSUFFICIENT_NORMAL_CONTEXT)
        if blocking_reason is not None and blocking_reason not in reasons:
            reasons.append(blocking_reason)
    inspected_boundary = anchor_index if blocking_record_index is None else blocking_record_index
    inspected_start = min(anchor_index, current_index, inspected_boundary)
    inspected_end = max(anchor_index, current_index, inspected_boundary)
    return AnchorStabilityDiagnostic(
        anchor_record_index=anchor_index,
        direction=direction,
        stable=stable,
        required_normal_transition_count=config.anchor_stability_min_normal_transitions,
        consecutive_normal_transition_count=normal_count,
        inspected_start_record_index=inspected_start,
        inspected_end_record_index=inspected_end,
        blocking_record_index=blocking_record_index,
        blocking_classification=blocking_classification,
        reasons=tuple(reasons),
    )


def _mixed_region(
    activity: ActivityData,
    integrity: IntegrityReport,
    interval: CorruptedInterval,
    transitions: dict[tuple[int, int], TransitionResult],
    config: CourseReconstructionConfig,
) -> MixedGnssRegion:
    window_start = max(
        0,
        interval.start_record_index - config.mixed_region_search_max_records,
    )
    window_end = min(
        len(activity.records) - 1,
        interval.end_record_index + config.mixed_region_search_max_records,
    )
    evidence = {
        record.index
        for record in activity.records[window_start : window_end + 1]
        if not _has_position(record)
    }
    for transition in integrity.transitions:
        if (
            transition.classification
            in {
                TransitionClassification.SUSPICIOUS,
                TransitionClassification.IMPOSSIBLE,
            }
            and transition.to_record_index >= window_start
            and transition.from_record_index <= window_end
        ):
            evidence.add(transition.from_record_index)
            evidence.add(transition.to_record_index)

    start_index = interval.start_record_index
    end_index = interval.end_record_index
    maximum_gap = config.mixed_region_max_clean_gap_records + 1
    ordered_evidence = sorted(evidence)
    changed = True
    while changed:
        changed = False
        for record_index in reversed(ordered_evidence):
            if record_index >= start_index:
                continue
            if start_index - record_index <= maximum_gap:
                start_index = record_index
                changed = True
            break
        for record_index in ordered_evidence:
            if record_index <= end_index:
                continue
            if record_index - end_index <= maximum_gap:
                end_index = record_index
                changed = True
            break

    proposed_before_index = start_index - 1 if start_index > 0 else None
    proposed_after_index = end_index + 1 if end_index + 1 < len(activity.records) else None
    outer_before = (
        _anchor_stability(
            activity,
            transitions,
            proposed_before_index,
            AnchorDirection.BEFORE,
            config,
        )
        if proposed_before_index is not None
        else None
    )
    outer_after = (
        _anchor_stability(
            activity,
            transitions,
            proposed_after_index,
            AnchorDirection.AFTER,
            config,
        )
        if proposed_after_index is not None
        else None
    )
    bridge_elapsed, bridge_distance, bridge_speed, bridge_plausible = _outer_bridge(
        activity,
        proposed_before_index,
        proposed_after_index,
        interval.bridge.maximum_plausible_speed_mps,
    )
    region_transitions = tuple(
        transition
        for transition in integrity.transitions
        if transition.from_record_index >= start_index and transition.to_record_index <= end_index
    )
    missing_count = sum(
        not _has_position(record) for record in activity.records[start_index : end_index + 1]
    )
    suspicious_count = sum(
        transition.classification is TransitionClassification.SUSPICIOUS
        for transition in region_transitions
    )
    impossible_count = sum(
        transition.classification is TransitionClassification.IMPOSSIBLE
        for transition in region_transitions
    )
    outer_stable = (
        outer_before is not None
        and outer_after is not None
        and outer_before.stable
        and outer_after.stable
    )
    reasons = [ReconstructionReason.MIXED_GNSS_REGION]
    if suspicious_count or impossible_count:
        reasons.append(ReconstructionReason.CLUSTERED_ABNORMAL_TRANSITIONS)
    if missing_count:
        reasons.append(ReconstructionReason.MISSING_GNSS_EVIDENCE)
    if outer_stable:
        reasons.append(ReconstructionReason.STABLE_OUTER_ANCHORS)
    if bridge_plausible:
        reasons.append(ReconstructionReason.PLAUSIBLE_OUTER_BRIDGE)
    reasons.append(ReconstructionReason.MIXED_REGION_REQUIRES_REVIEW)
    confidence = (
        IntegrityConfidence.MEDIUM if outer_stable and bridge_plausible else IntegrityConfidence.LOW
    )
    return MixedGnssRegion(
        start_record_index=start_index,
        end_record_index=end_index,
        record_count=end_index - start_index + 1,
        proposed_trusted_before_record_index=proposed_before_index,
        proposed_trusted_after_record_index=proposed_after_index,
        missing_position_record_count=missing_count,
        suspicious_transition_count=suspicious_count,
        impossible_transition_count=impossible_count,
        outer_anchor_before=outer_before,
        outer_anchor_after=outer_after,
        bridge_elapsed_seconds=bridge_elapsed,
        bridge_distance_m=bridge_distance,
        bridge_speed_mps=bridge_speed,
        bridge_speed_limit_mps=interval.bridge.maximum_plausible_speed_mps,
        bridge_plausible=bridge_plausible,
        confidence=confidence,
        repair_eligible=False,
        reasons=tuple(reasons),
    )


def _outer_bridge(
    activity: ActivityData,
    before_index: int | None,
    after_index: int | None,
    speed_limit_mps: float,
) -> tuple[float | None, float | None, float | None, bool]:
    if before_index is None or after_index is None:
        return None, None, None, False
    before = activity.records[before_index]
    after = activity.records[after_index]
    if (
        before.continuity_id != after.continuity_id
        or not _has_position(before)
        or not _has_position(after)
        or before.timestamp is None
        or after.timestamp is None
        or before.latitude is None
        or before.longitude is None
        or after.latitude is None
        or after.longitude is None
    ):
        return None, None, None, False
    elapsed_seconds = (after.timestamp - before.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return None, None, None, False
    distance_m = geodesic_distance_m(
        before.latitude,
        before.longitude,
        after.latitude,
        after.longitude,
    )
    speed_mps = distance_m / elapsed_seconds
    return elapsed_seconds, distance_m, speed_mps, speed_mps <= speed_limit_mps


def _has_position(record: ActivityRecord) -> bool:
    return record.latitude is not None and record.longitude is not None
