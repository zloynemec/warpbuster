"""Bounded geometry-only diagnostics for likely interpolated GNSS gaps."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from math import atan2, cos, degrees, hypot, radians, sin

from warpbuster.config import IntegrityConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord
from warpbuster.models.integrity import (
    GeometryScanDiagnostics,
    GeometryWarning,
    GeometryWarningKind,
    GeometryWarningReason,
    IntegrityConfidence,
)

_METRES_PER_LATITUDE_DEGREE = 111_195.0
_WARNING_REASONS = (
    GeometryWarningReason.LONG_NEAR_COLLINEAR_RUN,
    GeometryWarningReason.PATH_NEAR_CHORD,
    GeometryWarningReason.NARROW_CORRIDOR,
)


@dataclass(frozen=True, slots=True)
class GeometryDetectionResult:
    """Retained warnings plus aggregate bounded-scan diagnostics."""

    warnings: tuple[GeometryWarning, ...]
    diagnostics: GeometryScanDiagnostics


@dataclass(frozen=True, slots=True)
class _ProjectedPoint:
    record: ActivityRecord
    x_m: float
    y_m: float
    cumulative_distance_m: float


@dataclass(frozen=True, slots=True)
class _Candidate:
    start_index: int
    end_index: int
    bearing_radians: float


@dataclass(frozen=True, slots=True)
class _GeometryMetrics:
    chord_distance_m: float
    path_distance_m: float
    path_to_chord_ratio: float
    max_cross_track_deviation_m: float
    bearing_radians: float


def detect_geometry_warnings(
    activity: ActivityData,
    config: IntegrityConfig,
) -> GeometryDetectionResult:
    """Find long, densely sampled near-perfect chords without claiming corruption."""
    retained: list[GeometryWarning] = []
    continuity_segment_count = 0
    candidate_window_count = 0
    qualifying_window_count = 0
    warning_count = 0

    for records in _position_groups(activity):
        continuity_segment_count += 1
        points = _project(records)
        candidates, considered = _candidate_windows(points, config)
        candidate_window_count += considered
        qualifying_window_count += len(candidates)
        for candidate in _merge_candidates(points, candidates, config):
            metrics = _metrics(points, candidate.start_index, candidate.end_index)
            if not _qualifies(points, candidate.start_index, candidate.end_index, metrics, config):
                continue
            warning_count += 1
            if len(retained) < config.geometry_max_warnings:
                retained.append(_warning(points, candidate, metrics))

    return GeometryDetectionResult(
        warnings=tuple(retained),
        diagnostics=GeometryScanDiagnostics(
            continuity_segment_count=continuity_segment_count,
            candidate_window_count=candidate_window_count,
            qualifying_window_count=qualifying_window_count,
            warning_count=warning_count,
            retained_warning_count=len(retained),
            warnings_truncated_count=warning_count - len(retained),
        ),
    )


def _position_groups(activity: ActivityData) -> tuple[tuple[ActivityRecord, ...], ...]:
    groups: list[tuple[ActivityRecord, ...]] = []
    current: list[ActivityRecord] = []
    current_continuity_id: int | None = None
    for record in activity.records:
        if record.latitude is None or record.longitude is None:
            continue
        if current and record.continuity_id != current_continuity_id:
            groups.append(tuple(current))
            current = []
        current.append(record)
        current_continuity_id = record.continuity_id
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _project(records: tuple[ActivityRecord, ...]) -> tuple[_ProjectedPoint, ...]:
    first = records[0]
    if first.latitude is None or first.longitude is None:
        raise AssertionError("projected records must have positions")
    reference_latitude = first.latitude
    reference_longitude = first.longitude
    longitude_scale = _METRES_PER_LATITUDE_DEGREE * cos(radians(reference_latitude))
    points: list[_ProjectedPoint] = []
    cumulative_distance_m = 0.0
    previous: ActivityRecord | None = None
    for record in records:
        if record.latitude is None or record.longitude is None:
            raise AssertionError("projected records must have positions")
        if previous is not None:
            if previous.latitude is None or previous.longitude is None:
                raise AssertionError("projected records must have positions")
            cumulative_distance_m += geodesic_distance_m(
                previous.latitude,
                previous.longitude,
                record.latitude,
                record.longitude,
            )
        longitude_delta = ((record.longitude - reference_longitude + 180.0) % 360.0) - 180.0
        points.append(
            _ProjectedPoint(
                record=record,
                x_m=longitude_delta * longitude_scale,
                y_m=(record.latitude - reference_latitude) * _METRES_PER_LATITUDE_DEGREE,
                cumulative_distance_m=cumulative_distance_m,
            )
        )
        previous = record
    return tuple(points)


def _candidate_windows(
    points: tuple[_ProjectedPoint, ...],
    config: IntegrityConfig,
) -> tuple[tuple[_Candidate, ...], int]:
    minimum_count = config.geometry_min_position_count
    if len(points) < minimum_count:
        return (), 0
    cumulative = [point.cumulative_distance_m for point in points]
    candidates: list[_Candidate] = []
    considered = 0
    last_start = len(points) - minimum_count
    for start_index in range(0, last_start + 1, config.geometry_scan_stride_records):
        considered += 1
        maximum_end_exclusive = min(
            len(points),
            start_index + config.geometry_scan_max_window_records,
        )
        minimum_end = start_index + minimum_count - 1
        target_distance = (
            points[start_index].cumulative_distance_m + config.geometry_min_chord_distance_m
        )
        end_index = max(
            minimum_end,
            bisect_left(
                cumulative,
                target_distance,
                lo=minimum_end,
                hi=maximum_end_exclusive,
            ),
        )
        while end_index < maximum_end_exclusive:
            if (
                _chord_distance(points[start_index], points[end_index])
                >= config.geometry_min_chord_distance_m
            ):
                metrics = _metrics(points, start_index, end_index)
                if _qualifies(points, start_index, end_index, metrics, config):
                    candidates.append(_Candidate(start_index, end_index, metrics.bearing_radians))
                break
            end_index += 1
    return tuple(candidates), considered


def _merge_candidates(
    points: tuple[_ProjectedPoint, ...],
    candidates: tuple[_Candidate, ...],
    config: IntegrityConfig,
) -> tuple[_Candidate, ...]:
    if not candidates:
        return ()
    merged: list[_Candidate] = []
    current = candidates[0]
    for candidate in candidates[1:]:
        if (
            candidate.start_index <= current.end_index
            and _bearing_difference_degrees(
                current.bearing_radians,
                candidate.bearing_radians,
            )
            <= config.geometry_max_bearing_change_degrees
        ):
            end_index = max(current.end_index, candidate.end_index)
            current = _Candidate(
                current.start_index,
                end_index,
                _bearing(points[current.start_index], points[end_index]),
            )
            continue
        merged.append(current)
        current = candidate
    merged.append(current)
    return tuple(merged)


def _metrics(
    points: tuple[_ProjectedPoint, ...],
    start_index: int,
    end_index: int,
) -> _GeometryMetrics:
    start = points[start_index]
    end = points[end_index]
    chord_distance_m = _chord_distance(start, end)
    path_distance_m = end.cumulative_distance_m - start.cumulative_distance_m
    ratio = path_distance_m / chord_distance_m if chord_distance_m > 0 else float("inf")
    maximum_deviation = max(
        _cross_track_deviation(point, start, end) for point in points[start_index : end_index + 1]
    )
    return _GeometryMetrics(
        chord_distance_m=chord_distance_m,
        path_distance_m=path_distance_m,
        path_to_chord_ratio=ratio,
        max_cross_track_deviation_m=maximum_deviation,
        bearing_radians=_bearing(start, end),
    )


def _chord_distance(start: _ProjectedPoint, end: _ProjectedPoint) -> float:
    start_record = start.record
    end_record = end.record
    if (
        start_record.latitude is None
        or start_record.longitude is None
        or end_record.latitude is None
        or end_record.longitude is None
    ):
        raise AssertionError("geometry metrics require positions")
    return geodesic_distance_m(
        start_record.latitude,
        start_record.longitude,
        end_record.latitude,
        end_record.longitude,
    )


def _qualifies(
    points: tuple[_ProjectedPoint, ...],
    start_index: int,
    end_index: int,
    metrics: _GeometryMetrics,
    config: IntegrityConfig,
) -> bool:
    return (
        end_index - start_index + 1 >= config.geometry_min_position_count
        and metrics.chord_distance_m >= config.geometry_min_chord_distance_m
        and metrics.path_to_chord_ratio <= config.geometry_max_path_to_chord_ratio
        and metrics.max_cross_track_deviation_m <= config.geometry_max_cross_track_deviation_m
        and points[start_index].record.continuity_id == points[end_index].record.continuity_id
    )


def _warning(
    points: tuple[_ProjectedPoint, ...],
    candidate: _Candidate,
    metrics: _GeometryMetrics,
) -> GeometryWarning:
    selected = points[candidate.start_index : candidate.end_index + 1]
    start_record = selected[0].record
    end_record = selected[-1].record
    return GeometryWarning(
        kind=GeometryWarningKind.POSSIBLE_INTERPOLATED_GNSS_GAP,
        start_record_index=start_record.index,
        end_record_index=end_record.index,
        start_timestamp=start_record.timestamp,
        end_timestamp=end_record.timestamp,
        position_record_count=len(selected),
        chord_distance_m=metrics.chord_distance_m,
        path_distance_m=metrics.path_distance_m,
        path_to_chord_ratio=metrics.path_to_chord_ratio,
        max_cross_track_deviation_m=metrics.max_cross_track_deviation_m,
        timestamps_available=all(point.record.timestamp is not None for point in selected),
        confidence=IntegrityConfidence.LOW,
        reasons=_WARNING_REASONS,
    )


def _cross_track_deviation(
    point: _ProjectedPoint,
    start: _ProjectedPoint,
    end: _ProjectedPoint,
) -> float:
    delta_x = end.x_m - start.x_m
    delta_y = end.y_m - start.y_m
    denominator = hypot(delta_x, delta_y)
    if denominator == 0:
        return hypot(point.x_m - start.x_m, point.y_m - start.y_m)
    return (
        abs(delta_y * point.x_m - delta_x * point.y_m + end.x_m * start.y_m - end.y_m * start.x_m)
        / denominator
    )


def _bearing(start: _ProjectedPoint, end: _ProjectedPoint) -> float:
    return atan2(end.y_m - start.y_m, end.x_m - start.x_m)


def _bearing_difference_degrees(first: float, second: float) -> float:
    return abs(degrees(atan2(sin(second - first), cos(second - first))))
