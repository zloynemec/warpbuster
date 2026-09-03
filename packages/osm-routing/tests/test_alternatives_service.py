"""Malformed backend, resource limits and CLI compatibility contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest

from tests.helpers import encode_polyline6
from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import path_length_m, valid_wgs84
from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest
from warpbuster_osm_routing.route_service import RouteService, _bounded_response
from warpbuster_osm_routing.snapping import SnapCandidate, SnapDecision

START, END = GeoPoint(44, 33), GeoPoint(44, 33.003)
REQUEST = RouteAlternativesRequest("sha256:graph", START, END, 2)


class Backend:
    def __init__(self) -> None:
        self.traces: dict[str, Any] = {}
        trips = []
        for index in range(3):
            points = [START, GeoPoint(44, 33.001), GeoPoint(44 + index * 0.0002, 33.002), END]
            shape = encode_polyline6([(p.latitude, p.longitude) for p in points])
            trip = {
                "legs": [{"shape": shape}],
                "summary": {"length": path_length_m(points) / 1000, "time": 300, "cost": None},
            }
            trips.append(trip)
            self.traces[shape] = {
                "edges": [
                    {
                        "id": index * 10 + i,
                        "way_id": 101 + index,
                        "length": 0.1,
                        "begin_shape_index": i,
                        "end_shape_index": i + 1,
                        "sac_scale": 0,
                        "use": "path",
                        "surface": "dirt",
                        "unpaved": True,
                        "travel_mode": "pedestrian",
                        "pedestrian_type": "foot",
                    }
                    for i in range(3)
                ]
            }
        self.response: Any = {"trip": trips[0], "alternates": [{"trip": t} for t in trips[1:]]}
        self.route_calls: list[dict[str, Any]] = []
        self.trace_calls: list[dict[str, Any]] = []
        self.failure: Exception | None = None
        self.statuses = ["ACCEPTED", "ACCEPTED"]

    def route(self, payload: str) -> str:
        self.route_calls.append(json.loads(payload))
        if self.failure:
            raise self.failure
        return self.response if isinstance(self.response, str) else json.dumps(self.response)

    def trace_attributes(self, payload: str) -> str:
        request = json.loads(payload)
        self.trace_calls.append(request)
        assert request["shape_match"] == "edge_walk"
        assert request["costing"] == "pedestrian"
        result = self.traces[request["encoded_polyline"]]
        return result if isinstance(result, str) else json.dumps(result)

    def alternate_edge(self) -> dict[str, Any]:
        shape = self.response["alternates"][0]["trip"]["legs"][0]["shape"]
        edge: dict[str, Any] = self.traces[shape]["edges"][0]
        return edge


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> Backend:
    actor = Backend()

    def setup(service: RouteService, request: Any) -> tuple[Any, ...]:
        decisions = []
        for point, status in zip((START, END), actor.statuses, strict=True):
            snap = SnapCandidate(
                point, 0, 0, 1, 101, (), 0.5, True, None, "path", "dirt", 0, True, False, None
            )
            document = {
                "status": status,
                "selected": snap.as_dict() if status == "ACCEPTED" else None,
            }
            decisions.append(SnapDecision(status, snap if status == "ACCEPTED" else None, document))
        return (
            actor,
            *decisions,
            {
                "operation": "route",
                "protocol_version": 1,
                "request": {},
                "graph": {"graph_id": request.graph_id},
                "profile": {},
                "query_policy": service.config.query_policy_dict(),
                "snapping": {"start": decisions[0].document, "end": decisions[1].document},
            },
        )

    monkeypatch.setattr(RouteService, "_setup", setup)
    return actor


def run(config: RoutingCacheConfig | None = None) -> Any:
    return RouteService(config or RoutingCacheConfig.defaults()).alternatives(REQUEST)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_counts_and_native_call_bounds(backend: Backend, count: int) -> None:
    backend.response["alternates"] = backend.response["alternates"][:count]
    result = run()
    assert len(result.candidates) == 1 + count
    assert len(backend.route_calls) == 1
    assert backend.route_calls[0]["alternates"] == 2
    assert len(backend.route_calls[0]["locations"]) == 2
    assert len(backend.trace_calls) == 1 + count
    assert result.document["search"]["requested_count_reached"] == (count == 2)
    assert result.document["search"]["exhaustive"] is False


@pytest.mark.parametrize("count", [-1, 0, 3, 1.5, True, "1"])
def test_invalid_request_count_precedes_backend(backend: Backend, count: Any) -> None:
    with pytest.raises(RoutingError) as error:
        RouteService(RoutingCacheConfig.defaults()).alternatives(replace(REQUEST, alternates=count))
    assert error.value.code == "INVALID_REQUEST"
    assert not backend.route_calls


@pytest.mark.parametrize(
    "response", [None, [], {}, {"trip": {}}, {"trip": None}, {"alternates": {}}, "{", "[1]"]
)
def test_malformed_response_is_error_not_no_route(backend: Backend, response: Any) -> None:
    backend.response = response
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "ROUTE_AUDIT_FAILED"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", None),
        ("id", True),
        ("id", -1),
        ("way_id", []),
        ("way_id", 0),
        ("sac_scale", 4),
        ("sac_scale", float("nan")),
        ("sac_scale", True),
        ("surface", []),
        ("surface", "impassable"),
        ("surface", 7),
        ("travel_mode", "drive"),
        ("pedestrian_type", "wheelchair"),
        ("length", float("inf")),
        ("length", -1),
        ("length", 1e308),
        ("begin_shape_index", 1),
        ("end_shape_index", True),
        ("end_shape_index", 2),
        ("use", {}),
        ("unpaved", []),
    ],
)
def test_bad_alternative_never_hidden_by_valid_primary(
    backend: Backend, field: str, value: Any
) -> None:
    backend.alternate_edge()[field] = value
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "ROUTE_AUDIT_FAILED"
    assert caught.value.details["engine_slot"] == "alternative_1"
    assert "check" in caught.value.details
    assert len(backend.trace_calls) <= 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("length", -1),
        ("length", float("inf")),
        ("length", 100),
        ("length", 0),
        ("length", True),
        ("time", -1),
        ("time", "300"),
        ("cost", float("nan")),
        ("cost", True),
    ],
)
def test_invalid_summary_fails(backend: Backend, field: str, value: Any) -> None:
    backend.response["alternates"][0]["trip"]["summary"][field] = value
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "ROUTE_AUDIT_FAILED"
    assert caught.value.details["engine_slot"] == "alternative_1"


def test_endpoints_rechecked_for_every_alternative(backend: Backend) -> None:
    backend.response["alternates"][0]["trip"]["legs"][0]["shape"] = encode_polyline6(
        [(45, 34), (46, 35)]
    )
    with pytest.raises(RoutingError, match="endpoints") as caught:
        run()
    assert caught.value.details["engine_slot"] == "alternative_1"


def test_cost_optional_and_existing_warnings_retained(backend: Backend) -> None:
    backend.response["trip"]["summary"].pop("cost")
    backend.alternate_edge()["use"] = "ferry"
    result = run()
    assert result.candidates[0].as_dict()["summary"]["cost"] is None
    assert any({"code": "FERRY_USED"} in c.as_dict()["warnings"] for c in result.candidates)


@pytest.mark.parametrize(
    ("setting", "limit"),
    [
        ("maximum_total_route_shape_points", 11),
        ("maximum_total_route_edges", 8),
        ("maximum_route_shape_points", 3),
        ("maximum_route_edges", 2),
        ("maximum_alternatives_response_bytes", 10),
    ],
)
def test_resource_limits(backend: Backend, setting: str, limit: int) -> None:
    with pytest.raises(RoutingError) as caught:
        run(replace(RoutingCacheConfig.defaults(), **{setting: limit}))
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"


def test_aggregate_bounds_include_duplicates(backend: Backend) -> None:
    backend.response["alternates"] = [{"trip": deepcopy(backend.response["trip"])}] * 2
    with pytest.raises(RoutingError) as caught:
        run(replace(RoutingCacheConfig.defaults(), maximum_total_route_shape_points=11))
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    result = run(
        replace(
            RoutingCacheConfig.defaults(),
            maximum_total_route_shape_points=12,
            maximum_total_route_edges=9,
        )
    )
    assert len(result.candidates) == 1
    assert result.document["search"]["duplicates_removed"] == 2


def test_oversized_alternative_count_not_truncated(backend: Backend) -> None:
    backend.response["alternates"] *= 2
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert not backend.trace_calls


def test_trace_response_byte_bound(backend: Backend) -> None:
    shape = backend.response["trip"]["legs"][0]["shape"]
    backend.traces[shape] = " " * 3000
    with pytest.raises(RoutingError) as caught:
        run(replace(RoutingCacheConfig.defaults(), maximum_alternatives_response_bytes=2500))
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert len(backend.trace_calls) == 1


def test_utf8_size_boundary_and_invalid_json() -> None:
    raw = '{"text":"текст"}'
    limit = len(raw.encode())
    assert _bounded_response(raw, limit)["text"] == "текст"
    with pytest.raises(RoutingError) as caught:
        _bounded_response(raw, limit - 1)
    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    with pytest.raises(RoutingError):
        _bounded_response('{"text":"\ud800"}', 100)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["OUTSIDE_COVERAGE", "AMBIGUOUS_SNAP"], "OUTSIDE_COVERAGE"),
        (["AMBIGUOUS_SNAP", "NO_SNAP"], "NO_SNAP"),
        (["ACCEPTED", "AMBIGUOUS_SNAP"], "AMBIGUOUS_SNAP"),
    ],
)
def test_negative_snap_precedence_and_no_routing(
    backend: Backend, statuses: list[str], expected: str
) -> None:
    backend.statuses = statuses
    result = run()
    assert result.status == expected
    assert result.exit_code == 1
    assert result.candidates == ()
    assert result.document["route_choice"]["status"] == "NOT_EVALUATED"
    assert result.document["primary_route_id"] is None
    assert not result.document["search"]["executed"]
    assert not backend.route_calls


def test_no_route_distinct_from_engine_failure(backend: Backend) -> None:
    backend.failure = RuntimeError('{"error_code":442,"error":"No path could be found"}')
    result = run()
    assert result.status == "NO_ROUTE"
    assert result.exit_code == 1
    assert result.document["search"]["executed"]
    assert not backend.trace_calls
    backend.failure = RuntimeError("unexpected engine failure")
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "VALHALLA_REQUEST_FAILED"


def test_huge_number_and_invalid_result_are_controlled(backend: Backend) -> None:
    backend.response["trip"]["summary"]["length"] = 10**500
    with pytest.raises(RoutingError) as caught:
        run()
    assert caught.value.code == "ROUTE_AUDIT_FAILED"
    with pytest.raises(RoutingError, match="JSON"):
        RouteService._alternatives_result({"status": "READY", "routes": [], "number": float("nan")})


@pytest.mark.parametrize("value", [True, "44", float("nan"), float("inf"), 10**500])
def test_invalid_coordinates_do_not_raise_uncontrolled_errors(value: Any) -> None:
    assert not valid_wgs84(GeoPoint(value, 33))


def command(*extra: str) -> list[str]:
    return ["route", "sha256:graph", "--from", "44,33", "--to", "44,33.003", *extra]


def test_cli_legacy_and_zero_identical(
    backend: Backend, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(command("--json")) == 0
    old = json.loads(capsys.readouterr().out)
    assert main(command("--alternates", "0", "--json")) == 0
    assert json.loads(capsys.readouterr().out) == old
    assert old["operation"] == "route" and "routes" not in old
    assert all(request["alternates"] == 0 for request in backend.route_calls)


@pytest.mark.parametrize("count", ["-1", "3", "1.5", "false"])
def test_cli_invalid_count_json_error(
    backend: Backend, capsys: pytest.CaptureFixture[str], count: str
) -> None:
    assert main(command("--alternates", count, "--json")) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["error"]["code"] == "INVALID_REQUEST"
    assert result["operation"] == "route_alternatives" and result["protocol_version"] == 1
    assert not backend.route_calls


def test_cli_alternatives_success_error_and_console(
    backend: Backend, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(command("--alternates", "2", "--json")) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    result = json.loads(captured.out)
    assert result["route_choice"]["status"] == "MULTIPLE_CANDIDATES"
    assert main(command("--alternates", "2")) == 0
    console = capsys.readouterr().out
    assert "NOT exhaustive" in console and "Overlap" in console and "— | — | — | —" in console
    backend.statuses = ["NO_SNAP", "ACCEPTED"]
    assert main(command("--alternates", "2", "--json")) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NO_SNAP"
    backend.statuses = ["ACCEPTED", "ACCEPTED"]
    backend.alternate_edge()["way_id"] = None
    assert main(command("--alternates", "2", "--json")) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["details"]["engine_slot"] == "alternative_1"
    assert "routes" not in error
