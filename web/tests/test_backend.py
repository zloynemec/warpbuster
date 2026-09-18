"""Synthetic end-to-end repair and browser ownership contract."""

import asyncio
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from tempfile import SpooledTemporaryFile

import pytest
from starlette.testclient import TestClient
from tests.test_repair_cli import _missing_endpoint_fixture, _repairable_fixture
from tests.test_unreachable_tail import tail_fixture
from warpbuster.cli import main as cli_main
from warpbuster.fit.reader import read_fit
from warpbuster_web.app import create_app
from warpbuster_web.config import WebConfig
from warpbuster_web.events import EventLog
from warpbuster_web.processing import PUBLIC_FIELDS, process_job

ORIGIN = "http://127.0.0.1:8000"
HEADERS = {"X-WarpBuster-Request": "1", "Origin": ORIGIN}


@pytest.fixture
def app(tmp_path):
    return create_app(WebConfig(data_dir=tmp_path / "private"), start_worker=False)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=ORIGIN) as browser:
        assert browser.get("/api/session", headers=HEADERS).status_code == 200
        yield browser


@pytest.fixture
def files(tmp_path):
    directory = tmp_path / "synthetic"
    directory.mkdir()
    fit, gpx = _repairable_fixture(directory)
    return {
        "activity": ("private-athlete-SECRET.fit", fit.read_bytes()),
        "course": ("private-route-SECRET.gpx", gpx.read_bytes()),
    }


def submit(client, files, key=None):
    return client.post(
        "/api/jobs",
        headers={**HEADERS, "X-WarpBuster-Upload-ID": key or str(uuid.uuid4())},
        files=files,
    )


def test_health_checks_database_without_creating_a_session(app):
    with TestClient(app, base_url=ORIGIN) as probe:
        response = probe.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert probe.head("/health").status_code == 200


def test_health_reports_database_failure_without_details(app, monkeypatch):
    def unavailable():
        raise sqlite3.OperationalError("private database path")

    monkeypatch.setattr(app.state.store, "connect", unavailable)
    with TestClient(app, base_url=ORIGIN) as probe:
        response = probe.get("/health")
        assert response.status_code == 503
        assert response.json() == {"status": "unavailable"}
        assert "private database path" not in response.text


def finish(app, uid):
    assert app.state.store.claim() == uid
    app.state.worker.process(uid)
    assert app.state.store.get(uid)["status"] == "ready"


def test_actual_repair_public_projection_and_owner_only_download(app, client, files, tmp_path):
    response = submit(client, files)
    assert response.status_code == 202
    uid = response.json()["uid"]
    assert response.json()["url"] == f"/res/{uid}"
    assert len(uid) == 43
    endpoint = f"/api/results/{uid}"
    assert client.get(endpoint).status_code == 409
    assert client.get(f"{endpoint}/status").json()["status"] == "queued"
    finish(app, uid)

    status = client.get(f"{endpoint}/status")
    assert status.json()["is_owner"] and status.json()["can_download"]
    assert "no-store" in status.headers["cache-control"]
    report = client.get(endpoint).json()
    assert report["outcome"] == "repaired"
    assert report["summary"]["applied_intervals"] == 1
    assert report["fit_diff"]["changed_records"] == 1
    assert report["fit_diff"]["timestamps_unchanged"]
    assert report["fit_diff"]["sensors_unchanged"]
    assert report["tracks"]["original"] != report["tracks"]["corrected"]
    assert all(
        (change["message"], change["field"]) in PUBLIC_FIELDS
        for change in report["fit_diff"]["changes"]
    )
    payload = json.dumps(report)
    for secret in (
        "SECRET",
        str(tmp_path),
        "2026-01-01",
        "serial_number",
        '"heart_rate":',
        "source_path",
    ):
        assert secret not in payload
    cookie = client.cookies[app.state.config.cookie_name]
    assert cookie not in payload and hashlib.sha256(cookie.encode()).hexdigest() not in payload

    output = client.get(f"{endpoint}/download")
    assert output.status_code == 200 and "attachment" in output.headers["content-disposition"]
    assert "no-store" in output.headers["cache-control"]
    downloaded = tmp_path / "download.fit"
    downloaded.write_bytes(output.content)
    assert len(read_fit(downloaded).records) == 33
    assert output.content == (app.state.config.data_dir / uid / "corrected.fit").read_bytes()
    assert not (app.state.config.data_dir / uid / "original.fit").exists()
    assert not (app.state.config.data_dir / uid / "course.gpx").exists()
    assert (app.state.store.uploads_dir / uid / "original.fit").read_bytes() == files["activity"][1]
    assert (app.state.store.uploads_dir / uid / "course.gpx").read_bytes() == files["course"][1]

    with TestClient(app, base_url=ORIGIN) as guest:
        for with_own_cookie in (False, True):
            if with_own_cookie:
                guest.get("/api/session", headers=HEADERS)
            assert guest.get(f"/res/{uid}").status_code == 200
            assert guest.get(endpoint).json() == report
            assert not guest.get(f"{endpoint}/status").json()["can_download"]
            assert not guest.get(f"{endpoint}/status").json()["is_owner"]
            assert guest.get(f"{endpoint}/download").status_code == 403
            assert guest.head(f"{endpoint}/download").status_code == 403
            assert (
                guest.get(f"{endpoint}/download", headers={"Range": "bytes=0-10"}).status_code
                == 403
            )
        for path in (
            f"/{uid}/corrected.fit",
            f"/assets/{uid}/original.fit",
            "/jobs.sqlite3",
            f"/uploads/{uid}/original.fit",
            f"/uploads/{uid}/course.gpx",
            "/logs/events.jsonl",
        ):
            assert guest.get(path).status_code == 404


