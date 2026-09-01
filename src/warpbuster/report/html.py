"""Interactive local HTML reports for integrity analysis and FIT repair."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from importlib.resources import files
from math import isfinite
from pathlib import Path

from warpbuster import __version__
from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.fit import FitWriteResult
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport
from warpbuster.models.reconstruction import CourseData, RepairPlan, RepairSelection
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.report.analyze import analyze_report
from warpbuster.report.fit import write_result_report
from warpbuster.report.inspect import inspect_report
from warpbuster.report.repair import repair_report


class _UseActivityDistance:
    """Sentinel type selecting ActivityData.recorded_distance_m."""


_USE_ACTIVITY_DISTANCE = _UseActivityDistance()


class HtmlReportError(ValueError):
    """Raised when an HTML report cannot be produced without overwriting data."""


def write_analyze_html(
    activity: ActivityData,
    integrity: IntegrityReport,
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Write one interactive report, optionally replacing its destination atomically."""
    payload = _base_payload(activity, integrity, report_kind="analyze")
    payload["tracks"] = {
        "original": _activity_track(activity, integrity),
        "repaired": None,
        "candidate": None,
        "course": None,
    }
    payload["repair"] = None
    payload["write_result"] = None
    payload["metrics_comparison"] = _metrics_comparison(activity)
    payload["missing_position_runs"] = _missing_position_runs(activity)
    return _write_payload(payload, output_path, overwrite=overwrite)


def write_repair_html(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData,
    plan: RepairPlan,
    config: CourseReconstructionConfig,
    output_path: str | Path,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
    fixed_activity: ActivityData | None = None,
    write_result: FitWriteResult | None = None,
    overwrite: bool = False,
) -> Path:
    """Write a repair report, optionally replacing its destination atomically."""
    if (fixed_activity is None) is not (write_result is None):
        raise HtmlReportError("fixed_activity and write_result must be provided together")
    selection = select_repair_intervals(plan, minimum_confidence)
    payload = _base_payload(
        activity,
        integrity,
        report_kind="repair_write" if fixed_activity is not None else "repair_dry_run",
    )
    payload["tracks"] = {
        "original": _activity_track(activity, integrity),
        "repaired": (_activity_track(fixed_activity, None) if fixed_activity is not None else None),
        "candidate": (
            None if fixed_activity is not None else _candidate_track(activity, selection)
        ),
        "course": _course_track(course),
    }
    payload["repair"] = _compact_repair_report(
        plan,
        course,
        config,
        minimum_confidence,
    )
    payload["write_result"] = (
        write_result_report(write_result) if write_result is not None else None
    )
    coordinate_overrides = _selection_coordinate_overrides(selection)
    payload["metrics_comparison"] = _metrics_comparison(
        activity,
        course=course,
        comparison_activity=fixed_activity,
        comparison_coordinate_overrides=(coordinate_overrides if fixed_activity is None else None),
    )
    payload["missing_position_runs"] = (
        _missing_position_runs(fixed_activity)
        if fixed_activity is not None
        else _missing_position_runs(activity, coordinate_overrides)
    )
    return _write_payload(payload, output_path, overwrite=overwrite)


