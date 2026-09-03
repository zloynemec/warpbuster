"""Bounded, explicit snapping decisions over Valhalla locate candidates."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.coverage import contains
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import decode_polyline6, haversine_m
from warpbuster_osm_routing.models import GeoPoint, SnapshotCoverage
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, apply_profile


@dataclass(frozen=True, slots=True)
class SnapCandidate:
    point: GeoPoint
    distance_m: float
    engine_distance: float | None
    edge_id: int | str | None
    way_id: int | str | None
    osm_node_ids: tuple[int | str, ...]
    percent_along: float | None
    forward: bool | None
    end_node_id: int | str | None
    use: str | int | None
    surface: str | int | None
    sac_scale: str | int | None
    pedestrian_access: bool | None
    destination_only: bool
    endpoint: tuple[int | str, GeoPoint] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlated": {
                "latitude": self.point.latitude,
                "longitude": self.point.longitude,
            },
            "distance_m": round(self.distance_m, 3),
            "engine_distance": self.engine_distance,
            "edge_id": self.edge_id,
            "way_id": self.way_id,
            "osm_node_ids": list(self.osm_node_ids),
            "percent_along": self.percent_along,
            "forward": self.forward,
            "end_node_id": self.end_node_id,
            "use": self.use,
            "surface": self.surface,
            "sac_scale": self.sac_scale,
            "pedestrian_access": self.pedestrian_access,
            "destination_only": self.destination_only,
        }


@dataclass(frozen=True, slots=True)
class SnapDecision:
    status: str
    selected: SnapCandidate | None
    document: dict[str, Any]


def audit_snap(
    actor: Any,
    point: GeoPoint,
    coverage: SnapshotCoverage,
    config: RoutingCacheConfig,
) -> SnapDecision:
    if not contains(coverage, point):
        return _decision("OUTSIDE_COVERAGE", point, (), None, config)
    request: dict[str, Any] = {
        "locations": [
            {
                **point.as_valhalla(),
                "radius": config.snap_search_radius_m,
                "search_cutoff": config.snap_search_radius_m,
                "minimum_reachability": 0,
            }
        ],
        "verbose": True,
    }
    apply_profile(request, TRAIL_RUNNING_V1)
    try:
        response = json.loads(actor.locate(json.dumps(request, separators=(",", ":"))))
    except Exception as error:
        raise RoutingError("VALHALLA_REQUEST_FAILED", f"Valhalla locate failed: {error}") from error
    raw_candidates = response[0].get("edges", []) if response else []
    if not isinstance(raw_candidates, list):
        raise RoutingError("VALHALLA_REQUEST_FAILED", "Valhalla locate returned invalid edges")
    if len(raw_candidates) > config.maximum_snap_candidates:
        raise RoutingError(
            "RESOURCE_LIMIT_EXCEEDED",
            "Valhalla locate candidate count exceeds configured limit",
            {"count": len(raw_candidates), "limit": config.maximum_snap_candidates},
        )
    normalized: list[SnapCandidate] = []
    for item in raw_candidates:
        candidate = _normalize_candidate(item, point, config)
        if candidate is not None:
            normalized.append(candidate)
    candidates = tuple(sorted(normalized, key=_candidate_key))
    groups = _group_candidates(candidates, config.equivalent_snap_separation_m)
    if not groups or groups[0][0].distance_m > config.maximum_snap_distance_m:
        return _decision("NO_SNAP", point, groups, None, config)
    selected = groups[0][0]
    if len(groups) > 1:
        second = groups[1][0]
        if second.distance_m <= selected.distance_m + config.snap_ambiguity_distance_delta_m:
            return _decision("AMBIGUOUS_SNAP", point, groups, None, config)
    return _decision("ACCEPTED", point, groups, selected, config)


def _normalize_candidate(
    item: object, input_point: GeoPoint, config: RoutingCacheConfig
) -> SnapCandidate | None:
    if not isinstance(item, dict):
        return None
    latitude, longitude = item.get("correlated_lat"), item.get("correlated_lon")
    if not _finite(latitude) or not _finite(longitude):
        return None
    assert isinstance(latitude, int | float)
    assert isinstance(longitude, int | float)
    point = GeoPoint(float(latitude), float(longitude))
    if not (-90 <= point.latitude <= 90 and -180 <= point.longitude <= 180):
        return None
    edge_info_value = item.get("edge_info")
    edge_info: dict[str, Any] = edge_info_value if isinstance(edge_info_value, dict) else {}
    edge_value = item.get("edge")
    edge: dict[str, Any] = edge_value if isinstance(edge_value, dict) else {}
    classification_value = edge.get("classification")
    classification: dict[str, Any] = (
        classification_value if isinstance(classification_value, dict) else {}
    )
    access_value = edge.get("access")
    access: dict[str, Any] = access_value if isinstance(access_value, dict) else {}
    edge_id = item.get("edge_id")
    if isinstance(edge_id, dict):
        edge_id = edge_id.get("value")
    end_node_id = edge.get("end_node")
    if isinstance(end_node_id, dict):
        end_node_id = end_node_id.get("value")
    node_ids = edge_info.get("osm_node_ids", [])
    if not isinstance(node_ids, list):
        node_ids = []
    engine_distance = item.get("distance")
    engine_distance = float(engine_distance) if isinstance(engine_distance, int | float) else None
    percent_along = item.get("percent_along")
    percent_along = float(percent_along) if isinstance(percent_along, int | float) else None
    forward = edge.get("forward") if isinstance(edge.get("forward"), bool) else None
    endpoint = _endpoint(item, point, tuple(node_ids), config.equivalent_snap_separation_m)
    return SnapCandidate(
        point=point,
        distance_m=haversine_m(input_point, point),
        engine_distance=engine_distance,
        edge_id=edge_id,
        way_id=edge_info.get("way_id"),
        osm_node_ids=tuple(node_ids),
        percent_along=percent_along,
        forward=forward,
        end_node_id=end_node_id,
        use=classification.get("use"),
        surface=classification.get("surface"),
        sac_scale=edge.get("sac_scale"),
        pedestrian_access=access.get("pedestrian")
        if isinstance(access.get("pedestrian"), bool)
        else None,
        destination_only=bool(edge.get("destination_only", False)),
        endpoint=endpoint,
    )


def _endpoint(
    item: dict[str, Any],
    point: GeoPoint,
    node_ids: tuple[int | str, ...],
    separation_m: float,
) -> tuple[int | str, GeoPoint] | None:
    edge_info = item.get("edge_info")
    shape = edge_info.get("shape") if isinstance(edge_info, dict) else None
    if not isinstance(shape, str) or len(node_ids) < 2:
        return None
    try:
        points = decode_polyline6(shape)
    except RoutingError:
        return None
    if len(points) < 2:
        return None
    endpoints = ((node_ids[0], points[0]), (node_ids[-1], points[-1]))
    nearest = min(endpoints, key=lambda pair: haversine_m(point, pair[1]))
    return nearest if haversine_m(point, nearest[1]) <= separation_m else None


def _group_candidates(
    candidates: tuple[SnapCandidate, ...], separation_m: float
) -> tuple[tuple[SnapCandidate, ...], ...]:
    groups: list[list[SnapCandidate]] = []
    for candidate in candidates:
        for group in groups:
            if any(_equivalent(candidate, other, separation_m) for other in group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    normalized = [tuple(sorted(group, key=_candidate_key)) for group in groups]
    return tuple(sorted(normalized, key=lambda group: _candidate_key(group[0])))


def _equivalent(first: SnapCandidate, second: SnapCandidate, separation_m: float) -> bool:
    if haversine_m(first.point, second.point) > separation_m:
        return False
    if first.way_id is not None and first.way_id == second.way_id:
        return True
    return (
        first.endpoint is not None
        and second.endpoint is not None
        and first.endpoint[0] == second.endpoint[0]
        and haversine_m(first.endpoint[1], second.endpoint[1]) <= separation_m
    )


def _decision(
    status: str,
    point: GeoPoint,
    groups: tuple[tuple[SnapCandidate, ...], ...],
    selected: SnapCandidate | None,
    config: RoutingCacheConfig,
) -> SnapDecision:
    reports = []
    for index, group in enumerate(groups[: config.maximum_reported_candidate_groups]):
        reports.append(
            {
                "group_index": index,
                "best_distance_m": round(group[0].distance_m, 3),
                "candidate_count": len(group),
                "candidates": [candidate.as_dict() for candidate in group],
            }
        )
    document = {
        "status": status,
        "input": {"latitude": point.latitude, "longitude": point.longitude},
        "selected": selected.as_dict() if selected else None,
        "candidate_count": sum(len(group) for group in groups),
        "candidate_group_count": len(groups),
        "candidate_groups": reports,
        "candidate_groups_truncated": len(groups) > len(reports),
    }
    return SnapDecision(status, selected, document)


def _candidate_key(candidate: SnapCandidate) -> tuple[float, str, str]:
    return candidate.distance_m, str(candidate.way_id), str(candidate.edge_id)


def _finite(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
