"""023A: explicit input mode through upload, persistence and isolated processing."""

import json
import sqlite3
import sys
import time
import uuid
from dataclasses import replace
from math import cos, radians

import pytest
from starlette.testclient import TestClient
from tests.fit_factory import write_trajectory_activity
from tests.processing_factory import processing_fixture
from tests.test_repair_cli import _repairable_fixture
from warpbuster.cli import main as cli_main
from warpbuster.fit.reader import read_fit
from warpbuster.pipeline import OSMMode
from warpbuster.pipeline import repair as pipeline
from warpbuster_web.app import create_app
from warpbuster_web.config import DEMMode, WebConfig
from warpbuster_web.processing import process_job
from warpbuster_web.refresh_results import refresh_report
from warpbuster_web.store import Store
from warpbuster_web.worker import Worker

ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"Origin": ORIGIN, "X-WarpBuster-Request": "1"}


def fit_file(path, missing=(), *, times=None):
    stamps = list(range(401)) if times is None else times
    scale = 111_195.0 * cos(radians(44.0))
    write_trajectory_activity(
        path,
        [
            (stamp, None, None) if index in missing else (stamp, 44.0, 33.0 + index * 2 / scale)
            for index, stamp in enumerate(stamps)
        ],
        distances_m=[float(index * 2) for index in range(len(stamps))],
        speeds_mps=[2.0] * len(stamps),
    )
    return path.read_bytes()


def offline_config(path, **kwargs):
    return WebConfig(
        data_dir=path,
        osm_mode=OSMMode.DISABLED,
        approximate_osm=False,
        dem_mode=DEMMode.DISABLED,
        complete_missing_altitude=False,
        **kwargs,
    )


def upload(client, files, *, key=None, data=None):
    return client.post(
        "/api/jobs",
        headers={**HEADERS, "X-WarpBuster-Upload-ID": key or str(uuid.uuid4())},
        files=files,
        data=data,
    )


def diagnostic(directory):
    return json.loads((directory / "private-processing-audit.json").read_text())


@pytest.fixture
def app(tmp_path):
    return create_app(offline_config(tmp_path / "private"), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN) as browser:
        assert browser.get("/api/session", headers=HEADERS).status_code == 200
        yield browser


def finish(app, uid):
    assert app.state.store.claim() == uid
    app.state.worker.process(uid)
    assert app.state.store.get(uid)["status"] == "ready"


def test_one_file_persists_mode_through_recovery_processing_and_expiry(app, client, tmp_path):
    fit = fit_file(tmp_path / "clean.fit")
    response = upload(client, {"activity": ("private-SECRET.fit", fit)})
    assert response.status_code == 202
    uid = response.json()["uid"]
    store = app.state.store
    assert store.get(uid)["has_course"] == 0
    inputs = store.uploads_dir / uid
    assert {path.name for path in inputs.iterdir()} == {"original.fit"}
    assert inputs.stat().st_mode & 0o777 == 0o700
    assert (inputs / "original.fit").stat().st_mode & 0o777 == 0o600
    reopened = Store(store.config)
    reopened.recover()
    assert reopened.get(uid)["has_course"] == 0
    finish(app, uid)
    output = store.config.data_dir / uid
    audit = diagnostic(output)
    assert audit["mode"] == "fit_only"
    assert audit["observed_gps_coverage"]["status"] == "passed"
    assert client.get(f"/api/results/{uid}").json()["tracks"]["course"] == []
    assert not client.get(f"/api/results/{uid}/status").json()["can_download"]
    for url in (
        f"/res/{uid}/private-processing-audit.json",
        f"/{uid}/private-processing-audit.json",
    ):
        assert client.get(url).status_code == 404
    assert (output / "private-processing-audit.json").stat().st_mode & 0o777 == 0o600
    assert "SECRET" not in store.events.path.read_text()
    started = next(
        json.loads(line)
        for line in store.events.path.read_text().splitlines()
        if json.loads(line)["event"] == "processing_started"
    )
    assert "--fit-only" in started["command"]
    assert started["fill_missing_from_course"] is False
    assert not started["has_course"]
    with store.connect() as db:
        db.execute("UPDATE jobs SET expires=? WHERE uid=?", (time.time() - 1, uid))
    store.expire()
    assert not inputs.exists() and not output.exists()
    assert client.get(f"/api/results/{uid}").status_code == 410


