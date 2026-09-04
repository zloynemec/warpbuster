"""Interactive local HTML reports for integrity analysis and FIT repair."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from importlib.resources import files
from itertools import pairwise
from math import isfinite
from pathlib import Path

from warpbuster import __version__
from warpbuster.config import CourseReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.fit import FitWriteResult
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityReport,
    IntervalDetectionKind,
    TransitionClassification,
    TransitionResult,
)
from warpbuster.models.reconstruction import CourseData, RepairPlan, RepairSelection
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.report.analyze import analyze_report
from warpbuster.report.fit import write_result_report
from warpbuster.report.gaps import distance_policy
from warpbuster.report.inspect import inspect_report
from warpbuster.report.repair import repair_report


class _UseActivityDistance:
    """Sentinel type selecting ActivityData.recorded_distance_m."""


_USE_ACTIVITY_DISTANCE = _UseActivityDistance()
type _Position = tuple[float | None, float | None]

_DIAGNOSTIC_KIND_PRIORITY = {
    "corrupted_interval": 0,
    "one_sided_diagnostic": 1,
    "abnormal_transition_run": 2,
    "geometry_warning": 3,
    "vertical_warning": 4,
    "missing_position_run": 5,
}


@dataclass(frozen=True, slots=True)
class _DiagnosticRegionDraft:
    """One presentation-only projection of existing detector or data-quality evidence."""

    kind: str
    detector_stage: str
    start_record_index: int
    end_record_index: int
    status: str
    confidence: str
    repair_eligible: bool
    reasons: tuple[str, ...]
    evidence: tuple[Mapping[str, object], ...]
    metrics: Mapping[str, object]
    source_key: str


class HtmlReportError(ValueError):
    """Raised when an HTML report cannot be produced without overwriting data."""


def write_analyze_html(
    activity: ActivityData,
    integrity: IntegrityReport,
    output_path: str | Path,
    *,
    course: CourseData | None = None,
    overwrite: bool = False,
) -> Path:
    """Write one interactive report, optionally replacing its destination atomically."""
    ensure_html_output_available(
        output_path,
        overwrite=overwrite,
        protected_paths=(activity.preservation.source_path,)
        + ((course.source_path,) if course is not None else ()),
    )
    missing_position_runs = _missing_position_runs(activity)
    payload = _base_payload(activity, integrity, report_kind="analyze")
    payload["tracks"] = {
        "original": _activity_track(activity, integrity),
        "repaired": None,
        "candidate": None,
        "course": _course_track(course) if course is not None else None,
    }
    payload["repair"] = None
    payload["write_result"] = None
    payload["metrics_comparison"] = _metrics_comparison(activity, course=course)
    payload["missing_position_runs"] = missing_position_runs
    payload["diagnostic_regions"] = _diagnostic_regions(
        activity,
        integrity,
        missing_position_runs,
    )
    payload["activity_performance"] = {
        "source_label": (
            "Original FIT" if activity.preservation.source_format.value == "fit" else "Original GPX"
        ),
        **_activity_performance(activity),
    }
    payload["repaired_performance"] = None
    return _write_payload(payload, output_path, overwrite=overwrite)


def write_repair_html(
    activity: ActivityData,
    integrity: IntegrityReport,
    course: CourseData | None,
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
    ensure_html_output_available(
        output_path,
        overwrite=overwrite,
        protected_paths=(activity.preservation.source_path,)
        + ((course.source_path,) if course is not None else ())
        + ((fixed_activity.preservation.source_path,) if fixed_activity is not None else ()),
    )
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
        "course": _course_track(course) if course is not None else None,
    }
    payload["repair"] = _compact_repair_report(
        replace(plan, output_written=fixed_activity is not None),
        course,
        config,
        minimum_confidence,
    )
    payload["write_result"] = (
        write_result_report(write_result) if write_result is not None else None
    )
    coordinate_overrides = _selection_coordinate_overrides(selection)
    preserves_embedded_distance = all(
        candidate.preserve_recorded_distance for candidate in selection.selected_interval_plans
    )
    payload["metrics_comparison"] = _metrics_comparison(
        activity,
        course=course,
        comparison_activity=fixed_activity,
        comparison_coordinate_overrides=(coordinate_overrides if fixed_activity is None else None),
        comparison_preserves_embedded_distance=preserves_embedded_distance,
    )
    payload["missing_position_runs"] = (
        _missing_position_runs(fixed_activity)
        if fixed_activity is not None
        else _missing_position_runs(activity, coordinate_overrides)
    )
    payload["diagnostic_regions"] = _diagnostic_regions(
        activity,
        integrity,
        _missing_position_runs(activity),
    )
    payload["activity_performance"] = None
    payload["original_performance"] = {
        key: value
        for key, value in _activity_performance(activity).items()
        if key in {"distance_m", "timer_duration_seconds", "average_pace_seconds_per_km"}
    }
    payload["repaired_performance"] = (
        {"source_label": "Repaired FIT", **_activity_performance(fixed_activity)}
        if fixed_activity is not None
        else None
    )
    payload["gap_markers"] = _gap_markers(activity, plan, selection)
    payload["distance_quality"] = distance_policy(selection)["quality"]
    payload["coordinate_update_ranges"] = [
        list(bounds)
        for candidate in selection.selected_interval_plans
        for bounds in candidate.reconstruction_scope_ranges
    ]
    payload["output_summary"] = (
        {
            "recorded_distance_m": fixed_activity.recorded_distance_m,
            "missing_position_count": sum(
                r.latitude is None or r.longitude is None for r in fixed_activity.records
            ),
        }
        if fixed_activity is not None
        else None
    )
    return _write_payload(payload, output_path, overwrite=overwrite)


def ensure_html_output_available(
    output_path: str | Path,
    *,
    overwrite: bool = False,
    protected_paths: tuple[Path, ...] = (),
) -> Path:
    """Validate the destination before another operation creates side effects."""
    destination = Path(output_path)
    if any(destination.resolve() == path.resolve() for path in protected_paths):
        raise HtmlReportError("HTML report path must differ from input and FIT output paths")
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
    updates = _selection_coordinate_overrides(selection)
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
) -> dict[int, _Position]:
    return {
        **{item.record_index: (None, None) for item in selection.invalidations},
        **{
            update.record_index: (update.candidate_latitude, update.candidate_longitude)
            for interval in selection.selected_interval_plans
            for update in interval.coordinate_updates
        },
    }


def _gap_markers(
    activity: ActivityData,
    plan: RepairPlan,
    selection: RepairSelection,
) -> list[dict[str, object]]:
    """G-numbered reconstruction scopes, independent of detector-region numbering."""
    candidates = {
        (candidate.interval.start_record_index, candidate.interval.end_record_index): candidate
        for candidate in plan.interval_plans
    }
    overrides = _selection_coordinate_overrides(selection)
    result = []
    for number, gap in enumerate(plan.gaps, 1):
        candidate = candidates.get((gap.start_record_index, gap.end_record_index))
        points: list[tuple[float, float]] = []
        for anchor_index in (gap.anchor_before_record_index,):
            if anchor_index is not None:
                lat, lon = _record_coordinates(activity.records[anchor_index], overrides)
                if lat is not None and lon is not None:
                    points.append((lat, lon))
        if candidate is not None:
            points.extend(
                (update.candidate_latitude, update.candidate_longitude)
                for update in candidate.coordinate_updates
            )
        if gap.anchor_after_record_index is not None:
            lat, lon = _record_coordinates(
                activity.records[gap.anchor_after_record_index], overrides
            )
            if lat is not None and lon is not None:
                points.append((lat, lon))
        result.append(
            {
                "gap_id": gap.gap_id,
                "number": number,
                "marker": points[len(points) // 2] if points else None,
                "bounds": (
                    [
                        [min(p[0] for p in points), min(p[1] for p in points)],
                        [max(p[0] for p in points), max(p[1] for p in points)],
                    ]
                    if points
                    else None
                ),
                # Unresolved anchors do not define a known path: never join them solidly.
                "candidate_geometry": points if candidate is not None else [],
            }
        )
    return result


def _metrics_comparison(
    original: ActivityData,
    *,
    course: CourseData | None = None,
    comparison_activity: ActivityData | None = None,
    comparison_coordinate_overrides: Mapping[int, _Position] | None = None,
    comparison_preserves_embedded_distance: bool = False,
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
                embedded_distance_override=(
                    _USE_ACTIVITY_DISTANCE if comparison_preserves_embedded_distance else None
                ),
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
    coordinate_overrides: Mapping[int, _Position] | None = None,
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
    coordinate_overrides: Mapping[int, _Position] | None,
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


def _activity_performance(activity: ActivityData) -> dict[str, object]:
    """Summarize one actual activity's pace, ascent, and descent by kilometre."""
    timer_duration_seconds, timer_source = _timer_duration(activity)
    distance_m = activity.recorded_distance_m
    average_pace_seconds_per_km = (
        timer_duration_seconds / distance_m * 1_000.0
        if timer_duration_seconds is not None
        and timer_duration_seconds > 0.0
        and distance_m is not None
        and distance_m > 0.0
        else None
    )
    total_ascent_m, total_ascent_source = _activity_elevation_gain(activity)
    total_descent_m, total_descent_source = _activity_elevation_descent(activity)
    splits = _kilometre_splits(activity)
    return {
        "distance_m": distance_m,
        "timer_duration_seconds": timer_duration_seconds,
        "timer_source": timer_source,
        "average_pace_seconds_per_km": average_pace_seconds_per_km,
        "total_ascent_m": total_ascent_m,
        "total_ascent_source": total_ascent_source,
        "total_descent_m": total_descent_m,
        "total_descent_source": total_descent_source,
        "split_ascent_total_m": _sum_available_split_metric(splits, "ascent_m"),
        "split_descent_total_m": _sum_available_split_metric(splits, "descent_m"),
        "split_count": len(splits),
        "splits": splits,
        "notes": [
            "Average pace uses FIT session.total_timer_time and summary distance when available.",
            "Kilometre pace uses elapsed record timestamps interpolated at recorded-distance boundaries.",
            "Each elevation pair independently sums every positive altitude delta as ascent and every negative delta magnitude as descent inside that distance split.",
            "FIT total ascent/descent may differ from the unsmoothed record-altitude sums used by the kilometre bars.",
            "The final partial kilometre is normalized to min/km and labelled with its actual distance range.",
        ],
    }


