"""Bounded forest-path hypotheses: uncertainty produces options, never FIT edits."""

from __future__ import annotations

import json
import statistics
from dataclasses import replace
from itertools import product
from typing import TYPE_CHECKING, Any

from warpbuster_osm_routing.alternatives import (
    build_route_set,
    empty_route_set,
    geometry_weights,
    route_identity,
)
from warpbuster_osm_routing.context_snapping import project
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import (
    RouteAlternativesRequest,
    RouteAlternativesResult,
    RouteRequest,
    StartContext,
)
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1
from warpbuster_osm_routing.snapping import SnapCandidate, SnapDecision
from warpbuster_osm_routing.trace_audit import AuditBudget

if TYPE_CHECKING:
    from warpbuster_osm_routing.route_service import RouteService


def _allowed(c: SnapCandidate) -> bool:
    return (
        type(c.edge_id) is int
        and type(c.way_id) is int
        and c.pedestrian_access is True
        and c.forward is not None
        and c.use not in ("ferry", "rail-ferry")
        and c.surface not in ("impassable", 7)
        and c.sac_scale
        in (None, 0, 1, 2, 3, "none", "hiking", "mountain_hiking", "demanding_mountain_hiking")
    )


def _options(
    service: RouteService, snap: SnapDecision, context: StartContext | None, budget: AuditBudget
) -> list[dict[str, Any]]:
    c = service.config
    inventory: dict[Any, Any] = {}
    for raw in snap.raw_candidates:
        if not isinstance(raw, dict):
            continue
        key = raw.get("edge_id")
        key = key.get("value") if isinstance(key, dict) else key
        if type(key) is int:
            inventory[key] = raw
    options: list[dict[str, Any]] = []
    work = 0
    vertices = 0
    for group_index, group in enumerate(snap.groups):
        allowed = tuple(
            x for x in group if _allowed(x) and x.distance_m <= c.approximate_snap_distance_m
        )
        if not allowed:
            continue
        # Default to proximity; context ranks but never vetoes a road hypothesis.
        evidence: dict[str, Any] = {
            "status": "not_available",
            "usable_points": 0,
            "ignored_points": 0,
        }
        best = min(allowed, key=lambda x: (x.distance_m, str(x.way_id), str(x.edge_id)))
        score = best.distance_m
        if context and context.points:
            raw = inventory.get(best.edge_id, {})
            encoded = raw.get("edge_info", {}).get("shape")
            if isinstance(encoded, str):
                shape = budget.shape(encoded, c.context_maximum_vertices - vertices)
                vertices += len(shape)
                work += len(shape) * len(context.points)
                if work > c.context_maximum_projection_work:
                    raise RoutingError(
                        "RESOURCE_LIMIT_EXCEEDED", "approximate context work budget exceeded"
                    )
                projections = [
                    project(
                        p.point, shape, c.context_projection_tie_m, c.approximate_projection_merge_m
                    )
                    for p in context.points
                ]
                usable = [p for p in projections if p is not None]
                evidence = {
                    "status": "insufficient_context",
                    "usable_points": len(usable),
                    "ignored_points": len(projections) - len(usable),
                    "record_range": [
                        context.points[0].record_index,
                        context.points[-1].record_index,
                    ],
                    "stop_reason": context.stop_reason,
                    "projection_merge_m": c.approximate_projection_merge_m,
                }
                if usable:
                    evidence["median_error_m"] = statistics.median(p[0] for p in usable)
                    evidence["progress_m"] = usable[-1][1] - usable[0][1]
                if (
                    len(usable) >= c.context_minimum_points
                    and len(usable) / len(projections) >= c.approximate_minimum_context_support
                ):
                    score = evidence["median_error_m"]
                    evidence["status"] = "advisory"
        options.append(
            {
                "snap": best,
                "members": allowed,
                "score": score,
                "context": evidence,
                "group_index": group_index,
            }
        )
    options.sort(
        key=lambda x: (
            x["score"],
            x["snap"].distance_m,
            str(x["snap"].way_id),
            str(x["snap"].edge_id),
        )
    )
    snap.document["candidate_options"] = [
        {
            "way_id": x["snap"].way_id,
            "distance_m": x["snap"].distance_m,
            "group_index": x["group_index"],
            "context": x["context"],
        }
        for x in options
    ]
    snap.document["options_truncated"] = len(options) > c.approximate_maximum_groups
    return options[: c.approximate_maximum_groups]