def test_reused_upload_keeps_original_mode_and_new_request_can_add_course(app, client, tmp_path):
    fit, gpx = _repairable_fixture(tmp_path)
    files = {"activity": (fit.name, fit.read_bytes())}
    key = str(uuid.uuid4())
    first = upload(client, files, key=key)
    files["course"] = (gpx.name, gpx.read_bytes())
    repeated = upload(client, files, key=key)
    assert first.json() == repeated.json()
    uid = first.json()["uid"]
    assert app.state.store.get(uid)["has_course"] == 0
    assert not (app.state.store.uploads_dir / uid / "course.gpx").exists()
    added = upload(client, files)
    assert added.status_code == 202 and added.json()["uid"] != uid
    assert app.state.store.get(added.json()["uid"])["has_course"] == 1


@pytest.mark.parametrize(
    "field", ["start", "finish", "loop_point", "minimum_observed_gps_coverage_percent", "mode"]
)
@pytest.mark.parametrize("as_file", [False, True])
def test_unsupported_parameters_are_rejected_without_persistence(
    app, client, tmp_path, field, as_file
):
    files = {"activity": ("run.fit", fit_file(tmp_path / "run.fit"))}
    data = None
    if as_file:
        files[field] = ("unknown.gpx", b"PRIVATE_VALUE")
    else:
        data = {field: "PRIVATE_VALUE"}
    response = upload(client, files, data=data)
    assert response.status_code == 400
    assert "PRIVATE_VALUE" not in response.text + app.state.store.events.path.read_text()
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert not list(app.state.store.uploads_dir.iterdir())


@pytest.mark.parametrize("course", [("empty.gpx", b""), ("route.txt", b"not gpx")])
def test_present_bad_course_is_not_treated_as_absent(app, client, tmp_path, course):
    files = {"activity": ("run.fit", fit_file(tmp_path / "run.fit")), "course": course}
    assert upload(client, files).status_code == 400
    assert not list(app.state.store.uploads_dir.iterdir())


@pytest.mark.parametrize("missing", [False, True])
def test_legacy_database_migration_requires_course_and_preserves_old_job(tmp_path, missing):
    config = offline_config(tmp_path / "private")
    config.data_dir.mkdir()
    uid = "a" * 43
    with sqlite3.connect(config.data_dir / "jobs.sqlite3") as db:
        db.execute(
            "CREATE TABLE jobs (uid TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL, "
            "expires REAL NOT NULL, status TEXT NOT NULL, error TEXT, has_fit INTEGER NOT NULL "
            "DEFAULT 0, submission_key TEXT NOT NULL, UNIQUE(owner,submission_key))"
        )
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
            (uid, "owner", time.time(), time.time() + 1000, "queued", None, 0, "request"),
        )
    legacy = config.data_dir / uid
    legacy.mkdir()
    _, course = _repairable_fixture(legacy)
    if missing:
        course.unlink()
    store = Store(config)
    assert store.get(uid)["has_course"] == 1
    assert Store(config).get(uid)["submission_key"] == "request"
    store.recover()
    assert store.claim() == uid
    Worker(store).process(uid)
    result = store.get(uid)
    assert result["has_course"] == 1
    if missing:
        assert result["status"] == "failed" and result["error"] == "invalid_gpx"
        assert not (legacy / "corrected.fit").exists()
    else:
        assert result["status"] == "ready" and result["has_fit"]
        assert diagnostic(legacy)["observed_gps_coverage"]["status"] == "not_applicable"


