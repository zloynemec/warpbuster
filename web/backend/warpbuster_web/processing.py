"""Isolated Core adapter and allowlisted public projection, never the local HTML payload."""

import argparse
import json
import math
import os
import re
from dataclasses import replace
from pathlib import Path

from warpbuster.pipeline import (
    APPROXIMATE_DECISION_REASONS,
    DEMMode,
    OSMMode,
    PipelineError,
    run_repair,
)
from warpbuster.report.gaps import distance_policy, gap_audit

from .config import WebConfig
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


def _safe_id(value):
    return (
        value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value) else None
    )


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
):
    # Direct library calls remain offline. The public worker passes its deployment config.
    web_config = config or replace(
        WebConfig.from_environment(),
        osm_mode=OSMMode.DISABLED,
        approximate_osm=False,
        dem_mode=DEMMode.DISABLED,
        dem_snapshot_id=None,
        complete_missing_altitude=False,
    )
    input_directory = input_directory if input_directory is not None else directory
    try:
        run = run_repair(
            input_directory / "original.fit",
            input_directory / "course.gpx",
            directory / "corrected.fit",
            config=replace(web_config.pipeline_config(), record_limit=record_limit),
        )
    except PipelineError as error:
        raise ProcessingError(error.code) from error
    activity, course, integrity = run.activity, run.course, run.integrity
    assert course is not None
    plan, selection = run.plan, run.selection
    result, fixed = run.write_result, run.fixed_activity
    osm = run.osm
    osm_duration_seconds = run.osm_duration_seconds
    osm_eligible_gaps = run.osm_eligible_gaps

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
        "schema_version": 4 if web_config.approximate_osm else 3,
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
    if web_config.complete_missing_altitude:
        report["altitude_completion"] = run.dem.altitude.public_summary()
    if web_config.approximate_osm:
        automatic = json.loads(plan.automatic_osm_json) if plan.automatic_osm_json else {}
        report["approximate_osm"] = {
            "policy_id": _safe_id(automatic.get("policy")),
            "unconfirmed_route_warning": "Approximate route; actual movement is unconfirmed",
            "decisions": [
                {
                    "selected_route_id": _safe_id(details.get("selected_route_id")),
                    "selection_mode": details.get("selection_mode")
                    if details.get("selection_mode")
                    in {"gpx_first", "ranked", "approximate_tie_break", "approximate_low_evidence"}
                    else None,
                    "approximate": details.get("approximate") is True,
                    "attempted_count": evidence.get("attempted_count")
                    if type(evidence.get("attempted_count")) is int
                    else 0,
                    "rejected_count": evidence.get("rejected_count")
                    if type(evidence.get("rejected_count")) is int
                    else 0,
                    "dem_status": evidence.get("dem_status")
                    if evidence.get("dem_status")
                    in {"not_requested", "usable", "uninformative", "unavailable"}
                    else None,
                    "dem_snapshot_id": _safe_id(evidence.get("dem_snapshot_id")),
                    "dem_profile_ids": [
                        value
                        for item in evidence.get("dem_profile_ids", [])
                        if (value := _safe_id(item)) is not None
                    ]
                    if isinstance(evidence.get("dem_profile_ids"), list)
                    else [],
                    "reasons": [
                        reason
                        for reason in evidence.get("reasons", [])
                        if isinstance(reason, str) and reason in APPROXIMATE_DECISION_REASONS
                    ]
                    if isinstance(evidence.get("reasons"), list)
                    else [],
                }
                for details in automatic.get("decisions", [])
                if isinstance(details, dict)
                for evidence in [details.get("approximate_audit") or {}]
                if isinstance(evidence, dict)
            ],
            "dem": {
                "status": run.dem.status,
                "error_code": run.dem.error_code,
                "snapshot_id": _safe_id(run.dem.snapshot_id),
                "duration_seconds": round(run.dem.duration_seconds, 3),
            },
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
        if web_config.complete_missing_altitude:
            report["fit_diff"]["altitude_fields"] = result.altitude_field_change_count
            report["fit_diff"]["non_altitude_sensors_unchanged"] = (
                diff.sensors.compared_count - diff.sensors.unchanged_count
                == result.altitude_field_change_count
            )
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
