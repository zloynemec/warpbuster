"""FIT-only observed GPS coverage measured over the original active timeline."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise

from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityReport, TransitionClassification
from warpbuster.models.reconstruction import CoordinateDisposition, CoordinateState
from warpbuster.reconstruction.timing import activity_clock


class CoverageStatus(StrEnum):
    PASSED = "passed"
    BELOW_THRESHOLD = "below_threshold"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class ObservedGpsCoverage:
    status: CoverageStatus
    threshold_percent: float | None = None
    observed_percent: float | None = None
    active_duration_seconds: float | None = None
    observed_duration_seconds: float | None = None
    maximum_observed_interval_seconds: float | None = None
    reason: str | None = None


NOT_APPLICABLE_COVERAGE = ObservedGpsCoverage(CoverageStatus.NOT_APPLICABLE)


def measure_observed_gps_coverage(
    activity: ActivityData,
    integrity: IntegrityReport,
    mask: tuple[CoordinateDisposition, ...],
    *,
    minimum_percent: float,
    maximum_interval_seconds: float,
) -> ObservedGpsCoverage:
    """Count only consecutive, trusted original fixes and normal transitions."""
    if len(mask) != len(activity.records):
        raise ValueError("coordinate mask does not match activity records")
    clock = activity_clock(activity, activity.records)
    if clock is None:
        return ObservedGpsCoverage(
            CoverageStatus.UNAVAILABLE,
            threshold_percent=minimum_percent,
            maximum_observed_interval_seconds=maximum_interval_seconds,
            reason="invalid_record_timestamps",
        )
    if clock.audit.open_pause:
        return ObservedGpsCoverage(
            CoverageStatus.UNAVAILABLE,
            threshold_percent=minimum_percent,
            maximum_observed_interval_seconds=maximum_interval_seconds,
            reason="unresolved_timer_state",
        )
    active = clock.audit.active_seconds
    if active <= 0:
        return ObservedGpsCoverage(
            CoverageStatus.UNAVAILABLE,
            threshold_percent=minimum_percent,
            maximum_observed_interval_seconds=maximum_interval_seconds,
            reason="no_active_time",
        )

    normal_pairs = {
        (item.from_record_index, item.to_record_index)
        for item in integrity.transitions
        if item.classification is TransitionClassification.NORMAL
    }
    observed = 0.0
    for (before, after), (before_mask, after_mask), delta in zip(
        pairwise(activity.records), pairwise(mask), clock.active_deltas, strict=True
    ):
        if (
            0 < delta <= maximum_interval_seconds
            and before.continuity_id == after.continuity_id
            and before_mask.state is CoordinateState.PRESERVED
            and after_mask.state is CoordinateState.PRESERVED
            and before_mask.anchor_eligible
            and after_mask.anchor_eligible
            and (before.index, after.index) in normal_pairs
        ):
            observed += delta
    percent = 100.0 * observed / active
    return ObservedGpsCoverage(
        CoverageStatus.PASSED if percent >= minimum_percent else CoverageStatus.BELOW_THRESHOLD,
        threshold_percent=minimum_percent,
        maximum_observed_interval_seconds=maximum_interval_seconds,
        observed_percent=percent,
        active_duration_seconds=active,
        observed_duration_seconds=observed,
    )