def test_cookie_is_http_only_hashed_and_separate_from_public_uid(app, client, files):
    with TestClient(app, base_url=ORIGIN) as fresh:
        response = fresh.get("/api/session", headers=HEADERS)
        cookie_header = response.headers["set-cookie"]
        assert "HttpOnly" in cookie_header and "SameSite=lax" in cookie_header
        assert "Path=/" in cookie_header and "Max-Age=" in cookie_header
        token = fresh.cookies[app.state.config.cookie_name]
        uid = submit(fresh, files).json()["uid"]
        assert token != uid
        with app.state.store.connect() as db:
            hashes = [row[0] for row in db.execute("SELECT hash FROM sessions")]
        assert token not in hashes
        assert hashlib.sha256(token.encode()).hexdigest() in hashes
        assert "set-cookie" not in fresh.get("/api/session", headers=HEADERS).headers


def test_https_cookie_and_origin_protection(tmp_path):
    config = WebConfig(data_dir=tmp_path / "https", public_origin="https://example.test")
    app = create_app(config, start_worker=False)
    with TestClient(app, base_url=config.public_origin) as client:
        response = client.get("/api/session", headers={"X-WarpBuster-Request": "1"})
        assert response.headers["set-cookie"].startswith("__Host-warpbuster-owner=")
        assert "Secure" in response.headers["set-cookie"]
        assert "Domain=" not in response.headers["set-cookie"]
        assert client.get("/api/session").status_code == 403
        assert client.get("/api/session", headers=HEADERS).status_code == 403
        assert client.post("/api/jobs", headers={"Origin": "https://evil.test"}).status_code == 403
        assert "access-control-allow-origin" not in response.headers


def test_expired_owner_loses_download_but_report_stays_public(app, client, files):
    uid = submit(client, files).json()["uid"]
    finish(app, uid)
    with app.state.store.connect() as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.get(f"/api/results/{uid}/download").status_code == 403
    assert client.get(f"/api/results/{uid}").status_code == 200
    assert not client.get(f"/api/results/{uid}/status").json()["is_owner"]
    assert submit(client, files).status_code == 401


def test_result_expiry_denies_every_api_and_deletes_artifacts(app, client, files):
    uid = submit(client, files).json()["uid"]
    finish(app, uid)
    with app.state.store.connect() as db:
        db.execute("UPDATE jobs SET expires=? WHERE uid=?", (time.time() - 1, uid))
    for suffix in ("", "/status", "/download"):
        assert client.get(f"/api/results/{uid}{suffix}").status_code == 410
    app.state.store.expire()
    assert not (app.state.config.data_dir / uid).exists()
    assert not (app.state.store.uploads_dir / uid).exists()
    assert app.state.store.get(uid)["status"] == "expired"


