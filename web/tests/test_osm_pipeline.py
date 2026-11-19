"""Web 012C orchestration preserves the base GPX result on every OSM refusal."""

import json
import pickle
import sys
import time
from dataclasses import replace
from multiprocessing import Pipe
from types import SimpleNamespace

import pytest
from tests.processing_factory import processing_fixture
from tests.test_osm_application import fixture as osm_fixture
from tests.test_repair_cli import _repairable_fixture
from warpbuster.cli import main as cli_main
from warpbuster.pipeline.osm import (
    OSMResult,
    _ensure_cache_quota,
    _lease,
    execute_osm_pipeline,
    run_osm_pipeline_isolated,
)
from warpbuster_web.config import DEMMode, OSMMode, WebConfig
from warpbuster_web.processing import process_job


def test_disabled_osm_publishes_versioned_report_without_private_graph_data(tmp_path):
    _repairable_fixture(tmp_path)
    config = WebConfig(
        data_dir=tmp_path / "data",
        osm_mode=OSMMode.DISABLED,
        approximate_osm=False,
        dem_mode=DEMMode.DISABLED,
        complete_missing_altitude=False,
    )
    assert process_job(tmp_path, 100_000, config=config)
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["schema_version"] == 5
    assert report["osm"]["status"] == "disabled"
    assert report["osm"]["stage"] is report["osm"]["error_code"] is None
    assert report["osm"]["duration_seconds"] >= 0
    assert report["osm"]["eligible_gaps"] == 1
    assert report["osm"]["snapshot_cache_hit"] is None
    assert report["summary"]["applied_gpx_gaps"] == 1
    assert report["summary"]["applied_osm_gaps"] == 0
    assert "graph_id" not in json.dumps(report)


def test_osm_failure_keeps_gpx_plan_and_exposes_only_safe_code(tmp_path, monkeypatch):
    _repairable_fixture(tmp_path)
    calls = []

    def unavailable(activity, integrity, plan, config, **kwargs):
        calls.append((activity, integrity, plan, config))
        return OSMResult(plan, "unavailable", "acquisition", "offline_cache_miss")

    config = replace(WebConfig(), osm_mode=OSMMode.OFFLINE)
    monkeypatch.setattr("warpbuster.pipeline.repair.run_osm_pipeline", unavailable)
    assert process_job(tmp_path, 100_000, config=config)
    report = json.loads((tmp_path / "result.json").read_text())
    assert len(calls) == 1
    assert report["outcome"] == "repaired"
    assert report["osm"]["status"] == "unavailable"
    assert report["osm"]["stage"] == "acquisition"
    assert report["osm"]["error_code"] == "offline_cache_miss"
    assert report["osm"]["duration_seconds"] >= 0
    assert report["osm"]["eligible_gaps"] == 1
    assert (tmp_path / "corrected.fit").is_file()
    assert not (tmp_path / "private-osm-audit.json").exists()


