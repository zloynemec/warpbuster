"""Isolated Core adapter and allowlisted public projection, never the local HTML payload."""

import argparse
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.reconstruction.local import build_repair_plan
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.report.gaps import distance_policy, gap_audit

from .config import REPAIR_POLICY, OSMMode, WebConfig
from .osm_pipeline import OSMWebResult, eligible_gap_count, run_osm_pipeline_isolated
from .performance import public_performance

PUBLIC_FIELDS = {
    ("record", "position_lat"),
    ("record", "position_long"),
    ("record", "distance"),
    ("lap", "total_distance"),
    ("lap", "avg_speed"),
    ("lap", "enhanced_avg_speed"),
    ("session", "total_distance"),
    ("session", "avg_speed"),
    ("session", "enhanced_avg_speed"),
}


class ProcessingError(Exception):
    """An intentionally non-sensitive error code."""


def finite(value):
    return value if isinstance(value, int | float) and math.isfinite(value) else None


def track(activity):
    segments = []
    current = []
    continuity = None
    for record in activity.records:
        lat, lon = finite(record.latitude), finite(record.longitude)
        valid = lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180
        if not valid or record.continuity_id != continuity:
            if current:
                segments.append(current)
            current = []
        continuity = record.continuity_id
        if valid:
            current.append([lat, lon])
    if current:
        segments.append(current)
    return segments