@pytest.mark.parametrize("field,code", [("activity", "invalid_fit"), ("course", "invalid_gpx")])
def test_invalid_content_fails_without_private_details(app, client, files, field, code):
    files[field] = (files[field][0], b"invalid SECRET user content")
    uid = submit(client, files).json()["uid"]
    assert app.state.store.claim() == uid
    app.state.worker.process(uid)
    job = app.state.store.get(uid)
    assert job["status"] == "failed" and job["error"] == code
    response = client.get(f"/api/results/{uid}/status")
    assert response.json()["message"] and "SECRET" not in response.text
    assert not list((app.state.config.data_dir / uid).iterdir())
    assert (app.state.store.uploads_dir / uid / "original.fit").read_bytes() == files["activity"][1]
    assert (app.state.store.uploads_dir / uid / "course.gpx").read_bytes() == files["course"][1]
    assert client.get(f"/api/results/{uid}/download").status_code == 409


def test_duplicate_submit_is_idempotent_and_owned(app, client, files):
    key = str(uuid.uuid4())
    first = submit(client, files, key)
    second = submit(client, files, key)
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    with TestClient(app, base_url=ORIGIN) as another:
        another.get("/api/session", headers=HEADERS)
        assert submit(another, files, key).json()["uid"] != first.json()["uid"]


@pytest.mark.parametrize("variant", ["missing", "duplicate", "wrong_extension", "empty", "extra"])
def test_invalid_form_rejected_without_storing_a_job(app, client, files, variant):
    if variant == "missing":
        del files["activity"]
    elif variant == "duplicate":
        files = [("activity", files["activity"]), ("activity", files["activity"])]
    elif variant == "wrong_extension":
        files["activity"] = ("run.fit.zip", files["activity"][1])
    elif variant == "empty":
        files["course"] = ("route.gpx", b"")
    else:
        files["extra"] = ("other.fit", b"extra")
    assert submit(client, files).status_code == 400
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert not list(app.state.store.uploads_dir.iterdir())
    assert not any(
        path.is_dir() and path.name not in {"uploads", "logs"}
        for path in app.state.config.data_dir.iterdir()
    )


def test_size_and_queue_limits(tmp_path, files):
    for changes in ({"file_limit_bytes": 1}, {"body_limit_bytes": 20}):
        app = create_app(
            WebConfig(data_dir=tmp_path / str(uuid.uuid4()), **changes), start_worker=False
        )
        with TestClient(app, base_url=ORIGIN) as client:
            client.get("/api/session", headers=HEADERS)
            assert submit(client, files).status_code == 413
    app = create_app(WebConfig(data_dir=tmp_path / "queue", max_pending_jobs=1), start_worker=False)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/session", headers=HEADERS)
        assert submit(client, files).status_code == 202
        response = submit(client, files)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_stream_without_content_length_obeys_body_limit(tmp_path):
    app = create_app(
        WebConfig(data_dir=tmp_path / "stream", body_limit_bytes=100), start_worker=False
    )
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/session", headers=HEADERS)
        chunks = iter(
            [
                b'--boundary\r\nContent-Disposition: form-data; name="activity"; filename="a.fit"\r\n\r\n',
                b"X" * 200,
                b"\r\n--boundary--\r\n",
            ]
        )
        response = client.post(
            "/api/jobs",
            headers={
                **HEADERS,
                "Content-Type": "multipart/form-data; boundary=boundary",
                "X-WarpBuster-Upload-ID": str(uuid.uuid4()),
            },
            content=chunks,
        )
        assert response.status_code == 413
        with app.state.store.connect() as db:
            assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_processing_timeout_retains_input_pair(app, client, files, monkeypatch):
    uid = submit(client, files).json()["uid"]

    def timeout(*args, **kwargs):
        return None, "timeout"

    monkeypatch.setattr(app.state.worker, "_run_processor", timeout)
    assert app.state.store.claim() == uid
    app.state.worker.process(uid)
    assert app.state.store.get(uid)["error"] == "timeout"
    assert not list((app.state.config.data_dir / uid).iterdir())
    assert (app.state.store.uploads_dir / uid / "original.fit").read_bytes() == files["activity"][1]
    assert (app.state.store.uploads_dir / uid / "course.gpx").read_bytes() == files["course"][1]


