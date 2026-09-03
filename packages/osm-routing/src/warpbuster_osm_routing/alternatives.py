"""Bounded route identity and advisory comparisons, without selecting a repair."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from itertools import combinations, pairwise
from typing import Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import decode_polyline6, haversine_m


@dataclass(frozen=True)
class WeightedRoute:
    weights: dict[int, float]
    length_m: float
    repeated_edges: bool


def geometry_weights(route: dict[str, Any]) -> WeightedRoute:
    """Assign each actual shape segment once, including partial endpoint edges."""
    points = decode_polyline6(route["geometry"]["encoded_polyline"])
    segments = [haversine_m(a, b) for a, b in pairwise(points)]
    weights: dict[int, float] = defaultdict(float)
    previous_end = 0
    repeated = False
    for edge in route["edges"]:
        begin, end = edge["begin_shape_index"], edge["end_shape_index"]
        edge_id = edge["edge_id"]
        if (
            type(begin) is not int
            or type(end) is not int
            or not 0 <= begin <= end < len(points)
            or begin != previous_end
        ):
            raise RoutingError(
                "ROUTE_AUDIT_FAILED",
                "edge spans must cover each shape segment once",
                {"check": "complete_edge_spans"},
            )
        if type(edge_id) is not int or edge_id < 0:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED", "invalid directed edge identity", {"check": "edge_identity"}
            )
        repeated = repeated or edge_id in weights
        weights[edge_id] += math.fsum(segments[begin:end])
        previous_end = end
    if previous_end != len(points) - 1:
        raise RoutingError(
            "ROUTE_AUDIT_FAILED",
            "edge spans do not cover the route end",
            {"check": "complete_edge_spans"},
        )
    length = math.fsum(weights.values())
    if length <= 0:
        raise RoutingError(
            "ROUTE_AUDIT_FAILED",
            "route set requires positive geometry length",
            {"check": "positive_geometry_length"},
        )
    return WeightedRoute(dict(weights), length, repeated)


def route_identity(route: dict[str, Any], graph_id: str, profile_sha256: str) -> str:
    payload = {
        "schema": "audited-route-v1",
        "graph_id": graph_id,
        "profile_sha256": profile_sha256,
        "geometry": route["geometry"]["encoded_polyline"],
        "traversal": [
            [e["edge_id"], e["begin_shape_index"], e["end_shape_index"]] for e in route["edges"]
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


def compare_weights(
    first: WeightedRoute, second: WeightedRoute, config: RoutingCacheConfig
) -> dict[str, Any]:
    """A is the comparison baseline; all decisions use unrounded weights."""
    shared = math.fsum(
        min(weight, second.weights.get(edge, 0.0)) for edge, weight in first.weights.items()
    )
    # Guard harmless summation noise, not inconsistent route data.
    shared = min(shared, first.length_m, second.length_m)
    diversity = 1 - shared / min(first.length_m, second.length_m)
    ratio = second.length_m / first.length_m
    reasons = []
    if diversity < config.minimum_diversity_ratio:
        reasons.append("LOW_DIVERSITY")
    if ratio > config.detour_warning_ratio:
        reasons.append("LARGE_DETOUR")
    return {
        "metric": "directed_edge_weighted_v1",
        "shared_edge_weight_m": round(shared, 3),
        "overlap_a": round(shared / first.length_m, 6),
        "overlap_b": round(shared / second.length_m, 6),
        "diversity_ratio": round(diversity, 6),
        "length_delta_m": round(second.length_m - first.length_m, 3),
        "distance_ratio": round(ratio, 6),
        "reasons": reasons,
    }


def empty_route_set(requested: int, *, executed: bool = False) -> dict[str, Any]:
    """Unevaluated results cannot imply a unique or even an existing route."""
    return {
        "primary_route_id": None,
        "routes": [],
        "comparisons": [],
        "route_choice": {"status": "NOT_EVALUATED", "candidate_ids": []},
        "search": {
            "executed": executed,
            "exhaustive": False,
            "requested_alternates": requested,
            "engine_returned_routes": 0 if executed else None,
            "engine_returned_alternates": 0 if executed else None,
            "unique_alternates": 0,
            "duplicates_removed": 0,
            "requested_count_reached": False,
            "reasons": [],
        },
        "engine_diagnostics": {"slots": [], "duplicates": []},
    }


def build_route_set(
    routes: list[dict[str, Any]],
    graph_id: str,
    profile_sha256: str,
    requested: int,
    config: RoutingCacheConfig,
) -> dict[str, Any]:
    """Keep all exact-distinct paths. Engine slots are explicitly nonsemantic audit."""
    unique: dict[str, dict[str, Any]] = {}
    weighted: dict[str, WeightedRoute] = {}
    originals: dict[str, dict[str, Any]] = {}
    slots = []
    duplicates = []
    for index, original in enumerate(routes):
        slot = "primary" if index == 0 else f"alternative_{index}"
        route_id = route_identity(original, graph_id, profile_sha256)
        slots.append({"slot": slot, "route_id": route_id})
        if route_id in unique:
            if original != originals[route_id]:
                raise RoutingError(
                    "ROUTE_AUDIT_FAILED",
                    "exact duplicate has conflicting audit data",
                    {"check": "duplicate_consistency", "engine_slot": slot},
                )
            duplicates.append({"slot": slot, "duplicate_of": route_id})
            continue
        originals[route_id] = original
        route = deepcopy(original)
        route.update(route_id=route_id, role="primary" if index == 0 else "alternative")
        try:
            weighted[route_id] = geometry_weights(route)
        except RoutingError as error:
            raise RoutingError(
                error.code, error.message, {**error.details, "engine_slot": slot}
            ) from error
        if weighted[route_id].repeated_edges:
            _warn(route, "REPEATED_EDGE_TRAVERSAL")
        unique[route_id] = route
    primary = slots[0]["route_id"]
    ordered = [
        primary,
        *sorted(
            (key for key in unique if key != primary),
            key=lambda key: (round(weighted[key].length_m, 3), key),
        ),
    ]
    comparisons = []
    unique[primary]["vs_primary"] = None
    for first, second in combinations(ordered, 2):
        metrics = compare_weights(weighted[first], weighted[second], config)
        if (
            unique[first]["geometry"]["encoded_polyline"]
            == unique[second]["geometry"]["encoded_polyline"]
        ):
            metrics["reasons"].append("COINCIDENT_GEOMETRY_DIFFERENT_EDGES")
            for key in (first, second):
                _warn(unique[key], "COINCIDENT_GEOMETRY_DIFFERENT_EDGES")
        comparison = {"route_a_id": first, "route_b_id": second, **metrics}
        comparisons.append(comparison)
        if first == primary:
            unique[second]["vs_primary"] = deepcopy(comparison)
            for code in metrics["reasons"]:
                _warn(unique[second], code)
    reasons = []
    if len(routes) == 1:
        reasons.append("NO_ALTERNATIVES_RETURNED")
    elif len(routes) - 1 < requested:
        reasons.append("FEWER_ALTERNATIVES_RETURNED")
    if duplicates:
        reasons.append("EXACT_DUPLICATES_REMOVED")
    return {
        "primary_route_id": primary,
        "routes": [unique[key] for key in ordered],
        "comparisons": comparisons,
        "route_choice": {
            "status": "SINGLE_CANDIDATE" if len(ordered) == 1 else "MULTIPLE_CANDIDATES",
            "candidate_ids": ordered,
        },
        "search": {
            "executed": True,
            "exhaustive": False,
            "requested_alternates": requested,
            "engine_returned_routes": len(routes),
            "engine_returned_alternates": len(routes) - 1,
            "unique_alternates": len(ordered) - 1,
            "duplicates_removed": len(duplicates),
            "requested_count_reached": len(ordered) - 1 == requested,
            "reasons": reasons,
        },
        "engine_diagnostics": {"slots": slots, "duplicates": duplicates},
    }


def _warn(route: dict[str, Any], code: str) -> None:
    warning = {"code": code}
    if warning not in route["warnings"]:
        route["warnings"].append(warning)
    route["audit"]["status"] = "WARN"