def test_acquisition_failure_retains_bounded_private_cause_without_public_leaks(
    tmp_path, monkeypatch
):
    from warpbuster_osm_manager import OsmManager
    from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError

    _repairable_fixture(tmp_path)

    def unavailable(self, *args, **kwargs):
        try:
            raise TimeoutError("SECRET network reason " + "x" * 10_000)
        except TimeoutError as cause:
            raise OsmManagerError(
                ErrorCode.OVERPASS_UNAVAILABLE,
                "SECRET acquisition failure",
                {"attempt_count": 3, "last_error": "TimeoutError"},
            ) from cause

    monkeypatch.setattr(OsmManager, "ensure", unavailable)
    config = WebConfig(
        data_dir=tmp_path / "service",
        osm_mode=OSMMode.AUTO,
        isolate_osm=False,
        dem_mode=DEMMode.DISABLED,
        approximate_osm=False,
        complete_missing_altitude=False,
        osm_minimum_free_bytes=1,
    )
    assert process_job(tmp_path, 100_000, config=config)
    public = (tmp_path / "result.json").read_text()
    assert json.loads(public)["osm"]["error_code"] == "acquisition_failed"
    assert "SECRET" not in public and "TimeoutError" not in public
    audit_path = tmp_path / "private-osm-audit.json"
    assert audit_path.is_file()
    assert audit_path.stat().st_mode & 0o777 == 0o600
    failure = json.loads(audit_path.read_text())["failure"]
    assert failure["code"] == "OVERPASS_UNAVAILABLE"
    assert failure["message"] == "SECRET acquisition failure"
    assert "SECRET network reason" in failure["cause"]
    assert len(failure["cause"]) <= 4096
    assert "attempt_count" in failure["details"]


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, KeyError])
def test_unexpected_osm_exception_cannot_fail_job_or_leak_text(tmp_path, monkeypatch, error_type):
    _repairable_fixture(tmp_path)

    def explode(*args, **kwargs):
        private_local = "PRIVATE_FRAME_LOCAL_MUST_NOT_BE_LOGGED"
        assert private_local
        try:
            raise ValueError("SECRET chained cause")
        except ValueError as cause:
            raise error_type("SECRET native stderr /private/path") from cause

    monkeypatch.setattr("warpbuster.pipeline.repair.run_osm_pipeline", explode)
    assert process_job(tmp_path, 100_000)
    report_text = (tmp_path / "result.json").read_text()
    report = json.loads(report_text)
    assert report["osm"]["error_code"] == "routing_failed"
    assert report["outcome"] == "repaired"
    assert "SECRET" not in report_text and "/private/path" not in report_text
    audit_path = tmp_path / "private-osm-audit.json"
    assert audit_path.stat().st_mode & 0o777 == 0o600
    failure = json.loads(audit_path.read_text(encoding="utf-8"))["failure"]
    assert failure["diagnostic"]["exception_type"] == error_type.__name__
    assert "SECRET native stderr" in failure["diagnostic"]["reason"]
    assert "SECRET chained cause" in failure["diagnostic"]["traceback"]
    assert "explode" in failure["diagnostic"]["traceback"]
    assert "PRIVATE_FRAME_LOCAL_MUST_NOT_BE_LOGGED" not in failure["diagnostic"]["traceback"]


def test_unexpected_coverage_error_retains_private_diagnostic(tmp_path, monkeypatch):
    activity, _, integrity, plan, _ = osm_fixture(tmp_path)

    def fail(*args, **kwargs):
        raise KeyError("PRIVATE coverage bug")

    monkeypatch.setattr("warpbuster_osm_manager.coverage.plan_from_geometry", fail)
    config = replace(WebConfig(), data_dir=tmp_path / "data", osm_minimum_free_bytes=1)
    result = execute_osm_pipeline(activity, integrity, plan, config)
    assert result.plan is plan
    assert (result.stage, result.error_code) == ("coverage", "coverage_limit")
    assert result.private_audit["failure"]["diagnostic"]["exception_type"] == "KeyError"


def test_unexpected_routing_error_retains_private_diagnostic(tmp_path, monkeypatch):
    activity, integrity, plan, config = processing_fixture(tmp_path, seed_cache=False)
    snapshot = SimpleNamespace(
        manifest=SimpleNamespace(snapshot_id="snapshot"), manifest_path=tmp_path / "manifest"
    )
    monkeypatch.setattr("warpbuster_osm_manager.OsmManager.ensure", lambda *a, **k: snapshot)
    monkeypatch.setattr(
        "warpbuster_osm_routing.GraphCache.prepare",
        lambda *a, **k: SimpleNamespace(graph_id="graph"),
    )

    def fail(*args, **kwargs):
        raise TypeError("PRIVATE routing bug")

    monkeypatch.setattr("warpbuster.pipeline.osm._discover_and_apply", fail)
    result = execute_osm_pipeline(activity, integrity, plan, config)
    assert result.plan is plan
    assert (result.stage, result.error_code) == ("routing", "routing_failed")
    assert result.private_audit["failure"]["diagnostic"]["exception_type"] == "TypeError"


