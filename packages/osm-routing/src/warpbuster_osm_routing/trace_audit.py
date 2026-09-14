"""Identity-preserving trace audit, with a narrow partial-edge direction recovery."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, NoReturn

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import decode_polyline6, finite_number, haversine_m
from warpbuster_osm_routing.models import GeoPoint
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, apply_profile

POLICY = "direction-safe-trace-v1"


def fail(check: str, **details: Any) -> NoReturn:
    raise RoutingError(
        "ROUTE_AUDIT_FAILED",
        f"trace audit failed: {check}",
        {"stage": "trace_audit", "check": check, **details},
    )


@dataclass
class AuditBudget:
    remaining_points: int
    remaining_edges: int
    response_bytes: int

    def response(self, raw: object) -> Any:
        if not isinstance(raw, str):
            fail("response_type")
        assert isinstance(raw, str)
        if len(raw) > self.response_bytes:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "operation response byte budget exceeded")
        size = len(raw.encode("utf-8"))
        if size > self.response_bytes:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "operation response byte budget exceeded")
        self.response_bytes -= size
        try:
            return json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED",
                "invalid trace JSON",
                {"stage": "trace_audit", "check": "response_json"},
            ) from error

    def shape(self, encoded: str, per_shape_limit: int) -> tuple[GeoPoint, ...]:
        points = decode_polyline6(
            encoded, maximum_points=min(per_shape_limit, self.remaining_points)
        )
        self.remaining_points -= len(points)
        return points

    def edges(self, count: int) -> None:
        if count > self.remaining_edges:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "operation edge budget exceeded")
        self.remaining_edges -= count


def fractions(edge: dict[str, Any]) -> tuple[float, float]:
    a, b = edge.get("source_percent_along"), edge.get("target_percent_along")
    if (
        not isinstance(a, int | float)
        or not isinstance(b, int | float)
        or not finite_number(a)
        or not finite_number(b)
        or not 0 <= a <= 1
        or not 0 <= b <= 1
    ):
        fail("edge_percent_along")
    return float(a), float(b)


def audit_trace(
    actor: Any,
    request: dict[str, Any],
    points: tuple[GeoPoint, ...],
    config: RoutingCacheConfig,
    budget: AuditBudget,
    normalize: Callable[[dict[str, Any], int], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context: dict[str, Any] = {"mode": "edge_walk"}
    try:
        return _audit_trace(actor, request, points, config, budget, normalize, context)
    except RoutingError as error:
        raise RoutingError(
            error.code,
            error.message,
            {
                "stage": "trace_audit",
                "check": "trace_response",
                **error.details,
                "mode": context["mode"],
                "attempts": context.get("attempts", []),
            },
        ) from error


def _audit_trace(
    actor: Any,
    request: dict[str, Any],
    points: tuple[GeoPoint, ...],
    config: RoutingCacheConfig,
    budget: AuditBudget,
    normalize: Callable[[dict[str, Any], int], list[dict[str, Any]]],
    context: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    context["attempts"] = attempts

    def trace(mode: str) -> tuple[dict[str, Any], tuple[GeoPoint, ...], list[dict[str, Any]]]:
        context["mode"] = mode
        payload = deepcopy(request)
        payload["shape_match"] = mode
        try:
            raw = actor.trace_attributes(json.dumps(payload, separators=(",", ":")))
        except Exception as error:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED",
                "Valhalla trace request failed",
                {"stage": "trace_audit", "mode": mode, "check": "backend"},
            ) from error
        response = budget.response(raw)
        if not isinstance(response, dict) or not isinstance(response.get("shape"), str):
            fail("trace_shape_missing", mode=mode)
        traced = budget.shape(response["shape"], config.maximum_route_shape_points)
        raw_edges = response.get("edges")
        if not isinstance(raw_edges, list):
            fail("trace_edges_missing", mode=mode)
        budget.edges(len(raw_edges))
        # Validate IDs, access, terrain and indices BEFORE deciding on recovery.
        edges = normalize(response, len(traced))
        attempts.append(
            {
                "mode": mode,
                "point_count": len(traced),
                "edge_count": len(edges),
                "last_end_index": edges[-1]["end_shape_index"],
                "geometry_sha256": hashlib.sha256(response["shape"].encode()).hexdigest(),
            }
        )
        return response, traced, edges

    mode = "edge_walk"
    response, traced, edges = trace(mode)
    fallback = False
    direction: dict[str, Any] | None = None
    if traced != points:
        collapsed = (
            len(traced) == len(edges) == 1
            and traced[0] == points[-1]
            and edges[0]["begin_shape_index"] == edges[0]["end_shape_index"] == 0
            and response["edges"][0].get("length") == 0
        )
        if not collapsed:
            fail("trace_shape_identity", mode=mode, attempts=attempts)
        source, target = fractions(response["edges"][0])
        if source <= target or config.maximum_trace_fallback_attempts_per_route == 0:
            fail("trace_shape_identity", mode=mode, attempts=attempts)
        rejected = response["edges"][0]
        initial_way = edges[0]["way_id"]
        attempts[0]["rejection"] = "collapsed_reverse_partial_edge"
        mode = "map_snap"
        response, traced, edges = trace(mode)
        fallback = True
        if traced != points:
            fail("trace_shape_identity", mode=mode, attempts=attempts)
        if len(edges) != 1 or edges[0]["way_id"] != initial_way or edges[0]["length_m"] <= 0:
            fail("fallback_single_way", mode=mode, attempts=attempts)
        if response.get("alternate_paths") != []:
            fail("trace_ambiguity", mode=mode)
        source, target = fractions(response["edges"][0])
        if source >= target:
            fail("trace_direction", mode=mode)
        direction = directional_metadata(
            actor, points, edges[0], config, budget, rejected, response["edges"][0]
        )

    previous = 0
    for raw_edge, edge in zip(response["edges"], edges, strict=True):
        # Valhalla serializes only trimmed endpoints: full interior edges have
        # neither field. Validate every supplied fraction without inventing audit data.
        for key in ("source_percent_along", "target_percent_along"):
            if key in raw_edge and (
                not finite_number(raw_edge[key]) or not 0 <= raw_edge[key] <= 1
            ):
                fail("edge_percent_along", mode=mode)
        begin, end = edge["begin_shape_index"], edge["end_shape_index"]
        if "source_percent_along" in raw_edge and "target_percent_along" in raw_edge:
            source, target = fractions(raw_edge)
            if source > target or (end > begin and source == target):
                fail("trace_direction", mode=mode)
        if begin != previous:
            fail("complete_edge_spans", mode=mode)
        previous = end
    if previous != len(points) - 1:
        fail("complete_edge_spans", mode=mode)
    return edges, {
        "policy_version": POLICY,
        "mode": mode,
        "fallback_used": fallback,
        "attempts": attempts,
        "route_point_count": len(points),
        "route_geometry_sha256": hashlib.sha256(request["encoded_polyline"].encode()).hexdigest(),
        "trace_geometry_sha256": hashlib.sha256(response["shape"].encode()).hexdigest(),
        "shape_identity": "PASS",
        "complete_edge_spans": "PASS",
        "direction": "PASS",
        "direction_evidence": direction,
        "maximum_trace_fallback_attempts_per_route": config.maximum_trace_fallback_attempts_per_route,
        "maximum_trace_metadata_queries_per_route": config.maximum_trace_metadata_queries_per_route,
        "trace_edge_projection_tolerance_m": config.trace_edge_projection_tolerance_m,
        "trace_percent_along_tolerance": config.trace_percent_along_tolerance,
    }


def _projection(
    point: GeoPoint, shape: tuple[GeoPoint, ...], tolerance: float
) -> tuple[int, float] | None:
    """Unique chainage projection, linear in shape size; repeated locations refuse."""
    found: list[tuple[int, float, float]] = []
    cumulative = 0.0
    scale = math.cos(math.radians(point.latitude))

    def vector(p: GeoPoint) -> tuple[float, float]:
        return (
            ((p.longitude - point.longitude + 180) % 360 - 180) * scale,
            p.latitude - point.latitude,
        )

    for i, (a, b) in enumerate(pairwise(shape)):
        ax, ay = vector(a)
        bx, by = vector(b)
        dx, dy = bx - ax, by - ay
        denominator = dx * dx + dy * dy
        length = haversine_m(a, b)
        if denominator:
            f = max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
            delta = (b.longitude - a.longitude + 180) % 360 - 180
            projected = GeoPoint(
                a.latitude + f * (b.latitude - a.latitude),
                (a.longitude + f * delta + 180) % 360 - 180,
            )
            error = haversine_m(projected, point)
            if error <= tolerance:
                found.append((i, cumulative + f * length, error))
        cumulative += length
    if not found or max(x[1] for x in found) - min(x[1] for x in found) > tolerance:
        return None
    best = min(found, key=lambda x: (x[2], x[0]))
    return best[0], best[1]


def directional_metadata(
    actor: Any,
    points: tuple[GeoPoint, ...],
    edge: dict[str, Any],
    config: RoutingCacheConfig,
    budget: AuditBudget,
    rejected: dict[str, Any],
    accepted: dict[str, Any],
) -> dict[str, Any]:
    """Inspect both route endpoints; do not select or change any snap.

    Valhalla locate edge_info.shape follows OSM node order; edge.forward orients
    that shape for the directed ID. Native tests exercise both orientations.
    """
    if config.maximum_trace_metadata_queries_per_route == 0:
        fail("direction_metadata_disabled")
    request = {
        "locations": [
            {
                **p.as_valhalla(),
                "radius": config.snap_search_radius_m,
                "search_cutoff": config.snap_search_radius_m,
                "minimum_reachability": 0,
            }
            for p in (points[0], points[-1])
        ],
        "verbose": True,
    }
    apply_profile(request, TRAIL_RUNNING_V1)
    try:
        raw = actor.locate(json.dumps(request, separators=(",", ":")))
    except Exception as error:
        raise RoutingError(
            "ROUTE_AUDIT_FAILED",
            "direction metadata request failed",
            {"stage": "trace_audit", "check": "direction_metadata"},
        ) from error
    response = budget.response(raw)
    if not isinstance(response, list) or len(response) != 2:
        fail("direction_metadata")
    inventories: list[dict[int, dict[str, Any]]] = []
    for item in response:
        if not isinstance(item, dict) or not isinstance(item.get("edges"), list):
            fail("direction_metadata")
        raw_edges = item["edges"]
        if len(raw_edges) > config.maximum_snap_candidates:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "trace metadata candidate limit exceeded")
        budget.edges(len(raw_edges))
        inventory: dict[int, dict[str, Any]] = {}
        for candidate in raw_edges:
            if not isinstance(candidate, dict):
                fail("direction_metadata")
            identifier = candidate.get("edge_id")
            identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
            info, data = candidate.get("edge_info"), candidate.get("edge")
            if (
                type(identifier) is not int
                or identifier < 0
                or not isinstance(info, dict)
                or not isinstance(data, dict)
            ):
                fail("direction_metadata")
            if identifier in inventory and candidate != inventory[identifier]:
                fail("direction_metadata_duplicate")
            inventory[identifier] = candidate
        inventories.append(inventory)
    selected_id, rejected_id = edge["edge_id"], rejected["id"]
    if any(selected_id not in inv or rejected_id not in inv for inv in inventories):
        fail("direction_metadata_incomplete")
    selected, reverse = inventories[0][selected_id], inventories[0][rejected_id]
    if (
        selected["edge_info"] != reverse["edge_info"]
        or type(selected["edge"].get("forward")) is not bool
        or type(reverse["edge"].get("forward")) is not bool
        or selected["edge"]["forward"] == reverse["edge"]["forward"]
        or selected["edge"].get("end_node") == reverse["edge"].get("end_node")
    ):
        fail("opposite_edge_metadata")
    for raw_edge in (rejected, accepted):
        a_fraction, b_fraction = fractions(raw_edge)
        for raw_fraction, inv in zip((a_fraction, b_fraction), inventories, strict=True):
            located = inv[raw_edge["id"]].get("percent_along")
            if (
                not isinstance(located, int | float)
                or not finite_number(located)
                or abs(located - raw_fraction) > config.trace_percent_along_tolerance
            ):
                fail("trace_locate_fraction_mismatch")
    possible = []
    for identifier in sorted(inventories[0].keys() & inventories[1].keys()):
        a, b = inventories[0][identifier], inventories[1][identifier]
        info, data = a["edge_info"], a["edge"]
        if info != b["edge_info"] or data != b["edge"]:
            fail("direction_metadata_conflict")
        nodes, encoded = info.get("osm_node_ids"), info.get("shape")
        if (
            not isinstance(nodes, list)
            or len(nodes) < 2
            or type(data.get("forward")) is not bool
            or not isinstance(encoded, str)
            or not isinstance(data.get("end_node"), dict)
            or type(data["end_node"].get("value")) is not int
            or data["end_node"]["value"] < 0
            or any(type(node) is not int or node <= 0 for node in nodes)
            or type(info.get("way_id")) is not int
            or info["way_id"] <= 0
        ):
            fail("direction_metadata_incomplete")
        shape = budget.shape(encoded, config.maximum_route_shape_points)
        oriented = shape if data["forward"] else tuple(reversed(shape))
        start = _projection(points[0], oriented, config.trace_edge_projection_tolerance_m)
        end = _projection(points[-1], oriented, config.trace_edge_projection_tolerance_m)
        if start is None or end is None or start[1] >= end[1]:
            continue
        if tuple(oriented[start[0] + 1 : end[0] + 1]) != points[1:-1]:
            continue
        source, target = a.get("percent_along"), b.get("percent_along")
        if (
            not isinstance(source, int | float)
            or not isinstance(target, int | float)
            or not finite_number(source)
            or not finite_number(target)
            or not 0 <= source < target <= 1
        ):
            fail("direction_metadata_fraction")
        # Count all geometry-compatible IDs, even other coincident ways. Never rank.
        possible.append(
            (identifier, info.get("way_id"), data["forward"], nodes, encoded, source, target)
        )
    if len(possible) != 1 or possible[0][0] != edge["edge_id"] or possible[0][1] != edge["way_id"]:
        fail("direction_identity_ambiguous", compatible_edge_count=len(possible))
    identifier, way, forward, nodes, encoded, source, target = possible[0]
    return {
        "source": "verified_graph_locate",
        "metadata_queries": 1,
        "edge_id": identifier,
        "rejected_opposite_edge_id": rejected_id,
        "way_id": way,
        "forward": forward,
        "osm_endpoint_nodes": [nodes[0], nodes[-1]],
        "edge_geometry_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        "source_percent_along": source,
        "target_percent_along": target,
        "compatible_directed_edge_count": 1,
    }
