"""Course-independent qualification of recorded movement signals, not GPS proof.

A plausible signal is not claimed to be independent of GNSS. Its disagreement
with a proposed course path does not, by itself, authorize rewriting that field.
"""

from dataclasses import dataclass
from itertools import pairwise
from math import isfinite

from warpbuster.config import IntegrityConfig
from warpbuster.models.activity import ActivityRecord
from warpbuster.models.reconstruction import AllocationMethod
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction.timing import AllocationClock


def allocate_signal_progress(
    records: tuple[ActivityRecord, ...],
    length_m: float,
    config: IntegrityConfig,
    clock: AllocationClock,
    *,
    error_budget_m: float,
    maximum_speed_mps: float,
) -> tuple[AllocationMethod, tuple[float, ...], tuple[str, ...]] | Reason:
    """Choose a usable progress profile; unverified path mismatch is advisory.

    The path is a reconstruction hypothesis. Source speed/distance are not assumed
    independent of GNSS. Unusable profiles fall back to uniform active time without
    changing timestamps, pausing semantics, or the physical traversal limit.
    """
    if clock.audit.open_pause:
        return Reason.TIMER_STATE_UNRESOLVED
    if clock.audit.active_seconds <= 0:
        return Reason.NO_ACTIVE_TIME
    if not isfinite(length_m) or length_m <= 0:
        return Reason.COURSE_TRAVERSAL_IMPLAUSIBLE
    if length_m / clock.audit.active_seconds > maximum_speed_mps:
        return Reason.ACTIVE_TIME_TRAVERSAL_IMPLAUSIBLE
    signals = (
        ("distance", AllocationMethod.RECORDED_DISTANCE, qualify_distance(records, config)),
        (
            "speed",
            AllocationMethod.RECORDED_SPEED,
            qualify_speed(records, config, active_deltas=clock.active_deltas),
        ),
    )
    diagnostics = ["timer_pauses_excluded"] if clock.audit.paused_seconds else []
    usable = []
    for label, method, signal in signals:
        if signal.status not in {"plausible", "zero"}:
            diagnostics.append(f"{label}_{signal.status}")
            continue
        if any(
            active == 0 and b > a
            for (a, b), active in zip(pairwise(signal.cumulative), clock.active_deltas, strict=True)
        ):
            return Reason.PAUSE_DISTANCE_CONFLICT
        total = signal.cumulative[-1]
        if total <= 0:
            diagnostics.append(f"{label}_zero")
        elif abs(total - length_m) > error_budget_m:
            diagnostics.append(f"{label}_path_mismatch")
        else:
            usable.append((label, method, tuple(value / total for value in signal.cumulative)))
    for label, method, fractions in usable:
        if all(
            (b == a if active == 0 else (b - a) * length_m / active <= maximum_speed_mps)
            for (a, b), active in zip(pairwise(fractions), clock.active_deltas, strict=True)
        ):
            return method, fractions, tuple(diagnostics)
        diagnostics.append(f"{label}_allocation_implausible")
    return (
        AllocationMethod.TIMESTAMPS,
        tuple(value / clock.audit.active_seconds for value in clock.active_cumulative),
        (*diagnostics, "active_time_estimated"),
    )


@dataclass(frozen=True)
class DistanceSignal:
    status: str
    cumulative: tuple[float, ...] = ()
    # Full, finite, nondecreasing streams with physical jumps can be corrected
    # locally without guessing reset semantics or inserting absent FIT fields.
    correction_supported: bool = False


def qualify_distance(
    records: tuple[ActivityRecord, ...], config: IntegrityConfig
) -> DistanceSignal:
    if len(records) < 2 or any(
        r.distance is None or not isfinite(r.distance) or r.distance < 0 for r in records
    ):
        return DistanceSignal("unavailable")
    values = tuple(float(r.distance) for r in records if r.distance is not None)
    if any(b < a for a, b in pairwise(values)):
        return DistanceSignal("non_monotonic")
    cumulative = tuple(value - values[0] for value in values)
    if any(
        a.timestamp is None or b.timestamp is None or b.timestamp <= a.timestamp
        for a, b in pairwise(records)
    ):
        return DistanceSignal("unavailable")
    ceiling = config.absolute_impossible_speed_mps
    impossible = ceiling is not None and any(
        b - a > config.absolute_impossible_distance_m
        and (b - a) / (rb.timestamp - ra.timestamp).total_seconds() > ceiling
        for a, b, ra, rb in zip(values[:-1], values[1:], records[:-1], records[1:], strict=True)
        if ra.timestamp is not None and rb.timestamp is not None
    )
    if impossible:
        return DistanceSignal("implausible", cumulative, correction_supported=True)
    return DistanceSignal("plausible" if cumulative[-1] > 0 else "zero", cumulative)


def qualify_speed(
    records: tuple[ActivityRecord, ...],
    config: IntegrityConfig,
    *,
    active_deltas: tuple[float, ...] | None = None,
) -> DistanceSignal:
    if active_deltas is not None and len(active_deltas) != len(records) - 1:
        raise ValueError("active duration count must match record transitions")
    used = (
        set(range(len(records)))
        if active_deltas is None
        else {
            index + side
            for index, active in enumerate(active_deltas)
            if active > 0
            for side in (0, 1)
        }
    )
    if (
        len(records) < 2
        or any(r.timestamp is None for r in records)
        or any(
            speed is None or not isfinite(speed) or speed < 0
            for speed in (records[index].speed for index in used)
        )
    ):
        return DistanceSignal("unavailable")
    ceiling = config.absolute_impossible_speed_mps
    if ceiling is not None and any(
        speed > ceiling for speed in (records[index].speed for index in used) if speed is not None
    ):
        return DistanceSignal("implausible")
    cumulative = [0.0]
    for index, (a, b) in enumerate(pairwise(records)):
        assert a.timestamp is not None and b.timestamp is not None
        elapsed = (b.timestamp - a.timestamp).total_seconds()
        if elapsed <= 0:
            return DistanceSignal("unavailable")
        if active_deltas is not None:
            active = active_deltas[index]
            if not isfinite(active) or not 0 <= active <= elapsed:
                return DistanceSignal("unavailable")
            elapsed = active
        if elapsed == 0:
            cumulative.append(cumulative[-1])
            continue
        assert a.speed is not None and b.speed is not None
        cumulative.append(cumulative[-1] + (a.speed + b.speed) * elapsed / 2)
        if not isfinite(cumulative[-1]):
            return DistanceSignal("unavailable")
    return DistanceSignal("plausible" if cumulative[-1] > 0 else "zero", tuple(cumulative))