def _repaired_performance(activity: ActivityData) -> dict[str, object]:
    """Compatibility wrapper for tests and callers using the original helper name."""
    return _activity_performance(activity)


def _activity_elevation_descent(activity: ActivityData) -> tuple[float | None, str]:
    session_totals = [
        value
        for session in activity.sessions
        if (value := _finite_number(session.fields.get("total_descent"))) is not None
    ]
    if session_totals:
        return sum(session_totals), "FIT session.total_descent"
    descent = _negative_elevation_deltas(
        tuple((record.continuity_id, record.altitude) for record in activity.records)
    )
    return (
        (descent, "record altitude negative deltas (unsmoothed)")
        if descent is not None
        else (None, "unavailable")
    )


def _negative_elevation_deltas(
    observations: tuple[tuple[int, float | None], ...],
) -> float | None:
    descent = 0.0
    edge_count = 0
    previous: tuple[int, float] | None = None
    for continuity_id, elevation in observations:
        if elevation is None or not isfinite(elevation):
            previous = None
            continue
        if previous is not None and previous[0] == continuity_id:
            descent += max(0.0, previous[1] - elevation)
            edge_count += 1
        previous = continuity_id, elevation
    return descent if edge_count else None


def _timer_duration(activity: ActivityData) -> tuple[float | None, str]:
    timer_totals = [
        value
        for session in activity.sessions
        if (value := _finite_number(session.fields.get("total_timer_time"))) is not None
        and value >= 0.0
    ]
    if timer_totals:
        return sum(timer_totals), "FIT session.total_timer_time"
    timestamps = [record.timestamp for record in activity.records if record.timestamp is not None]
    if len(timestamps) >= 2:
        return (
            (timestamps[-1] - timestamps[0]).total_seconds(),
            "record timestamp elapsed time",
        )
    return None, "unavailable"


