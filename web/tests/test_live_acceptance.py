"""023C opt-in HTTP acceptance against a running local Linux Web service.

WARPBUSTER_ACCEPTANCE_ORIGIN=http://127.0.0.1:8023 python -m pytest ...
Uses synthetic inputs and independent HTTP sessions, never browser credentials.
"""

import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
import pytest
from tests.fit_factory import write_trajectory_activity
from tests.test_repair_cli import _repairable_fixture
from warpbuster.fit.reader import read_fit

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def origin():
    value = os.environ.get("WARPBUSTER_ACCEPTANCE_ORIGIN", "").rstrip("/")
    if not value:
        pytest.skip("set WARPBUSTER_ACCEPTANCE_ORIGIN for local live acceptance")
    assert urlsplit(value).hostname in {"127.0.0.1", "localhost", "::1"}
    assert httpx.get(f"{value}/health", trust_env=False).status_code == 200
    return value


@pytest.fixture
def owner(origin):
    with httpx.Client(base_url=origin, timeout=15, trust_env=False) as client:
        response = client.get(
            "/api/session", headers={"Origin": origin, "X-WarpBuster-Request": "1"}
        )
        assert response.status_code == 200
        yield client


def submit(client, origin, files):
    response = client.post(
        "/api/jobs",
        headers={
            "Origin": origin,
            "X-WarpBuster-Request": "1",
            "X-WarpBuster-Upload-ID": str(uuid.uuid4()),
        },
        files=files,
    )
    assert response.status_code == 202, response.text
    endpoint = f"/api/results/{response.json()['uid']}"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = client.get(f"{endpoint}/status").json()
        if status["status"] not in {"queued", "processing"}:
            assert status["status"] == "ready", status
            return endpoint, status, client.get(endpoint).json()
        time.sleep(0.25)
    pytest.fail("Local worker did not finish within 120 seconds")


def test_live_gpx_repair_and_download_authorization(origin, owner, tmp_path):
    fit, gpx = _repairable_fixture(tmp_path)
    endpoint, status, report = submit(
        owner,
        origin,
        {"activity": ("run.fit", fit.read_bytes()), "course": ("route.gpx", gpx.read_bytes())},
    )
    assert report["mode"] == "fit_with_course"
    assert report["observed_gps_coverage"]["status"] == "not_applicable"
    assert report["outcome"] == "repaired"
    assert status["is_owner"] and status["can_download"]
    download = owner.get(f"{endpoint}/download")
    assert download.status_code == 200
    fixed = tmp_path / "downloaded.fit"
    fixed.write_bytes(download.content)
    assert [r.timestamp for r in read_fit(fixed).records] == [
        r.timestamp for r in read_fit(fit).records
    ]
    assert owner.head(f"{endpoint}/download").status_code == 200
    ranged = owner.get(f"{endpoint}/download", headers={"Range": "bytes=0-15"})
    assert ranged.status_code == 206 and ranged.content == download.content[:16]
    with httpx.Client(base_url=origin, timeout=15, trust_env=False) as guest:
        for create_session in (False, True):
            if create_session:
                assert (
                    guest.get(
                        "/api/session", headers={"Origin": origin, "X-WarpBuster-Request": "1"}
                    ).status_code
                    == 200
                )
            assert guest.get(endpoint).status_code == 200
            visible = guest.get(f"{endpoint}/status").json()
            assert not visible["is_owner"] and not visible["can_download"]
            assert guest.get(f"{endpoint}/download").status_code == 403
            assert guest.head(f"{endpoint}/download").status_code == 403
            assert (
                guest.get(f"{endpoint}/download", headers={"Range": "bytes=0-15"}).status_code
                == 403
            )


@pytest.mark.parametrize("expected", ["passed", "below_threshold", "unavailable"])
def test_live_fit_only_gate_and_no_false_download(origin, owner, tmp_path, expected):
    times = [0, 0, 1] if expected == "unavailable" else range(401)
    fit = tmp_path / "source.fit"
    write_trajectory_activity(
        fit,
        [
            (stamp, None, None)
            if expected == "below_threshold" and i < 250
            else (stamp, 44.0, 33.0 + i * 0.00001)
            for i, stamp in enumerate(times)
        ],
    )
    endpoint, status, report = submit(owner, origin, {"activity": ("run.fit", fit.read_bytes())})
    assert report["mode"] == "fit_only"
    assert report["observed_gps_coverage"]["status"] == expected
    assert report["tracks"]["course"] == []
    assert not status["can_download"]
    assert owner.get(f"{endpoint}/download").status_code == 409
    if expected == "unavailable":
        assert report["observed_gps_coverage"]["observed_gps_coverage_percent"] is None
    if expected != "passed":
        assert report["outcome"] == "unresolved"
        assert report["osm"]["routing_queries"] is None