def discover_approximate(
    service: RouteService, request: RouteAlternativesRequest
) -> RouteAlternativesResult:
    # The strict route APIs remain available; this mode explicitly explores snaps.
    from warpbuster_osm_routing.route_service import _is_no_route_error

    single = RouteRequest(request.graph_id, request.start, request.end, request.start_context)
    actor, start, end, common = service._setup(single, approximate=True)
    common.update(operation="route_alternatives", discovery_policy="approximate-candidates-v1")
    common["request"].update(alternates=request.alternates, approximate_candidates=True)
    common.update(empty_route_set(request.alternates))
    for status in ("OUTSIDE_COVERAGE", "NO_SNAP"):
        if status in (start.status, end.status):
            common["status"] = status
            return service._alternatives_result(common)
    budget = start.budget
    assert budget is not None
    starts = _options(service, start, request.start_context, budget)
    ends = _options(service, end, None, budget)
    if not starts or not ends:
        common["status"] = "NO_SNAP"
        return service._alternatives_result(common)
    pairs = list(product(starts, ends))
    # A bounded number of anchor hypotheses, independent of native alternate count.
    pairs.sort(
        key=lambda p: (p[0]["score"] + p[1]["score"], p[0]["group_index"], p[1]["group_index"])
    )
    selected = pairs[: service.config.approximate_maximum_pairs]
    routes: dict[str, dict[str, Any]] = {}
    attempts = []
    for a, b in selected:
        sa, sb = a["snap"], b["snap"]
        attempt = {"start_way_id": sa.way_id, "end_way_id": sb.way_id, "status": "pending"}
        attempts.append(attempt)
        # Routing coordinates belong to this explicit hypothesis. Original FIT
        # anchors stay unchanged and their off-road connectors are reported below.
        hypothesis = RouteRequest(request.graph_id, sa.point, sb.point)
        payload = service._route_payload(hypothesis, request.alternates)
        try:
            raw = actor.route(json.dumps(payload, separators=(",", ":")))
        except Exception as error:
            if _is_no_route_error(error):
                attempt["status"] = "NO_ROUTE"
                continue
            raise RoutingError(
                "VALHALLA_REQUEST_FAILED", "approximate route request failed"
            ) from error
        response = budget.response(raw)
        if not isinstance(response, dict):
            raise RoutingError("ROUTE_AUDIT_FAILED", "invalid native route response")
        alternates = response.get("alternates", [])
        if not isinstance(alternates, list) or len(alternates) > request.alternates:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "native alternate count exceeds request")
        trips = [
            response.get("trip"),
            *[x.get("trip") if isinstance(x, dict) else None for x in alternates],
        ]
        pair_attempt = attempt
        pair_attempt.update(status="evaluated", native_routes=[])
        for slot, trip in enumerate(trips):
            attempt = {"native_slot": slot}
            pair_attempt["native_routes"].append(attempt)
            try:
                if not isinstance(trip, dict):
                    raise RoutingError("ROUTE_AUDIT_FAILED", "missing approximate route trip")
                route = service._audit_trip(
                    actor,
                    trip,
                    replace(sa, context_resolved=False),
                    replace(sb, context_resolved=False),
                    budget=budget,
                )
                geometry_weights(route)
                start_ids = {x.edge_id for x in a["members"]}
                end_ids = {x.edge_id for x in b["members"]}
                if (
                    route["edges"][0]["edge_id"] not in start_ids
                    or route["edges"][-1]["edge_id"] not in end_ids
                ):
                    raise RoutingError(
                        "ROUTE_AUDIT_FAILED",
                        "native route changed proposed road",
                        {"check": "hypothesis_edges"},
                    )
            except RoutingError as error:
                if error.code != "ROUTE_AUDIT_FAILED":
                    raise
                attempt.update(status=error.code, reason=error.message, details=error.details)
                continue
            rid = route_identity(route, request.graph_id, TRAIL_RUNNING_V1.sha256())
            actual_start = next(
                x for x in a["members"] if x.edge_id == route["edges"][0]["edge_id"]
            )
            actual_end = next(x for x in b["members"] if x.edge_id == route["edges"][-1]["edge_id"])
            progress = a["context"].get("progress_m", 0)
            direction_supported = (
                abs(progress) > service.config.context_minimum_progress_m
                and actual_start.forward == (progress > 0)
                and actual_start.way_id == sa.way_id
            )
            sa, sb = actual_start, actual_end
            attachment = {
                "start": sa.as_dict(),
                "end": sb.as_dict(),
                "start_context": a["context"],
                "approach_direction_supported": direction_supported,
                "start_connector": [
                    [request.start.latitude, request.start.longitude],
                    [sa.point.latitude, sa.point.longitude],
                ],
                "end_connector": [
                    [sb.point.latitude, sb.point.longitude],
                    [request.end.latitude, request.end.longitude],
                ],
            }
            attempt.update(status="READY", route_id=rid)
            if rid in routes:
                routes[rid]["attachment_options"].append(attachment)
                continue
            route["attachment_options"] = [attachment]
            route["discovery_confidence"] = (
                "medium"
                if a["context"]["status"] == "advisory"
                and direction_supported
                and max(sa.distance_m, sb.distance_m) <= service.config.maximum_snap_distance_m
                else "low"
            )
            route["warnings"].append({"code": "APPROXIMATE_PATH_HYPOTHESIS"})
            if not direction_supported:
                route["warnings"].append({"code": "APPROACH_DIRECTION_UNCERTAIN"})
            route["audit"]["status"] = "WARN"
            routes[rid] = route
    if routes:
        common.update(
            build_route_set(
                list(routes.values()),
                request.graph_id,
                TRAIL_RUNNING_V1.sha256(),
                request.alternates,
                service.config,
            )
        )
        common["status"] = "READY"
    else:
        common["status"] = "NO_ROUTE"
    common["search"].update(
        executed=True,
        exhaustive=False,
        strategy="bounded_snap_pairs",
        requested_snap_pairs=len(pairs),
        attempted_snap_pairs=len(selected),
        truncated=len(selected) < len(pairs)
        or start.document["options_truncated"]
        or end.document["options_truncated"],
        native_alternates_per_pair=request.alternates,
        engine_returned_routes=sum(len(x.get("native_routes", [])) for x in attempts),
        duplicates_removed=sum(
            y.get("status") == "READY" for x in attempts for y in x.get("native_routes", [])
        )
        - len(routes),
        engine_returned_alternates=sum(
            max(0, len(x.get("native_routes", [])) - 1) for x in attempts
        ),
        requested_count_reached=len(routes) >= request.alternates + 1,
    )
    common["search"]["reasons"] = ["APPROXIMATE_PATH_HYPOTHESES"]
    common["engine_diagnostics"]["snap_attempts"] = attempts
    return service._alternatives_result(common)