def test_child_failure_serializes_private_diagnostic(monkeypatch):
    from warpbuster.pipeline.osm_worker import child_main

    def fail(*args, **kwargs):
        raise TypeError("PRIVATE child serialization failure")

    monkeypatch.setattr("warpbuster.pipeline.osm.execute_osm_pipeline", fail)
    monkeypatch.setattr("warpbuster.pipeline.osm_worker.os.setsid", lambda: None)
    monkeypatch.setattr(
        "warpbuster.pipeline.osm_worker._apply_resource_limits", lambda config: None
    )
    receiving, sending = Pipe(duplex=False)
    try:
        child_main(sending, None, None, None, WebConfig(), None)
        assert receiving.poll(1)
        kind, result = pickle.loads(receiving.recv_bytes())
        assert kind == "result"
        assert result.private_audit["failure"]["diagnostic"]["exception_type"] == "TypeError"
    finally:
        receiving.close()
        sending.close()


@pytest.mark.skipif(sys.platform != "linux", reason="OSM process-group isolation is Linux-only")
def test_unexpected_child_error_transfers_private_diagnostic(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise TypeError("PRIVATE isolated child failure")

    monkeypatch.setattr("warpbuster.pipeline.osm.eligible_gap_count", lambda *args: 1)
    monkeypatch.setattr("warpbuster.pipeline.osm.execute_osm_pipeline", fail)
    config = replace(
        WebConfig(), data_dir=tmp_path, osm_total_timeout_seconds=5, osm_minimum_free_bytes=1
    )
    result = run_osm_pipeline_isolated(None, None, None, config)
    assert (result.stage, result.error_code) == ("setup", "resource_limit")
    assert result.private_audit["failure"]["diagnostic"]["exception_type"] == "TypeError"
    assert (
        "PRIVATE isolated child failure" in result.private_audit["failure"]["diagnostic"]["reason"]
    )


def test_coverage_and_existing_cache_quota_fail_before_network(tmp_path):
    activity, _, integrity, plan, _ = osm_fixture(tmp_path)
    config = replace(
        WebConfig(),
        data_dir=tmp_path / "web",
        osm_mode=OSMMode.OFFLINE,
        osm_maximum_area_km2=0.0001,
        osm_minimum_free_bytes=1,
    )
    result = execute_osm_pipeline(activity, integrity, plan, config)
    assert (result.stage, result.error_code) == ("coverage", "coverage_limit")

    cache = config.data_dir / "osm"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "oversize").write_bytes(b"xx")
    result = execute_osm_pipeline(
        activity,
        integrity,
        plan,
        replace(config, osm_maximum_area_km2=250, osm_cache_quota_bytes=1),
    )
    assert (result.stage, result.error_code) == ("coverage", "cache_quota")


def test_cache_eviction_never_removes_entries_while_a_lease_is_active(tmp_path):
    cache = tmp_path / "osm"
    graph = cache / "routing" / "graphs" / ("a" * 64)
    graph.mkdir(parents=True)
    (graph / "tile").write_bytes(b"oversize")
    with _lease(cache, "active"):
        assert not _ensure_cache_quota(cache, 1)
        assert graph.is_dir()
    assert _ensure_cache_quota(cache, 1)
    assert not graph.exists()


def test_isolated_osm_timeout_kills_its_process_group(monkeypatch):
    def hang(connection, *args):
        import os

        os.setsid()
        connection.send_bytes(pickle.dumps(("stage", "routing")))
        time.sleep(30)

    monkeypatch.setattr("warpbuster.pipeline.osm.sys.platform", "linux")
    monkeypatch.setattr("warpbuster.pipeline.osm.eligible_gap_count", lambda *args: 1)
    monkeypatch.setattr("warpbuster.pipeline.osm.child_main", hang)
    config = replace(WebConfig(), osm_total_timeout_seconds=0.2)
    started = time.monotonic()
    result = run_osm_pipeline_isolated(None, None, None, config)
    assert time.monotonic() - started < 2
    assert result.status == "unavailable"
    assert result.stage == "routing"
    assert result.error_code == "osm_timeout"


