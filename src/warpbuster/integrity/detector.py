"""Local physical-transition integrity detector."""

from __future__ import annotations

from itertools import pairwise
from statistics import median

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    BaselineStats,
    IntegrityConfidence,
    IntegrityReport,
    IntegrityStatus,
    TransitionClassification,
    TransitionReason,
    TransitionResult,
)


def analyze_integrity(
    activity: ActivityData,
    config: IntegrityConfig | None = None,
) -> IntegrityReport:
    """Analyze physical continuity between consecutive valid GNSS observations."""
    effective_config = config or IntegrityConfig.for_sport(activity.sport)
    position_records = tuple(record for record in activity.records if _has_position(record))
    measurements = tuple(
        _measure_transition(previous, current) for previous, current in pairwise(position_records)
    )
    baseline = _baseline_stats(measurements, effective_config)
    transitions = tuple(
        _classify_transition(transition, baseline, effective_config) for transition in measurements
    )
    missing_position_record_count = len(activity.records) - len(position_records)
    status, confidence = _summarize(
        transitions,
        baseline,
        effective_config,
        missing_position_record_count,
    )
    return IntegrityReport(
        status=status,
        confidence=confidence,
        record_count=len(activity.records),
        position_record_count=len(position_records),
        missing_position_record_count=missing_position_record_count,
        baseline=baseline,
        transitions=transitions,
        config=effective_config,
    )


def _has_position(record: ActivityRecord) -> bool:
    return record.latitude is not None and record.longitude is not None


def _measure_transition(
    previous: ActivityRecord,
    current: ActivityRecord,
) -> TransitionResult:
    if previous.latitude is None or previous.longitude is None:
        raise AssertionError("previous record must contain a position")
    if current.latitude is None or current.longitude is None:
        raise AssertionError("current record must contain a position")
    distance_m = geodesic_distance_m(
        previous.latitude,
        previous.longitude,
        current.latitude,
        current.longitude,
    )
    if previous.timestamp is None or current.timestamp is None:
        return TransitionResult(
            from_record_index=previous.index,
            to_record_index=current.index,
            from_timestamp=previous.timestamp,
            to_timestamp=current.timestamp,
            elapsed_seconds=None,
            distance_m=distance_m,
            apparent_speed_mps=None,
            classification=TransitionClassification.UNKNOWN,
            reasons=(TransitionReason.MISSING_TIMESTAMP,),
        )

    elapsed_seconds = (current.timestamp - previous.timestamp).total_seconds()
    if elapsed_seconds <= 0:
        return TransitionResult(
            from_record_index=previous.index,
            to_record_index=current.index,
            from_timestamp=previous.timestamp,
            to_timestamp=current.timestamp,
            elapsed_seconds=elapsed_seconds,
            distance_m=distance_m,
            apparent_speed_mps=None,
            classification=TransitionClassification.UNKNOWN,
            reasons=(TransitionReason.NON_POSITIVE_TIME_DELTA,),
        )
    return TransitionResult(
        from_record_index=previous.index,
        to_record_index=current.index,
        from_timestamp=previous.timestamp,
        to_timestamp=current.timestamp,
        elapsed_seconds=elapsed_seconds,
        distance_m=distance_m,
        apparent_speed_mps=distance_m / elapsed_seconds,
        classification=TransitionClassification.NORMAL,
        reasons=(),
    )


def _baseline_stats(
    transitions: tuple[TransitionResult, ...],
    config: IntegrityConfig,
) -> BaselineStats:
    speeds = sorted(
        transition.apparent_speed_mps
        for transition in transitions
        if transition.apparent_speed_mps is not None
    )
    if not speeds:
        return BaselineStats(0, None, None, None, None)

    median_speed = float(median(speeds))
    deviations = [abs(speed - median_speed) for speed in speeds]
    mad_speed = float(median(deviations))
    relative_threshold = config.relative_suspicious_speed_floor_mps
    if len(speeds) >= config.minimum_baseline_samples:
        relative_threshold = max(
            config.relative_suspicious_speed_floor_mps,
            median_speed * config.relative_speed_multiplier,
            median_speed + mad_speed * config.relative_mad_multiplier,
        )
    return BaselineStats(
        sample_count=len(speeds),
        median_speed_mps=median_speed,
        percentile_95_speed_mps=_percentile(speeds, 0.95),
        median_absolute_deviation_mps=mad_speed,
        relative_suspicious_threshold_mps=relative_threshold,
    )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    weight = position - lower_index
    return sorted_values[lower_index] * (1.0 - weight) + sorted_values[upper_index] * weight


def _classify_transition(
    transition: TransitionResult,
    baseline: BaselineStats,
    config: IntegrityConfig,
) -> TransitionResult:
    speed = transition.apparent_speed_mps
    if speed is None:
        return transition
    if (
        config.absolute_impossible_speed_mps is not None
        and speed >= config.absolute_impossible_speed_mps
        and transition.distance_m >= config.absolute_impossible_distance_m
    ):
        return _with_classification(
            transition,
            TransitionClassification.IMPOSSIBLE,
            TransitionReason.ABSOLUTE_SPEED_AND_DISTANCE_EXCEEDED,
        )
    relative_threshold = baseline.relative_suspicious_threshold_mps
    if (
        relative_threshold is not None
        and speed >= relative_threshold
        and transition.distance_m >= config.relative_suspicious_distance_m
    ):
        return _with_classification(
            transition,
            TransitionClassification.SUSPICIOUS,
            TransitionReason.RELATIVE_SPEED_OUTLIER,
        )
    return transition


def _with_classification(
    transition: TransitionResult,
    classification: TransitionClassification,
    reason: TransitionReason,
) -> TransitionResult:
    return TransitionResult(
        from_record_index=transition.from_record_index,
        to_record_index=transition.to_record_index,
        from_timestamp=transition.from_timestamp,
        to_timestamp=transition.to_timestamp,
        elapsed_seconds=transition.elapsed_seconds,
        distance_m=transition.distance_m,
        apparent_speed_mps=transition.apparent_speed_mps,
        classification=classification,
        reasons=(reason,),
    )


def _summarize(
    transitions: tuple[TransitionResult, ...],
    baseline: BaselineStats,
    config: IntegrityConfig,
    missing_position_record_count: int,
) -> tuple[IntegrityStatus, IntegrityConfidence]:
    classifications = {transition.classification for transition in transitions}
    if TransitionClassification.IMPOSSIBLE in classifications:
        return IntegrityStatus.CORRUPTED, IntegrityConfidence.HIGH
    if TransitionClassification.SUSPICIOUS in classifications:
        return IntegrityStatus.SUSPICIOUS, IntegrityConfidence.LOW
    if TransitionClassification.UNKNOWN in classifications or missing_position_record_count > 0:
        return IntegrityStatus.UNKNOWN, IntegrityConfidence.LOW
    if TransitionClassification.NORMAL in classifications:
        confidence = (
            IntegrityConfidence.HIGH
            if baseline.sample_count >= config.minimum_baseline_samples
            else IntegrityConfidence.MEDIUM
        )
        return IntegrityStatus.CLEAN, confidence
    return IntegrityStatus.UNKNOWN, IntegrityConfidence.LOW
