"""Stable audited single-route service built on an exact cached graph."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import valhalla

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.coverage import parse_coverage
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import (
    bounds,
    decode_polyline6,
    haversine_m,
    path_length_m,
    valid_wgs84,
)
from warpbuster_osm_routing.graph_cache import GRAPH_MANIFEST_VERSION, GraphCache
from warpbuster_osm_routing.models import RouteRequest, RouteResult, RouteStatus
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, apply_profile
from warpbuster_osm_routing.snapping import SnapCandidate, SnapDecision, audit_snap

_NEGATIVE_PRECEDENCE = ("OUTSIDE_COVERAGE", "NO_SNAP", "AMBIGUOUS_SNAP")
_FERRY_USES = frozenset({"ferry", "rail-ferry", "rail_ferry"})
_IMPASSABLE_SURFACES = frozenset({"impassable", 7})
_MAX_ALLOWED_SAC_SCALE = 3


class RouteService:
    """Execute bounded route queries without exposing raw Valhalla requests."""

    def __init__(self, config: RoutingCacheConfig) -> None:
        self.config = config.validated()
        self.cache = GraphCache(self.config)

    def route(self, request: RouteRequest) -> RouteResult:
        self._validate_request(request)
        graph = self.cache.inspect(request.graph_id)
        if graph.document.get("manifest_version") != GRAPH_MANIFEST_VERSION:
            raise RoutingError(
                "GRAPH_CAPABILITY_MISSING",
                "graph does not contain audited coverage; prepare the source snapshot again",
                {"graph_id": request.graph_id, "required_manifest_version": 2},
            )
        source = graph.document.get("source", {})
        coverage = parse_coverage(source.get("coverage"))
        config_path = graph.manifest_path.parent / "valhalla.json"
        try:
            actor_config = json.loads(config_path.read_text(encoding="utf-8"))
            actor = valhalla.Actor(actor_config)
        except Exception as error:
            raise RoutingError(
                "VALHALLA_REQUEST_FAILED", f"cannot initialize Valhalla: {error}"
            ) from error

        start = audit_snap(actor, request.start, coverage, self.config)
        end = audit_snap(actor, request.end, coverage, self.config)
        common = self._base_document(request, graph.document, start, end)
        for status in _NEGATIVE_PRECEDENCE:
            if status in {start.status, end.status}:
                result_status = RouteStatus(status)
                common.update({"status": result_status.value, "route": None})
                return RouteResult(result_status, common)

        assert start.selected is not None and end.selected is not None
        route = self._query_route(actor, request, start.selected, end.selected)
        if route is None:
            common.update({"status": RouteStatus.NO_ROUTE.value, "route": None})
            return RouteResult(RouteStatus.NO_ROUTE, common)
        common.update({"status": RouteStatus.READY.value, "route": route})
        coordinates = decode_polyline6(route["geometry"]["encoded_polyline"])
        return RouteResult(RouteStatus.READY, common, coordinates)

    def _validate_request(self, request: RouteRequest) -> None:
        if not request.graph_id:
            raise RoutingError("INVALID_GRAPH_ID", "graph_id must not be empty")
        if not valid_wgs84(request.start) or not valid_wgs84(request.end):
            raise RoutingError("INVALID_REQUEST", "route anchors must be finite WGS84 points")
        direct_distance = haversine_m(request.start, request.end)
        if direct_distance > self.config.maximum_route_distance_m:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "anchor separation exceeds maximum route distance",
                {"distance_m": direct_distance, "limit_m": self.config.maximum_route_distance_m},
            )

    def _base_document(
        self,
        request: RouteRequest,
        graph: dict[str, Any],
        start: SnapDecision,
        end: SnapDecision,
    ) -> dict[str, Any]:
        key = graph["cache_key"]
        source = graph["source"]
        return {
            "protocol_version": 1,
            "operation": "route",
            "status": None,
            "request": {
                "start": {"latitude": request.start.latitude, "longitude": request.start.longitude},
                "end": {"latitude": request.end.latitude, "longitude": request.end.longitude},
            },
            "graph": {
                "graph_id": request.graph_id,
                "graph_manifest_version": graph["manifest_version"],
                "snapshot_id": source["snapshot_id"],
                "source_sha256": key["source_sha256"],
                "materializer_schema": key["materializer_schema"],
                "build_profile": key["build_profile"],
                "build_config_sha256": key["build_config_sha256"],
                "valhalla_version": key["runtime"]["valhalla"],
                "coverage": source["coverage"],
            },
            "profile": {
                "profile_id": TRAIL_RUNNING_V1.profile_id,
                "profile_sha256": TRAIL_RUNNING_V1.sha256(),
            },
            "query_policy": self.config.query_policy_dict(),
            "snapping": {"start": start.document, "end": end.document},
        }

    def _query_route(
        self,
        actor: Any,
        request: RouteRequest,
        start: SnapCandidate,
        end: SnapCandidate,
    ) -> dict[str, Any] | None:
        locations = [
            {
                **point.as_valhalla(),
                "radius": self.config.snap_search_radius_m,
                "search_cutoff": self.config.snap_search_radius_m,
                "minimum_reachability": 0,
            }
            for point in (request.start, request.end)
        ]
        route_request: dict[str, Any] = {
            "locations": locations,
            "units": "kilometers",
            "directions_type": "none",
            "alternates": 0,
        }
        apply_profile(route_request, TRAIL_RUNNING_V1)
        try:
            response = json.loads(
                actor.route(json.dumps(route_request, separators=(",", ":")))
            )
        except Exception as error:
            if _is_no_route_error(error):
                return None
            raise RoutingError(
                "VALHALLA_REQUEST_FAILED", f"Valhalla route request failed: {error}"
            ) from error
        trip = response.get("trip") if isinstance(response, dict) else None
        if not isinstance(trip, dict):
            return None
        return self._audit_trip(actor, trip, start, end)

    def _audit_trip(
        self,
        actor: Any,
        trip: dict[str, Any],
        start: SnapCandidate,
        end: SnapCandidate,
    ) -> dict[str, Any]:
        legs = trip.get("legs")
        if not isinstance(legs, list) or len(legs) != 1 or not isinstance(legs[0], dict):
            raise RoutingError("ROUTE_AUDIT_FAILED", "route must contain exactly one leg")
        encoded = legs[0].get("shape")
        if not isinstance(encoded, str) or not encoded:
            raise RoutingError("ROUTE_AUDIT_FAILED", "route has no encoded geometry")
        points = decode_polyline6(encoded)
        if not 2 <= len(points) <= self.config.maximum_route_shape_points:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "decoded route point count exceeds configured bounds",
                {"count": len(points), "limit": self.config.maximum_route_shape_points},
            )
        start_delta = haversine_m(points[0], start.point)
        end_delta = haversine_m(points[-1], end.point)
        if max(start_delta, end_delta) > self.config.route_endpoint_tolerance_m:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED",
                "route endpoints do not match audited snaps",
                {
                    "start_delta_m": start_delta,
                    "end_delta_m": end_delta,
                    "tolerance_m": self.config.route_endpoint_tolerance_m,
                },
            )
        summary = trip.get("summary")
        if not isinstance(summary, dict) or not _finite(summary.get("length")):
            raise RoutingError("ROUTE_AUDIT_FAILED", "route has no finite summary length")
        summary_length_m = float(summary["length"]) * 1000.0
        geometry_length_m = path_length_m(points)
        if summary_length_m > self.config.maximum_route_distance_m:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "route distance exceeds configured limit",
                {"distance_m": summary_length_m, "limit_m": self.config.maximum_route_distance_m},
            )
        tolerance = max(
            self.config.route_length_absolute_tolerance_m,
            summary_length_m * self.config.route_length_relative_tolerance,
        )
        if abs(summary_length_m - geometry_length_m) > tolerance:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED",
                "route geometry length does not match summary",
                {
                    "summary_length_m": summary_length_m,
                    "geometry_length_m": geometry_length_m,
                    "tolerance_m": tolerance,
                },
            )
        edges = self._trace_edges(actor, encoded, len(points))
        warnings: list[dict[str, Any]] = []
        if start.destination_only:
            warnings.append({"code": "DESTINATION_ONLY_SNAP", "anchor": "start"})
        if end.destination_only:
            warnings.append({"code": "DESTINATION_ONLY_SNAP", "anchor": "end"})
        if any(edge["use"] in _FERRY_USES for edge in edges):
            warnings.append({"code": "FERRY_USED"})
        checks = [
            {"name": "geometry", "status": "PASS"},
            {
                "name": "endpoints",
                "status": "PASS",
                "start_delta_m": round(start_delta, 3),
                "end_delta_m": round(end_delta, 3),
            },
            {
                "name": "length",
                "status": "PASS",
                "delta_m": round(abs(summary_length_m - geometry_length_m), 3),
                "tolerance_m": round(tolerance, 3),
            },
            {"name": "ordered_edges", "status": "PASS", "count": len(edges)},
        ]
        return {
            "summary": {
                "length_m": round(summary_length_m, 3),
                "time_seconds": summary.get("time"),
                "cost": summary.get("cost"),
            },
            "geometry": {
                "encoding": "polyline6",
                "encoded_polyline": encoded,
                "geometry_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "point_count": len(points),
                "bounds": bounds(points),
                "calculated_length_m": round(geometry_length_m, 3),
            },
            "edges": edges,
            "audit": {"status": "WARN" if warnings else "PASS", "checks": checks},
            "warnings": warnings,
        }

    def _trace_edges(
        self, actor: Any, encoded: str, point_count: int
    ) -> list[dict[str, Any]]:
        trace_request: dict[str, Any] = {
            "encoded_polyline": encoded,
            "shape_match": "edge_walk",
            "filters": {
                "action": "include",
                "attributes": [
                    "edge.id",
                    "edge.length",
                    "edge.begin_shape_index",
                    "edge.end_shape_index",
                    "edge.sac_scale",
                    "edge.surface",
                    "edge.use",
                    "edge.unpaved",
                    "edge.travel_mode",
                    "edge.pedestrian_type",
                    "edge.way_id",
                ],
            },
        }
        apply_profile(trace_request, TRAIL_RUNNING_V1)
        try:
            response = json.loads(
                actor.trace_attributes(json.dumps(trace_request, separators=(",", ":")))
            )
        except Exception as error:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED", f"Valhalla trace audit failed: {error}"
            ) from error
        raw_edges = response.get("edges") if isinstance(response, dict) else None
        if not isinstance(raw_edges, list) or not raw_edges:
            raise RoutingError("ROUTE_AUDIT_FAILED", "trace audit returned no edges")
        if len(raw_edges) > self.config.maximum_route_edges:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "route edge count exceeds configured limit",
                {"count": len(raw_edges), "limit": self.config.maximum_route_edges},
            )
        edges: list[dict[str, Any]] = []
        previous_begin = -1
        previous_end = -1
        for sequence, raw in enumerate(raw_edges):
            if not isinstance(raw, dict):
                raise RoutingError("ROUTE_AUDIT_FAILED", "trace audit returned invalid edge")
            edge_id = raw.get("id")
            way_id = raw.get("way_id")
            begin, end = raw.get("begin_shape_index"), raw.get("end_shape_index")
            indexes_valid = (
                isinstance(begin, int)
                and not isinstance(begin, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and 0 <= begin <= end < point_count
                and begin >= previous_begin
                and end >= previous_end
                and (sequence == 0 or begin <= previous_end + 1)
            )
            if edge_id is None or way_id is None:
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge lacks provenance IDs")
            if not indexes_valid:
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge shape indexes are invalid")
            assert isinstance(begin, int) and isinstance(end, int)
            if raw.get("travel_mode") != "pedestrian" or raw.get("pedestrian_type") != "foot":
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge is not pedestrian/foot")
            sac_scale = raw.get("sac_scale", 0)
            if not isinstance(sac_scale, int | float) or sac_scale > _MAX_ALLOWED_SAC_SCALE:
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge exceeds allowed sac_scale")
            surface = raw.get("surface")
            if surface in _IMPASSABLE_SURFACES:
                raise RoutingError("ROUTE_AUDIT_FAILED", "route contains an impassable surface")
            length = raw.get("length")
            if not isinstance(length, int | float) or isinstance(length, bool):
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge has invalid length")
            length_value = float(length)
            if not math.isfinite(length_value) or length_value < 0:
                raise RoutingError("ROUTE_AUDIT_FAILED", "route edge has invalid length")
            edges.append(
                {
                    "sequence": sequence,
                    "edge_id": edge_id,
                    "way_id": way_id,
                    "length_m": round(length_value * 1000.0, 3),
                    "begin_shape_index": begin,
                    "end_shape_index": end,
                    "use": raw.get("use"),
                    "surface": surface,
                    "sac_scale": sac_scale,
                    "unpaved": raw.get("unpaved"),
                    "travel_mode": raw.get("travel_mode"),
                    "pedestrian_type": raw.get("pedestrian_type"),
                }
            )
            previous_begin, previous_end = begin, end
        return edges


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _is_no_route_error(error: Exception) -> bool:
    diagnostic = str(error).lower()
    return "no path could be found" in diagnostic or any(
        marker in diagnostic for marker in ('"error_code":442', '"error_code": 442')
    )
