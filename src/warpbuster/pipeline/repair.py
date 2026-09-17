"""The single FIT/GPX processing sequence used by CLI and web adapters."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from pathlib import Path

from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.fit import FitWriteResult
from warpbuster.models.integrity import IntegrityReport
from warpbuster.models.reconstruction import (
    CandidateRankingResult,
    CourseData,
    RepairPlan,
    RepairSelection,
)
from warpbuster.reconstruction import (
    build_repair_plan,
    rank_gap_candidates,
    select_repair_intervals,
)
from warpbuster.reconstruction.altitude_completion import (
    AltitudeCompletionPlan,
    AltitudeCompletionReason,
    AltitudeCompletionStatus,
)

from .config import DEFAULT_REPAIR_POLICY, PipelineConfig, RepairPolicy
from .coverage import (
    NOT_APPLICABLE_COVERAGE,
    CoverageStatus,
    ObservedGpsCoverage,
    measure_observed_gps_coverage,
)
from .dem import DEMResult, run_dem_stage
from .osm import OSMResult, eligible_gap_count, run_osm_pipeline


class PipelineError(Exception):
    """Stable adapter-safe code with a separate diagnostic for local CLI users."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RepairRun:
    """Typed processing result; adapters own only presentation and transport."""

    activity: ActivityData
    course: CourseData | None
    integrity: IntegrityReport
    reconstruction_config: CourseReconstructionConfig
    policy: RepairPolicy
    plan: RepairPlan
    selection: RepairSelection
    osm: OSMResult
    dem: DEMResult
    osm_duration_seconds: float
    osm_eligible_gaps: int
    ranking: CandidateRankingResult | None
    write_result: FitWriteResult | None
    fixed_activity: ActivityData | None
    coverage: ObservedGpsCoverage = NOT_APPLICABLE_COVERAGE


