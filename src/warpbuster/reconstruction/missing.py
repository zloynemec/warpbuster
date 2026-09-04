"""Compatibility entry point for opt-in local completion of every geometry gap."""

from __future__ import annotations

from warpbuster.config import CourseReconstructionConfig
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport
from warpbuster.models.reconstruction import CourseData, RepairPlan


def build_missing_course_plan(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    config: CourseReconstructionConfig | None = None,
    *,
    minimum_invalidation_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> RepairPlan:
    """Enable unified completion; no global longest-run alignment is performed."""
    from warpbuster.reconstruction.local import build_repair_plan

    return build_repair_plan(
        activity,
        integrity,
        course,
        config,
        fill_missing_from_course=True,
        minimum_invalidation_confidence=minimum_invalidation_confidence,
    )
