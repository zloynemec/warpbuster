"""Merge independent reconstruction providers before one atomic FIT write."""

from __future__ import annotations

from dataclasses import replace

from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    MissingCourseCompletionPlan,
    ReconstructionReason,
    RepairCandidate,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedMissingCourseRun,
)


def merge_repair_plans(primary: RepairPlan, missing: RepairPlan) -> RepairPlan:
    """Merge corruption and opt-in missing completion without duplicate updates."""
    if primary.activity_path != missing.activity_path or primary.course_path != missing.course_path:
        raise ValueError("repair plans must describe the same activity and course")
    primary_indices = {
        update.record_index
        for candidate in primary.interval_plans
        for update in candidate.coordinate_updates
    }
    accepted_missing: list[MissingCourseCompletionPlan] = []
    rejected_missing = list(missing.unresolved_missing_runs)
    for candidate in missing.interval_plans:
        if not isinstance(candidate, MissingCourseCompletionPlan):
            raise TypeError("missing provider returned a non-missing candidate")
        if any(update.record_index in primary_indices for update in candidate.coordinate_updates):
            rejected_missing.append(
                UnresolvedMissingCourseRun(
                    interval=candidate.interval,
                    confidence=IntegrityConfidence.LOW,
                    reasons=(ReconstructionReason.OVERLAPS_PRIMARY_RECONSTRUCTION,),
                )
            )
            continue
        accepted_missing.append(candidate)

    candidates: tuple[RepairCandidate, ...] = tuple(
        sorted(
            (*primary.interval_plans, *accepted_missing),
            key=lambda candidate: candidate.interval.start_record_index,
        )
    )
    unresolved_count = len(primary.unresolved_intervals) + len(rejected_missing)
    if not candidates and not unresolved_count:
        status = RepairPlanStatus.NOT_NEEDED
        confidence = IntegrityConfidence.HIGH
        reasons = (ReconstructionReason.NO_CORRUPTED_INTERVALS,)
    elif (
        candidates
        and not unresolved_count
        and all(candidate.repair_eligible for candidate in candidates)
    ):
        status = RepairPlanStatus.READY
        confidence = IntegrityConfidence.HIGH
        reasons = (ReconstructionReason.ALL_INTERVALS_READY,)
    elif candidates:
        status = RepairPlanStatus.PARTIAL
        confidence = IntegrityConfidence.LOW
        reasons = (ReconstructionReason.SOME_INTERVALS_UNRESOLVED,)
    else:
        status = RepairPlanStatus.REFUSED
        confidence = IntegrityConfidence.LOW
        reasons = (ReconstructionReason.NO_INTERVAL_READY,)
    return replace(
        primary,
        status=status,
        confidence=confidence,
        detected_interval_count=(
            len(candidates) + len(primary.unresolved_intervals) + len(rejected_missing)
        ),
        interval_plans=candidates,
        unresolved_missing_runs=tuple(rejected_missing),
        missing_completion_enabled=True,
        reasons=(ReconstructionReason.MISSING_COMPLETION_ENABLED, *reasons),
    )
