"""Linear, course-independent proof of unreachable observations after a stable run.

Existing two-sided proofs take precedence. A fixed prefix anchor is never advanced
into the suspect tail, even if its internal transitions look normal. Reachability
ends the proof; a consecutive reachable NORMAL run is required to restore anchors.
"""

from __future__ import annotations

from math import isfinite

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    CorruptedInterval,
    IntegrityConfidence,
    IntervalDetectionKind,
    IntervalReason,
    TailReachabilityProof,
    TransitionClassification,
    TransitionResult,
)


def position_available(record: ActivityRecord) -> bool:
    return (
        record.latitude is not None
        and record.longitude is not None
        and isfinite(record.latitude)
        and isfinite(record.longitude)
        and -90 <= record.latitude <= 90
        and -180 <= record.longitude <= 180
    )


def reachability_excess(
    anchor: ActivityRecord, record: ActivityRecord, speed_limit: float, error_budget: float
) -> float | None:
    """Distance beyond the generous reachable radius, or unavailable evidence."""
    if (
        not position_available(anchor)
        or not position_available(record)
        or anchor.timestamp is None
        or record.timestamp is None
        or anchor.continuity_id != record.continuity_id
    ):
        return None
    elapsed = (record.timestamp - anchor.timestamp).total_seconds()
    if elapsed <= 0:
        return None
    assert anchor.latitude is not None and anchor.longitude is not None
    assert record.latitude is not None and record.longitude is not None
    distance = geodesic_distance_m(
        anchor.latitude, anchor.longitude, record.latitude, record.longitude
    )
    return distance - speed_limit * elapsed - error_budget


def detect_unreachable_tails(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
    config: IntegrityConfig,
    existing: tuple[CorruptedInterval, ...],
) -> tuple[CorruptedInterval, ...]:
    """Scan disjoint continuity blocks once; never launch FIT-by-FIT reachability."""
    speed_limit = config.absolute_impossible_speed_mps
    if speed_limit is None or not isfinite(speed_limit) or speed_limit <= 0:
        return ()
    records = activity.records
    covered = {
        index
        for interval in existing
        for index in range(interval.start_record_index, interval.end_record_index + 1)
    }
    adjacent = {
        t.to_record_index: t for t in transitions if t.to_record_index == t.from_record_index + 1
    }
    normal_counts: list[int] = []
    for record in records:
        transition = adjacent.get(record.index)
        normal_counts.append(
            normal_counts[-1] + 1
            if normal_counts
            and transition is not None
            and transition.classification is TransitionClassification.NORMAL
            and record.index not in covered
            and record.index - 1 not in covered
            else 0
        )
    # O(n) block endpoint index, also used to quarantine ambiguous remainders.
    block_ends = [0] * len(records)
    for index in range(len(records) - 1, -1, -1):
        block_ends[index] = (
            block_ends[index + 1]
            if index + 1 < len(records)
            and records[index].continuity_id == records[index + 1].continuity_id
            else index
        )
    intervals = []
    consumed_through = -1
    required = config.tail_anchor_min_normal_transitions
    for entry in transitions:
        anchor_index = entry.from_record_index
        start = entry.to_record_index
        if (
            entry.classification is not TransitionClassification.IMPOSSIBLE
            or anchor_index <= consumed_through
            or anchor_index in covered
            or start in covered
        ):
            continue
        anchor = records[anchor_index]
        block_end = block_ends[anchor_index]
        if start > block_end:
            continue
        # A weak entry still quarantines its smooth displaced component. It
        # cannot prove corruption, but must not become a reference for deleting
        # an actual return later. Only a reachable stable run releases quarantine.
        proof_open = normal_counts[anchor_index] >= required
        minimum_excess = float("inf")
        positioned_count = 0
        end = start - 1
        stop_reason = "end_of_recording" if block_end == len(records) - 1 else "continuity_boundary"
        uncertain_start = None
        uncertain_end = None
        recovery_index = None
        reachable_normal_count = 0
        previous_reachable = False
        consumed_through = block_end
        previous = anchor
        for index in range(anchor_index + 1, block_end + 1):
            record = records[index]
            if (
                record.timestamp is None
                or previous.timestamp is None
                or record.timestamp <= previous.timestamp
            ):
                stop_reason = "untrustworthy_time"
                uncertain_start = uncertain_start if uncertain_start is not None else index
                uncertain_end = block_end
                break
            previous = record
            if index in covered:
                stop_reason = "existing_proof_boundary"
                consumed_through = index
                break
            if not position_available(record):
                reachable_normal_count = 0
                previous_reachable = False
                continue
            excess = reachability_excess(
                anchor, record, speed_limit, config.tail_position_error_budget_m
            )
            if excess is None or excess <= 0:
                if proof_open:
                    stop_reason = "reachable_position_not_confirmed"
                    uncertain_start = index
                    uncertain_end = block_end
                proof_open = False
                transition = adjacent.get(index)
                reachable_normal_count = (
                    reachable_normal_count + 1
                    if previous_reachable
                    and transition is not None
                    and transition.classification is TransitionClassification.NORMAL
                    else 0
                )
                previous_reachable = excess is not None
                if previous_reachable and reachable_normal_count >= required:
                    recovery_index = index
                    consumed_through = index - 1
                    if uncertain_start is not None:
                        uncertain_end = index - 1
                    break
            else:
                reachable_normal_count = 0
                previous_reachable = False
                if proof_open:
                    positioned_count += 1
                    minimum_excess = min(minimum_excess, excess)
                    end = index
        if not positioned_count:
            continue
        intervals.append(
            CorruptedInterval(
                start_record_index=start,
                end_record_index=end,
                start_timestamp=records[start].timestamp,
                end_timestamp=records[end].timestamp,
                trusted_before_record_index=anchor_index,
                trusted_after_record_index=None,
                entry_transition=entry,
                exit_transition=None,
                bridge=None,
                confidence=IntegrityConfidence.MEDIUM,
                reasons=(
                    IntervalReason.IMPOSSIBLE_TRANSITION_IN,
                    IntervalReason.STABLE_PREFIX_ANCHOR,
                    IntervalReason.OUTSIDE_PHYSICAL_REACHABILITY,
                ),
                detection_kind=IntervalDetectionKind.UNREACHABLE_TAIL,
                reachability=TailReachabilityProof(
                    anchor_context_start_record_index=anchor_index - required,
                    anchor_normal_transition_count=required,
                    speed_limit_mps=speed_limit,
                    position_error_budget_m=config.tail_position_error_budget_m,
                    positioned_record_count=positioned_count,
                    minimum_excess_distance_m=minimum_excess,
                    stop_reason=stop_reason,
                    uncertain_start_record_index=uncertain_start,
                    uncertain_end_record_index=uncertain_end,
                    recovered_anchor_record_index=recovery_index,
                ),
            )
        )
    return tuple(intervals)
