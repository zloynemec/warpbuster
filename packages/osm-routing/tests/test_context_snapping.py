"""010H sequence evidence, conservative refusals and native graph integration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from tests.helpers import encode_polyline6, make_manifest
from tests.test_snapping import LocateActor, _coverage, _edge
from warpbuster_osm_routing import GraphCache, RouteService, RoutingCacheConfig
from warpbuster_osm_routing.context_snapping import project, validate_context
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import (
    GeoPoint,
    RouteAlternativesRequest,
    RouteRequest,
    StartContext,
    StartContextPoint,
)
from warpbuster_osm_routing.snapping import audit_snap
from warpbuster_osm_routing.trace_audit import AuditBudget


def context(reverse=False):
    return StartContext(
        tuple(
            StartContextPoint(
                i,
                float(i),
                GeoPoint(
                    44.0001 if i == 19 else 44.0,
                    (33.006 - i * 0.00002) if reverse else (33.004 + i * 0.00002),
                ),
            )
            for i in range(20)
        ),
        "point_limit",
    )


def candidates(ctx):
    lon = ctx.points[-1].point.longitude
    result = []
    for way, lat in [(101, 44.0), (102, 44.00022)]:
        shape = encode_polyline6([(lat, 33.0), (lat, 33.01)])
        for forward in [True, False]:
            e = _edge(
                lat, lon, way, way * 10 + int(forward), node_ids=[way * 2, way * 2 + 1], shape=shape
            )
            e["edge"]["forward"] = forward
            e["edge"]["end_node"] = {"value": way * 2 + int(forward)}
            result.append(e)
    return result


def run(ctx=None, *, raw=None, cfg=None, budget=None):
    ctx = ctx if ctx is not None else context()
    cfg = cfg or RoutingCacheConfig.defaults()
    actor = LocateActor(raw if raw is not None else candidates(ctx))
    budget = budget or AuditBudget(
        cfg.maximum_total_route_shape_points,
        cfg.maximum_total_route_edges,
        cfg.maximum_alternatives_response_bytes,
    )
    return audit_snap(actor, ctx.points[-1].point, _coverage(), cfg, ctx, budget)


@pytest.mark.parametrize("reverse", [False, True])
def test_context_resolves_direction_and_permutation(reverse):
    ctx = context(reverse)
    a = run(ctx)
    b = run(ctx, raw=list(reversed(candidates(ctx))))
    assert a.status == "ACCEPTED"
    assert a.selected.way_id == 101
    assert a.selected.forward is not reverse
    assert a.selected.context_resolved
    assert a.document == b.document == run(ctx).document
    assert a.document["context"]["single_point_status"] == "AMBIGUOUS_SNAP"
    assert a.document["context"]["metadata_calls"] == 0


def test_disabled_and_absent_context_preserve_legacy():
    ctx = context()
    cfg = RoutingCacheConfig.defaults()
    baseline = audit_snap(LocateActor(candidates(ctx)), ctx.points[-1].point, _coverage(), cfg)
    disabled = run(ctx, cfg=replace(cfg, context_snapping_enabled=0))
    assert baseline.document == disabled.document
    assert baseline.status == "AMBIGUOUS_SNAP"


@pytest.mark.parametrize(
    "kind", ["short", "stationary", "equal", "late_turn", "reverse_jitter", "too_far"]
)
def test_insufficient_or_conflicting_context_refuses(kind):
    ctx = context()
    ps = list(ctx.points)
    if kind == "short":
        ps = ps[-5:]
    if kind == "stationary":
        ps = [replace(p, point=ps[-1].point) for p in ps]
    if kind == "equal":
        ps = [replace(p, point=GeoPoint(44.00011, p.point.longitude)) for p in ps]
    if kind == "late_turn":
        ps = [
            replace(
                p, point=GeoPoint(44.00022 if p.record_index >= 16 else 44.0, p.point.longitude)
            )
            for p in ps
        ]
        ps[-1] = ctx.points[-1]
    if kind == "reverse_jitter":
        ps[10] = replace(ps[10], point=ps[3].point)
    if kind == "too_far":
        ps = [replace(p, point=GeoPoint(44.0004, p.point.longitude)) for p in ps]
        ps[-1] = ctx.points[-1]
    assert run(replace(ctx, points=tuple(ps))).status == "AMBIGUOUS_SNAP"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_shape",
        "missing_direction",
        "denied",
        "ferry",
        "duplicate",
        "junction",
        "invalid_id",
    ],
)
def test_unsupported_metadata_refuses(fault):
    ctx = context()
    raw = candidates(ctx)
    if fault == "missing_shape":
        raw[0]["edge_info"].pop("shape")
    if fault == "missing_direction":
        raw[0]["edge"].pop("forward")
    if fault == "denied":
        raw[0]["edge"]["access"]["pedestrian"] = False
    if fault == "ferry":
        raw[0]["edge"]["classification"]["use"] = "ferry"
    if fault == "duplicate":
        raw.append(deepcopy(raw[0]))
        raw[-1]["edge_info"]["names"] = ["changed"]
    if fault == "junction":
        raw[0]["edge_info"]["way_id"] = 103
    if fault == "invalid_id":
        raw[0]["edge_id"] = {"value": "x"}
    assert run(ctx, raw=raw).status == "AMBIGUOUS_SNAP"


def test_loop_projection_is_ambiguous():
    assert (
        project(
            GeoPoint(44, 33.001), (GeoPoint(44, 33), GeoPoint(44, 33.002), GeoPoint(44, 33)), 0.25
        )
        is None
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_snapping_enabled", True),
        ("context_snapping_enabled", 2),
        ("context_minimum_support_ratio", 1.1),
        ("context_minimum_support_ratio", 0),
        ("context_recent_points", 1),
        ("context_minimum_points", 61),
        ("context_minimum_duration_s", 60),
        ("context_minimum_progress_m", 150),
        ("context_maximum_median_error_m", 26),
        ("context_maximum_age_s", float("nan")),
    ],
)
def test_config_invalid(field, value):
    with pytest.raises(ValueError):
        replace(RoutingCacheConfig.defaults(), **{field: value}).validated()


@pytest.mark.parametrize("fault", ["index", "time", "nan", "bool", "anchor", "long", "step"])
def test_malformed_context_is_input_error(fault):
    ctx = context()
    ps = list(ctx.points)
    start = ps[-1].point
    if fault == "index":
        ps[1] = replace(ps[1], record_index=5)
    if fault == "time":
        ps[1] = replace(ps[1], timestamp_seconds=0)
    if fault == "nan":
        ps[1] = replace(ps[1], point=GeoPoint(float("nan"), 33))
    if fault == "bool":
        ps[1] = replace(ps[1], timestamp_seconds=True)
    if fault == "anchor":
        start = GeoPoint(44, 33)
    if fault == "long":
        ps = [replace(p, timestamp_seconds=p.timestamp_seconds * 4) for p in ps]
    if fault == "step":
        ps[0] = replace(ps[0], timestamp_seconds=-10)
    with pytest.raises(RoutingError):
        validate_context(replace(ctx, points=tuple(ps)), start, RoutingCacheConfig.defaults())


@pytest.mark.parametrize(
    "field,value",
    [
        ("context_maximum_groups", 1),
        ("context_maximum_vertices", 3),
        ("context_maximum_projection_work", 79),
        ("maximum_total_route_shape_points", 3),
        ("maximum_total_route_edges", 3),
        ("maximum_alternatives_response_bytes", 10),
    ],
)
def test_shared_and_local_resource_limits(field, value):
    with pytest.raises(RoutingError) as error:
        run(cfg=replace(RoutingCacheConfig.defaults(), **{field: value}))
    assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"


PARALLEL = b"""<osm version="0.6">
<node id="1" lat="44" lon="33" version="1"/>
<node id="2" lat="44" lon="33.01" version="1"/>
<node id="3" lat="44.00022" lon="33.003" version="1"/>
<node id="4" lat="44.00022" lon="33.007" version="1"/>
<node id="5" lat="44" lon="33.003" version="1"/>
<node id="6" lat="44" lon="33.007" version="1"/>
<node id="7" lat="43.999" lon="33.003" version="1"/>
<node id="8" lat="43.999" lon="33.007" version="1"/>
<way id="103" version="1"><nd ref="5"/><nd ref="7"/><tag k="highway" v="path"/></way>
<way id="104" version="1"><nd ref="6"/><nd ref="8"/><tag k="highway" v="path"/></way>
<way id="101" version="1"><nd ref="1"/><nd ref="5"/><nd ref="6"/><nd ref="2"/><tag k="highway" v="path"/></way>
<way id="102" version="1"><nd ref="1"/><nd ref="3"/><nd ref="4"/><nd ref="2"/><tag k="highway" v="path"/></way>
</osm>"""


@pytest.mark.parametrize("reverse", [False, True])
def test_native_parallel_roads_both_apis(tmp_path, reverse):
    cfg = replace(RoutingCacheConfig.defaults(), cache_directory=tmp_path / "cache")
    graph = GraphCache(cfg).prepare(make_manifest(tmp_path, [PARALLEL]))
    service = RouteService(cfg)
    ctx = context(reverse)
    start = ctx.points[-1].point
    end = GeoPoint(44, 33.002 if reverse else 33.008)
    request = RouteRequest(graph.graph_id, start, end, ctx)
    legacy = service.route(replace(request, start_context=None))
    assert legacy.document["snapping"]["start"]["status"] == "AMBIGUOUS_SNAP"
    result = service.route(request)
    assert result.status.value == "READY"
    assert result.document["route"]["edges"][0]["way_id"] == 101
    assert result.document == service.route(request).document
    alt = service.alternatives(RouteAlternativesRequest(graph.graph_id, start, end, 2, ctx))
    assert alt.status.value == "READY"
    assert all(x.as_dict()["edges"][0]["way_id"] == 101 for x in alt.candidates)
    assert alt.document["snapping"]["start"]["context"]["status"] == "resolved"


def test_adjacent_vertex_is_not_a_competing_local_minimum():
    # Observation just beyond a vertex: its clamped projection onto the previous
    # segment must not compete with the unique interior projection on the next.
    shape = (GeoPoint(44, 33), GeoPoint(44, 33.001), GeoPoint(44, 33.002))
    assert project(GeoPoint(44.0001, 33.00101), shape, 0.25) is not None


def test_native_recorrelation_to_other_directed_edge_refuses():
    from tests.test_trace_audit import POINTS, SHAPE, Actor
    from warpbuster_osm_routing.geometry import decode_polyline6, path_length_m
    from warpbuster_osm_routing.snapping import SnapCandidate

    snaps = [
        SnapCandidate(
            GeoPoint(*p), 0, 0, 10, 101, (), 0.5, True, None, "path", "dirt", 0, True, False, None
        )
        for p in (POINTS[0], POINTS[-1])
    ]
    snaps[0] = replace(snaps[0], edge_id=999, context_resolved=True)
    trip = {
        "legs": [{"shape": SHAPE}],
        "summary": {"length": path_length_m(decode_polyline6(SHAPE)) / 1000},
    }
    with pytest.raises(RoutingError) as error:
        RouteService(RoutingCacheConfig.defaults())._audit_trip(Actor(False), trip, *snaps)
    assert error.value.details["check"] == "context_start_edge"


def test_resource_limits_include_normalization_and_context_geometry():
    ctx = context()
    cfg = RoutingCacheConfig.defaults()
    # Four directed metadata shapes of two points, then two unique shapes.
    budget = AuditBudget(12, 4, cfg.maximum_alternatives_response_bytes)
    result = run(ctx, budget=budget)
    assert (
        result.status == "ACCEPTED" and budget.remaining_points == 0 and budget.remaining_edges == 0
    )
    with pytest.raises(RoutingError):
        run(ctx, budget=AuditBudget(11, 4, cfg.maximum_alternatives_response_bytes))


@pytest.mark.parametrize(
    "field",
    [
        "context_maximum_points",
        "context_maximum_age_s",
        "context_maximum_length_m",
        "context_maximum_step_s",
        "context_minimum_points",
        "context_minimum_duration_s",
        "context_minimum_progress_m",
        "context_maximum_median_error_m",
        "context_maximum_p90_error_m",
        "context_maximum_backtrack_m",
        "context_minimum_margin_m",
        "context_minimum_support_ratio",
        "context_recent_points",
        "context_maximum_groups",
        "context_maximum_vertices",
        "context_maximum_projection_work",
        "context_projection_tie_m",
    ],
)
@pytest.mark.parametrize("value", [0, True, float("inf")])
def test_each_numeric_policy_has_typed_positive_bounds(field, value):
    with pytest.raises(ValueError):
        replace(RoutingCacheConfig.defaults(), **{field: value}).validated()


def test_context_does_not_change_an_already_accepted_snap():
    ctx = context()
    ctx = replace(
        ctx,
        points=(
            *ctx.points[:-1],
            replace(ctx.points[-1], point=GeoPoint(44, ctx.points[-1].point.longitude)),
        ),
    )
    cfg = RoutingCacheConfig.defaults()
    legacy = audit_snap(LocateActor(candidates(ctx)), ctx.points[-1].point, _coverage(), cfg)
    assert legacy.status == "ACCEPTED"
    assert run(ctx).document == legacy.document


def test_context_does_not_relax_maximum_snap_distance():
    assert (
        run(cfg=replace(RoutingCacheConfig.defaults(), maximum_snap_distance_m=5)).status
        == "NO_SNAP"
    )