@pytest.mark.parametrize("case", ["below", "unavailable", "custom_threshold"])
def test_gate_prevents_osm_dem_and_write_but_keeps_diagnostics(tmp_path, monkeypatch, case):
    missing = range(250) if case == "below" else range(100, 190)
    fit_file(tmp_path / "original.fit", missing, times=[0, 0, 1] if case == "unavailable" else None)
    config = WebConfig(
        data_dir=tmp_path / "cache",
        minimum_observed_gps_coverage_percent=80.0 if case == "custom_threshold" else 51.0,
    )

    def unexpected(*args, **kwargs):
        pytest.fail("Coverage refusal must precede optional reconstruction and FIT writing")

    monkeypatch.setattr(pipeline, "run_osm_pipeline", unexpected)
    monkeypatch.setattr(pipeline, "run_dem_stage", unexpected)
    monkeypatch.setattr(pipeline, "write_repaired_fit", unexpected)
    assert not process_job(tmp_path, 100_000, config=config, has_course=False)
    coverage = diagnostic(tmp_path)["observed_gps_coverage"]
    assert coverage["status"] == ("unavailable" if case == "unavailable" else "below_threshold")
    assert (
        coverage["minimum_observed_gps_coverage_percent"]
        == config.minimum_observed_gps_coverage_percent
    )
    if case == "unavailable":
        assert coverage["observed_gps_coverage_percent"] is None
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["outcome"] == "unresolved" and report["fit_diff"] is None
    assert report["tracks"]["course"] == []
    assert not (tmp_path / "corrected.fit").exists()


def test_custom_coverage_config_reaches_isolated_worker(app, client, tmp_path):
    config = replace(app.state.config, minimum_observed_gps_coverage_percent=80.0)
    app.state.worker = Worker(Store(config))
    fit = fit_file(tmp_path / "run.fit", range(100, 190))
    uid = upload(client, {"activity": ("run.fit", fit)}).json()["uid"]
    finish(app, uid)
    coverage = diagnostic(config.data_dir / uid)["observed_gps_coverage"]
    assert coverage["status"] == "below_threshold"
    assert coverage["minimum_observed_gps_coverage_percent"] == 80
    status = client.get(f"/api/results/{uid}/status").json()
    assert status["status"] == "ready" and not status["can_download"]


@pytest.mark.integration
@pytest.mark.parametrize(
    "isolated",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                sys.platform != "linux", reason="OSM resource isolation requires Linux"
            ),
        ),
    ],
)
def test_fit_only_cached_repair_matches_cli_preserves_ends_and_owner_contract(
    tmp_path, capsys, isolated
):
    _, _, _, cache = processing_fixture(tmp_path)
    config = WebConfig(
        data_dir=cache.data_dir,
        osm_mode=OSMMode.OFFLINE,
        dem_mode=DEMMode.DISABLED,
        complete_missing_altitude=False,
        isolate_osm=isolated,
    )
    missing = set(range(20)) | set(range(100, 151)) | set(range(381, 401))
    source = tmp_path / "original.fit"
    payload = fit_file(source, missing)
    app = create_app(config, start_worker=False)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/session", headers=HEADERS)
        uid = upload(client, {"activity": ("run.fit", payload)}).json()["uid"]
        if isolated:
            finish(app, uid)
        else:
            # Exercise real native routing on macOS too, without changing the
            # production worker's mandatory Linux resource isolation.
            assert app.state.store.claim() == uid
            has_fit = process_job(
                config.data_dir / uid,
                config.record_limit,
                input_directory=app.state.store.uploads_dir / uid,
                config=config,
                has_course=False,
            )
            app.state.store.state(uid, "ready", has_fit=has_fit)
        report = client.get(f"/api/results/{uid}").json()
        assert report["outcome"] == "repaired" and report["partial"]
        assert report["summary"]["filled_points"] == 51
        assert report["summary"]["unresolved_points"] == 40
        assert report["tracks"]["course"] == []
        downloaded = client.get(f"/api/results/{uid}/download")
        assert downloaded.status_code == 200
        with TestClient(app, base_url=ORIGIN) as other:
            assert other.get(f"/api/results/{uid}/download").status_code == 403
        fixed_path = config.data_dir / uid / "corrected.fit"
        fixed = read_fit(fixed_path)
        original = read_fit(source)
        for before, after in zip(original.records, fixed.records, strict=True):
            assert before.timestamp == after.timestamp
            if before.index not in missing:
                assert (before.latitude, before.longitude) == (after.latitude, after.longitude)
        assert fixed.records[0].latitude is fixed.records[-1].latitude is None
        output = tmp_path / "cli.fixed.fit"
        assert (
            cli_main(
                [
                    "process",
                    str(source),
                    "--osm-mode",
                    "offline",
                    "--dem-mode",
                    "disabled",
                    "--work-dir",
                    str(config.data_dir),
                    "--output",
                    str(output),
                    "--json",
                ]
            )
            == 0
        )
        cli_report = json.loads(capsys.readouterr().out)
        assert output.read_bytes() == downloaded.content
        assert (
            cli_report["pipeline"]["observed_gps_coverage"]
            == diagnostic(fixed_path.parent)["observed_gps_coverage"]
        )


