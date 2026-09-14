"""Build a bounded original-record suffix; never bridge missing or suspect data."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from warpbuster.config import OSMReconstructionConfig
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData
from warpbuster.models.reconstruction import CoordinateState, RepairPlan


@dataclass(frozen=True, slots=True)
class OSMStartContext:
    # index, UTC epoch seconds, latitude, longitude; independent of routing types.
    points: tuple[tuple[int, float, float, float], ...]
    stop_reason: str


def collect_start_context(
    activity: ActivityData, plan: RepairPlan, anchor: int, config: OSMReconstructionConfig
) -> OSMStartContext:
    collected: list[tuple[int, float, float, float]] = []
    length = 0.0
    reason = "activity_start"
    continuity = activity.records[anchor].continuity_id
    events = tuple((e.index, e.fields.get("timestamp")) for e in activity.events)
    for i in range(anchor, max(-1, anchor - config.context_maximum_points - 1), -1):
        if len(collected) == config.context_maximum_points:
            reason = "point_limit"
            break
        r, mask = activity.records[i], plan.coordinate_mask[i]
        if mask.state is not CoordinateState.PRESERVED or not mask.anchor_eligible:
            reason = "ineligible_record"
            break
        if r.continuity_id != continuity:
            reason = "continuity_boundary"
            break
        if r.timestamp is None or r.timestamp.tzinfo is None:
            reason = "invalid_timestamp"
            break
        lat, lon = r.latitude, r.longitude
        if (
            lat is None
            or lon is None
            or not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            reason = "invalid_position"
            break
        stamp = r.timestamp.timestamp()
        if collected:
            next_record = activity.records[i + 1]
            # All source events are conservative boundaries, including unknown timer semantics.
            if any(
                (
                    isinstance(t, datetime)
                    and t.tzinfo is not None
                    and stamp < t.timestamp() <= collected[-1][1]
                )
                or (r.source.message_index < idx <= next_record.source.message_index)
                for idx, t in events
            ):
                reason = "event_boundary"
                break
            if not 0 < collected[-1][1] - stamp <= config.context_maximum_step_s:
                reason = "time_step"
                break
            if collected[0][1] - stamp > config.context_maximum_age_s:
                reason = "age_limit"
                break
            distance = geodesic_distance_m(lat, lon, collected[-1][2], collected[-1][3])
            if length + distance > config.context_maximum_length_m:
                reason = "length_limit"
                break
            length += distance
        collected.append((i, stamp, lat, lon))
    return OSMStartContext(tuple(reversed(collected)), reason)
