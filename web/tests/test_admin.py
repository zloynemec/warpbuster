"""Administrative cleanup is explicit, scoped, and refuses a live worker."""

import fcntl
import json
from datetime import UTC, datetime

import pytest
from warpbuster_web.admin import AdminError, purge_osm_cache, purge_tracks
from warpbuster_web.admin.__main__ import main
from warpbuster_web.config import WebConfig
from warpbuster_web.store import Store


def _job(store, uid: str, created: datetime) -> None:
    with store.connect() as database:
        database.execute(
            "INSERT INTO jobs(uid,owner,created,expires,status,submission_key) VALUES(?,?,?,?,?,?)",
            (uid, uid[0] * 64, created.timestamp(), created.timestamp() + 604_800, "ready", uid),
        )
    for directory in (store.uploads_dir / uid, store.config.data_dir / uid):
        directory.mkdir(parents=True)
        (directory / "payload").write_bytes(b"private")


def test_purge_tracks_dry_run_then_removes_only_selected_utc_date(tmp_path):
    store = Store(WebConfig(data_dir=tmp_path))
    selected = "a" * 43
    retained = "b" * 43
    _job(store, selected, datetime(2026, 9, 15, 23, 59, tzinfo=UTC))
    _job(store, retained, datetime(2026, 9, 16, 0, 0, tzinfo=UTC))

    preview = purge_tracks(tmp_path, "2026-09-15")
    assert preview.status == "dry_run" and preview.pair_count == 1
    assert preview.size_bytes == 14
    assert (tmp_path / selected).is_dir()

    result = purge_tracks(tmp_path, "2026-09-15", confirm=True)
    assert result.status == "complete" and result.pair_count == 1
    assert result.size_bytes == 14
    assert not (tmp_path / selected).exists()
    assert not (store.uploads_dir / selected).exists()
    assert (tmp_path / retained).is_dir()
    with store.connect() as database:
        assert [row[0] for row in database.execute("SELECT uid FROM jobs")] == [retained]


def test_confirmed_track_cleanup_refuses_live_worker_lock(tmp_path):
    store = Store(WebConfig(data_dir=tmp_path))
    uid = "a" * 43
    _job(store, uid, datetime(2026, 9, 15, tzinfo=UTC))
    with (tmp_path / "worker.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AdminError, match="worker is running"):
            purge_tracks(tmp_path, "2026-09-15", confirm=True)
    assert (tmp_path / uid).is_dir()


def test_track_cleanup_rejects_invalid_date_and_uid_without_deleting(tmp_path):
    store = Store(WebConfig(data_dir=tmp_path))
    with pytest.raises(AdminError, match="YYYY-MM-DD"):
        purge_tracks(tmp_path, "15-09-2026", confirm=True)
    with store.connect() as database:
        database.execute(
            "INSERT INTO jobs(uid,owner,created,expires,status,submission_key,has_fit) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "../invalid",
                "owner",
                datetime(2026, 9, 15, tzinfo=UTC).timestamp(),
                datetime(2026, 9, 22, tzinfo=UTC).timestamp(),
                "ready",
                "invalid",
                0,
            ),
        )
    with pytest.raises(AdminError, match="invalid UID"):
        purge_tracks(tmp_path, "2026-09-15", confirm=True)


def test_purge_osm_cache_preserves_other_application_data(tmp_path):
    Store(WebConfig(data_dir=tmp_path))
    graph = tmp_path / "osm" / "routing" / "graphs" / ("a" * 64)
    graph.mkdir(parents=True)
    (graph / "tile").write_bytes(b"cache")
    preview = purge_osm_cache(tmp_path)
    assert preview.status == "dry_run" and preview.size_bytes == 5
    result = purge_osm_cache(tmp_path, confirm=True)
    assert result.status == "complete" and result.size_bytes == 5
    assert not (tmp_path / "osm").exists()
    assert (tmp_path / "jobs.sqlite3").is_file()
    assert (tmp_path / "uploads").is_dir()


def test_admin_cli_prints_bounded_json_summary(tmp_path, capsys):
    Store(WebConfig(data_dir=tmp_path))
    assert main(["--data-dir", str(tmp_path), "purge-osm-cache"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "action": "purge_osm_cache",
        "pair_count": 0,
        "size_bytes": 0,
        "status": "dry_run",
        "utc_date": None,
    }
