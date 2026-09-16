"""Versioned, distance-domain DEM presentation filter and elevation totals."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number

FILTERING_POLICY_ID = "distance-triangle-excursion-v1"


@dataclass(frozen=True, slots=True)
class ElevationFilteringPolicy:
    """Triangular spatial mean; hysteretic peak/valley totals."""

    window_radius_m: float = 60.0
    minimum_excursion_m: float = 3.0

    def validated(self) -> ElevationFilteringPolicy:
        if not finite_number(self.window_radius_m) or self.window_radius_m < 0:
            raise RoutingError("INVALID_REQUEST", "window_radius_m must be nonnegative metres")
        if not finite_number(self.minimum_excursion_m) or self.minimum_excursion_m < 0:
            raise RoutingError("INVALID_REQUEST", "minimum_excursion_m must be nonnegative metres")
        return self

    def inspection_document(self) -> dict[str, Any]:
        return {
            "policy_id": FILTERING_POLICY_ID,
            "window_radius_m": self.window_radius_m,
            "minimum_excursion_m": self.minimum_excursion_m,
            "smoothing": "triangular distance-weighted mean within each covered run",
            "totals": "peak/valley swing with minimum reversal excursion",
            "missing": "never bridge a missing sample",
            "units": "metres",
        }

    def sha256(self) -> str:
        encoded = json.dumps(
            self.inspection_document(), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ElevationTotals:
    ascent_m: float
    descent_m: float

    def as_dict(self) -> dict[str, float]:
        return {"ascent_m": self.ascent_m, "descent_m": self.descent_m}


@dataclass(frozen=True, slots=True)
class FilteredElevation:
    values: tuple[float | None, ...]
    raw_totals: ElevationTotals
    filtered_totals: ElevationTotals


def filter_elevations(
    chainages_m: tuple[float, ...],
    raw_elevations_m: tuple[float | None, ...],
    policy: ElevationFilteringPolicy,
) -> FilteredElevation:
    """Filter bounded raw samples without changing geometry or filling gaps."""
    policy.validated()
    if (
        not chainages_m
        or len(chainages_m) != len(raw_elevations_m)
        or any(not finite_number(value) or value < 0 for value in chainages_m)
        or any(right < left for left, right in pairwise(chainages_m))
        or any(value is not None and not finite_number(value) for value in raw_elevations_m)
    ):
        raise RoutingError("INVALID_REQUEST", "invalid raw elevation series")
    filtered: list[float | None] = [None] * len(raw_elevations_m)
    raw_ascent = raw_descent = filtered_ascent = filtered_descent = 0.0
    start = 0
    while start < len(raw_elevations_m):
        if raw_elevations_m[start] is None:
            start += 1
            continue
        end = start + 1
        while end < len(raw_elevations_m) and raw_elevations_m[end] is not None:
            end += 1
        positions = chainages_m[start:end]
        values = tuple(value for value in raw_elevations_m[start:end] if value is not None)
        smoothed = _smooth_run(positions, values, policy.window_radius_m)
        filtered[start:end] = smoothed
        up, down = _totals(values, 0.0)
        raw_ascent += up
        raw_descent += down
        up, down = _totals(smoothed, policy.minimum_excursion_m)
        filtered_ascent += up
        filtered_descent += down
        start = end
    return FilteredElevation(
        tuple(filtered),
        ElevationTotals(raw_ascent, raw_descent),
        ElevationTotals(filtered_ascent, filtered_descent),
    )


def _smooth_run(
    positions: tuple[float, ...], values: tuple[float, ...], radius: float
) -> tuple[float, ...]:
    if radius == 0 or len(values) <= 2:
        return values
    origin = positions[0]
    x = tuple(position - origin for position in positions)
    sums_v = [0.0]
    sums_x = [0.0]
    sums_vx = [0.0]
    for position, value in zip(x, values, strict=True):
        sums_v.append(sums_v[-1] + value)
        sums_x.append(sums_x[-1] + position)
        sums_vx.append(sums_vx[-1] + value * position)
    result: list[float] = []
    minimums: deque[int] = deque()
    maximums: deque[int] = deque()
    entered = 0
    for position in x:
        left = bisect_left(x, position - radius)
        middle = bisect_right(x, position)
        right = bisect_right(x, position + radius)
        while entered < right:
            while minimums and values[minimums[-1]] >= values[entered]:
                minimums.pop()
            while maximums and values[maximums[-1]] <= values[entered]:
                maximums.pop()
            minimums.append(entered)
            maximums.append(entered)
            entered += 1
        while minimums[0] < left:
            minimums.popleft()
        while maximums[0] < left:
            maximums.popleft()
        left_v = sums_v[middle] - sums_v[left]
        left_x = sums_x[middle] - sums_x[left]
        left_vx = sums_vx[middle] - sums_vx[left]
        right_v = sums_v[right] - sums_v[middle]
        right_x = sums_x[right] - sums_x[middle]
        right_vx = sums_vx[right] - sums_vx[middle]
        numerator = (
            left_v * (1 - position / radius)
            + left_vx / radius
            + right_v * (1 + position / radius)
            - right_vx / radius
        )
        denominator = (
            (middle - left) * (1 - position / radius)
            + left_x / radius
            + (right - middle) * (1 + position / radius)
            - right_x / radius
        )
        if denominator <= 0:
            raise RoutingError("DEM_RESPONSE_INVALID", "elevation filter weights are invalid")
        # Floating-point prefix subtraction can drift by a few ulps; the filter
        # must never invent an elevation outside its finite local input range.
        result.append(min(max(numerator / denominator, values[minimums[0]]), values[maximums[0]]))
    result[0] = values[0]
    result[-1] = values[-1]
    return tuple(result)


def _totals(values: tuple[float, ...], minimum_excursion_m: float) -> tuple[float, float]:
    if len(values) < 2:
        return 0.0, 0.0
    if minimum_excursion_m == 0:
        ascent = sum(max(right - left, 0.0) for left, right in pairwise(values))
        descent = sum(max(left - right, 0.0) for left, right in pairwise(values))
        return ascent, descent
    ascent = descent = 0.0
    low = high = values[0]
    start = values[0]
    extreme = values[0]
    direction = 0
    for value in values[1:]:
        if direction == 0:
            low = min(low, value)
            high = max(high, value)
            if value - low >= minimum_excursion_m:
                direction, start, extreme = 1, low, value
            elif high - value >= minimum_excursion_m:
                direction, start, extreme = -1, high, value
        elif direction == 1:
            extreme = max(extreme, value)
            if extreme - value >= minimum_excursion_m:
                ascent += extreme - start
                direction, start, extreme = -1, extreme, value
        else:
            extreme = min(extreme, value)
            if value - extreme >= minimum_excursion_m:
                descent += start - extreme
                direction, start, extreme = 1, extreme, value
    if direction == 1:
        ascent += extreme - start
    elif direction == -1:
        descent += start - extreme
    return ascent, descent