def test_worker_graceful_stop_interrupts_active_process_group(app):
    worker = app.state.worker
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            worker._run_processor,
            [sys.executable, "-c", "import time; time.sleep(30)"],
            30,
            {},
        )
        deadline = time.monotonic() + 2
        while worker.active_process is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.active_process is not None
        worker.stop_event.set()
        return_code, error = future.result(timeout=5)
    assert return_code is not None and error == "interrupted"
    assert worker.active_process is None


def test_restart_marks_interrupted_jobs_and_preserves_queued_inputs(app, client, files):
    interrupted = submit(client, files).json()["uid"]
    queued = submit(client, files).json()["uid"]
    assert app.state.store.claim() == interrupted
    app.state.store.recover()
    assert app.state.store.get(interrupted)["error"] == "interrupted"
    assert not (app.state.config.data_dir / interrupted).exists()
    assert (app.state.store.uploads_dir / interrupted / "original.fit").exists()
    assert (app.state.store.uploads_dir / queued / "original.fit").exists()
    finish(app, queued)


def test_background_worker_processes_submitted_job(tmp_path, files):
    config = WebConfig(
        data_dir=tmp_path / "live", worker_poll_seconds=0.01, process_timeout_seconds=10
    )
    app = create_app(config)
    with TestClient(app, base_url=ORIGIN) as client:
        client.get("/api/session", headers=HEADERS)
        uid = submit(client, files).json()["uid"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status = client.get(f"/api/results/{uid}/status").json()
            if status["status"] in {"ready", "failed"}:
                break
            time.sleep(0.02)
        assert status["status"] == "ready" and status["can_download"]
    assert not app.state.worker.thread.is_alive()


def test_clean_track_does_not_produce_fake_repaired_file(tmp_path):
    source = tmp_path / "first"
    source.mkdir()
    _, course = _repairable_fixture(source)
    assert process_job(source, 100_000)
    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "original.fit").write_bytes((source / "corrected.fit").read_bytes())
    (clean / "course.gpx").write_bytes(course.read_bytes())
    assert not process_job(clean, 100_000)
    assert not (clean / "corrected.fit").exists()
    report = json.loads((clean / "result.json").read_text())
    assert report["outcome"] == "unchanged" and report["fit_diff"] is None


