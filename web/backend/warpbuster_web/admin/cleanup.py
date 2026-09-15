"""Conservative destructive maintenance guarded by the web worker lock."""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

UID_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


class AdminError(Exception):
    """Safe operational error from an administrative command."""


@dataclass(frozen=True, slots=True)
class PurgeResult:
    action: str
    status: str
    pair_count: int = 0
    size_bytes: int = 0
    utc_date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def purge_tracks(data_dir: Path, utc_date: str, *, confirm: bool = False) -> PurgeResult:
    """Remove database-indexed input/output pairs created on one UTC date."""
    day = _utc_date(utc_date)
    root = _data_root(data_dir)
    if not confirm:
        uids = _read_uids(root, day)
        _validate_uids(uids)
        targets = _track_targets(root, uids)
        for path in targets:
            _validate_directory_target(path)
        return PurgeResult(
            "purge_tracks",
            "dry_run",
            len(uids),
            sum(_tree_size(path) for path in targets),
            day.isoformat(),
        )
    with _exclusive_worker_lock(root):
        return _purge_tracks(root, day)


def purge_osm_cache(data_dir: Path, *, confirm: bool = False) -> PurgeResult:
    """Remove the complete OSM dataset/graph cache while the worker is offline."""
    root = _data_root(data_dir)
    target = root / "osm"
    _validate_directory_target(target)
    size = _tree_size(target)
    if not confirm:
        return PurgeResult("purge_osm_cache", "dry_run", size_bytes=size)
    with _exclusive_worker_lock(root):
        _validate_directory_target(target)
        size = _tree_size(target)
        if not target.exists():
            return PurgeResult("purge_osm_cache", "complete")
        trash = root / ".admin-osm-trash"
        _require_unused_trash(trash)
        target.replace(trash)
        try:
            shutil.rmtree(trash)
        except OSError as error:
            raise AdminError("OSM cache moved to /data/.admin-osm-trash but not deleted") from error
        return PurgeResult("purge_osm_cache", "complete", size_bytes=size)


def _purge_tracks(root: Path, day: date) -> PurgeResult:
    database = root / "jobs.sqlite3"
    if not database.exists():
        return PurgeResult("purge_tracks", "complete", utc_date=day.isoformat())
    if database.is_symlink() or not database.is_file():
        raise AdminError("refusing unexpected jobs.sqlite3")
    connection = sqlite3.connect(f"file:{database}?mode=rw", uri=True, timeout=30)
    trash = root / ".admin-track-trash"
    moved: list[tuple[Path, Path]] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        uids = _select_uids(connection, day)
        _validate_uids(uids)
        targets = _track_targets(root, uids)
        for path in targets:
            _validate_directory_target(path)
        size = sum(_tree_size(path) for path in targets)
        if targets:
            _require_unused_trash(trash)
            trash.mkdir(mode=0o700)
        for index, source in enumerate(targets):
            if source.is_dir():
                destination = trash / f"{index}-{source.name}"
                source.replace(destination)
                moved.append((destination, source))
        connection.executemany("DELETE FROM jobs WHERE uid=?", ((uid,) for uid in uids))
        connection.execute("DELETE FROM sessions WHERE hash NOT IN (SELECT owner FROM jobs)")
        connection.commit()
    except (OSError, sqlite3.Error, AdminError) as error:
        connection.rollback()
        for source, destination in reversed(moved):
            if source.exists() and not destination.exists():
                source.replace(destination)
        if trash.is_dir() and not any(trash.iterdir()):
            trash.rmdir()
        if isinstance(error, AdminError):
            raise
        raise AdminError("track cleanup failed; moved directories were restored") from error
    finally:
        connection.close()
    try:
        if trash.is_dir():
            shutil.rmtree(trash)
    except OSError as error:
        raise AdminError("tracks were detached but /data/.admin-track-trash remains") from error
    return PurgeResult("purge_tracks", "complete", len(uids), size, day.isoformat())


def _read_uids(root: Path, day: date) -> list[str]:
    database = root / "jobs.sqlite3"
    if not database.exists():
        return []
    if database.is_symlink() or not database.is_file():
        raise AdminError("refusing unexpected jobs.sqlite3")
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=10) as connection:
            return _select_uids(connection, day)
    except sqlite3.Error as error:
        raise AdminError("cannot read jobs database") from error


def _select_uids(connection: sqlite3.Connection, day: date) -> list[str]:
    start = datetime.combine(day, time.min, tzinfo=UTC).timestamp()
    finish = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC).timestamp()
    rows = connection.execute(
        "SELECT uid FROM jobs WHERE created>=? AND created<? ORDER BY created", (start, finish)
    )
    return [str(row[0]) for row in rows]


def _track_targets(root: Path, uids: list[str]) -> list[Path]:
    uploads = root / "uploads"
    return [path for uid in uids for path in (uploads / uid, root / uid)]


def _validate_uids(uids: list[str]) -> None:
    if any(UID_PATTERN.fullmatch(uid) is None for uid in uids):
        raise AdminError("refusing cleanup because the database contains an invalid UID")


def _utc_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise AdminError("date must have YYYY-MM-DD format") from error
    if parsed.isoformat() != value:
        raise AdminError("date must have YYYY-MM-DD format")
    return parsed


def _data_root(data_dir: Path) -> Path:
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise AdminError("data directory must be an existing real directory")
    root = data_dir.resolve()
    if root == Path(root.anchor):
        raise AdminError("refusing filesystem root as data directory")
    return root


def _validate_directory_target(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise AdminError(f"refusing unexpected cleanup target: {path.name}")


def _require_unused_trash(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise AdminError(f"refusing cleanup while recovery target exists: {path.name}")


def _tree_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for directory, _, filenames in os.walk(path, followlinks=False):
        for filename in filenames:
            try:
                total += (Path(directory) / filename).stat(follow_symlinks=False).st_size
            except OSError as error:
                raise AdminError("cannot inspect cleanup target") from error
    return total


@contextmanager
def _exclusive_worker_lock(root: Path) -> Iterator[None]:
    lock_path = root / "worker.lock"
    if lock_path.is_symlink() or (lock_path.exists() and not lock_path.is_file()):
        raise AdminError("refusing unexpected worker.lock")
    try:
        with lock_path.open("a+") as stream:
            os.fchmod(stream.fileno(), 0o600)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise AdminError(
                    "web worker is running; stop it before confirmed cleanup"
                ) from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    except OSError as error:
        raise AdminError("cannot acquire worker.lock") from error
