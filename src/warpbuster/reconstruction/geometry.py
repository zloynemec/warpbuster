"""Reusable course geometry primitives, independent of reconstruction decisions."""

from __future__ import annotations

from bisect import bisect_right
from math import cos, radians

from warpbuster.models.reconstruction import CourseSegment

_METRES_PER_LATITUDE_DEGREE = 111_195.0


def _coordinate_at_distance(
    segment: CourseSegment,
    distance_m: float,
) -> tuple[float, float]:
    clamped = min(segment.length_m, max(0.0, distance_m))
    cumulative = tuple(point.cumulative_distance_m for point in segment.points)
    end_index = min(len(segment.points) - 1, max(1, bisect_right(cumulative, clamped)))
    start = segment.points[end_index - 1]
    end = segment.points[end_index]
    edge_distance = end.cumulative_distance_m - start.cumulative_distance_m
    fraction = (
        0.0 if edge_distance <= 0 else (clamped - start.cumulative_distance_m) / edge_distance
    )
    return _interpolate_coordinate(
        start.latitude,
        start.longitude,
        end.latitude,
        end.longitude,
        fraction,
    )


def _project_onto_edge(
    latitude: float,
    longitude: float,
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
) -> tuple[float, float, float]:
    reference_latitude = (start_latitude + end_latitude + latitude) / 3.0
    longitude_scale = _METRES_PER_LATITUDE_DEGREE * cos(radians(reference_latitude))
    edge_longitude_delta = _wrapped_longitude_delta(end_longitude, start_longitude)
    point_longitude_delta = _wrapped_longitude_delta(longitude, start_longitude)
    edge_x = edge_longitude_delta * longitude_scale
    edge_y = (end_latitude - start_latitude) * _METRES_PER_LATITUDE_DEGREE
    point_x = point_longitude_delta * longitude_scale
    point_y = (latitude - start_latitude) * _METRES_PER_LATITUDE_DEGREE
    denominator = edge_x * edge_x + edge_y * edge_y
    fraction = 0.0 if denominator == 0 else (point_x * edge_x + point_y * edge_y) / denominator
    fraction = min(1.0, max(0.0, fraction))
    projected_latitude, projected_longitude = _interpolate_coordinate(
        start_latitude,
        start_longitude,
        end_latitude,
        end_longitude,
        fraction,
    )
    return fraction, projected_latitude, projected_longitude


def _interpolate_coordinate(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
    fraction: float,
) -> tuple[float, float]:
    longitude_delta = _wrapped_longitude_delta(end_longitude, start_longitude)
    longitude = ((start_longitude + longitude_delta * fraction + 180.0) % 360.0) - 180.0
    latitude = start_latitude + (end_latitude - start_latitude) * fraction
    return latitude, longitude


def _wrapped_longitude_delta(longitude: float, reference: float) -> float:
    return ((longitude - reference + 180.0) % 360.0) - 180.0
