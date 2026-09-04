"""Course-independent coordinate evidence, masking and linear gap inventory."""

from __future__ import annotations

from dataclasses import replace
from math import isfinite

from warpbuster.integrity.tail import position_available, reachability_excess
from warpbuster.models.activity import ActivityData, ActivityRecord, FitPreservationData
from warpbuster.models.integrity import (
    CorruptedInterval,
    IntegrityConfidence,
    IntegrityReport,
    IntervalDetectionKind,
    TransitionClassification,
)
from warpbuster.models.reconstruction import (
    CoordinateDisposition,
    CoordinateState,
    GapOrigin,
    MissingCourseRunKind,
    ReconstructionGap,
    ReconstructionReason,
)

CONFIDENCE_RANK = {
    IntegrityConfidence.LOW: 0,
    IntegrityConfidence.MEDIUM: 1,
    IntegrityConfidence.HIGH: 2,
}


def has_position(record: ActivityRecord) -> bool:
    return (
        record.latitude is not None
        and record.longitude is not None
        and isfinite(record.latitude)
        and isfinite(record.longitude)
        and -90 <= record.latitude <= 90
        and -180 <= record.longitude <= 180
    )


def coordinate_mask(
    activity: ActivityData,
    integrity: IntegrityReport,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> tuple[CoordinateDisposition, ...]:
    """Trust only a detector-established scope, never a suspicious edge or course."""
    if minimum_confidence is IntegrityConfidence.LOW:
        raise ValueError("minimum invalidation confidence must be high or medium")
    # Detector intervals are disjoint in normal input. Index their evidence once, not
    # once per gap or context search. Sorting also makes synthetic overlapping proof stable.
    covered: dict[int, list[CorruptedInterval]] = {}
    normal_pairs = {
        (t.from_record_index, t.to_record_index)
        for t in integrity.transitions
        if t.classification is TransitionClassification.NORMAL
    }
    proven_ids = {
        id(interval)
        for interval in integrity.corrupted_intervals
        if _has_scope_proof(interval, activity, integrity, normal_pairs)
    }
    uncertain_anchors: set[int] = set()
    for interval in integrity.corrupted_intervals:
        proof = interval.reachability
        if (
            id(interval) in proven_ids
            and proof is not None
            and proof.uncertain_start_record_index is not None
            and proof.uncertain_end_record_index is not None
        ):
            uncertain_anchors.update(
                range(proof.uncertain_start_record_index, proof.uncertain_end_record_index + 1)
            )
    for interval in sorted(
        integrity.corrupted_intervals,
        key=lambda item: (item.start_record_index, item.end_record_index),
    ):
        for index in range(
            max(0, interval.start_record_index),
            min(len(activity.records), interval.end_record_index + 1),
        ):
            covered.setdefault(index, []).append(interval)
    result = []
    for record in activity.records:
        evidence = covered.get(record.index, [])
        proven = [interval for interval in evidence if id(interval) in proven_ids]
        confidence = max(
            (interval.confidence for interval in proven),
            key=CONFIDENCE_RANK.__getitem__,
            default=IntegrityConfidence.LOW,
        )
        selected = (
            bool(proven) and CONFIDENCE_RANK[confidence] >= CONFIDENCE_RANK[minimum_confidence]
        )
        reason: tuple[ReconstructionReason, ...] = ()
        if evidence and not proven:
            reason = (ReconstructionReason.INSUFFICIENT_CORRUPTION_PROOF,)
        elif proven and not selected:
            reason = (ReconstructionReason.INVALIDATION_BELOW_THRESHOLD,)
        elif selected:
            reason = (ReconstructionReason.INDEPENDENT_CORRUPTION_PROOF,)
        state = (
            CoordinateState.ORIGINAL_MISSING
            if not has_position(record)
            else CoordinateState.INVALIDATED
            if selected
            else CoordinateState.PRESERVED
        )
        result.append(
            CoordinateDisposition(
                record_index=record.index,
                state=state,
                original_latitude=record.latitude,
                original_longitude=record.longitude,
                anchor_eligible=(
                    state is CoordinateState.PRESERVED
                    and not evidence
                    and record.index not in uncertain_anchors
                ),
                confidence=confidence,
                proof_ranges=tuple(
                    (interval.start_record_index, interval.end_record_index)
                    for interval in evidence
                ),
                proof_reasons=tuple(
                    sorted({reason.value for i in evidence for reason in i.reasons})
                ),
                reasons=reason,
            )
        )
    return tuple(result)


def _has_scope_proof(
    interval: CorruptedInterval,
    activity: ActivityData,
    integrity: IntegrityReport,
    normal_pairs: set[tuple[int, int]],
) -> bool:
    """Diagnostic/composite envelopes alone are not coordinate invalidation proof."""
    if (
        interval.confidence is IntegrityConfidence.LOW
        or interval.entry_transition.classification is not TransitionClassification.IMPOSSIBLE
    ):
        return False
    if interval.detection_kind is IntervalDetectionKind.UNREACHABLE_TAIL:
        return _has_tail_scope_proof(interval, activity, integrity, normal_pairs)
    if (
        interval.bridge is None
        or interval.bridge.elapsed_seconds <= 0
        or interval.bridge.apparent_speed_mps > interval.bridge.maximum_plausible_speed_mps
    ):
        return False
    if interval.detection_kind is IntervalDetectionKind.CLASSIC_ISLAND:
        return (
            interval.exit_transition is not None
            and interval.exit_transition.classification is TransitionClassification.IMPOSSIBLE
        )
    # The detector's one-sided proof requires the stable outer context, missing
    # termination and plausible bridge, not merely a TAINTED diagnostic component.
    if interval.detection_kind is IntervalDetectionKind.ONE_SIDED_CLUSTER:
        reasons = {reason.value for reason in interval.reasons}
        return {"stable_outer_anchors", "missing_exit_boundary", "plausible_bridge"} <= reasons
    return False


def _has_tail_scope_proof(
    interval: CorruptedInterval,
    activity: ActivityData,
    integrity: IntegrityReport,
    normal_pairs: set[tuple[int, int]],
) -> bool:
    """Recheck each positioned record once; an envelope/reason alone proves nothing."""
    proof = interval.reachability
    config = integrity.config
    anchor_index = interval.trusted_before_record_index
    if (
        proof is None
        or interval.bridge is not None
        or interval.trusted_after_record_index is not None
        or not 0 <= anchor_index < interval.start_record_index
        or not interval.start_record_index <= interval.end_record_index < len(activity.records)
        or proof.speed_limit_mps != config.absolute_impossible_speed_mps
        or proof.position_error_budget_m != config.tail_position_error_budget_m
        or proof.anchor_normal_transition_count < config.tail_anchor_min_normal_transitions
        or proof.anchor_context_start_record_index
        != anchor_index - proof.anchor_normal_transition_count
        or proof.anchor_context_start_record_index < 0
        or interval.entry_transition.from_record_index != anchor_index
        or interval.entry_transition.to_record_index != interval.start_record_index
    ):
        return False
    if any(
        (index, index + 1) not in normal_pairs
        for index in range(proof.anchor_context_start_record_index, anchor_index)
    ):
        return False
    anchor = activity.records[anchor_index]
    previous = anchor
    count = 0
    for record in activity.records[anchor_index + 1 : interval.end_record_index + 1]:
        if (
            record.continuity_id != anchor.continuity_id
            or record.timestamp is None
            or previous.timestamp is None
            or record.timestamp <= previous.timestamp
        ):
            return False
        previous = record
        if not position_available(record):
            continue
        if record.index < interval.start_record_index:
            return False
        excess = reachability_excess(
            anchor, record, proof.speed_limit_mps, proof.position_error_budget_m
        )
        if excess is None or excess <= 0:
            return False
        count += 1
    return count > 0 and count == proof.positioned_record_count


def masked_activity(
    activity: ActivityData, mask: tuple[CoordinateDisposition, ...]
) -> ActivityData:
    """Make a planning view without mutating raw FIT or telemetry."""
    return replace(
        activity,
        records=tuple(
            record
            if item.state is CoordinateState.PRESERVED
            else replace(record, latitude=None, longitude=None)
            for record, item in zip(activity.records, mask, strict=True)
        ),
    )


def inventory_gaps(
    activity: ActivityData, mask: tuple[CoordinateDisposition, ...]
) -> tuple[ReconstructionGap, ...]:
    """Inventory every hole; preserved observations and continuity breaks split it."""
    gaps = []
    index = 0
    records = activity.records
    while index < len(records):
        if mask[index].state is CoordinateState.PRESERVED:
            index += 1
            continue
        start = index
        continuity = records[index].continuity_id
        while (
            index + 1 < len(records)
            and records[index + 1].continuity_id == continuity
            and mask[index + 1].state is not CoordinateState.PRESERVED
        ):
            index += 1
        end = index
        before = start - 1 if start > 0 and records[start - 1].continuity_id == continuity else None
        after = (
            end + 1
            if end + 1 < len(records) and records[end + 1].continuity_id == continuity
            else None
        )
        original_missing = sum(
            item.state is CoordinateState.ORIGINAL_MISSING for item in mask[start : end + 1]
        )
        invalidated = end - start + 1 - original_missing
        confidence = min(
            (
                item.confidence
                for item in mask[start : end + 1]
                if item.state is CoordinateState.INVALIDATED
            ),
            key=CONFIDENCE_RANK.__getitem__,
            default=None,
        )
        kind = (
            MissingCourseRunKind.PREFIX
            if start == 0
            else MissingCourseRunKind.SUFFIX
            if end == len(records) - 1
            else MissingCourseRunKind.INTERNAL
        )
        origin = (
            GapOrigin.MIXED
            if original_missing and invalidated
            else GapOrigin.ORIGINAL_MISSING
            if original_missing
            else GapOrigin.INVALIDATED
        )
        gaps.append(
            ReconstructionGap(
                gap_id=f"gap-{start}-{end}",
                start_record_index=start,
                end_record_index=end,
                start_timestamp=records[start].timestamp,
                end_timestamp=records[end].timestamp,
                kind=kind,
                origin=origin,
                continuity_id=continuity,
                anchor_before_record_index=before,
                anchor_after_record_index=after,
                original_missing_count=original_missing,
                invalidated_count=invalidated,
                invalidation_confidence=confidence,
                reasons=(
                    (ReconstructionReason.CONTINUITY_BREAK,)
                    if (start > 0 and before is None) or (end < len(records) - 1 and after is None)
                    else ()
                ),
            )
        )
        index += 1
    return tuple(gaps)


def position_fields_patchable(activity: ActivityData, gap: ReconstructionGap) -> bool:
    """Native record coordinates can be patched or added by the scoped FIT writer.

    Unknown numeric coordinate fields are not permission to add duplicate IDs.
    The writer validates the exact binary definition before publishing any output.
    """
    preservation = activity.preservation
    if not isinstance(preservation, FitPreservationData):
        return False
    if not preservation.raw_bytes:
        return True
    return all(
        preservation.messages[record.source.message_index].message_type == "record"
        and not {0, 1} & preservation.messages[record.source.message_index].fields.keys()
        for record in activity.records[gap.start_record_index : gap.end_record_index + 1]
    )
