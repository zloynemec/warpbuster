"""A cached graph's build version must match both routing entry points."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import valhalla

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import (
    GeoPoint,
    GraphResult,
    RouteAlternativesRequest,
    RouteRequest,
)
from warpbuster_osm_routing.route_service import RouteService

GRAPH_ID = "sha256:" + "0" * 64
START = GeoPoint(44.0, 33.0004)
END = GeoPoint(44.0016, 33.002)


def query(service: RouteService, operation: str, graph_id: str = GRAPH_ID):
    if operation == "route":
        return service.route(RouteRequest(graph_id, START, END))
    return service.alternatives(RouteAlternativesRequest(graph_id, START, END, 2))


@pytest.fixture(params=["route", "alternatives"])
def operation(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def guarded_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service = RouteService(RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache"))
    document = {"manifest_version": 2, "cache_key": {"runtime": {"valhalla": "3.8.3"}}}
    graph = GraphResult("READY", GRAPH_ID, tmp_path / "manifest.json", document)
    monkeypatch.setattr(service.cache, "inspect", lambda graph_id: graph)
    actor = Mock(side_effect=AssertionError("must fail before creating Actor"))
    monkeypatch.setattr(valhalla, "Actor", actor)
    monkeypatch.setattr(valhalla, "__version__", "3.8.3")
    return service, document, actor


@pytest.mark.parametrize("current", ["3.8.4", "3.8.3+local", "3.8.3-rc1"])
def test_exact_version_mismatch_precedes_actor(
    guarded_service, operation: str, current: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, actor = guarded_service
    monkeypatch.setattr(valhalla, "__version__", current)
    with pytest.raises(RoutingError) as caught:
        query(service, operation)
    assert caught.value.code == "GRAPH_ENGINE_MISMATCH"
    assert caught.value.details == {
        "graph_id": GRAPH_ID,
        "graph_valhalla_version": "3.8.3",
        "runtime_valhalla_version": current,
    }
    assert "3.8.3" in caught.value.message
    assert current in caught.value.message
    assert "prepare" in caught.value.message
    actor.assert_not_called()


@pytest.mark.parametrize(
    "key",
    [None, [], {}, {"runtime": None}, {"runtime": []}, {"runtime": {}}],
)
def test_missing_build_identity_is_controlled(guarded_service, operation: str, key) -> None:
    service, document, actor = guarded_service
    if key is None:
        del document["cache_key"]
    else:
        document["cache_key"] = key
    with pytest.raises(RoutingError) as caught:
        query(service, operation)
    assert caught.value.code == "CACHE_CORRUPT"
    actor.assert_not_called()


@pytest.mark.parametrize("version", [None, 3.8, True, [], {}, "", " ", "3.8.3\n", "3.8. 3"])
def test_invalid_build_version_is_controlled(guarded_service, operation: str, version) -> None:
    service, document, actor = guarded_service
    document["cache_key"]["runtime"]["valhalla"] = version
    with pytest.raises(RoutingError) as caught:
        query(service, operation)
    assert caught.value.code == "CACHE_CORRUPT"
    assert caught.value.details["field"] == "cache_key.runtime.valhalla"
    actor.assert_not_called()


@pytest.mark.parametrize("version", [None, 3.8, True, "", "3.8.3\n"])
def test_invalid_installed_version_is_controlled(
    guarded_service, operation: str, version, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, actor = guarded_service
    if version is None:
        monkeypatch.delattr(valhalla, "__version__")
    else:
        monkeypatch.setattr(valhalla, "__version__", version)
    with pytest.raises(RoutingError) as caught:
        query(service, operation)
    assert caught.value.code == "VALHALLA_REQUEST_FAILED"
    actor.assert_not_called()


def test_legacy_diagnostic_keeps_precedence(guarded_service, operation: str) -> None:
    service, document, actor = guarded_service
    document.clear()
    document["manifest_version"] = 1
    with pytest.raises(RoutingError) as caught:
        query(service, operation)
    assert caught.value.code == "GRAPH_CAPABILITY_MISSING"
    actor.assert_not_called()


@pytest.mark.integration
def test_native_matching_versions_then_mismatch_is_read_only(
    snapshot_manifest: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache_path = tmp_path / "cache"
    config = RoutingCacheConfig.defaults().with_cache_directory(cache_path)
    cache = GraphCache(config)
    graph = cache.prepare(snapshot_manifest)
    service = RouteService(config)
    for operation in ("route", "alternatives"):
        assert query(service, operation, graph.graph_id).status == "READY"

    def cache_bytes():
        return {
            p.relative_to(cache_path): p.read_bytes() for p in cache_path.rglob("*") if p.is_file()
        }

    before = cache_bytes()
    cached_version = valhalla.__version__
    changed_version = cached_version + "+different"
    monkeypatch.setattr(valhalla, "__version__", changed_version)
    actor = Mock(side_effect=AssertionError("must fail before creating Actor"))
    monkeypatch.setattr(valhalla, "Actor", actor)
    for operation in ("route", "alternatives"):
        with pytest.raises(RoutingError, match="prepare") as caught:
            query(service, operation, graph.graph_id)
        assert caught.value.code == "GRAPH_ENGINE_MISMATCH"

    capsys.readouterr()
    args = [
        "route",
        graph.graph_id,
        "--from",
        "44,33.0004",
        "--to",
        "44.0016,33.002",
        "--cache-dir",
        str(cache_path),
    ]
    for alternates, operation in ((0, "route"), (2, "route_alternatives")):
        assert main([*args, "--alternates", str(alternates), "--json"]) == 2
        result = json.loads(capsys.readouterr().out)
        assert result["operation"] == operation
        assert result["status"] == "ERROR"
        assert result["error"]["code"] == "GRAPH_ENGINE_MISMATCH"
        assert result["error"]["details"] == {
            "graph_id": graph.graph_id,
            "graph_valhalla_version": cached_version,
            "runtime_valhalla_version": changed_version,
        }
    assert main(args) == 2
    error_text = capsys.readouterr().err
    assert cached_version in error_text
    assert changed_version in error_text
    assert "prepare" in error_text

    assert cache.inspect(graph.graph_id).status == "READY"
    assert cache.list_graphs()[0]["status"] == "READY"
    for command in (["inspect", graph.graph_id], ["list"]):
        assert main([*command, "--cache-dir", str(cache_path), "--json"]) == 0
        json.loads(capsys.readouterr().out)
    actor.assert_not_called()
    assert cache_bytes() == before
