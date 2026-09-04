"""Compatibility entry point for the unified local reconstruction planner."""

from __future__ import annotations

from warpbuster.config import CourseReconstructionConfig
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport
from warpbuster.models.reconstruction import CourseData, RepairPlan


def build_course_repair_plan(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    config: CourseReconstructionConfig | None = None,
    *,
    minimum_invalidation_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> RepairPlan:
    """Plan proven corruption; all original missing gaps remain visible but disabled.

    Prefer build_repair_plan for the single provider-neutral entry point. The old
    name no longer invokes course-driven write-scope expansion or composite envelopes.
    """
    from warpbuster.reconstruction.local import build_repair_plan

    return build_repair_plan(
        activity,
        integrity,
        course,
        config,
        minimum_invalidation_confidence=minimum_invalidation_confidence,
    )