def test_explicit_fit_only_refresh_ignores_unrelated_course_file(tmp_path):
    fit_file(tmp_path / "original.fit")
    (tmp_path / "course.gpx").write_text("unrelated invalid GPX")
    assert not process_job(tmp_path, 100_000, has_course=False)
    path = tmp_path / "result.json"
    report = json.loads(path.read_text())
    report["schema_version"] = 1
    path.write_text(json.dumps(report))
    assert refresh_report(tmp_path, tmp_path, has_course=False)
    assert json.loads(path.read_text())["tracks"]["course"] == []
    assert not (tmp_path / "corrected.fit").exists()


def test_same_fit_with_course_bypasses_gate_without_changing_other_jobs(tmp_path):
    source = tmp_path / "inputs"
    source.mkdir()
    _repairable_fixture(source)
    config = offline_config(tmp_path / "data", minimum_observed_gps_coverage_percent=100.0)
    for index, has_course in enumerate((False, True, False)):
        output = tmp_path / str(index)
        output.mkdir()
        assert (
            process_job(
                output, 100_000, input_directory=source, config=config, has_course=has_course
            )
            == has_course
        )
        coverage = diagnostic(output)["observed_gps_coverage"]
        assert coverage["status"] == ("not_applicable" if has_course else "below_threshold")
        assert (output / "corrected.fit").exists() == has_course


@pytest.mark.parametrize("case", ["passed", "below_threshold", "unavailable", "not_applicable"])
def test_public_coverage_and_gap_schema_is_explicit(tmp_path, case):
    if case == "not_applicable":
        _repairable_fixture(tmp_path)
    else:
        fit_file(
            tmp_path / "original.fit",
            range(250) if case == "below_threshold" else range(100, 151),
            times=[0, 0, 1] if case == "unavailable" else None,
        )
    process_job(
        tmp_path,
        100_000,
        config=offline_config(tmp_path / "cache"),
        has_course=case == "not_applicable",
    )
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["schema_version"] == 5
    assert report["mode"] == ("fit_with_course" if case == "not_applicable" else "fit_only")
    public = report["observed_gps_coverage"]
    assert set(public) == {
        "status",
        "minimum_observed_gps_coverage_percent",
        "observed_gps_coverage_percent",
        "active_duration_seconds",
        "observed_gps_duration_seconds",
        "reason",
    }
    private = diagnostic(tmp_path)["observed_gps_coverage"]
    assert public == {key: private[key] for key in public}
    assert public["status"] == case
    if case == "unavailable":
        assert public["observed_gps_coverage_percent"] is None
    for gap in report["gaps"]:
        assert gap["kind"] in {"prefix", "internal", "suffix"}
        assert isinstance(gap["filled_points"], int)
        assert isinstance(gap["unresolved_points"], int)
    serialized = json.dumps(report)
    assert str(tmp_path) not in serialized
    assert "endpoint_hints" not in serialized
    assert "maximum_observed_gps_interval_seconds" not in serialized
