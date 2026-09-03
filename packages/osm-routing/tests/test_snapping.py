from __future__ import annotations

import json
from dataclasses import replace

import pytest

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.coverage import parse_coverage
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import haversine_m
from warpbuster_osm_routing.models import GeoPoint, SnapshotCoverage
from warpbuster_osm_routing.snapping import audit_snap


class LocateActor:
    def __init__(self, edges: list[dict[str, object]]) -> None:
        self.edges = edges
        self.called = False

    def locate(self, request: str) -> str:
        self.called = True
        document = json.loads(request)
        assert document["locations"][0]["radius"] == 100
        return json.dumps([{"edges": self.edges}])


def _coverage() -> SnapshotCoverage:
    return parse_coverage(
        {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000,
            "area_km2": 1,
        }
    )


def _edge(
    latitude: float,
    longitude: float,
    way_id: int,
    edge_id: int,
    *,
    node_ids: list[int] | None = None,
    shape: str | None = None,
) -> dict[str, object]:
    edge_info: dict[str, object] = {
        "way_id": way_id,
        "osm_node_ids": node_ids or [1, 2],
    }
    if shape is not None:
        edge_info["shape"] = shape
    return {
        "correlated_lat": latitude,
        "correlated_lon": longitude,
        "distance": 0.001,
        "percent_along": 0.5,
        "edge_id": {"value": edge_id},
        "edge_info": edge_info,
        "edge": {
            "forward": True,
            "end_node": {"value": 2},
            "classification": {"use": "path", "surface": "dirt"},
            "access": {"pedestrian": True},
        },
    }


def test_same_way_directed_candidates_are_one_group() -> None:
    actor = LocateActor([_edge(44.0, 33.00001, 101, 1), _edge(44.0, 33.00001, 101, 2)])

    decision = audit_snap(actor, GeoPoint(44.0, 33.0), _coverage(), RoutingCacheConfig.defaults())

    assert decision.status == "ACCEPTED"
    assert decision.document["candidate_group_count"] == 1
    assert decision.document["candidate_groups"][0]["candidate_count"] == 2


def test_near_parallel_distinct_ways_are_ambiguous() -> None:
    actor = LocateActor([_edge(44.0, 33.00001, 101, 1), _edge(44.0, 33.00002, 102, 2)])

    decision = audit_snap(actor, GeoPoint(44.0, 33.0), _coverage(), RoutingCacheConfig.defaults())

    assert decision.status == "AMBIGUOUS_SNAP"
    assert decision.document["candidate_group_count"] == 2


def test_candidate_beyond_maximum_distance_is_no_snap() -> None:
    actor = LocateActor([_edge(44.0, 33.001, 101, 1)])

    decision = audit_snap(actor, GeoPoint(44.0, 33.0), _coverage(), RoutingCacheConfig.defaults())

    assert decision.status == "NO_SNAP"


def test_outside_coverage_does_not_call_valhalla() -> None:
    actor = LocateActor([])

    decision = audit_snap(actor, GeoPoint(45.0, 34.0), _coverage(), RoutingCacheConfig.defaults())

    assert decision.status == "OUTSIDE_COVERAGE"
    assert not actor.called


def test_maximum_snap_distance_boundary_is_inclusive() -> None:
    input_point = GeoPoint(44.0, 33.0)
    candidate_point = GeoPoint(44.0, 33.0001)
    limit = haversine_m(input_point, candidate_point)
    config = replace(
        RoutingCacheConfig.defaults(),
        maximum_snap_distance_m=limit,
    )

    decision = audit_snap(
        LocateActor([_edge(candidate_point.latitude, candidate_point.longitude, 101, 1)]),
        input_point,
        _coverage(),
        config,
    )

    assert decision.status == "ACCEPTED"


def test_candidate_limit_is_enforced_before_grouping() -> None:
    config = replace(RoutingCacheConfig.defaults(), maximum_snap_candidates=1)
    actor = LocateActor([_edge(44.0, 33.0, 101, 1), _edge(44.0, 33.0, 101, 2)])

    with pytest.raises(RoutingError) as caught:
        audit_snap(actor, GeoPoint(44.0, 33.0), _coverage(), config)

    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"


def test_report_is_bounded_without_changing_ambiguity_decision() -> None:
    actor = LocateActor(
        [_edge(44.0, 33.0 + index / 1_000_000, 100 + index, index) for index in range(4)]
    )
    config = replace(RoutingCacheConfig.defaults(), maximum_reported_candidate_groups=1)

    decision = audit_snap(actor, GeoPoint(44.0, 33.0), _coverage(), config)

    assert decision.status == "AMBIGUOUS_SNAP"
    assert len(decision.document["candidate_groups"]) == 1
    assert decision.document["candidate_groups_truncated"] is True


def test_distinct_ways_at_proven_shared_endpoint_are_one_group() -> None:
    first_shape = _encode_polyline6([(44.0, 33.0), (44.0, 33.001)])
    second_shape = _encode_polyline6([(44.0, 33.001), (44.001, 33.001)])
    actor = LocateActor(
        [
            _edge(
                44.0,
                33.001,
                101,
                1,
                node_ids=[10, 20],
                shape=first_shape,
            ),
            _edge(
                44.0,
                33.001,
                102,
                2,
                node_ids=[20, 30],
                shape=second_shape,
            ),
        ]
    )

    decision = audit_snap(actor, GeoPoint(44.0, 33.001), _coverage(), RoutingCacheConfig.defaults())

    assert decision.status == "ACCEPTED"
    assert decision.document["candidate_group_count"] == 1


def _encode_polyline6(points: list[tuple[float, float]]) -> str:
    previous_latitude = previous_longitude = 0
    output: list[str] = []
    for latitude, longitude in points:
        latitude_value = round(latitude * 1_000_000)
        longitude_value = round(longitude * 1_000_000)
        for delta in (
            latitude_value - previous_latitude,
            longitude_value - previous_longitude,
        ):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                output.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            output.append(chr(value + 63))
        previous_latitude = latitude_value
        previous_longitude = longitude_value
    return "".join(output)
