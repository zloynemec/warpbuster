"""Confidence-threshold selection of available reconstruction candidates."""

from __future__ import annotations

from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    RepairIntervalAction,
    RepairIntervalDecision,
    RepairPlan,
    RepairSelection,
    RepairSelectionReason,
)

_CONFIDENCE_RANK = {
    IntegrityConfidence.LOW: 0,
    IntegrityConfidence.MEDIUM: 1,
    IntegrityConfidence.HIGH: 2,
}


def select_repair_intervals(
    plan: RepairPlan,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> RepairSelection:
    """Select every available candidate at or above the explicit threshold."""
    selected = []
    decisions: list[RepairIntervalDecision] = []
    for interval_plan in plan.interval_plans:
        meets_threshold = (
            _CONFIDENCE_RANK[interval_plan.confidence] >= _CONFIDENCE_RANK[minimum_confidence]
        )
        if meets_threshold:
            selected.append(interval_plan)
        decisions.append(
            RepairIntervalDecision(
                interval=interval_plan.interval,
                confidence=interval_plan.confidence,
                action=(
                    RepairIntervalAction.APPLIED
                    if meets_threshold
                    else RepairIntervalAction.SKIPPED
                ),
                candidate_available=True,
                coordinate_update_count=len(interval_plan.coordinate_updates),
                selection_reasons=(
                    RepairSelectionReason.CONFIDENCE_AT_OR_ABOVE_THRESHOLD
                    if meets_threshold
                    else RepairSelectionReason.BELOW_MINIMUM_CONFIDENCE,
                ),
                reconstruction_reasons=interval_plan.reasons,
            )
        )
    decisions.extend(
        RepairIntervalDecision(
            interval=unresolved.interval,
            confidence=unresolved.confidence,
            action=RepairIntervalAction.SKIPPED,
            candidate_available=False,
            coordinate_update_count=0,
            selection_reasons=(RepairSelectionReason.NO_RECONSTRUCTION_CANDIDATE,),
            reconstruction_reasons=unresolved.reasons,
        )
        for unresolved in plan.unresolved_intervals
    )
    decisions.extend(
        RepairIntervalDecision(
            interval=unresolved.interval,
            confidence=unresolved.confidence,
            action=RepairIntervalAction.SKIPPED,
            candidate_available=False,
            coordinate_update_count=0,
            selection_reasons=(RepairSelectionReason.NO_RECONSTRUCTION_CANDIDATE,),
            reconstruction_reasons=unresolved.reasons,
        )
        for unresolved in plan.unresolved_missing_runs
    )
    decisions.sort(key=lambda decision: decision.interval.start_record_index)
    selected.sort(key=lambda candidate: candidate.interval.start_record_index)
    return RepairSelection(
        minimum_confidence=minimum_confidence,
        detected_interval_count=plan.detected_interval_count,
        selected_interval_plans=tuple(selected),
        decisions=tuple(decisions),
    )
