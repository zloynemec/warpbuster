"""Approximate hypotheses do not require a unique GPS snap."""

from dataclasses import replace

import pytest

from tests.helpers import make_manifest
from tests.test_context_snapping import PARALLEL, context
from warpbuster_osm_routing import GraphCache, RouteService, RoutingCacheConfig
from warpbuster_osm_routing.context_snapping import project
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest


def test_nearby_projections_merge_but_distant_loop_does_not():
    # Two arms 3 m apart along the line, both close to an observation.
    a = GeoPoint(44, 33)
    b = GeoPoint(44, 33.00002)
    shape = (a, b, a)
    point = GeoPoint(44.0001, 33.000005)
    assert project(point, shape, 0.25) is None
    assert project(point, shape, 0.25, 20) is not None
    assert (
        project(
            GeoPoint(44, 33.001),
            (GeoPoint(44, 33), GeoPoint(44, 33.002), GeoPoint(44, 33)),
            0.25,
            20,
        )
        is None
    )


@pytest.fixture
def native(tmp_path):
    cfg = replace(RoutingCacheConfig.defaults(), cache_directory=tmp_path / "cache")
    graph = GraphCache(cfg).prepare(make_manifest(tmp_path, [PARALLEL]))
    return cfg, graph.graph_id


def test_native_ambiguous_roads_produce_audited_options(native):
    cfg, graph = native
    service = RouteService(cfg)
    ctx = context()
    request = RouteAlternativesRequest(
        graph, ctx.points[-1].point, GeoPoint(44, 33.008), 2, ctx, True
    )
    result = service.alternatives(request)
    assert result.status.value == "READY"
    ways = {r.as_dict()["edges"][0]["way_id"] for r in result.candidates}
    assert {101, 102} <= ways
    assert result.document == service.alternatives(request).document
    assert result.document["search"]["attempted_snap_pairs"] <= 4
    assert not result.document["search"]["exhaustive"]
    for candidate in result.candidates:
        r = candidate.as_dict()
        attachment = r["attachment_options"][0]
        assert r["audit"]["trace"]["shape_identity"] == "PASS"
        assert attachment["start_connector"][0] == [request.start.latitude, request.start.longitude]
        assert attachment["start"]["distance_m"] <= 100
        assert r["discovery_confidence"] in ("medium", "low")


def test_context_is_optional_and_never_a_veto(native):
    cfg, graph = native
    request = RouteAlternativesRequest(
        graph, GeoPoint(44.0001, 33.00438), GeoPoint(44, 33.008), 2, None, True
    )
    result = RouteService(cfg).alternatives(request)
    assert result.candidates
    assert all(x.as_dict()["discovery_confidence"] == "low" for x in result.candidates)


def test_distant_connector_is_reported_and_bounded(native):
    cfg, graph = native
    request = RouteAlternativesRequest(
        graph, GeoPoint(43.9995, 33.005), GeoPoint(44, 33.008), 2, None, True
    )
    service = RouteService(cfg)
    result = service.alternatives(request)
    assert result.candidates
    assert result.candidates[0].as_dict()["attachment_options"][0]["start"]["distance_m"] > 30
    far = replace(request, start=GeoPoint(43.998, 33.005))
    assert service.alternatives(far).status.value == "NO_SNAP"


def test_pair_cap_and_shared_byte_budget(native):
    cfg, graph = native
    request = RouteAlternativesRequest(
        graph, GeoPoint(44.0001, 33.00438), GeoPoint(44, 33.008), 2, None, True
    )
    result = RouteService(replace(cfg, approximate_maximum_pairs=1)).alternatives(request)
    assert result.document["search"]["truncated"]
    assert result.document["search"]["attempted_snap_pairs"] == 1
    with pytest.raises(RoutingError) as error:
        RouteService(replace(cfg, maximum_alternatives_response_bytes=10)).alternatives(request)
    assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"


@pytest.mark.parametrize(
    "field,value",
    [
        ("approximate_snap_distance_m", 101),
        ("approximate_projection_merge_m", 0),
        ("approximate_maximum_groups", 0),
        ("approximate_maximum_pairs", True),
        ("approximate_minimum_context_support", 1.1),
    ],
)
def test_config_bounds(field, value):
    with pytest.raises(ValueError):
        replace(RoutingCacheConfig.defaults(), **{field: value}).validated()


def test_bad_hypothesis_is_reported_without_discarding_good_ones(native, monkeypatch):
    cfg, graph = native
    service = RouteService(cfg)
    audit = service._audit_trip
    calls = 0

    def fail_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RoutingError(
                "ROUTE_AUDIT_FAILED", "test wrong graph edge", {"check": "hypothesis_edges"}
            )
        return audit(*args, **kwargs)

    monkeypatch.setattr(service, "_audit_trip", fail_first)
    result = service.alternatives(
        RouteAlternativesRequest(
            graph, context().points[-1].point, GeoPoint(44, 33.008), 2, None, True
        )
    )
    assert result.candidates
    assert (
        result.document["engine_diagnostics"]["snap_attempts"][0]["native_routes"][0]["status"]
        == "ROUTE_AUDIT_FAILED"
    )
    assert len({x.route_id for x in result.candidates}) == len(result.candidates)


def test_resource_failure_after_success_is_not_partial_success(native, monkeypatch):
    cfg, graph = native
    service = RouteService(cfg)
    audit = service._audit_trip
    calls = 0

    def exhaust(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "test budget")
        return audit(*args, **kwargs)

    monkeypatch.setattr(service, "_audit_trip", exhaust)
    with pytest.raises(RoutingError) as error:
        service.alternatives(
            RouteAlternativesRequest(
                graph, context().points[-1].point, GeoPoint(44, 33.008), 2, None, True
            )
        )
    assert error.value.code == "RESOURCE_LIMIT_EXCEEDED"