def _kilometre_splits(activity: ActivityData) -> list[dict[str, object]]:
    if len(activity.records) < 2 or any(
        record.distance is None or not isfinite(record.distance) or record.timestamp is None
        for record in activity.records
    ):
        return []
    first = activity.records[0]
    if first.distance is None or first.timestamp is None:
        return []
    origin_distance = first.distance
    origin_timestamp = first.timestamp
    samples: list[tuple[float, float, float | None]] = []
    for record in activity.records:
        if record.distance is None or record.timestamp is None:
            return []
        relative_distance = record.distance - origin_distance
        elapsed_seconds = (record.timestamp - origin_timestamp).total_seconds()
        altitude = (
            record.altitude if record.altitude is not None and isfinite(record.altitude) else None
        )
        if relative_distance < 0.0 or elapsed_seconds < 0.0:
            return []
        if samples and (relative_distance < samples[-1][0] or elapsed_seconds < samples[-1][1]):
            return []
        samples.append((relative_distance, elapsed_seconds, altitude))
    total_distance = samples[-1][0]
    if total_distance <= 0.0:
        return []
    boundaries = [0.0]
    boundary = 1_000.0
    while boundary < total_distance:
        boundaries.append(boundary)
        boundary += 1_000.0
    boundaries.append(total_distance)
    states = [_sample_at_distance(samples, distance_m) for distance_m in boundaries]
    splits: list[dict[str, object]] = []
    for index, ((start_time, start_altitude), (end_time, end_altitude)) in enumerate(
        pairwise(states),
        start=1,
    ):
        start_distance = boundaries[index - 1]
        end_distance = boundaries[index]
        split_distance = end_distance - start_distance
        elapsed_seconds = end_time - start_time
        ascent_m, descent_m = _elevation_totals_between(
            samples,
            start_distance,
            end_distance,
            start_altitude,
            end_altitude,
        )
        splits.append(
            {
                "index": index,
                "start_distance_m": start_distance,
                "end_distance_m": end_distance,
                "distance_m": split_distance,
                "complete_kilometre": abs(split_distance - 1_000.0) < 1e-6,
                "elapsed_seconds": elapsed_seconds,
                "pace_seconds_per_km": (
                    elapsed_seconds / split_distance * 1_000.0
                    if elapsed_seconds >= 0.0 and split_distance > 0.0
                    else None
                ),
                "ascent_m": ascent_m,
                "descent_m": descent_m,
            }
        )
    return splits


