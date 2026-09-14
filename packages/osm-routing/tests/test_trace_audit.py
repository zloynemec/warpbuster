"""010G: trace shape identity and direction recovery, without route selection."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from tests.helpers import encode_polyline6, make_manifest
from warpbuster_osm_routing import GraphCache, RouteService, RoutingCacheConfig
from warpbuster_osm_routing.alternatives import route_identity
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import decode_polyline6
from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest, RouteRequest
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1
from warpbuster_osm_routing.trace_audit import AuditBudget

POINTS = [(44, 33.0002), (44, 33.001), (44, 33.002), (44, 33.0028)]
SHAPE = encode_polyline6(POINTS)
FULL = encode_polyline6([(44, 33), *POINTS[1:-1], (44, 33.003)])


def edge(identifier=10, begin=0, end=3, source=0.066, target=0.933):
    return {
        "id": identifier,
        "way_id": 101,
        "begin_shape_index": begin,
        "end_shape_index": end,
        "source_percent_along": source,
        "target_percent_along": target,
        "length": 0.2,
        "use": "path",
        "sac_scale": 0,
        "surface": "compacted",
        "unpaved": True,
        "travel_mode": "pedestrian",
        "pedestrian_type": "foot",
    }


class Actor:
    def __init__(self, collapsed=True):
        self.normal = {"shape": SHAPE, "edges": [edge()], "alternate_paths": []}
        self.initial = (
            {
                "shape": encode_polyline6([POINTS[-1]]),
                "edges": [{**edge(20, 0, 0, 0.933, 0.066), "length": 0}],
                "alternate_paths": [],
            }
            if collapsed
            else deepcopy(self.normal)
        )
        self.metadata = [
            {
                "edges": [
                    {
                        "edge_id": {"value": identifier},
                        "percent_along": value if forward else 1 - value,
                        "edge_info": {"way_id": 101, "shape": FULL, "osm_node_ids": [1, 2, 3, 4]},
                        "edge": {"forward": forward, "end_node": {"value": 2 if forward else 1}},
                    }
                    for identifier, forward in [(10, True), (20, False)]
                ]
            }
            for value in (0.06666, 0.93333)
        ]
        self.calls = []

    def trace_attributes(self, raw):
        request = json.loads(raw)
        assert request["encoded_polyline"] == SHAPE
        assert "shape" in request["filters"]["attributes"]
        self.calls.append(request["shape_match"])
        result = self.initial if request["shape_match"] == "edge_walk" else self.normal
        return result if isinstance(result, str) else json.dumps(result)

    def locate(self, raw):
        self.calls.append("locate")
        request = json.loads(raw)
        assert len(request["locations"]) == 2
        assert request["costing"] == "pedestrian"
        return json.dumps(self.metadata)


def audit(actor, config=None, budget=None):
    return RouteService(config or RoutingCacheConfig.defaults())._trace_edges(
        actor, SHAPE, len(POINTS), budget=budget
    )


def test_normal_trace_never_retries_and_keeps_route_identity():
    actor = Actor(False)
    edges, report = audit(actor)
    assert actor.calls == ["edge_walk"]
    assert report["fallback_used"] is False
    base = {"geometry": {"encoded_polyline": SHAPE}, "edges": edges}
    assert route_identity(base, "graph", "profile") == route_identity(
        {
            "geometry": {"encoded_polyline": SHAPE},
            "edges": [{"edge_id": 10, "begin_shape_index": 0, "end_shape_index": 3}],
        },
        "graph",
        "profile",
    )


def test_fallback_preserves_geometry_and_verifies_direction_repeatably():
    actor = Actor()
    edges, report = audit(actor)
    assert actor.calls == ["edge_walk", "map_snap", "locate"]
    assert edges[0]["edge_id"] == 10
    assert report["fallback_used"]
    assert report["direction_evidence"]["compatible_directed_edge_count"] == 1
    assert report["route_geometry_sha256"] == report["trace_geometry_sha256"]
    assert (edges, report) == audit(Actor())


@pytest.mark.parametrize(
    "fault",
    [
        "shape",
        "endpoint",
        "reversed",
        "points",
        "missing",
        "negative_percent",
        "wrong_direction",
        "indices",
        "forbidden",
        "malformed",
        "way",
        "alternate",
        "multi_edge",
    ],
)
def test_invalid_fallback_never_publishes_or_retries(fault):
    actor = Actor()
    if fault == "shape":
        actor.normal["shape"] = encode_polyline6([(44.01, 33), POINTS[-1]])
    elif fault == "endpoint":
        actor.normal["shape"] = encode_polyline6([*POINTS[:-1], (44, 33.002801)])
    elif fault == "reversed":
        actor.normal["shape"] = encode_polyline6(list(reversed(POINTS)))
    elif fault == "points":
        actor.normal["shape"] = encode_polyline6([POINTS[0], POINTS[-1]])
    elif fault == "missing":
        del actor.normal["shape"]
    elif fault == "negative_percent":
        actor.normal["edges"][0]["source_percent_along"] = -1
    elif fault == "wrong_direction":
        actor.normal["edges"][0]["id"] = 20
    elif fault == "indices":
        actor.normal["edges"][0]["end_shape_index"] = 2
    elif fault == "forbidden":
        actor.normal["edges"][0]["sac_scale"] = 6
    elif fault == "malformed":
        actor.normal = "{"
    elif fault == "way":
        actor.normal["edges"][0]["way_id"] = 999
    elif fault == "alternate":
        actor.normal["alternate_paths"] = [{}]
    elif fault == "multi_edge":
        actor.normal["edges"].append(edge())
    with pytest.raises(RoutingError) as error:
        audit(actor)
    assert error.value.code == "ROUTE_AUDIT_FAILED"
    assert error.value.details["stage"] == "trace_audit"
    assert error.value.details["mode"] == "map_snap"
    assert error.value.details["check"]
    assert actor.calls.count("edge_walk") == actor.calls.count("map_snap") == 1
    assert actor.calls.count("locate") <= 1


@pytest.mark.parametrize("fault", ["shift", "forbidden", "fraction", "length", "id", "missing"])
def test_nonqualifying_initial_failure_does_not_fallback(fault):
    actor = Actor()
    e = actor.initial["edges"][0]
    if fault == "shift":
        actor.initial["shape"] = encode_polyline6([POINTS[0]])
    elif fault == "forbidden":
        e["pedestrian_type"] = "wheelchair"
    elif fault == "fraction":
        e["source_percent_along"] = 0
    elif fault == "length":
        e["length"] = 0.1
    elif fault == "id":
        e["id"] = True
    elif fault == "missing":
        del actor.initial["shape"]
    with pytest.raises(RoutingError):
        audit(actor)
    assert actor.calls == ["edge_walk"]


@pytest.mark.parametrize(
    "fault",
    ["missing_shape", "wrong_forward", "conflict", "same_way", "other_way", "fractions", "loop"],
)
def test_metadata_insufficient_or_ambiguous_is_not_direction_proof(fault):
    actor = Actor()
    for item in actor.metadata:
        selected = item["edges"][0]
        if fault == "missing_shape":
            del selected["edge_info"]["shape"]
        elif fault == "wrong_forward":
            selected["edge"]["forward"] = False
        elif fault in {"same_way", "other_way"}:
            clone = deepcopy(selected)
            clone["edge_id"]["value"] = 11
            if fault == "other_way":
                clone["edge_info"]["way_id"] = 999
            item["edges"].append(clone)
        elif fault == "fractions":
            selected["percent_along"] = 0.5
        elif fault == "loop":
            selected["edge_info"]["shape"] = encode_polyline6(
                [(44, 33), *POINTS, (44, 33), *POINTS, (44, 33.003)]
            )
    if fault == "conflict":
        actor.metadata[1]["edges"][0]["edge_info"]["way_id"] = 999
    with pytest.raises(RoutingError):
        audit(actor)


@pytest.mark.parametrize(
    "name",
    ["maximum_trace_fallback_attempts_per_route", "maximum_trace_metadata_queries_per_route"],
)
def test_config_toggle_and_bounds(name):
    cfg = RoutingCacheConfig.defaults()
    assert getattr(cfg, name) == 1
    for value in (-1, 2, True, 0.5, float("nan"), "1"):
        with pytest.raises(ValueError):
            replace(cfg, **{name: value}).validated()
    actor = Actor()
    with pytest.raises(RoutingError):
        audit(actor, replace(cfg, **{name: 0}))
    assert "locate" not in actor.calls


def test_all_attempts_and_metadata_consume_shared_budget():
    actor = Actor()
    for budget in [
        AuditBudget(12, 100, 100_000),
        AuditBudget(100, 5, 100_000),
        AuditBudget(100, 100, len(json.dumps(actor.initial).encode())),
    ]:
        a = Actor()
        with pytest.raises(RoutingError) as error:
            audit(a, budget=budget)
        assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"
        assert a.calls.count("map_snap") <= 1
    budget = AuditBudget(13, 6, 100_000)
    audit(Actor(), budget=budget)
    assert budget.remaining_points == budget.remaining_edges == 0


CURVED = b"""<osm version="0.6">
<node id="1" lat="44" lon="33" version="1"/>
<node id="2" lat="44.0003" lon="33.001" version="1"/>
<node id="3" lat="44.0005" lon="33.002" version="1"/>
<node id="4" lat="44.001" lon="33.003" version="1"/>
<node id="5" lat="44.002" lon="33.03" version="1"/>
<way id="101" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>
<nd ref="5"/><tag k="highway" v="path"/></way></osm>"""


@pytest.mark.integration
def test_native_curved_partial_edge_both_directions_and_both_apis(tmp_path: Path):
    cfg = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    graph = GraphCache(cfg).prepare(make_manifest(tmp_path, [CURVED]))
    service = RouteService(cfg)
    first, last = GeoPoint(44.00015, 33.0005), GeoPoint(44.00075, 33.0025)
    ids = []
    for start, end, fallback in [(first, last, False), (last, first, True)]:
        request = RouteRequest(graph.graph_id, start, end)
        actor, _before, _after, _ = service._setup(request)
        raw = json.loads(actor.route(json.dumps(service._route_payload(request, 0))))
        shape = raw["trip"]["legs"][0]["shape"]
        original = json.loads(
            actor.trace_attributes(
                json.dumps(
                    {
                        "encoded_polyline": shape,
                        "shape_match": "edge_walk",
                        "costing": "pedestrian",
                        "filters": {
                            "action": "include",
                            "attributes": ["shape", "edge.id", "edge.length"],
                        },
                    }
                )
            )
        )
        assert (len(decode_polyline6(original["shape"])) == 1) is fallback
        single = service.route(request)
        candidate = single.document["route"]
        assert candidate["geometry"]["encoded_polyline"] == shape
        assert candidate["audit"]["trace"]["fallback_used"] is fallback
        assert candidate["edges"][-1]["end_shape_index"] == 3
        ids.append(candidate["edges"][0]["edge_id"])
        alt_request = RouteAlternativesRequest(graph.graph_id, start, end, 2)
        alternatives = service.alternatives(alt_request)
        assert alternatives.document["search"]["exhaustive"] is False
        assert alternatives.candidates[0].as_dict()["audit"]["trace"]["fallback_used"] is fallback
        assert alternatives.as_dict() == service.alternatives(alt_request).as_dict()
        assert alternatives.candidates[0].route_id == route_identity(
            candidate, graph.graph_id, TRAIL_RUNNING_V1.sha256()
        )
        if fallback:
            assert {"code": "TRACE_AUDIT_FALLBACK_USED"} in candidate["warnings"]
            with pytest.raises(RoutingError):
                RouteService(replace(cfg, maximum_trace_fallback_attempts_per_route=0)).route(
                    request
                )
    assert ids[0] != ids[1]


@pytest.mark.parametrize(
    "fault", ["source_fraction", "opposite_id", "topology", "node_ids", "end_node"]
)
def test_opposite_pair_and_partial_fractions_are_proven(fault):
    actor = Actor()
    if fault == "source_fraction":
        actor.normal["edges"][0]["source_percent_along"] = 0.5
    elif fault == "opposite_id":
        actor.initial["edges"][0]["id"] = 1234
    else:
        for item in actor.metadata:
            for e in item["edges"]:
                if fault == "topology":
                    e["edge"]["end_node"] = {"value": 7}
                elif fault == "node_ids":
                    e["edge_info"]["osm_node_ids"] = [True, 2]
                elif fault == "end_node":
                    e["edge"]["end_node"] = {"value": True}
    with pytest.raises(RoutingError):
        audit(actor)


@pytest.mark.parametrize(
    "field,default",
    [("trace_edge_projection_tolerance_m", 0.25), ("trace_percent_along_tolerance", 0.001)],
)
def test_projection_and_fraction_config(field, default):
    config = RoutingCacheConfig.defaults()
    assert getattr(config, field) == default
    assert config.query_policy_dict()[field] == default
    assert field not in config.build_limits_dict()
    for invalid in (0, -1, True, float("inf"), float("nan"), "1"):
        with pytest.raises(ValueError):
            replace(config, **{field: invalid}).validated()


def test_byte_budget_sums_initial_fallback_and_metadata():
    actor = Actor()
    total = sum(len(json.dumps(x).encode()) for x in (actor.initial, actor.normal, actor.metadata))
    with pytest.raises(RoutingError) as error:
        audit(Actor(), budget=AuditBudget(100, 100, total - 1))
    assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"
    budget = AuditBudget(100, 100, total)
    audit(Actor(), budget=budget)
    assert budget.response_bytes == 0


@pytest.mark.parametrize("fault", ["gap", "overlap", "bool", "negative", "truncated"])
def test_normal_path_requires_complete_index_coverage(fault):
    actor = Actor(False)
    actor.initial["edges"] = [edge(10, 0, 1), edge(11, 1, 3)]
    if fault == "gap":
        actor.initial["edges"][1]["begin_shape_index"] = 2
    elif fault == "overlap":
        actor.initial["edges"][1]["begin_shape_index"] = 0
    elif fault == "bool":
        actor.initial["edges"][0]["begin_shape_index"] = False
    elif fault == "negative":
        actor.initial["edges"][0]["begin_shape_index"] = -1
    elif fault == "truncated":
        actor.initial["edges"][1]["end_shape_index"] = 2
    with pytest.raises(RoutingError):
        audit(actor)
    assert actor.calls == ["edge_walk"]


def test_access_failure_is_not_recovered_by_single_route_api():
    from warpbuster_osm_routing.geometry import path_length_m
    from warpbuster_osm_routing.snapping import SnapCandidate

    service = RouteService(RoutingCacheConfig.defaults())
    actor = Actor(False)
    actor.initial["edges"][0]["pedestrian_type"] = "wheelchair"
    snaps = [
        SnapCandidate(
            GeoPoint(*p), 0, 0, 10, 101, (), 0.5, True, None, "path", "dirt", 0, True, False, None
        )
        for p in (POINTS[0], POINTS[-1])
    ]
    trip = {
        "legs": [{"shape": SHAPE}],
        "summary": {"length": path_length_m(decode_polyline6(SHAPE)) / 1000},
    }
    with pytest.raises(RoutingError):
        service._audit_trip(actor, trip, *snaps)
    assert actor.calls == ["edge_walk"]


def test_native_omitted_fractions_are_only_untrimmed_edges():
    # Output structure of native forked graph: only source of first partial edge
    # and target of last partial edge are serialized. Interior full edges omit both.
    actor = Actor(False)
    actor.initial["edges"] = [edge(10, 0, 1), edge(11, 1, 2), edge(12, 2, 3)]
    for index, e in enumerate(actor.initial["edges"]):
        if index != 0:
            del e["source_percent_along"]
        if index != 2:
            del e["target_percent_along"]
    edges, report = audit(actor)
    assert len(edges) == 3
    assert report["complete_edge_spans"] == "PASS"
    assert actor.calls == ["edge_walk"]


def test_zero_length_fallback_and_nonfinite_fraction_refuse():
    for field, value in [("length", 0), ("target_percent_along", float("nan"))]:
        actor = Actor()
        actor.normal["edges"][0][field] = value
        with pytest.raises(RoutingError):
            audit(actor)
        assert actor.calls == ["edge_walk", "map_snap"]
