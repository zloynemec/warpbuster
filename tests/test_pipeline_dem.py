"""021D shared entry-point opt-in, bounded DEM fallback and offline behavior."""

import gzip
import io
import json
import time
from dataclasses import replace

import pytest

from tests.test_automatic_osm import discover, fixture
from warpbuster.cli import build_parser, main
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.pipeline import DEMMode, OSMMode, PipelineConfig
from warpbuster.pipeline.config import DEFAULT_REPAIR_POLICY
from warpbuster.pipeline.dem import run_dem_stage
from warpbuster.pipeline.osm import OSMResult
from warpbuster.reconstruction.approximate_contract import ApproximateSelectionPolicy
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes
from warpbuster.report.html import write_repair_html


def test_cli_approximate_and_dem_options_are_shared_by_repair_and_process():
    parser = build_parser()
    for command in (
        ["repair", "activity.fit", "--osm-graph-id", "sha256:" + "a" * 64],
        ["process", "activity.fit", "course.gpx", "--osm-mode", "offline"],
    ):
        args = parser.parse_args(
            [
                *command,
                "--approximate-missing-osm",
                "--dem-mode",
                "offline",
                "--complete-missing-altitude",
                "--dry-run",
            ]
        )
        assert args.approximate_missing_osm and args.dem_mode is DEMMode.OFFLINE
        assert args.dry_run
        assert args.complete_missing_altitude


@pytest.mark.parametrize(
    ("options", "approximate", "dem", "altitude"),
    [
        ([], True, DEMMode.AUTO, True),
        (["--osm-mode", "offline"], True, DEMMode.OFFLINE, True),
        (["--osm-mode", "disabled"], False, DEMMode.DISABLED, False),
        (["--no-approximate-missing-osm"], False, DEMMode.DISABLED, False),
        (["--dem-mode", "disabled"], True, DEMMode.DISABLED, False),
        (["--no-complete-missing-altitude"], True, DEMMode.AUTO, False),
    ],
)
def test_process_default_chain_and_opt_out(monkeypatch, options, approximate, dem, altitude):
    from warpbuster import cli
    from warpbuster.pipeline import PipelineError

    seen = []

    def capture(*args, config, **kwargs):
        seen.append(config)
        raise PipelineError("test_stop", "captured")

    monkeypatch.setattr(cli, "run_repair", capture)
    assert cli.main(["process", "missing.fit", "missing.gpx", *options]) == 2
    assert len(seen) == 1
    assert seen[0].approximate_osm is approximate
    assert seen[0].dem_mode is dem
    assert seen[0].complete_missing_altitude is altitude
    assert seen[0].osm_maximum_area_km2 == 1_000.0


def test_process_osm_area_limit_is_configurable(monkeypatch):
    from warpbuster import cli
    from warpbuster.pipeline import PipelineError

    seen = []

    def capture(*args, config, **kwargs):
        seen.append(config.osm_maximum_area_km2)
        raise PipelineError("test_stop", "captured")

    monkeypatch.setattr(cli, "run_repair", capture)
    assert cli.main(["process", "missing.fit", "missing.gpx", "--osm-max-area-km2", "750"]) == 2
    assert seen == [750.0]
    assert cli.main(["process", "missing.fit", "missing.gpx", "--osm-max-area-km2", "0"]) == 2
    assert seen == [750.0]


def test_invalid_option_combinations_refuse_before_reading_fit(capsys):
    assert main(["repair", "missing.fit", "--approximate-missing-osm"]) == 2
    assert "requires enabled OSM" in capsys.readouterr().err
    assert (
        main(
            [
                "process",
                "missing.fit",
                "missing.gpx",
                "--no-approximate-missing-osm",
                "--dem-mode",
                "offline",
            ]
        )
        == 2
    )
    assert "requires approximate" in capsys.readouterr().err


@pytest.mark.parametrize(
    "changes",
    [
        {"approximate_osm": True},
        {"dem_mode": DEMMode.OFFLINE},
        {"dem_snapshot_id": "sha256:" + "a" * 64},
        {"complete_missing_altitude": True},
        {"approximate_osm": True, "dem_mode": DEMMode.AUTO, "dem_snapshot_id": "bad"},
    ],
)
def test_invalid_pipeline_dem_configuration(changes):
    with pytest.raises(ValueError):
        replace(PipelineConfig(), **changes)


