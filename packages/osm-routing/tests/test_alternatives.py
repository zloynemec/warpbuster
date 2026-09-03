"""Identity and explicitly limited edge-weight similarity without route selection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from tests.helpers import encode_polyline6
from warpbuster_osm_routing.alternatives import (
    WeightedRoute,
    build_route_set,
    compare_weights,
    geometry_weights,
    route_identity,
)
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError

CONFIG = RoutingCacheConfig.defaults()


def route_doc(
    ids: tuple[int, ...] = (1, 2, 3), points: list[tuple[float, float]] | None = None
) -> dict[str, Any]:
    return {
        "geometry": {
            "encoded_polyline": encode_polyline6(
                points
                or [
                    (44, 33),
                    (44, 33.001),
                    (44, 33.002),
                    (44, 33.003),
                ]
            )
        },
        "edges": [
            {
                "edge_id": edge,
                "way_id": 101,
                "begin_shape_index": i,
                "end_shape_index": i + 1,
                "length_m": 99999,
            }
            for i, edge in enumerate(ids)
        ],
        "warnings": [],
        "audit": {"status": "PASS", "checks": []},
    }


@pytest.mark.parametrize(
    ("a", "b", "shared", "diversity"),
    [
        ({1: 100}, {1: 100}, 100, 0),
        ({1: 100}, {2: 100}, 0, 1),
        ({1: 100, 2: 100}, {1: 100, 3: 300}, 100, 0.5),
        ({1: 30}, {1: 100}, 30, 0),
        ({1: 200}, {1: 100}, 100, 0),
    ],
)
def test_weighted_metric(
    a: dict[int, float], b: dict[int, float], shared: float, diversity: float
) -> None:
    first, second = (
        WeightedRoute(a, sum(a.values()), False),
        WeightedRoute(b, sum(b.values()), False),
    )
    result = compare_weights(first, second, CONFIG)
    assert result["shared_edge_weight_m"] == shared
    assert result["diversity_ratio"] == diversity
    assert result["overlap_a"] == pytest.approx(shared / first.length_m, abs=1e-6)
    assert result["overlap_b"] == pytest.approx(shared / second.length_m, abs=1e-6)
    assert result["length_delta_m"] == second.length_m - first.length_m


def test_threshold_equality_is_not_a_warning() -> None:
    a, b = WeightedRoute({1: 100}, 100, False), WeightedRoute({1: 50, 2: 100}, 150, False)
    policy = replace(CONFIG, minimum_diversity_ratio=0.5, detour_warning_ratio=1.5)
    assert compare_weights(a, b, policy)["reasons"] == []
    policy = replace(policy, minimum_diversity_ratio=0.51, detour_warning_ratio=1.49)
    assert compare_weights(a, b, policy)["reasons"] == ["LOW_DIVERSITY", "LARGE_DETOUR"]


def test_identity_depends_on_order_graph_profile_geometry_not_summary() -> None:
    original = route_doc()
    key = route_identity(original, "graph", "profile")
    changed = deepcopy(original)
    changed["summary"] = {"time_seconds": 1}
    assert route_identity(changed, "graph", "profile") == key
    for graph, profile in (("other", "profile"), ("graph", "other")):
        assert route_identity(original, graph, profile) != key
    assert route_identity(route_doc((3, 2, 1)), "graph", "profile") != key
    changed["geometry"]["encoded_polyline"] = encode_polyline6([(44, 33), (44.001, 33.002)])
    assert route_identity(changed, "graph", "profile") != key


def test_deduplication_and_order_ignore_engine_alternative_order() -> None:
    primary, second, third = route_doc(), route_doc((1, 4, 5)), route_doc((6, 7, 8))
    first = build_route_set([primary, second, third], "g", "p", 2, CONFIG)
    reordered = build_route_set([primary, third, second], "g", "p", 2, CONFIG)
    first.pop("engine_diagnostics")
    reordered.pop("engine_diagnostics")
    assert first == reordered
    assert len(first["comparisons"]) == 3
    assert first["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    assert first["routes"][0]["role"] == "primary"
    assert {"code": "COINCIDENT_GEOMETRY_DIFFERENT_EDGES"} in first["routes"][1]["warnings"]
    duplicate = build_route_set([primary, second, deepcopy(second)], "g", "p", 2, CONFIG)
    assert len(duplicate["routes"]) == 2
    assert duplicate["search"]["duplicates_removed"] == 1
    assert not duplicate["search"]["requested_count_reached"]
    assert duplicate["engine_diagnostics"]["duplicates"][0]["slot"] == "alternative_2"
    single = build_route_set([primary, deepcopy(primary)], "g", "p", 1, CONFIG)
    assert single["route_choice"]["status"] == "SINGLE_CANDIDATE"
    assert single["search"]["exhaustive"] is False


def test_high_similarity_and_detour_do_not_remove_candidates() -> None:
    result = build_route_set([route_doc((1, 2, 1)), route_doc((1, 1, 2))], "g", "p", 2, CONFIG)
    assert result["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    assert result["comparisons"][0]["diversity_ratio"] == 0
    assert "LOW_DIVERSITY" in result["comparisons"][0]["reasons"]
    assert {"code": "REPEATED_EDGE_TRAVERSAL"} in result["routes"][0]["warnings"]
    detour = route_doc(points=[(44, 33), (44.001, 33.001), (44.001, 33.002), (44, 33.003)])
    result = build_route_set(
        [route_doc(), detour], "g", "p", 1, replace(CONFIG, detour_warning_ratio=1)
    )
    assert len(result["routes"]) == 2
    assert "LARGE_DETOUR" in result["comparisons"][0]["reasons"]


def test_disjoint_partial_edges_are_similarity_not_identity() -> None:
    a = route_doc((1,), [(44, 33), (44, 33.001)])
    b = route_doc((1,), [(44, 33.002), (44, 33.003)])
    result = build_route_set([a, b], "g", "p", 1, CONFIG)
    assert result["comparisons"][0]["diversity_ratio"] == 0
    assert len(result["routes"]) == 2
    assert result["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    assert geometry_weights(a).length_m < 100  # Not the full engine edge length.


@pytest.mark.parametrize(
    "spans",
    [
        [(1, 2), (2, 3)],
        [(0, 1), (2, 3)],
        [(0, 2), (1, 3)],
        [(0, 1), (1, 2)],
        [(0, 4)],
        [(0, True)],
    ],
)
def test_incomplete_or_overlapping_edge_spans_fail(spans: list[tuple[int, int]]) -> None:
    route = route_doc()
    route["edges"] = [
        {"edge_id": i, "begin_shape_index": begin, "end_shape_index": end}
        for i, (begin, end) in enumerate(spans)
    ]
    with pytest.raises(RoutingError, match="span"):
        geometry_weights(route)


def test_zero_length_is_not_comparable() -> None:
    with pytest.raises(RoutingError, match="positive"):
        geometry_weights(route_doc((1,), [(44, 33), (44, 33)]))


def test_exact_duplicates_with_conflicting_audit_are_not_silently_merged() -> None:
    original = route_doc()
    different = deepcopy(original)
    different["edges"][0]["way_id"] = 777
    with pytest.raises(RoutingError, match="conflicting") as caught:
        build_route_set([original, different], "g", "p", 1, CONFIG)
    assert caught.value.details["engine_slot"] == "alternative_1"


@pytest.mark.performance
def test_three_maximum_size_routes_have_bounded_comparison_time() -> None:
    import time

    points = [(44, 33 + index / 1_000_000) for index in range(16_000)]
    routes = [
        route_doc(tuple(range(offset, offset + 15_999)), points) for offset in (0, 8000, 16000)
    ]
    started = time.monotonic()
    result = build_route_set(routes, "g", "p", 2, CONFIG)
    assert len(result["comparisons"]) == 3
    assert time.monotonic() - started < 5