def process_job(
    directory: Path,
    record_limit: int,
    *,
    input_directory: Path | None = None,
    config: WebConfig | None = None,
    osm_runner: Callable[..., OSMWebResult] = run_osm_pipeline_isolated,
):
    started = time.monotonic()
    # The public worker passes an explicit environment-derived config. Direct library
    # calls stay deterministic/offline unless the caller explicitly opts into OSM.
    web_config = config or replace(WebConfig.from_environment(), osm_mode=OSMMode.DISABLED)
    input_directory = input_directory if input_directory is not None else directory
    try:
        activity = read_fit(input_directory / "original.fit")
    except (FitReadError, OSError, ValueError) as error:
        raise ProcessingError("invalid_fit") from error
    try:
        course = read_gpx_course(input_directory / "course.gpx")
    except (GpxCourseReadError, OSError, ValueError) as error:
        raise ProcessingError("invalid_gpx") from error
    if not activity.records:
        raise ProcessingError("empty_activity")
    if len(activity.records) > record_limit or course.point_count > record_limit:
        raise ProcessingError("too_many_records")

    # Web policy explicitly enables course gap filling and MEDIUM thresholds.
    # Course never enters detection; the Core defaults remain unchanged.
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(
        activity,
        integrity,
        course,
        CourseReconstructionConfig(),
        fill_missing_from_course=REPAIR_POLICY["fill_missing_from_course"],
        minimum_invalidation_confidence=IntegrityConfidence(
            REPAIR_POLICY["minimum_invalidation_confidence"]
        ),
    )
    if time.monotonic() - started > web_config.base_plan_timeout_seconds:
        raise ProcessingError("timeout")
    minimum_confidence = IntegrityConfidence(REPAIR_POLICY["minimum_confidence"])
    osm_eligible_gaps = eligible_gap_count(activity, plan)
    remaining = web_config.process_timeout_seconds - (time.monotonic() - started)
    osm_started = time.monotonic()
    if remaining <= web_config.publish_reserve_seconds:
        osm = OSMWebResult(plan, "unavailable", "setup", "osm_timeout")
    else:
        try:
            osm = osm_runner(activity, integrity, plan, web_config)
        except Exception:
            # OSM is an optional reconstruction stage. Keep the immutable base plan
            # and never expose exception text from companion/native code.
            osm = OSMWebResult(plan, "unavailable", "routing", "routing_failed")
    osm_duration_seconds = time.monotonic() - osm_started
    plan = osm.plan
    selection = select_repair_intervals(plan, minimum_confidence)
    result = None
    fixed = None
    if selection.has_changes:
        try:
            result = write_repaired_fit(
                activity,
                plan,
                directory / "corrected.fit",
                minimum_confidence=minimum_confidence,
            )
            fixed = read_fit(result.output_path)
        except (FitWriteError, FitReadError, OSError, ValueError) as error:
            (directory / "corrected.fit").unlink(missing_ok=True)
            raise ProcessingError("repair_refused") from error
        if (
            not result.validation.valid
            or not result.post_write_verified
            or result.diff.unexpected_changed_field_count
            or result.diff.timestamps.compared_count != result.diff.timestamps.unchanged_count
            or result.diff.sensors.compared_count != result.diff.sensors.unchanged_count
        ):
            raise ProcessingError("repair_refused")

    # Construct every public field explicitly. Never serialize dataclasses, inspect_report,
    # repair_report, local HTML, exception text, source filenames or preservation objects.
    written_plan = result.plan if result is not None and result.plan is not None else plan
    audit = gap_audit(written_plan, selection)
    public_gaps = [
        {
            "number": item["number"],
            "start": item["start_record_index"],
            "end": item["end_record_index"],
            "provider": item["provider"],
            "action": item["status"],
            "confidence": item["path_confidence"],
            "allocation_method": (item.get("provenance") or {}).get("allocation_method"),
            "estimated": (item.get("provenance") or {}).get("allocation_method") == "timestamps",
            "reasons": [str(reason) for reason in item["reasons"]],
        }
        for item in audit["gap_inventory"]
    ]
    applied_gpx = sum(
        item["action"] == "applied" and item["provider"] == "gpx" for item in public_gaps
    )
    applied_osm = sum(
        item["action"] == "applied" and item["provider"] == "osm" for item in public_gaps
    )
    report = {
        "schema_version": 3,
        "outcome": "repaired"
        if result
        else ("unchanged" if plan.status.value == "not_needed" else "unresolved"),
        "partial": selection.is_partial
        or bool(selection.unresolved_invalidated_indices)
        or any(item["action"] == "unresolved" for item in public_gaps),
        "summary": {
            "record_count": len(activity.records),
            "detected_intervals": len(integrity.corrupted_intervals),
            "applied_intervals": selection.applied_interval_count,
            "skipped_intervals": selection.skipped_interval_count,
            "unresolved_points": audit["coordinate_coverage"]["unresolved"],
            "filled_points": audit["coordinate_coverage"]["filled"],
            "all_unresolved_points": audit["coordinate_coverage"]["unresolved"],
            "applied_gpx_gaps": applied_gpx,
            "applied_osm_gaps": applied_osm,
            "unresolved_gaps": sum(item["action"] == "unresolved" for item in public_gaps),
            "original_distance_m": finite(activity.recorded_distance_m),
            "corrected_distance_m": finite(fixed.recorded_distance_m) if fixed else None,
        },
        "tracks": {
            "original": track(activity),
            "corrected": track(fixed) if fixed else [],
            "course": [
                [[point.latitude, point.longitude] for point in segment.points]
                for segment in course.segments
            ],
        },
        "performance": public_performance(
            fixed if fixed else activity,
            source="corrected" if fixed else "original",
            distance_quality=distance_policy(selection)["quality"],
        ),
        "intervals": [
            {
                "start": decision.interval.start_record_index,
                "end": decision.interval.end_record_index,
                "confidence": decision.confidence.value,
                "action": decision.action.value,
            }
            for decision in selection.decisions
        ],
        "gaps": public_gaps,
        "osm": {
            "status": osm.status,
            "stage": osm.stage,
            "error_code": osm.error_code,
            "duration_seconds": round(osm_duration_seconds, 3),
            "eligible_gaps": osm_eligible_gaps,
            "coverage_cells": osm.metrics.coverage_cells if osm.metrics else None,
            "coverage_area_km2": round(osm.metrics.coverage_area_km2, 3) if osm.metrics else None,
            "coverage_seconds": round(osm.metrics.coverage_seconds, 3) if osm.metrics else None,
            "acquisition_seconds": round(osm.metrics.acquisition_seconds, 3)
            if osm.metrics
            else None,
            "prepare_seconds": round(osm.metrics.prepare_seconds, 3) if osm.metrics else None,
            "routing_seconds": round(osm.metrics.routing_seconds, 3) if osm.metrics else None,
            "snapshot_cache_hit": osm.metrics.snapshot_cache_hit if osm.metrics else None,
            "snapshot_stale": osm.metrics.snapshot_stale if osm.metrics else None,
            "graph_cache_hit": osm.metrics.graph_cache_hit if osm.metrics else None,
            "routing_queries": osm.metrics.routing_queries if osm.metrics else None,
            "candidate_gaps": osm.metrics.candidate_gaps if osm.metrics else None,
            "candidates": osm.metrics.candidates if osm.metrics else None,
        },
        "distance": {
            "quality": audit["distance"]["quality"],
            "uncertain": audit["distance"]["quality"] == "uncertain",
            "reason": audit["distance"]["reason"],
        },
        "fit_diff": None,
    }
    if result:
        diff = result.diff
        report["fit_diff"] = {
            "changed_records": diff.changed_record_count,
            "changed_fields": diff.changed_field_count,
            "coordinate_fields": result.coordinate_field_change_count,
            "distance_fields": result.distance_field_change_count,
            "summary_fields": result.summary_field_change_count,
            "timestamps_unchanged": diff.timestamps.compared_count
            == diff.timestamps.unchanged_count,
            "sensors_unchanged": diff.sensors.compared_count == diff.sensors.unchanged_count,
            "developer_fields_unchanged": diff.developer_fields.compared_count
            == diff.developer_fields.unchanged_count,
            "unknown_fields_unchanged": diff.unknown_fields.compared_count
            == diff.unknown_fields.unchanged_count,
            "truncated_changes": diff.truncated_change_count,
            "changes": [
                {
                    "message": change.message_type,
                    "index": change.occurrence_index,
                    "field": change.field_name,
                    "before": finite(change.original_value),
                    "after": finite(change.fixed_value),
                }
                for change in diff.retained_changes
                if (change.message_type, change.field_name) in PUBLIC_FIELDS
            ],
        }
    temporary = directory / "result.json.tmp"
    temporary.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(directory / "result.json")
    if osm.private_audit is not None:
        audit_temporary = directory / "private-osm-audit.json.tmp"
        audit_temporary.write_text(
            json.dumps(osm.private_audit, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        audit_temporary.chmod(0o600)
        audit_temporary.replace(directory / "private-osm-audit.json")
    return bool(result)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Process one private FIT/GPX pair")
    parser.add_argument("directory", type=Path, help="existing output directory")
    parser.add_argument("record_limit", type=int)
    parser.add_argument(
        "--inputs", type=Path, help="input pair directory; defaults to output directory"
    )
    args = parser.parse_args()
    directory = args.directory
    try:
        process_job(
            directory,
            args.record_limit,
            input_directory=args.inputs,
            config=WebConfig.from_environment(),
        )
    except ProcessingError as error:
        (directory / "failure.json").write_text(json.dumps({"code": str(error)}), encoding="utf-8")
        return 1
    except Exception:
        (directory / "failure.json").write_text('{"code":"processing_failed"}', encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