def ensure_html_output_available(
    output_path: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Validate the destination before another operation creates side effects."""
    destination = Path(output_path)
    if destination.exists() and not overwrite:
        raise HtmlReportError(f"HTML output already exists: {destination}")
    if not destination.parent.exists():
        raise HtmlReportError(f"HTML output directory does not exist: {destination.parent}")
    if not destination.parent.is_dir():
        raise HtmlReportError(f"HTML output parent is not a directory: {destination.parent}")
    if destination.is_dir():
        raise HtmlReportError(f"HTML output is a directory: {destination}")
    return destination


def _base_payload(
    activity: ActivityData,
    integrity: IntegrityReport,
    *,
    report_kind: str,
) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "report_kind": report_kind,
        "generator": {"name": "WarpBuster", "version": __version__},
        "inspect": inspect_report(activity),
        "analysis": analyze_report(activity, integrity),
        "chart_axis": _chart_axis(activity),
    }


def _activity_track(
    activity: ActivityData,
    integrity: IntegrityReport | None,
) -> dict[str, object]:
    apparent_speed = (
        {
            transition.to_record_index: transition.apparent_speed_mps
            for transition in integrity.transitions
        }
        if integrity is not None
        else {}
    )
    timed = bool(activity.records) and all(
        record.timestamp is not None for record in activity.records
    )
    first_timestamp = activity.records[0].timestamp if timed else None
    return {
        "record_count": len(activity.records),
        "records": [
            _record_row(
                record,
                _record_x(record, first_timestamp),
                apparent_speed.get(record.index),
            )
            for record in activity.records
        ],
    }


def _candidate_track(
    activity: ActivityData,
    selection: RepairSelection,
) -> dict[str, object] | None:
    updates = {
        update.record_index: (update.candidate_latitude, update.candidate_longitude)
        for interval in selection.selected_interval_plans
        for update in interval.coordinate_updates
    }
    if not updates:
        return None
    return {
        "record_count": len(activity.records),
        "records": [
            [
                record.index,
                *(updates.get(record.index, (record.latitude, record.longitude))),
                record.continuity_id,
            ]
            for record in activity.records
        ],
    }


def _selection_coordinate_overrides(
    selection: RepairSelection,
) -> dict[int, tuple[float, float]]:
    return {
        update.record_index: (update.candidate_latitude, update.candidate_longitude)
        for interval in selection.selected_interval_plans
        for update in interval.coordinate_updates
    }


def _metrics_comparison(
    original: ActivityData,
    *,
    course: CourseData | None = None,
    comparison_activity: ActivityData | None = None,
    comparison_coordinate_overrides: Mapping[int, tuple[float, float]] | None = None,
) -> dict[str, object]:
    course_distance = course.total_distance_m if course is not None else None
    rows = [
        _activity_metrics_row(
            "original",
            (
                "Original FIT"
                if original.preservation.source_format.value == "fit"
                else "Original GPX"
            ),
            original,
            course_distance=course_distance,
        )
    ]
    if course is not None:
        course_ascent = _course_elevation_gain_m(course)
        rows.append(
            {
                "id": "course",
                "label": "Reference course",
                "embedded_distance_m": None,
                "map_geometry_distance_m": course.total_distance_m,
                "solid_geometry_distance_m": course.total_distance_m,
                "geometry_delta_vs_course_m": 0.0,
                "elevation_gain_m": course_ascent,
                "elevation_gain_source": (
                    "GPX positive elevation deltas (unsmoothed)"
                    if course_ascent is not None
                    else "unavailable"
                ),
            }
        )
    if comparison_activity is not None:
        rows.append(
            _activity_metrics_row(
                "repaired",
                "Repaired FIT",
                comparison_activity,
                course_distance=course_distance,
            )
        )
    elif comparison_coordinate_overrides is not None:
        rows.append(
            _activity_metrics_row(
                "candidate",
                "Candidate preview",
                original,
                coordinate_overrides=comparison_coordinate_overrides,
                embedded_distance_override=None,
                course_distance=course_distance,
                elevation_source_suffix=" (unchanged preview)",
            )
        )
    return {
        "rows": rows,
        "notes": [
            "FIT summary distance is session.total_distance (or the final record.distance when no session total exists); GPX course has no FIT summary.",
            "Map geometry connects available coordinates across missing-position runs with straight chords.",
            "Solid known geometry excludes those unknown chords and all continuity boundaries.",
            "GPX elevation gain is an unsmoothed sum of positive elevation deltas and may differ from FIT device ascent.",
        ],
    }


def _activity_metrics_row(
    row_id: str,
    label: str,
    activity: ActivityData,
    *,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None = None,
    embedded_distance_override: float | _UseActivityDistance | None = _USE_ACTIVITY_DISTANCE,
    course_distance: float | None,
    elevation_source_suffix: str = "",
) -> dict[str, object]:
    map_geometry, solid_geometry = _activity_geometry_distances(
        activity,
        coordinate_overrides,
    )
    elevation_gain, elevation_source = _activity_elevation_gain(activity)
    embedded_distance = (
        activity.recorded_distance_m
        if isinstance(embedded_distance_override, _UseActivityDistance)
        else embedded_distance_override
    )
    return {
        "id": row_id,
        "label": label,
        "embedded_distance_m": embedded_distance,
        "map_geometry_distance_m": map_geometry,
        "solid_geometry_distance_m": solid_geometry,
        "geometry_delta_vs_course_m": (
            map_geometry - course_distance
            if map_geometry is not None and course_distance is not None
            else None
        ),
        "elevation_gain_m": elevation_gain,
        "elevation_gain_source": elevation_source + elevation_source_suffix,
    }


def _activity_geometry_distances(
    activity: ActivityData,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None,
) -> tuple[float | None, float | None]:
    map_geometry_m = 0.0
    solid_geometry_m = 0.0
    map_edge_count = 0
    solid_edge_count = 0
    previous_positioned: tuple[ActivityRecord, float, float] | None = None
    previous_record: tuple[ActivityRecord, float, float] | None = None
    for record in activity.records:
        latitude, longitude = _record_coordinates(record, coordinate_overrides)
        if latitude is None or longitude is None:
            previous_record = None
            continue
        if (
            previous_positioned is not None
            and previous_positioned[0].continuity_id == record.continuity_id
        ):
            map_geometry_m += geodesic_distance_m(
                previous_positioned[1],
                previous_positioned[2],
                latitude,
                longitude,
            )
            map_edge_count += 1
        if previous_record is not None and previous_record[0].continuity_id == record.continuity_id:
            solid_geometry_m += geodesic_distance_m(
                previous_record[1],
                previous_record[2],
                latitude,
                longitude,
            )
            solid_edge_count += 1
        positioned = record, latitude, longitude
        previous_positioned = positioned
        previous_record = positioned
    return (
        map_geometry_m if map_edge_count else None,
        solid_geometry_m if solid_edge_count else None,
    )


def _activity_elevation_gain(activity: ActivityData) -> tuple[float | None, str]:
    session_totals = [
        value
        for session in activity.sessions
        if (value := _finite_number(session.fields.get("total_ascent"))) is not None
    ]
    if session_totals:
        return sum(session_totals), "FIT session.total_ascent"
    gain = _positive_elevation_deltas(
        tuple((record.continuity_id, record.altitude) for record in activity.records)
    )
    return (
        (gain, "record altitude positive deltas (unsmoothed)")
        if gain is not None
        else (None, "unavailable")
    )


def _course_elevation_gain_m(course: CourseData) -> float | None:
    return _positive_elevation_deltas(
        tuple(
            (segment.index, point.elevation_m)
            for segment in course.segments
            for point in segment.points
        )
    )


def _positive_elevation_deltas(
    observations: tuple[tuple[int, float | None], ...],
) -> float | None:
    gain = 0.0
    edge_count = 0
    previous: tuple[int, float] | None = None
    for continuity_id, elevation in observations:
        if elevation is None or not isfinite(elevation):
            previous = None
            continue
        if previous is not None and previous[0] == continuity_id:
            gain += max(0.0, elevation - previous[1])
            edge_count += 1
        previous = continuity_id, elevation
    return gain if edge_count else None


def _missing_position_runs(
    activity: ActivityData,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None = None,
) -> list[dict[str, object]]:
    runs: list[dict[str, object]] = []
    records = activity.records
    start = 0
    while start < len(records):
        continuity_id = records[start].continuity_id
        stop = start + 1
        while stop < len(records) and records[stop].continuity_id == continuity_id:
            stop += 1
        cursor = start
        while cursor < stop:
            if _record_has_position(records[cursor], coordinate_overrides):
                cursor += 1
                continue
            run_start = cursor
            while cursor < stop and not _record_has_position(records[cursor], coordinate_overrides):
                cursor += 1
            run_end = cursor - 1
            before = records[run_start - 1] if run_start > start else None
            after = records[cursor] if cursor < stop else None
            runs.append(
                _missing_run_report(
                    records[run_start],
                    records[run_end],
                    before,
                    after,
                    coordinate_overrides,
                )
            )
        start = stop
    return runs


def _missing_run_report(
    first_missing: ActivityRecord,
    last_missing: ActivityRecord,
    before: ActivityRecord | None,
    after: ActivityRecord | None,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None,
) -> dict[str, object]:
    elapsed_seconds = (
        (after.timestamp - before.timestamp).total_seconds()
        if before is not None
        and after is not None
        and before.timestamp is not None
        and after.timestamp is not None
        else None
    )
    chord_distance_m: float | None = None
    if before is not None and after is not None:
        before_latitude, before_longitude = _record_coordinates(before, coordinate_overrides)
        after_latitude, after_longitude = _record_coordinates(after, coordinate_overrides)
        if (
            before_latitude is not None
            and before_longitude is not None
            and after_latitude is not None
            and after_longitude is not None
        ):
            chord_distance_m = geodesic_distance_m(
                before_latitude,
                before_longitude,
                after_latitude,
                after_longitude,
            )
    recorded_distance_delta_m = (
        after.distance - before.distance
        if before is not None
        and after is not None
        and before.distance is not None
        and after.distance is not None
        else None
    )
    return {
        "start_record_index": first_missing.index,
        "end_record_index": last_missing.index,
        "missing_record_count": last_missing.index - first_missing.index + 1,
        "continuity_id": first_missing.continuity_id,
        "anchor_before_record_index": before.index if before is not None else None,
        "anchor_after_record_index": after.index if after is not None else None,
        "anchor_elapsed_seconds": elapsed_seconds,
        "straight_line_distance_m": chord_distance_m,
        "straight_line_speed_mps": (
            chord_distance_m / elapsed_seconds
            if chord_distance_m is not None
            and elapsed_seconds is not None
            and elapsed_seconds > 0.0
            else None
        ),
        "recorded_distance_delta_m": recorded_distance_delta_m,
    }


def _record_has_position(
    record: ActivityRecord,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None,
) -> bool:
    latitude, longitude = _record_coordinates(record, coordinate_overrides)
    return latitude is not None and longitude is not None


def _record_coordinates(
    record: ActivityRecord,
    coordinate_overrides: Mapping[int, tuple[float, float]] | None,
) -> tuple[float | None, float | None]:
    if coordinate_overrides is not None and record.index in coordinate_overrides:
        return coordinate_overrides[record.index]
    return record.latitude, record.longitude


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _course_track(course: CourseData) -> dict[str, object]:
    return {
        "segment_count": len(course.segments),
        "point_count": course.point_count,
        "segments": [
            [[point.latitude, point.longitude] for point in segment.points]
            for segment in course.segments
        ],
    }


def _record_row(
    record: ActivityRecord,
    x_value: float,
    apparent_speed_mps: float | None,
) -> list[object]:
    return [
        record.index,
        x_value,
        record.latitude,
        record.longitude,
        record.altitude,
        record.distance,
        record.speed,
        apparent_speed_mps,
        record.heart_rate,
        record.continuity_id,
    ]


def _chart_axis(activity: ActivityData) -> dict[str, str]:
    timed = bool(activity.records) and all(
        record.timestamp is not None for record in activity.records
    )
    return {
        "mode": "elapsed_time" if timed else "record_index",
        "label": "Elapsed time (s)" if timed else "Record index",
    }


def _record_x(record: ActivityRecord, first_timestamp: datetime | None) -> float:
    if first_timestamp is not None:
        if record.timestamp is None:
            raise HtmlReportError("inconsistent timestamp axis")
        return (record.timestamp - first_timestamp).total_seconds()
    return float(record.index)


def _compact_repair_report(
    plan: RepairPlan,
    course: CourseData,
    config: CourseReconstructionConfig,
    minimum_confidence: IntegrityConfidence,
) -> dict[str, object]:
    report = repair_report(
        plan,
        course,
        config,
        minimum_confidence=minimum_confidence,
    )
    interval_plans = report.get("interval_plans")
    if isinstance(interval_plans, list):
        for raw_interval in interval_plans:
            if isinstance(raw_interval, dict):
                updates = raw_interval.pop("coordinate_updates", [])
                raw_interval["coordinate_update_count"] = (
                    len(updates) if isinstance(updates, list) else 0
                )
    return report


def _write_payload(
    payload: Mapping[str, object],
    output_path: str | Path,
    *,
    overwrite: bool,
) -> Path:
    destination = ensure_html_output_available(output_path, overwrite=overwrite)
    template = (
        files("warpbuster.report")
        .joinpath("assets")
        .joinpath("report.html")
        .read_text(encoding="utf-8")
    )
    encoded_payload = _safe_json(payload)
    rendered = template.replace("__WARPBUSTER_REPORT_DATA__", encoded_payload)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        if overwrite:
            os.replace(temporary_path, destination)
        else:
            try:
                os.link(temporary_path, destination)
            except FileExistsError as error:
                raise HtmlReportError(f"HTML output already exists: {destination}") from error
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return destination


def _safe_json(payload: Mapping[str, object]) -> str:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