def _elevation_totals_between(
    samples: list[tuple[float, float, float | None]],
    start_distance_m: float,
    end_distance_m: float,
    start_altitude_m: float | None,
    end_altitude_m: float | None,
) -> tuple[float | None, float | None]:
    altitudes = [
        start_altitude_m,
        *(
            altitude
            for distance_m, _elapsed_seconds, altitude in samples
            if start_distance_m < distance_m < end_distance_m
        ),
        end_altitude_m,
    ]
    ascent_m = 0.0
    descent_m = 0.0
    edge_count = 0
    previous: float | None = None
    for altitude in altitudes:
        if altitude is None:
            previous = None
            continue
        if previous is not None:
            delta = altitude - previous
            ascent_m += max(0.0, delta)
            descent_m += max(0.0, -delta)
            edge_count += 1
        previous = altitude
    return (ascent_m, descent_m) if edge_count else (None, None)


def _sum_available_split_metric(
    splits: list[dict[str, object]],
    field: str,
) -> float | None:
    values = [
        float(value)
        for split in splits
        if (value := split.get(field)) is not None
        and isinstance(value, int | float)
        and not isinstance(value, bool)
        and isfinite(value)
    ]
    return sum(values) if values else None


def _sample_at_distance(
    samples: list[tuple[float, float, float | None]],
    target_distance_m: float,
) -> tuple[float, float | None]:
    previous = samples[0]
    if target_distance_m <= previous[0]:
        return previous[1], previous[2]
    for current in samples[1:]:
        if target_distance_m > current[0]:
            previous = current
            continue
        if target_distance_m <= previous[0] or current[0] == previous[0]:
            return previous[1], previous[2]
        fraction = (target_distance_m - previous[0]) / (current[0] - previous[0])
        elapsed_seconds = previous[1] + fraction * (current[1] - previous[1])
        altitude = (
            previous[2] + fraction * (current[2] - previous[2])
            if previous[2] is not None and current[2] is not None
            else None
        )
        return elapsed_seconds, altitude
    return samples[-1][1], samples[-1][2]