def test_offline_dem_cache_miss_keeps_identical_2d_selection(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base)
    fallback = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=ApproximateSelectionPolicy(),
    )
    assert fallback.interval_plans[0].osm_provenance is not None
    config = PipelineConfig(
        data_dir=tmp_path / "work",
        osm_mode=OSMMode.OFFLINE,
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        dem_snapshot_id="sha256:" + "a" * 64,
    )
    result = run_dem_stage(
        activity,
        integrity,
        base,
        fallback,
        discovery,
        config,
        DEFAULT_REPAIR_POLICY,
        budget_seconds=5,
    )
    assert result.status == "unavailable"
    assert result.error_code == "cache_unavailable"
    assert result.plan == fallback
    assert (
        json.loads(result.plan.automatic_osm_json)["policy"]
        == "approximate-original-missing-osm-v1"
    )


def test_verified_offline_snapshot_reaches_shared_core_sampler(tmp_path):
    pytest.importorskip("valhalla")
    from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig
    from warpbuster_osm_routing.dem_coverage import DemCoveragePlan

    class Response(io.BytesIO):
        status = 200

        def __init__(self, data, url):
            super().__init__(data)
            self.headers = {"Content-Length": str(len(data))}
            self.url = url

        def geturl(self):
            return self.url

    compressed = gzip.compress(bytes(25_934_402), mtime=0)
    cache_root = tmp_path / "dem"
    cache = DemCache(
        DemCacheConfig(cache_directory=cache_root),
        response_factory=lambda url, _timeout: Response(compressed, url),
    )
    # FIT semicircle quantization puts the trusted 55° anchor just south of
    # the tile boundary, so the buffered route legitimately needs both tiles.
    snapshot = cache.ensure(DemCoveragePlan(("N54E037", "N55E037"), 2, 30), "auto")
    assert snapshot.snapshot_id is not None
    activity, _, integrity, base, _ = fixture(tmp_path, include_altitude=False)
    discovery = discover(activity, base)
    fallback = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    config = PipelineConfig(
        osm_mode=OSMMode.OFFLINE,
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        dem_snapshot_id=snapshot.snapshot_id,
        dem_cache_dir=cache_root,
        complete_missing_altitude=True,
    )
    result = run_dem_stage(
        activity,
        integrity,
        base,
        fallback,
        discovery,
        config,
        DEFAULT_REPAIR_POLICY,
        budget_seconds=10,
    )
    assert result.status == "complete"
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.altitude.status.value == "planned"
    assert len(result.altitude.updates) == 30
    written = write_repaired_fit(
        activity,
        result.plan,
        tmp_path / "dem-fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
        altitude_completion=result.altitude,
    )
    assert written.altitude_field_change_count == 30
    assert read_fit(written.output_path).records[150].altitude == 0.0
    evidence = json.loads(result.plan.automatic_osm_json)["decisions"][0]["approximate_audit"]
    assert evidence["dem_status"] in {"usable", "uninformative", "unavailable"}
    cached_auto = run_dem_stage(
        activity,
        integrity,
        base,
        fallback,
        discovery,
        replace(config, dem_mode=DEMMode.AUTO, dem_snapshot_id=None),
        DEFAULT_REPAIR_POLICY,
        budget_seconds=10,
    )
    assert cached_auto.status == "complete", cached_auto.error_code
    assert cached_auto.snapshot_id == snapshot.snapshot_id


