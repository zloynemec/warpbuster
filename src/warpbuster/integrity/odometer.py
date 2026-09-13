"""Corroborated GNSS-island and impossible distance-step evidence."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    CorruptedInterval,
    DistanceSpikeEvidence,
    IntegrityConfidence,
    IntervalDetectionKind,
    IntervalReason,
    TransitionClassification,
    TransitionResult,
)


@dataclass(frozen=True, slots=True)
class OdometerDetectionResult:
    """Bounded signal evidence and coordinate islands proven by that evidence."""

    evidence: tuple[DistanceSpikeEvidence, ...]
    intervals: tuple[CorruptedInterval, ...]


def detect_correlated_distance_spikes(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
    config: IntegrityConfig,
    existing: tuple[CorruptedInterval, ...],
) -> OdometerDetectionResult:
    """Find impossible one-step odometer jumps corroborated by speed and GNSS.

    Missing-position spans are assessed only when distance accumulated before the
    final step already agrees with a complete speed integral. This distinguishes a
    corrupt GPS-derived jump from a delayed but otherwise legitimate odometer update.
    """
    if config.absolute_impossible_speed_mps is None or config.distance_spike_max_evidence == 0:
        return OdometerDetectionResult((), ())
    transition_by_pair = {
        (item.from_record_index, item.to_record_index): item for item in transitions
    }
    evidence: list[DistanceSpikeEvidence] = []
    for current in activity.records[1:]:
        previous = activity.records[current.index - 1]
        proof = _evidence_for_edge(activity, previous, current, transition_by_pair, config)
        if proof is not None:
            evidence.append(proof)
            if len(evidence) >= config.distance_spike_max_evidence:
                break

    intervals: list[CorruptedInterval] = []
    occupied = {
        index
        for interval in existing
        for index in range(interval.start_record_index, interval.end_record_index + 1)
    }
    for proof in evidence:
        start = proof.record_index
        if start == 0 or _has_position(activity.records[start - 1]):
            continue
        end = start
        while end + 1 < len(activity.records) and _has_position(activity.records[end + 1]):
            if activity.records[end + 1].continuity_id != activity.records[start].continuity_id:
                break
            end += 1
            if end - start + 1 > config.distance_spike_max_position_island_records:
                break
        if (
            end - start + 1 > config.distance_spike_max_position_island_records
            or end + 1 >= len(activity.records)
            or _has_position(activity.records[end + 1])
            or any(index in occupied for index in range(start, end + 1))
        ):
            continue
        entry = transition_by_pair.get((proof.anchor_record_index, proof.record_index))
        if entry is None:
            continue
        after = _next_position(activity, end + 1, config.distance_spike_max_gap_records)
        intervals.append(
            CorruptedInterval(
                start_record_index=start,
                end_record_index=end,
                start_timestamp=activity.records[start].timestamp,
                end_timestamp=activity.records[end].timestamp,
                trusted_before_record_index=proof.anchor_record_index,
                trusted_after_record_index=after,
                entry_transition=entry,
                exit_transition=None,
                bridge=None,
                confidence=IntegrityConfidence.MEDIUM,
                reasons=(
                    IntervalReason.IMPOSSIBLE_DISTANCE_SPIKE,
                    IntervalReason.SPEED_DISTANCE_CONSISTENCY,
                    IntervalReason.POSITION_DISPLACEMENT_CONFLICT,
                    IntervalReason.SHORT_POSITION_ISLAND,
                ),
                detection_kind=IntervalDetectionKind.SIGNAL_CORROBORATED_ISLAND,
                distance_spike_proof=proof,
            )
        )
        occupied.update(range(start, end + 1))
    return OdometerDetectionResult(tuple(evidence), tuple(intervals))


def validate_distance_spike_evidence(activity: ActivityData, proof: DistanceSpikeEvidence) -> bool:
    """Recompute a proof from its threshold snapshot before applying any edit."""
    if (
        not 0 < proof.record_index < len(activity.records)
        or proof.previous_record_index != proof.record_index - 1
        or not 0 <= proof.anchor_record_index < proof.record_index
    ):
        return False
    previous = activity.records[proof.previous_record_index]
    current = activity.records[proof.record_index]
    if previous.index + 1 != current.index:
        return False
    try:
        config = IntegrityConfig(
            absolute_impossible_speed_mps=proof.maximum_increment_speed_mps,
            bridge_max_speed_mps=None,
            distance_spike_min_increment_m=proof.minimum_increment_m,
            distance_spike_signal_absolute_tolerance_m=proof.signal_absolute_tolerance_m,
            distance_spike_signal_relative_tolerance=proof.signal_relative_tolerance,
            distance_spike_position_excess_m=proof.position_excess_m,
            distance_spike_position_ratio=proof.position_ratio,
            distance_spike_max_gap_records=max(1, current.index - proof.anchor_record_index),
        )
    except ValueError:
        return False
    candidate = _evidence_for_edge(activity, previous, current, {}, config)
    return candidate == proof


def _evidence_for_edge(
    activity: ActivityData,
    previous: ActivityRecord,
    current: ActivityRecord,
    transitions: dict[tuple[int, int], TransitionResult],
    config: IntegrityConfig,
) -> DistanceSpikeEvidence | None:
    ceiling = config.absolute_impossible_speed_mps
    if (
        ceiling is None
        or current.continuity_id != previous.continuity_id
        or previous.timestamp is None
        or current.timestamp is None
        or previous.distance is None
        or current.distance is None
        or previous.speed is None
        or current.speed is None
    ):
        return None
    delta_seconds = (current.timestamp - previous.timestamp).total_seconds()
    increment = current.distance - previous.distance
    if (
        delta_seconds <= 0
        or increment < config.distance_spike_min_increment_m
        or increment / delta_seconds <= ceiling
        or not _valid_speed(previous.speed, ceiling)
        or not _valid_speed(current.speed, ceiling)
        or not _has_position(current)
    ):
        return None
    replacement = (previous.speed + current.speed) * delta_seconds / 2

    if _has_position(previous):
        transition = transitions.get((previous.index, current.index))
        if transitions and (
            transition is None
            or transition.classification is not TransitionClassification.IMPOSSIBLE
        ):
            return None
        anchor = previous
        source_before = integrated_before = 0.0
        displacement = _distance(anchor, current)
        integrated_total = replacement
    else:
        found_anchor = _previous_position(
            activity, previous.index, config.distance_spike_max_gap_records
        )
        if found_anchor is None or found_anchor.distance is None or found_anchor.timestamp is None:
            return None
        anchor = found_anchor
        records = activity.records[anchor.index : current.index + 1]
        if not _complete_signal(records, ceiling):
            return None
        assert previous.distance is not None and anchor.distance is not None
        source_before = previous.distance - anchor.distance
        if source_before < 0:
            return None
        integrated_before = _integrated_speed(records[:-1])
        tolerance = max(
            config.distance_spike_signal_absolute_tolerance_m,
            max(source_before, integrated_before) * config.distance_spike_signal_relative_tolerance,
        )
        if abs(source_before - integrated_before) > tolerance:
            return None
        integrated_total = integrated_before + replacement
        displacement = _distance(anchor, current)

    if (
        displacement - integrated_total < config.distance_spike_position_excess_m
        or displacement < integrated_total * config.distance_spike_position_ratio
    ):
        return None
    assert current.timestamp is not None and anchor.timestamp is not None
    elapsed = (current.timestamp - anchor.timestamp).total_seconds()
    if elapsed <= 0:
        return None
    return DistanceSpikeEvidence(
        previous_record_index=previous.index,
        record_index=current.index,
        anchor_record_index=anchor.index,
        original_increment_m=increment,
        replacement_increment_m=replacement,
        source_before_increment_m=source_before,
        integrated_speed_before_m=integrated_before,
        position_displacement_m=displacement,
        integrated_speed_to_record_m=integrated_total,
        elapsed_from_anchor_seconds=elapsed,
        minimum_increment_m=config.distance_spike_min_increment_m,
        maximum_increment_speed_mps=ceiling,
        signal_absolute_tolerance_m=config.distance_spike_signal_absolute_tolerance_m,
        signal_relative_tolerance=config.distance_spike_signal_relative_tolerance,
        position_excess_m=config.distance_spike_position_excess_m,
        position_ratio=config.distance_spike_position_ratio,
    )


def _complete_signal(records: tuple[ActivityRecord, ...], ceiling: float) -> bool:
    if len(records) < 2:
        return False
    for record in records:
        if (
            record.timestamp is None
            or record.distance is None
            or record.speed is None
            or not _valid_speed(record.speed, ceiling)
        ):
            return False
    for previous, current in pairwise(records):
        assert previous.timestamp is not None and current.timestamp is not None
        assert previous.distance is not None and current.distance is not None
        if current.timestamp <= previous.timestamp or current.distance < previous.distance:
            return False
    return True


def _integrated_speed(records: tuple[ActivityRecord, ...]) -> float:
    total = 0.0
    for previous, current in pairwise(records):
        assert previous.timestamp is not None and current.timestamp is not None
        assert previous.speed is not None and current.speed is not None
        total += (
            (previous.speed + current.speed)
            / 2
            * (current.timestamp - previous.timestamp).total_seconds()
        )
    return total


def _valid_speed(value: float, ceiling: float) -> bool:
    return isfinite(value) and 0 <= value <= ceiling


def _distance(left: ActivityRecord, right: ActivityRecord) -> float:
    assert left.latitude is not None and left.longitude is not None
    assert right.latitude is not None and right.longitude is not None
    return geodesic_distance_m(left.latitude, left.longitude, right.latitude, right.longitude)


def _has_position(record: ActivityRecord) -> bool:
    return (
        record.latitude is not None
        and record.longitude is not None
        and isfinite(record.latitude)
        and isfinite(record.longitude)
    )


def _previous_position(activity: ActivityData, start: int, limit: int) -> ActivityRecord | None:
    continuity = activity.records[start].continuity_id
    for index in range(start, max(-1, start - limit - 1), -1):
        record = activity.records[index]
        if record.continuity_id != continuity:
            return None
        if _has_position(record):
            return record
    return None


def _next_position(activity: ActivityData, start: int, limit: int) -> int | None:
    if start >= len(activity.records):
        return None
    continuity = activity.records[start].continuity_id
    for index in range(start, min(len(activity.records), start + limit)):
        record = activity.records[index]
        if record.continuity_id != continuity:
            return None
        if _has_position(record):
            return index
    return None