@pytest.mark.parametrize("scenario", ["missing_endpoints", "medium_tail"])
def test_web_matches_requested_cli_gap_filling_and_medium_policy(
    app, client, tmp_path, scenario, capsys
):
    directory = tmp_path / "policy"
    directory.mkdir()
    if scenario == "missing_endpoints":
        fit, course = _missing_endpoint_fixture(directory)
    else:
        tail_fixture(directory, missing_entry=True)
        fit, course = directory / "activity.fit", directory / "activity.gpx"
    original = read_fit(fit)
    files = {"activity": (fit.name, fit.read_bytes()), "course": (course.name, course.read_bytes())}
    uid = submit(client, files).json()["uid"]
    finish(app, uid)
    endpoint = f"/api/results/{uid}"
    report = client.get(endpoint).json()
    assert report["outcome"] == "repaired"
    assert report["summary"]["applied_intervals"] >= 1
    assert any(
        item["confidence"] == "medium" and item["action"] == "applied"
        for item in report["intervals"]
    )
    assert report["fit_diff"]["timestamps_unchanged"] and report["fit_diff"]["sensors_unchanged"]

    expected = directory / "cli.fit"
    assert (
        cli_main(
            [
                "process",
                str(fit),
                str(course),
                "--output",
                str(expected),
                "--osm-mode",
                "disabled",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert client.get(f"{endpoint}/download").content == expected.read_bytes()
    fixed = read_fit(expected)
    assert all(
        record.latitude is not None and record.longitude is not None for record in fixed.records
    )
    assert [record.timestamp for record in fixed.records] == [
        record.timestamp for record in original.records
    ]
    assert [record.distance for record in fixed.records] == [
        record.distance for record in original.records
    ]
    if scenario == "medium_tail":
        assert fixed.records[110].latitude != original.records[110].latitude


def test_configuration_rejects_public_http_and_static_private_storage(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        WebConfig(public_origin="http://example.test")
    with pytest.raises(ValueError, match="outside"):
        WebConfig(static_dir=tmp_path, data_dir=tmp_path / "private")
    with pytest.raises(ValueError, match="positive"):
        replace(WebConfig(), process_timeout_seconds=0)


def test_missing_result_and_noindex_headers(client):
    response = client.get(f"/api/results/{'a' * 43}/status")
    assert response.status_code == 404
    assert "noindex" in response.headers["x-robots-tag"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/res/not-a-valid-uid").status_code == 404


def capture_upload_spools(monkeypatch):
    streams = []

    def spool(*args, **kwargs):
        stream = SpooledTemporaryFile(*args, **kwargs)  # noqa: SIM115 — parser owns its lifetime
        streams.append(stream)
        return stream

    monkeypatch.setattr("starlette.formparsers.SpooledTemporaryFile", spool)
    return streams


def test_malformed_multipart_closes_already_opened_files(app, client, monkeypatch):
    streams = capture_upload_spools(monkeypatch)
    response = client.post(
        "/api/jobs",
        headers={
            **HEADERS,
            "X-WarpBuster-Upload-ID": str(uuid.uuid4()),
            "Content-Type": "multipart/form-data; boundary=b",
        },
        content=(
            b'--b\r\nContent-Disposition: form-data; name="activity"; filename="a.fit"\r\n\r\n'
            b"content\r\n--b\r\nInvalid\x00header: broken\r\n\r\n"
        ),
    )
    assert response.status_code == 400
    assert streams and all(stream.closed for stream in streams)
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_slow_upload_times_out_and_closes_partial_files(tmp_path, monkeypatch):
    config = WebConfig(data_dir=tmp_path / "slow", upload_timeout_seconds=0.05)
    app = create_app(config, start_worker=False)
    token = app.state.store.new_session()
    streams = capture_upload_spools(monkeypatch)

    async def request():
        sent = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {
                    "type": "http.request",
                    "more_body": True,
                    "body": b'--b\r\nContent-Disposition: form-data; name="activity"; filename="a.fit"\r\n\r\npartial',
                }
            await asyncio.sleep(10)
            raise AssertionError("The server must time out before this")

        async def send(message):
            sent.append(message)

        headers = {
            **HEADERS,
            "Host": "127.0.0.1:8000",
            "Content-Type": "multipart/form-data; boundary=b",
            "Cookie": f"{config.cookie_name}={token}",
            "X-WarpBuster-Upload-ID": str(uuid.uuid4()),
        }
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "POST",
            "scheme": "http",
            "path": "/api/jobs",
            "raw_path": b"/api/jobs",
            "query_string": b"",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "server": ("127.0.0.1", 8000),
            "client": ("127.0.0.1", 12345),
        }
        await app(scope, receive, send)
        assert next(item["status"] for item in sent if item["type"] == "http.response.start") == 408

    asyncio.run(request())
    assert streams and all(stream.closed for stream in streams)
    with app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def events(app):
    return [json.loads(line) for line in app.state.store.events.path.read_text().splitlines()]


def test_log_correlates_upload_pair_and_actual_processor_invocation(app, client, files):
    key = str(uuid.uuid4())
    uid = submit(client, files, key).json()["uid"]
    assert submit(client, files, key).json()["uid"] == uid
    finish(app, uid)
    journal = events(app)
    assert all(event["pair_id"] == uid for event in journal)
    assert [event["event"] for event in journal] == [
        "upload_started",
        "file_uploaded",
        "file_uploaded",
        "upload_completed",
        "upload_reused",
        "processing_started",
        "osm_pipeline_completed",
        "dem_pipeline_completed",
        "altitude_completion_completed",
        "processing_completed",
    ]
    for event, role in zip(journal[1:3], ("activity", "course"), strict=True):
        assert event["role"] == role
        assert event["size_bytes"] == len(files[role][1])
        assert event["sha256"] == hashlib.sha256(files[role][1]).hexdigest()
    call, osm, result = journal[5], journal[6], journal[-1]
    assert call["command"][-2:] == ["--inputs", str(app.state.store.uploads_dir / uid)]
    assert call["minimum_confidence"] == call["minimum_invalidation_confidence"] == "medium"
    assert call["fill_missing_from_course"] is True
    assert osm["status"] in {"not_needed", "unavailable", "complete", "partial"}
    assert osm["duration_seconds"] >= 0
    assert isinstance(osm["eligible_gaps"], int)
    assert osm["applied_gpx_gaps"] >= 0
    assert osm["applied_osm_gaps"] >= 0
    assert osm["unresolved_gaps"] >= 0
    assert result["return_code"] == 0 and result["has_fit"] is True
    assert result["duration_seconds"] >= 0
    text = app.state.store.events.path.read_text()
    assert "SECRET" not in text
    assert client.cookies[app.state.config.cookie_name] not in text
    assert key not in text
    assert app.state.store.events.path.stat().st_mode & 0o777 == 0o600
    pair = app.state.store.uploads_dir / uid
    assert pair.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in pair.iterdir()} == {"original.fit", "course.gpx"}
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in pair.iterdir())


def test_processing_failure_and_expiry_are_logged_with_pair_id(app, client, files):
    files["activity"] = ("private.fit", b"not a FIT")
    uid = submit(client, files).json()["uid"]
    assert app.state.store.claim() == uid
    app.state.worker.process(uid)
    failure = events(app)[-1]
    assert failure["event"] == "processing_failed" and failure["pair_id"] == uid
    assert failure["error"] == "invalid_fit" and failure["return_code"] == 1
    assert (app.state.store.uploads_dir / uid / "original.fit").read_bytes() == b"not a FIT"
    with app.state.store.connect() as db:
        db.execute("UPDATE jobs SET expires=? WHERE uid=?", (time.time() - 1, uid))
    app.state.store.expire()
    assert events(app)[-1]["event"] == "pair_expired"
    assert not (app.state.store.uploads_dir / uid).exists()


def test_rejected_pair_is_logged_and_partial_inputs_are_removed(app, client, files):
    files["course"] = ("course.gpx", b"")
    assert submit(client, files).status_code == 400
    journal = events(app)
    assert journal[0]["event"] == "upload_started"
    assert journal[-1]["event"] == "upload_failed"
    assert len({item["pair_id"] for item in journal}) == 1
    assert not list(app.state.store.uploads_dir.iterdir())


def test_legacy_queued_pair_migrates_and_still_processes(app, client, files):
    uid = submit(client, files).json()["uid"]
    pair = app.state.store.uploads_dir / uid
    for name in ("original.fit", "course.gpx"):
        (pair / name).replace(app.state.config.data_dir / uid / name)
    pair.rmdir()
    app.state.store.recover()
    assert (pair / "original.fit").read_bytes() == files["activity"][1]
    assert (pair / "course.gpx").read_bytes() == files["course"][1]
    assert len([event for event in events(app) if event["event"] == "upload_migrated"]) == 2
    finish(app, uid)


def test_event_log_rotation_and_concurrent_lines(tmp_path):
    config = WebConfig(data_dir=tmp_path, log_max_bytes=300, log_backups=2)
    journal = EventLog(config)
    for index in range(10):
        journal.write("example", f"pair-{index}", text="one\ntwo")
    paths = list(journal.directory.glob("events.jsonl*"))
    assert len(paths) == 3
    for path in paths:
        assert path.stat().st_mode & 0o777 == 0o600
        assert all(json.loads(line)["event"] == "example" for line in path.read_text().splitlines())
    assert json.loads(journal.path.read_text().splitlines()[-1])["pair_id"] == "pair-9"
    concurrent_dir = tmp_path / "concurrent"
    concurrent_dir.mkdir(mode=0o700)
    concurrent = EventLog(replace(config, data_dir=concurrent_dir, log_max_bytes=100_000))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: concurrent.write("example", f"pair-{i}"), range(40)))
    assert (
        len({json.loads(line)["pair_id"] for line in concurrent.path.read_text().splitlines()})
        == 40
    )