def _diagnostic_regions(
    activity: ActivityData,
    integrity: IntegrityReport,
    missing_position_runs: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Project authoritative findings into stable, numbered presentation regions."""
    drafts: list[_DiagnosticRegionDraft] = []
    covered_transition_pairs: set[tuple[int, int]] = set()

    for interval in integrity.corrupted_intervals:
        interval_pairs = _covered_transition_pairs(
            integrity,
            interval.start_record_index,
            interval.end_record_index,
        )
        interval_pairs.add(
            (
                interval.entry_transition.from_record_index,
                interval.entry_transition.to_record_index,
            )
        )
        if interval.exit_transition is not None:
            interval_pairs.add(
                (
                    interval.exit_transition.from_record_index,
                    interval.exit_transition.to_record_index,
                )
            )
        covered_transition_pairs.update(interval_pairs)
        evidence = tuple(
            _transition_evidence(transition)
            for transition in integrity.transitions
            if (transition.from_record_index, transition.to_record_index) in interval_pairs
        )
        drafts.append(
            _DiagnosticRegionDraft(
                kind="corrupted_interval",
                detector_stage=(
                    "physical_tail_reachability"
                    if interval.detection_kind is IntervalDetectionKind.UNREACHABLE_TAIL
                    else "one_sided_gnss_clusters"
                    if interval.detection_kind is IntervalDetectionKind.ONE_SIDED_CLUSTER
                    else "spoofing_islands"
                ),
                start_record_index=interval.start_record_index,
                end_record_index=interval.end_record_index,
                status="corrupted",
                confidence=interval.confidence.value,
                repair_eligible=True,
                reasons=tuple(reason.value for reason in interval.reasons),
                evidence=evidence,
                metrics={
                    "record_count": interval.record_count,
                    "detection_kind": interval.detection_kind.value,
                    "trusted_before_record_index": interval.trusted_before_record_index,
                    "trusted_after_record_index": interval.trusted_after_record_index,
                    "reachability": asdict(interval.reachability)
                    if interval.reachability
                    else None,
                    "bridge": {
                        "elapsed_seconds": interval.bridge.elapsed_seconds,
                        "distance_m": interval.bridge.distance_m,
                        "apparent_speed_mps": interval.bridge.apparent_speed_mps,
                        "maximum_plausible_speed_mps": (
                            interval.bridge.maximum_plausible_speed_mps
                        ),
                    }
                    if interval.bridge is not None
                    else None,
                },
                source_key=(
                    f"interval:{interval.detection_kind.value}:"
                    f"{interval.start_record_index}:{interval.end_record_index}"
                ),
            )
        )

    for cluster_index, cluster in enumerate(
        integrity.one_sided_search_diagnostics.retained_clusters
    ):
        cluster_end = (
            cluster.end_record_index
            if cluster.end_record_index is not None
            else cluster.start_record_index
        )
        represented_by_interval = any(
            interval.detection_kind is IntervalDetectionKind.ONE_SIDED_CLUSTER
            and interval.start_record_index == cluster.start_record_index
            and interval.end_record_index == cluster_end
            for interval in integrity.corrupted_intervals
        )
        if represented_by_interval:
            continue
        cluster_pairs = _covered_transition_pairs(
            integrity,
            cluster.start_record_index,
            cluster_end,
        )
        covered_transition_pairs.update(cluster_pairs)
        bridge = cluster.bridge
        drafts.append(
            _DiagnosticRegionDraft(
                kind="one_sided_diagnostic",
                detector_stage="one_sided_gnss_clusters",
                start_record_index=cluster.start_record_index,
                end_record_index=cluster_end,
                status="reconstructable" if cluster.reconstructable else "unresolved",
                confidence=cluster.confidence.value,
                repair_eligible=cluster.reconstructable,
                reasons=tuple(reason.value for reason in cluster.reasons),
                evidence=(
                    {
                        "entity": "one_sided_cluster",
                        "retained_cluster_index": cluster_index,
                        "reported_end_record_index": cluster.end_record_index,
                    },
                    *(
                        _transition_evidence(transition)
                        for transition in integrity.transitions
                        if (transition.from_record_index, transition.to_record_index)
                        in cluster_pairs
                    ),
                ),
                metrics={
                    "reported_end_record_index": cluster.end_record_index,
                    "record_count": cluster.record_count,
                    "trusted_before_record_index": cluster.trusted_before_record_index,
                    "trusted_after_record_index": cluster.trusted_after_record_index,
                    "missing_position_record_count": cluster.missing_position_record_count,
                    "impossible_transition_count": cluster.impossible_transition_count,
                    "suspicious_transition_count": cluster.suspicious_transition_count,
                    "positioned_component_count": cluster.positioned_component_count,
                    "tainted_positioned_component_count": (
                        cluster.tainted_positioned_component_count
                    ),
                    "anchor_before_normal_transition_count": (
                        cluster.anchor_before_normal_transition_count
                    ),
                    "anchor_after_normal_transition_count": (
                        cluster.anchor_after_normal_transition_count
                    ),
                    "anchor_required_normal_transition_count": (
                        cluster.anchor_required_normal_transition_count
                    ),
                    "bridge": (
                        {
                            "elapsed_seconds": bridge.elapsed_seconds,
                            "distance_m": bridge.distance_m,
                            "apparent_speed_mps": bridge.apparent_speed_mps,
                            "maximum_plausible_speed_mps": (bridge.maximum_plausible_speed_mps),
                        }
                        if bridge is not None
                        else None
                    ),
                },
                source_key=(
                    f"one-sided:{cluster.start_record_index}:"
                    f"{cluster.end_record_index}:{cluster_index}"
                ),
            )
        )

    raw_abnormal = tuple(
        transition
        for transition in integrity.transitions
        if transition.classification
        in (TransitionClassification.SUSPICIOUS, TransitionClassification.IMPOSSIBLE)
        and (
            transition.from_record_index,
            transition.to_record_index,
        )
        not in covered_transition_pairs
    )
    for run_index, transitions in enumerate(_adjacent_transition_runs(activity, raw_abnormal)):
        classifications = {transition.classification for transition in transitions}
        status = (
            TransitionClassification.IMPOSSIBLE.value
            if TransitionClassification.IMPOSSIBLE in classifications
            else TransitionClassification.SUSPICIOUS.value
        )
        elapsed_values = tuple(transition.elapsed_seconds for transition in transitions)
        speeds = tuple(
            transition.apparent_speed_mps
            for transition in transitions
            if transition.apparent_speed_mps is not None
        )
        drafts.append(
            _DiagnosticRegionDraft(
                kind="abnormal_transition_run",
                detector_stage="local_transitions",
                start_record_index=transitions[0].from_record_index,
                end_record_index=transitions[-1].to_record_index,
                status=status,
                confidence=(
                    IntegrityConfidence.HIGH.value
                    if status == TransitionClassification.IMPOSSIBLE.value
                    else IntegrityConfidence.LOW.value
                ),
                repair_eligible=False,
                reasons=_unique_strings(
                    reason.value for transition in transitions for reason in transition.reasons
                ),
                evidence=tuple(_transition_evidence(item) for item in transitions),
                metrics={
                    "transition_count": len(transitions),
                    "classification_counts": {
                        classification.value: sum(
                            transition.classification is classification
                            for transition in transitions
                        )
                        for classification in (
                            TransitionClassification.SUSPICIOUS,
                            TransitionClassification.IMPOSSIBLE,
                        )
                    },
                    "total_transition_distance_m": sum(
                        transition.distance_m for transition in transitions
                    ),
                    "total_elapsed_seconds": (
                        sum(float(value) for value in elapsed_values if value is not None)
                        if all(value is not None for value in elapsed_values)
                        else None
                    ),
                    "maximum_apparent_speed_mps": max(speeds) if speeds else None,
                },
                source_key=(
                    f"transition-run:{transitions[0].from_record_index}:"
                    f"{transitions[-1].to_record_index}:{run_index}"
                ),
            )
        )

    for warning_index, geometry_warning in enumerate(integrity.geometry_warnings):
        drafts.append(
            _DiagnosticRegionDraft(
                kind="geometry_warning",
                detector_stage="geometry_gap_diagnostics",
                start_record_index=geometry_warning.start_record_index,
                end_record_index=geometry_warning.end_record_index,
                status=geometry_warning.kind.value,
                confidence=geometry_warning.confidence.value,
                repair_eligible=False,
                reasons=tuple(reason.value for reason in geometry_warning.reasons),
                evidence=(
                    {
                        "entity": "geometry_warning",
                        "warning_index": warning_index,
                    },
                ),
                metrics={
                    "position_record_count": geometry_warning.position_record_count,
                    "chord_distance_m": geometry_warning.chord_distance_m,
                    "path_distance_m": geometry_warning.path_distance_m,
                    "path_to_chord_ratio": geometry_warning.path_to_chord_ratio,
                    "max_cross_track_deviation_m": (geometry_warning.max_cross_track_deviation_m),
                    "timestamps_available": geometry_warning.timestamps_available,
                },
                source_key=(
                    f"geometry:{geometry_warning.start_record_index}:"
                    f"{geometry_warning.end_record_index}:{warning_index}"
                ),
            )
        )

    for warning_index, vertical_warning in enumerate(integrity.vertical_warnings):
        drafts.append(
            _DiagnosticRegionDraft(
                kind="vertical_warning",
                detector_stage="vertical_plausibility",
                start_record_index=vertical_warning.start_record_index,
                end_record_index=vertical_warning.end_record_index,
                status="sensor_consistency_warning",
                confidence=vertical_warning.confidence.value,
                repair_eligible=False,
                reasons=tuple(reason.value for reason in vertical_warning.reasons),
                evidence=(
                    {
                        "entity": "vertical_warning",
                        "warning_index": warning_index,
                    },
                ),
                metrics={
                    "transition_count": vertical_warning.transition_count,
                    "elapsed_seconds": vertical_warning.elapsed_seconds,
                    "altitude_delta_m": vertical_warning.altitude_delta_m,
                    "maximum_absolute_vertical_speed_mps": (
                        vertical_warning.maximum_absolute_vertical_speed_mps
                    ),
                },
                source_key=(
                    f"vertical:{vertical_warning.start_record_index}:"
                    f"{vertical_warning.end_record_index}:{warning_index}"
                ),
            )
        )

    for run_index, missing_run in enumerate(missing_position_runs):
        start_record_index = missing_run["start_record_index"]
        end_record_index = missing_run["end_record_index"]
        if not isinstance(start_record_index, int) or not isinstance(end_record_index, int):
            raise AssertionError("missing-position report must contain integer boundaries")
        drafts.append(
            _DiagnosticRegionDraft(
                kind="missing_position_run",
                detector_stage="data_quality",
                start_record_index=start_record_index,
                end_record_index=end_record_index,
                status="missing",
                confidence=IntegrityConfidence.LOW.value,
                repair_eligible=False,
                reasons=("position_unavailable",),
                evidence=(
                    {
                        "entity": "missing_position_run",
                        "missing_run_index": run_index,
                    },
                ),
                metrics=dict(missing_run),
                source_key=f"missing:{start_record_index}:{end_record_index}:{run_index}",
            )
        )

    ordered = sorted(
        drafts,
        key=lambda draft: (
            _continuity_id(activity, draft.start_record_index),
            draft.start_record_index,
            draft.end_record_index,
            _DIAGNOSTIC_KIND_PRIORITY[draft.kind],
            draft.source_key,
        ),
    )
    reports = [
        _diagnostic_region_report(activity, display_id, draft)
        for display_id, draft in enumerate(ordered, start=1)
    ]
    overlap_ids: list[list[int]] = [[] for _region in reports]
    active: list[tuple[int, _DiagnosticRegionDraft]] = []
    active_continuity_id: int | None = None
    for region_index, draft in enumerate(ordered):
        continuity_id = _continuity_id(activity, draft.start_record_index)
        if continuity_id != active_continuity_id:
            active = []
            active_continuity_id = continuity_id
        active = [
            (other_index, other_draft)
            for other_index, other_draft in active
            if other_draft.end_record_index >= draft.start_record_index
        ]
        for other_index, _other_draft in active:
            overlap_ids[other_index].append(region_index + 1)
            overlap_ids[region_index].append(other_index + 1)
        active.append((region_index, draft))
    for region, display_ids in zip(reports, overlap_ids, strict=True):
        region["overlaps_display_ids"] = sorted(display_ids)
    return reports


def _covered_transition_pairs(
    integrity: IntegrityReport,
    start_record_index: int,
    end_record_index: int,
) -> set[tuple[int, int]]:
    return {
        (transition.from_record_index, transition.to_record_index)
        for transition in integrity.transitions
        if transition.classification
        in (TransitionClassification.SUSPICIOUS, TransitionClassification.IMPOSSIBLE)
        and (
            (
                transition.from_record_index >= start_record_index
                and transition.to_record_index <= end_record_index
            )
            or transition.to_record_index == start_record_index
        )
    }


def _adjacent_transition_runs(
    activity: ActivityData,
    transitions: tuple[TransitionResult, ...],
) -> tuple[tuple[TransitionResult, ...], ...]:
    runs: list[list[TransitionResult]] = []
    for transition in sorted(
        transitions,
        key=lambda item: (item.from_record_index, item.to_record_index),
    ):
        if (
            runs
            and runs[-1][-1].to_record_index == transition.from_record_index
            and _continuity_id(activity, runs[-1][0].from_record_index)
            == _continuity_id(activity, transition.to_record_index)
        ):
            runs[-1].append(transition)
        else:
            runs.append([transition])
    return tuple(tuple(run) for run in runs)


def _transition_evidence(transition: TransitionResult) -> dict[str, object]:
    return {
        "entity": "transition",
        "from_record_index": transition.from_record_index,
        "to_record_index": transition.to_record_index,
        "from_timestamp": (
            transition.from_timestamp.isoformat() if transition.from_timestamp is not None else None
        ),
        "to_timestamp": (
            transition.to_timestamp.isoformat() if transition.to_timestamp is not None else None
        ),
        "elapsed_seconds": transition.elapsed_seconds,
        "distance_m": transition.distance_m,
        "apparent_speed_mps": transition.apparent_speed_mps,
        "classification": transition.classification.value,
        "reasons": [reason.value for reason in transition.reasons],
    }


def _unique_strings(values: Iterable[object]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values))


def _diagnostic_region_report(
    activity: ActivityData,
    display_id: int,
    draft: _DiagnosticRegionDraft,
) -> dict[str, object]:
    records = activity.records[draft.start_record_index : draft.end_record_index + 1]
    start_timestamp = records[0].timestamp if records else None
    end_timestamp = records[-1].timestamp if records else None
    position_record_count = sum(_record_has_position(record, None) for record in records)
    missing_record_count = len(records) - position_record_count
    bridge_points = _draft_bridge_points(activity, draft)
    return {
        "display_id": display_id,
        "kind": draft.kind,
        "detector_stage": draft.detector_stage,
        "start_record_index": draft.start_record_index,
        "end_record_index": draft.end_record_index,
        "start_timestamp": start_timestamp.isoformat() if start_timestamp is not None else None,
        "end_timestamp": end_timestamp.isoformat() if end_timestamp is not None else None,
        "duration_seconds": (
            (end_timestamp - start_timestamp).total_seconds()
            if start_timestamp is not None and end_timestamp is not None
            else None
        ),
        "continuity_id": _continuity_id(activity, draft.start_record_index),
        "status": draft.status,
        "confidence": draft.confidence,
        "repair_eligible": draft.repair_eligible,
        "reasons": list(draft.reasons),
        "evidence": [dict(item) for item in draft.evidence],
        "position_record_count": position_record_count,
        "missing_position_record_count": missing_record_count,
        "metrics": dict(draft.metrics),
        "source_key": draft.source_key,
        "map": _diagnostic_region_map(
            activity,
            draft.start_record_index,
            draft.end_record_index,
            bridge_points,
        ),
    }


def _draft_bridge_points(
    activity: ActivityData,
    draft: _DiagnosticRegionDraft,
) -> tuple[tuple[float, float], ...]:
    if draft.kind != "missing_position_run":
        return ()
    points: list[tuple[float, float]] = []
    for key in ("anchor_before_record_index", "anchor_after_record_index"):
        value = draft.metrics.get(key)
        if not isinstance(value, int) or not 0 <= value < len(activity.records):
            continue
        record = activity.records[value]
        if record.latitude is not None and record.longitude is not None:
            points.append((record.latitude, record.longitude))
    return tuple(points)


def _diagnostic_region_map(
    activity: ActivityData,
    start_record_index: int,
    end_record_index: int,
    bridge_points: tuple[tuple[float, float], ...],
) -> dict[str, object]:
    geometry_ranges: list[list[int]] = []
    positioned: list[ActivityRecord] = []
    range_start: int | None = None
    previous: ActivityRecord | None = None
    for record in activity.records[start_record_index : end_record_index + 1]:
        has_position = _record_has_position(record, None)
        if not has_position or (
            previous is not None and previous.continuity_id != record.continuity_id
        ):
            if range_start is not None and previous is not None:
                geometry_ranges.append([range_start, previous.index])
            range_start = None
        if has_position:
            if range_start is None:
                range_start = record.index
            positioned.append(record)
        previous = record if has_position else None
    if range_start is not None and previous is not None:
        geometry_ranges.append([range_start, previous.index])

    map_points = [
        (record.latitude, record.longitude)
        for record in positioned
        if record.latitude is not None and record.longitude is not None
    ]
    map_points.extend(bridge_points)
    marker: list[float] | None = None
    marker_record_index: int | None = None
    if positioned:
        record = positioned[len(positioned) // 2]
        if record.latitude is not None and record.longitude is not None:
            marker = [record.latitude, record.longitude]
            marker_record_index = record.index
    elif len(bridge_points) == 2:
        marker = [
            (bridge_points[0][0] + bridge_points[1][0]) / 2.0,
            (bridge_points[0][1] + bridge_points[1][1]) / 2.0,
        ]
    elif len(bridge_points) == 1:
        marker = [bridge_points[0][0], bridge_points[0][1]]

    bounds = (
        [
            [min(point[0] for point in map_points), min(point[1] for point in map_points)],
            [max(point[0] for point in map_points), max(point[1] for point in map_points)],
        ]
        if map_points
        else None
    )
    return {
        "mappable": marker is not None,
        "marker": marker,
        "marker_record_index": marker_record_index,
        "bounds": bounds,
        "geometry_ranges": geometry_ranges,
        "bridge_points": [list(point) for point in bridge_points],
    }


def _continuity_id(activity: ActivityData, record_index: int) -> int:
    if 0 <= record_index < len(activity.records):
        return activity.records[record_index].continuity_id
    return -1


def _missing_position_runs(
    activity: ActivityData,
    coordinate_overrides: Mapping[int, _Position] | None = None,
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
    coordinate_overrides: Mapping[int, _Position] | None,
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
    coordinate_overrides: Mapping[int, _Position] | None,
) -> bool:
    latitude, longitude = _record_coordinates(record, coordinate_overrides)
    return latitude is not None and longitude is not None


def _record_coordinates(
    record: ActivityRecord,
    coordinate_overrides: Mapping[int, _Position] | None,
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
        "source_path": str(course.source_path),
        "source_name": course.source_path.name,
        "segment_count": len(course.segments),
        "point_count": course.point_count,
        "total_distance_m": course.total_distance_m,
        "reference_only": True,
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
    course: CourseData | None,
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
