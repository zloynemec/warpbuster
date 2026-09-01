"""Course-independent altitude plausibility diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from warpbuster.config import IntegrityConfig
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import (
    IntegrityConfidence,
    VerticalScanDiagnostics,
    VerticalWarning,
    VerticalWarningReason,
)


@dataclass(frozen=True, slots=True)
class VerticalDetectionResult:
    """Retained warnings and aggregate scan diagnostics."""

    warnings: tuple[VerticalWarning, ...]
    diagnostics: VerticalScanDiagnostics


@dataclass(frozen=True, slots=True)
class _Measurement:
    from_index: int
    to_index: int
    elapsed_seconds: float
    altitude_delta_m: float
    vertical_speed_mps: float


def detect_vertical_warnings(
    activity: ActivityData,
    config: IntegrityConfig,
) -> VerticalDetectionResult:
    """Find extreme altitude rates without claiming the coordinates are corrupted."""
    threshold = config.vertical_warning_speed_mps
    if threshold is None:
        return VerticalDetectionResult(
            warnings=(),
            diagnostics=VerticalScanDiagnostics(False, 0, 0, 0, 0),
        )
    measurements = tuple(
        measurement
        for index in range(len(activity.records) - 1)
        if (measurement := _measure(activity, index)) is not None
    )
    sustained_runs: list[tuple[_Measurement, ...]] = []
    current: list[_Measurement] = []
    for measurement in measurements:
        qualifies = (
            abs(measurement.vertical_speed_mps) >= threshold
            and abs(measurement.altitude_delta_m) >= config.vertical_warning_min_delta_m
        )
        adjacent = not current or current[-1].to_index == measurement.from_index
        same_direction = not current or (
            current[-1].altitude_delta_m * measurement.altitude_delta_m > 0
        )
        if qualifies and adjacent and same_direction:
            current.append(measurement)
            continue
        _finish_run(current, sustained_runs, config)
        current = [measurement] if qualifies else []
    _finish_run(current, sustained_runs, config)

    covered = {
        (measurement.from_index, measurement.to_index)
        for run in sustained_runs
        for measurement in run
    }
    warning_specs: list[tuple[tuple[_Measurement, ...], VerticalWarningReason]] = [
        (run, VerticalWarningReason.SUSTAINED_VERTICAL_RATE) for run in sustained_runs
    ]
    warning_specs.extend(
        ((measurement,), VerticalWarningReason.SINGLE_EXTREME_VERTICAL_RATE)
        for measurement in measurements
        if (measurement.from_index, measurement.to_index) not in covered
        and abs(measurement.vertical_speed_mps)
        >= config.vertical_warning_single_transition_speed_mps
        and abs(measurement.altitude_delta_m) >= config.vertical_warning_min_delta_m
    )
    warning_specs.sort(key=lambda item: item[0][0].from_index)
    total = len(warning_specs)
    retained = tuple(
        _warning(activity, run, reason)
        for run, reason in warning_specs[: config.vertical_warning_max_count]
    )
    return VerticalDetectionResult(
        warnings=retained,
        diagnostics=VerticalScanDiagnostics(
            enabled=True,
            measured_transition_count=len(measurements),
            warning_count=total,
            retained_warning_count=len(retained),
            warnings_truncated_count=total - len(retained),
        ),
    )


def _measure(activity: ActivityData, index: int) -> _Measurement | None:
    before = activity.records[index]
    after = activity.records[index + 1]
    if (
        before.continuity_id != after.continuity_id
        or before.timestamp is None
        or after.timestamp is None
        or before.altitude is None
        or after.altitude is None
    ):
        return None
    elapsed = (after.timestamp - before.timestamp).total_seconds()
    if elapsed <= 0:
        return None
    delta = after.altitude - before.altitude
    return _Measurement(index, index + 1, elapsed, delta, delta / elapsed)


def _finish_run(
    current: list[_Measurement],
    runs: list[tuple[_Measurement, ...]],
    config: IntegrityConfig,
) -> None:
    if len(current) >= config.vertical_warning_min_consecutive_transitions:
        runs.append(tuple(current))


def _warning(
    activity: ActivityData,
    run: tuple[_Measurement, ...],
    reason: VerticalWarningReason,
) -> VerticalWarning:
    first = run[0]
    last = run[-1]
    start = activity.records[first.from_index]
    end = activity.records[last.to_index]
    assert start.timestamp is not None and end.timestamp is not None
    assert start.altitude is not None and end.altitude is not None
    return VerticalWarning(
        start_record_index=first.from_index,
        end_record_index=last.to_index,
        start_timestamp=start.timestamp,
        end_timestamp=end.timestamp,
        transition_count=len(run),
        elapsed_seconds=(end.timestamp - start.timestamp).total_seconds(),
        altitude_delta_m=end.altitude - start.altitude,
        maximum_absolute_vertical_speed_mps=max(
            abs(measurement.vertical_speed_mps) for measurement in run
        ),
        confidence=IntegrityConfidence.MEDIUM,
        reasons=(reason,),
    )
