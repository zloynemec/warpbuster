"""Conservative comparison of a bounded approach sequence on local graph edges."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number, haversine_m, path_length_m, valid_wgs84
from warpbuster_osm_routing.models import GeoPoint, StartContext, StartContextPoint
from warpbuster_osm_routing.trace_audit import AuditBudget

if TYPE_CHECKING:
    from warpbuster_osm_routing.snapping import SnapCandidate

POLICY = "start-context-v1"


def validate_context(context: StartContext | None, start: GeoPoint, c: RoutingCacheConfig) -> None:
    if context is None:
        return
    if (
        not isinstance(context, StartContext)
        or not isinstance(context.points, tuple)
        or not isinstance(context.stop_reason, str)
        or not context.stop_reason
        or len(context.stop_reason) > 64
    ):
        raise RoutingError("INVALID_REQUEST", "invalid start context")
    points = context.points
    if len(points) > c.context_maximum_points:
        raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "start context point limit exceeded")
    for i, p in enumerate(points):
        if (
            not isinstance(p, StartContextPoint)
            or type(p.record_index) is not int
            or p.record_index < 0
            or not finite_number(p.timestamp_seconds)
            or not isinstance(p.point, GeoPoint)
            or not valid_wgs84(p.point)
        ):
            raise RoutingError("INVALID_REQUEST", "invalid start context point")
        if i and (
            p.record_index != points[i - 1].record_index + 1
            or not 0
            < p.timestamp_seconds - points[i - 1].timestamp_seconds
            <= c.context_maximum_step_s
        ):
            raise RoutingError("INVALID_REQUEST", "non-contiguous start context")
    if points and points[-1].point != start:
        raise RoutingError("INVALID_REQUEST", "start context must end at original anchor")
    if points and (
        points[-1].timestamp_seconds - points[0].timestamp_seconds > c.context_maximum_age_s
        or path_length_m(tuple(p.point for p in points)) > c.context_maximum_length_m
    ):
        raise RoutingError("INVALID_REQUEST", "start context exceeds time or distance bounds")


def project(
    point: GeoPoint, shape: tuple[GeoPoint, ...], tie_m: float, merge_m: float | None = None
) -> tuple[float, float] | None:
    """Nearest chainage; near-equal projections at distinct chainages refuse."""
    results = []
    chain = 0.0
    scale = math.cos(math.radians(point.latitude))
    for a, b in pairwise(shape):
        dx = (b.longitude - a.longitude + 180) % 360 - 180
        ax = ((a.longitude - point.longitude + 180) % 360 - 180) * scale
        ay, vx, vy = a.latitude - point.latitude, dx * scale, b.latitude - a.latitude
        length = haversine_m(a, b)
        if length == 0:
            continue
        f = max(0.0, min(1.0, -(ax * vx + ay * vy) / (vx * vx + vy * vy)))
        q = GeoPoint(a.latitude + f * vy, (a.longitude + f * dx + 180) % 360 - 180)
        results.append((haversine_m(point, q), chain + f * length, f))
        chain += length
    if not results:
        return None
    # Clamped endpoints are not local minima if the adjacent segment is closer.
    minima = [
        r
        for i, r in enumerate(results)
        if not (
            (r[2] == 0 and i > 0 and results[i - 1][2] < 1)
            or (r[2] == 1 and i + 1 < len(results) and results[i + 1][2] > 0)
        )
    ]
    if not minima:
        return None
    best = min(minima)
    if any(
        error <= best[0] + tie_m and abs(along - best[1]) > (tie_m if merge_m is None else merge_m)
        for error, along, _ in minima
    ):
        return None
    return best[0], best[1]


def resolve_context(
    context: StartContext | None,
    groups: tuple[tuple[SnapCandidate, ...], ...],
    raw: list[dict[str, Any]],
    c: RoutingCacheConfig,
    budget: AuditBudget,
) -> tuple[SnapCandidate | None, dict[str, Any]]:
    samples = context.points if context else ()
    audit: dict[str, Any] = {
        "policy_version": POLICY,
        "single_point_status": "AMBIGUOUS_SNAP",
        "status": "insufficient_context",
        "stop_reason": context.stop_reason if context else "not_supplied",
        "record_range": [samples[0].record_index, samples[-1].record_index] if samples else [],
        "point_count": len(samples),
        "duration_s": samples[-1].timestamp_seconds - samples[0].timestamp_seconds
        if samples
        else 0,
        "context_sha256": hashlib.sha256(
            json.dumps(
                [
                    [p.record_index, p.timestamp_seconds, p.point.latitude, p.point.longitude]
                    for p in samples
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "config": {k: v for k, v in c.query_policy_dict().items() if k.startswith("context_")},
        "groups": [],
        "metadata_calls": 0,  # Reuse original locate; no extra native queries.
    }
    if (
        len(samples) < c.context_minimum_points
        or audit["duration_s"] < c.context_minimum_duration_s
    ):
        return None, audit
    contenders = tuple(
        g
        for g in groups
        if g[0].distance_m
        <= min(
            groups[0][0].distance_m + c.snap_ambiguity_distance_delta_m, c.maximum_snap_distance_m
        )
    )
    if len(contenders) > c.context_maximum_groups:
        raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "context group limit exceeded")
    inventory: dict[Any, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            audit["status"] = "unsupported_geometry"
            return None, audit
        identifier = item.get("edge_id")
        identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
        if type(identifier) is not int:
            audit["status"] = "unsupported_geometry"
            return None, audit
        if identifier in inventory and inventory[identifier] != item:
            audit["status"] = "unsupported_geometry"
            return None, audit
        inventory[identifier] = item
    evaluated = []
    vertices = 0
    work = 0
    for index, group in enumerate(contenders):
        # A junction group containing several ways needs graph expansion: out of scope.
        infos = [inventory.get(x.edge_id, {}).get("edge_info", {}) for x in group]
        if (
            not infos
            or any(info != infos[0] for info in infos)
            or any(
                type(x.edge_id) is not int
                or type(x.way_id) is not int
                or x.edge_id < 0
                or x.way_id <= 0
                or type(x.end_node_id) is not int
                or any(type(n) is not int or n <= 0 for n in x.osm_node_ids)
                or x.pedestrian_access is not True
                or x.forward is None
                or x.use in ("ferry", "rail-ferry")
                or x.surface == "impassable"
                or x.sac_scale
                not in (
                    None,
                    0,
                    1,
                    2,
                    3,
                    "none",
                    "hiking",
                    "mountain_hiking",
                    "demanding_mountain_hiking",
                )
                for x in group
            )
        ):
            audit["status"] = "unsupported_geometry"
            return None, audit
        encoded = infos[0].get("shape")
        if not isinstance(encoded, str) or len(group[0].osm_node_ids) < 2:
            audit["status"] = "unsupported_geometry"
            return None, audit
        shape = budget.shape(encoded, c.context_maximum_vertices - vertices)
        vertices += len(shape)
        work += len(shape) * (len(samples) + len(group))
        if work > c.context_maximum_projection_work:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "context projection work limit exceeded")
        projections = [project(p.point, shape, c.context_projection_tie_m) for p in samples]
        if any(p is None for p in projections):
            audit["status"] = "ambiguous_projection"
            audit["failed_projection"] = {
                "way_id": group[0].way_id,
                "record_indices": [
                    samples[i].record_index for i, p in enumerate(projections) if p is None
                ],
            }
            return None, audit
        values = [p for p in projections if p is not None]
        errors, chain = [p[0] for p in values], [p[1] for p in values]
        for candidate in group:
            correlated = project(candidate.point, shape, c.context_projection_tie_m)
            if (
                correlated is None
                or correlated[0] > c.context_projection_tie_m
                or abs(correlated[1] - chain[-1]) > c.context_projection_tie_m
                or candidate.percent_along is None
                or not finite_number(candidate.percent_along)
                or not 0 <= candidate.percent_along <= 1
            ):
                audit["status"] = "unsupported_geometry"
                return None, audit
        progress = chain[-1] - chain[0]
        forward = progress > 0
        backtrack = sum(max(0.0, (a - b) if forward else (b - a)) for a, b in pairwise(chain))
        directions = [x for x in group if x.forward is forward]
        recent = errors[-c.context_recent_points :]
        p90 = sorted(errors)[math.ceil(0.9 * len(errors)) - 1]
        eligible = (
            abs(progress) > c.context_minimum_progress_m
            and backtrack < c.context_maximum_backtrack_m
            and statistics.median(errors) < c.context_maximum_median_error_m
            and p90 < c.context_maximum_p90_error_m
            and max(recent) < c.context_maximum_p90_error_m
            and len(directions) == 1
        )
        evidence: dict[str, Any] = {
            "group_index": index,
            "way_id": group[0].way_id,
            "edge_ids": sorted((x.edge_id for x in group), key=str),
            "median_error_m": statistics.median(errors),
            "p90_error_m": p90,
            "errors_m": errors,
            "chainage_m": chain,
            "progress_m": progress,
            "backtrack_m": backtrack,
            "forward": forward,
            "absolute_gates_passed": eligible,
            "geometry_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        audit["groups"].append(evidence)
        evaluated.append((directions[0] if eligible else None, errors, chain, evidence))
    winners = []
    for i, (selected, errors, _chain, evidence) in enumerate(evaluated):
        if selected is None:
            continue
        support = []
        for j, (_, other, _, _) in enumerate(evaluated):
            if i == j:
                continue
            margins = [b - a for a, b in zip(errors, other, strict=True)]
            full = sum(m > c.context_minimum_margin_m for m in margins) / len(margins)
            recent = margins[-c.context_recent_points :]
            last = sum(m > c.context_minimum_margin_m for m in recent) / len(recent)
            support.append(
                {
                    "competitor": j,
                    "support_fraction": full,
                    "recent_support_fraction": last,
                    "median_margin_m": statistics.median(margins),
                }
            )
        evidence["comparisons"] = support
        if all(
            x["support_fraction"] >= c.context_minimum_support_ratio
            and x["recent_support_fraction"] >= c.context_minimum_support_ratio
            for x in support
        ):
            winners.append(selected)
    if len(winners) != 1:
        audit["status"] = (
            "insufficient_margin" if any(x[0] for x in evaluated) else "conflicting_context"
        )
        return None, audit
    audit.update(
        status="resolved", selected_edge_id=winners[0].edge_id, selected_way_id=winners[0].way_id
    )
    return winners[0], audit