def run_repair(
    activity_path: str | Path,
    course_path: str | Path | None = None,
    output_path: str | Path | None = None,
    *,
    policy: RepairPolicy = DEFAULT_REPAIR_POLICY,
    config: PipelineConfig | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> RepairRun:
    """Read inputs, detect independently, reconstruct and perform one atomic write.

    OSM is disabled in the default library configuration. Callers can explicitly
    enable acquisition, request offline cached coverage or supply an exact graph.
    All three routes share the same reconstruction policy and writer.
    """
    started = time.monotonic()
    execution = config or PipelineConfig()
    try:
        activity = read_fit(activity_path)
    except (FitReadError, OSError, ValueError) as error:
        raise PipelineError("invalid_fit", str(error)) from error
    try:
        course = read_gpx_course(course_path) if course_path is not None else None
    except (GpxCourseReadError, OSError, ValueError) as error:
        raise PipelineError("invalid_gpx", str(error)) from error
    effective_policy = (
        policy
        if course is not None or not policy.fill_missing_from_course
        else replace(policy, fill_missing_from_course=False)
    )
    if not activity.records:
        raise PipelineError("empty_activity", "FIT contains no activity records")
    if len(activity.records) > execution.record_limit or (
        course is not None and course.point_count > execution.record_limit
    ):
        raise PipelineError("too_many_records", "FIT/GPX exceeds the processing record limit")

    integrity = analyze_integrity(activity)
    reconstruction = CourseReconstructionConfig()
    base_plan = build_repair_plan(
        activity,
        integrity,
        course,
        reconstruction,
        fill_missing_from_course=effective_policy.fill_missing_from_course,
        minimum_invalidation_confidence=effective_policy.minimum_invalidation_confidence,
    )
    if time.monotonic() - started > execution.base_plan_timeout_seconds:
        raise PipelineError("timeout", "base repair planning exceeded its time budget")
    coverage = (
        NOT_APPLICABLE_COVERAGE
        if course is not None
        else measure_observed_gps_coverage(
            activity,
            integrity,
            base_plan.coordinate_mask,
            minimum_percent=execution.minimum_observed_gps_coverage_percent,
            maximum_interval_seconds=execution.maximum_observed_gps_interval_seconds,
        )
    )
    if coverage.status in {CoverageStatus.BELOW_THRESHOLD, CoverageStatus.UNAVAILABLE}:
        blocked_selection = select_repair_intervals(
            base_plan, effective_policy.minimum_confidence
        )
        return RepairRun(
            activity,
            course,
            integrity,
            reconstruction,
            effective_policy,
            base_plan,
            replace(blocked_selection, invalidations=(), distance_spike_repairs=()),
            OSMResult(base_plan, "not_needed"),
            DEMResult(base_plan, "not_needed"),
            0.0,
            0,
            None,
            None,
            None,
            coverage,
        )
    osm_eligible_gaps = eligible_gap_count(activity, base_plan)
    remaining = execution.process_timeout_seconds - (time.monotonic() - started)
    osm_started = time.monotonic()
    if remaining <= execution.publish_reserve_seconds:
        osm = OSMResult(base_plan, "unavailable", "setup", "osm_timeout")
    else:
        bounded = replace(
            execution,
            osm_total_timeout_seconds=min(
                execution.osm_total_timeout_seconds,
                max(1, int(remaining - execution.publish_reserve_seconds)),
            ),
        )
        try:
            osm = run_osm_pipeline(activity, integrity, base_plan, bounded, policy=effective_policy)
        except Exception:
            # Optional companion/native failures never discard the base GPX plan.
            osm = OSMResult(base_plan, "unavailable", "routing", "routing_failed")
    osm_duration_seconds = time.monotonic() - osm_started
    remaining = execution.process_timeout_seconds - (time.monotonic() - started)
    dem = run_dem_stage(
        activity,
        integrity,
        base_plan,
        osm.plan,
        osm.discovery,
        execution,
        effective_policy,
        budget_seconds=max(0.0, remaining - execution.publish_reserve_seconds),
    )
    if (
        execution.complete_missing_altitude
        and dem.altitude.status is AltitudeCompletionStatus.DISABLED
    ):
        dem = replace(
            dem,
            altitude=AltitudeCompletionPlan(
                AltitudeCompletionStatus.NOT_NEEDED
                if dem.status == "not_needed"
                else AltitudeCompletionStatus.UNAVAILABLE,
                reason=None
                if dem.status == "not_needed"
                else AltitudeCompletionReason.DEM_UNAVAILABLE,
            ),
        )
    plan = dem.plan
    if plan is not osm.plan:
        private_audit = osm.private_audit
        if private_audit is not None and plan.automatic_osm_json is not None:
            private_audit = {
                **private_audit,
                "application": {"decisions": json.loads(plan.automatic_osm_json)["decisions"]},
            }
        osm = replace(osm, plan=plan, private_audit=private_audit)
    ranking = (
        rank_gap_candidates(activity, base_plan, osm.discovery)
        if course is not None or osm.discovery is not None
        else None
    )
    selection = select_repair_intervals(plan, effective_policy.minimum_confidence)
    written = None
    fixed = None
    if not dry_run and selection.has_changes:
        try:
            altitude_plan = (
                dem.altitude if dem.altitude.status is AltitudeCompletionStatus.PLANNED else None
            )
            try:
                written = write_repaired_fit(
                    activity,
                    plan,
                    output_path,
                    minimum_confidence=effective_policy.minimum_confidence,
                    overwrite=overwrite,
                    altitude_completion=altitude_plan,
                )
            except FitWriteError:
                if altitude_plan is None:
                    raise
                # A DEM/FIT altitude mismatch cannot cancel safe coordinate repair.
                dem = replace(
                    dem,
                    altitude=AltitudeCompletionPlan(
                        AltitudeCompletionStatus.UNAVAILABLE,
                        reason=AltitudeCompletionReason.WRITER_REFUSED,
                        eligible_records=altitude_plan.eligible_records,
                    ),
                )
                written = write_repaired_fit(
                    activity,
                    plan,
                    output_path,
                    minimum_confidence=effective_policy.minimum_confidence,
                    overwrite=overwrite,
                )
            else:
                if altitude_plan is not None:
                    dem = replace(
                        dem,
                        altitude=replace(altitude_plan, status=AltitudeCompletionStatus.APPLIED),
                    )
            fixed = read_fit(written.output_path)
            if (
                not written.validation.valid
                or not written.post_write_verified
                or written.diff.unexpected_changed_field_count
                or written.diff.timestamps.compared_count != written.diff.timestamps.unchanged_count
                or written.diff.sensors.compared_count - written.diff.sensors.unchanged_count
                != written.altitude_field_change_count
            ):
                raise FitWriteError("written FIT failed preservation verification")
        except (FitWriteError, FitReadError, OSError, ValueError) as error:
            raise PipelineError("repair_refused", str(error)) from error
    return RepairRun(
        activity,
        course,
        integrity,
        reconstruction,
        effective_policy,
        plan,
        selection,
        osm,
        dem,
        osm_duration_seconds,
        osm_eligible_gaps,
        ranking,
        written,
        fixed,
        coverage,
    )