def test_isolated_osm_enforces_temp_quota_and_cleans_partial_build(monkeypatch, tmp_path):
    def fill_temporary(connection, activity, integrity, plan, config, policy):
        import os

        os.setsid()
        connection.send_bytes(pickle.dumps(("stage", "prepare")))
        temporary = config.data_dir / "osm" / "routing" / "staging" / "partial"
        temporary.mkdir(parents=True)
        (temporary / "tile").write_bytes(b"too-large")
        time.sleep(30)

    monkeypatch.setattr("warpbuster.pipeline.osm.sys.platform", "linux")
    monkeypatch.setattr("warpbuster.pipeline.osm.eligible_gap_count", lambda *args: 1)
    monkeypatch.setattr("warpbuster.pipeline.osm.child_main", fill_temporary)
    config = replace(
        WebConfig(),
        data_dir=tmp_path,
        osm_total_timeout_seconds=5,
        osm_job_temp_quota_bytes=1,
        osm_minimum_free_bytes=1,
    )
    result = run_osm_pipeline_isolated(None, None, None, config)
    assert result.stage == "prepare" and result.error_code == "cache_quota"
    staging = tmp_path / "osm" / "routing" / "staging"
    assert not staging.exists() or not list(staging.iterdir())


def test_real_offline_manager_graph_and_core_application(tmp_path, capsys):
    activity, integrity, plan, core_config = processing_fixture(tmp_path)
    config = WebConfig(**vars(core_config))
    result = execute_osm_pipeline(activity, integrity, plan, config)
    assert result.status in {"complete", "partial"}
    assert result.plan.automatic_osm_json is not None
    assert result.plan.interval_plans
    assert result.plan.interval_plans[0].osm_provenance is not None
    assert result.private_audit["graph"]["graph_id"].startswith("sha256:")
    assert result.metrics is not None
    assert result.metrics.coverage_cells > 0
    assert result.metrics.snapshot_cache_hit is True
    assert result.metrics.snapshot_stale is False
    assert result.metrics.graph_cache_hit is False
    assert result.metrics.routing_queries == 1
    assert result.metrics.candidate_gaps == 1
    assert result.metrics.candidates >= 1
    assert process_job(tmp_path, 100_000, config=replace(config, isolate_osm=False))
    public = json.loads((tmp_path / "result.json").read_text())
    assert public["summary"]["applied_osm_gaps"] == 1
    assert public["summary"]["applied_gpx_gaps"] == 0
    assert public["osm"]["status"] == "complete"
    assert public["osm"]["snapshot_cache_hit"] is True
    assert public["osm"]["graph_cache_hit"] is True
    assert public["osm"]["routing_queries"] == 1
    assert (tmp_path / "corrected.fit").is_file()
    private = json.loads((tmp_path / "private-osm-audit.json").read_text())
    assert private["snapshot"]["downloaded"] is False
    assert private["graph"]["status"] == "CACHED"

    assert (
        cli_main(
            [
                "process",
                str(tmp_path / "original.fit"),
                str(tmp_path / "course.gpx"),
                "--osm-mode",
                "offline",
                "--work-dir",
                str(config.data_dir),
                "--output",
                str(tmp_path / "cli.fit"),
                "--json",
            ]
        )
        == 0
    )
    cli_report = json.loads(capsys.readouterr().out)
    assert cli_report["pipeline"]["osm"]["status"] == public["osm"]["status"]
    assert (
        cli_report["pipeline"]["osm"]["audit"]["graph"]["graph_id"] == private["graph"]["graph_id"]
    )
    assert (tmp_path / "cli.fit").read_bytes() == (tmp_path / "corrected.fit").read_bytes()