def test_shared_pipeline_dry_run_and_fit_write_use_same_approximate_choice(tmp_path, monkeypatch):
    from warpbuster.pipeline import repair as pipeline

    activity, _, _, _, _ = fixture(tmp_path)
    source = activity.preservation.source_path

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            minimum_confidence=policy.minimum_confidence,
            approximate_policy=ApproximateSelectionPolicy() if config.approximate_osm else None,
        )
        return OSMResult(plan, "complete", discovery=discovery)

    monkeypatch.setattr(pipeline, "run_osm_pipeline", osm_stage)
    config = PipelineConfig(osm_mode=OSMMode.OFFLINE, approximate_osm=True)
    policy = replace(DEFAULT_REPAIR_POLICY, fill_missing_from_course=False)
    preview = pipeline.run_repair(source, policy=policy, config=config, dry_run=True)
    written = pipeline.run_repair(
        source, output_path=tmp_path / "fixed.fit", policy=policy, config=config
    )
    assert preview.plan == written.plan
    assert preview.selection == written.selection
    assert written.write_result is not None
    assert written.write_result.diff.unexpected_changed_field_count == 0
    assert written.write_result.diff.timestamps.compared_count == (
        written.write_result.diff.timestamps.unchanged_count
    )
    assert written.write_result.diff.sensors.compared_count == (
        written.write_result.diff.sensors.unchanged_count
    )


def test_dem_deadline_preserves_2d_plan(tmp_path, monkeypatch):
    from multiprocessing import get_context

    from warpbuster.pipeline import dem as stage

    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base)
    fallback = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )

    def slow_child(*_args):
        time.sleep(2)

    # This test injects a local function; use fork solely for the injected timeout stub.
    monkeypatch.setattr(stage, "get_context", lambda _method: get_context("fork"))
    monkeypatch.setattr(stage, "_dem_child", slow_child)
    config = PipelineConfig(
        osm_mode=OSMMode.OFFLINE, approximate_osm=True, dem_mode=DEMMode.OFFLINE
    )
    started = time.monotonic()
    result = run_dem_stage(
        activity,
        integrity,
        base,
        fallback,
        discovery,
        config,
        DEFAULT_REPAIR_POLICY,
        budget_seconds=0.05,
    )
    assert result.error_code == "timeout" and result.plan == fallback
    assert time.monotonic() - started < 1


def test_dead_dem_child_releases_only_its_own_cache_locks(tmp_path):
    from warpbuster.pipeline.dem import _cleanup_dead_child_locks

    config = PipelineConfig(data_dir=tmp_path / "work")
    lock_dir = config.data_dir / "dem" / "locks"
    lock_dir.mkdir(parents=True)
    owned = lock_dir / "maintenance.lock"
    other = lock_dir / "tile-N44E033.lock"
    owned.write_text("12345")
    other.write_text("99999")
    _cleanup_dead_child_locks(config, 12345)
    assert not owned.exists()
    assert other.read_text() == "99999"


def test_html_report_exposes_bounded_dem_stage(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base)
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    destination = tmp_path / "repair.html"
    write_repair_html(
        activity,
        integrity,
        None,
        plan,
        CourseReconstructionConfig(),
        destination,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        dem_stage={"status": "unavailable", "error_code": "cache_unavailable"},
    )
    html = destination.read_text()
    assert '"dem_stage"' in html
    assert '"cache_unavailable"' in html


def test_process_cli_reports_approximate_policy_and_dem_fallback(tmp_path, monkeypatch, capsys):
    from warpbuster.pipeline import repair as pipeline

    fixture(tmp_path)
    course = tmp_path / "unrelated.gpx"
    course.write_text(
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg><trkpt lat="45" lon="34"/><trkpt lat="45.001" lon="34.001"/>'
        "</trkseg></trk></gpx>"
    )

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            minimum_confidence=policy.minimum_confidence,
            approximate_policy=ApproximateSelectionPolicy(),
        )
        return OSMResult(plan, "complete", discovery=discovery)

    monkeypatch.setattr(pipeline, "run_osm_pipeline", osm_stage)
    code = main(
        [
            "process",
            str(tmp_path / "activity.fit"),
            str(course),
            "--osm-mode",
            "offline",
            "--approximate-missing-osm",
            "--dem-mode",
            "offline",
            "--dem-snapshot-id",
            "sha256:" + "a" * 64,
            "--work-dir",
            str(tmp_path / "work"),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["automatic_osm"]["policy"] == "approximate-original-missing-osm-v1"
    assert report["pipeline"]["dem"]["error_code"] == "cache_unavailable"
    assert report["automatic_osm"]["decisions"][0]["selected_route_id"] == "route-0"
    assert not (tmp_path / "activity.fixed.fit").exists()
