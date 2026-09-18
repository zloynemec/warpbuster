"""Durable job metadata and hashed anonymous sessions; no uploaded filenames."""

import hashlib
import re
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager

from .config import WebConfig
from .events import EventLog

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def valid_token(value: str) -> bool:
    return TOKEN_PATTERN.fullmatch(value) is not None


class CapacityError(Exception):
    """The bounded local service is full."""


class Store:
    def __init__(self, config: WebConfig):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        config.data_dir.chmod(0o700)
        self.uploads_dir = config.data_dir / "uploads"
        self.uploads_dir.mkdir(mode=0o700, exist_ok=True)
        self.uploads_dir.chmod(0o700)
        self.osm_dir = config.data_dir / "osm"
        self.events = EventLog(config)
        self.path = config.data_dir / "jobs.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    uid TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL,
                    expires REAL NOT NULL, status TEXT NOT NULL,
                    error TEXT, has_fit INTEGER NOT NULL DEFAULT 0,
                    has_course INTEGER NOT NULL DEFAULT 1 CHECK (has_course IN (0, 1)),
                    submission_key TEXT NOT NULL, UNIQUE(owner, submission_key)
                );
                CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, created);
                CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner);
            """)
            # Existing jobs were always submitted with a GPX. Never reinterpret a
            # missing legacy input as FIT-only; it must still fail as invalid_gpx.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "has_course" not in columns:
                db.execute(
                    "ALTER TABLE jobs ADD COLUMN has_course INTEGER NOT NULL DEFAULT 1 "
                    "CHECK (has_course IN (0, 1))"
                )
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def owner(self, token: str | None) -> str | None:
        if not token or not valid_token(token):
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute(
                "SELECT hash FROM sessions WHERE hash=? AND expires>?", (digest, time.time())
            ).fetchone()
        return row["hash"] if row else None

    def new_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            if (
                db.execute("SELECT count(*) FROM sessions").fetchone()[0]
                >= self.config.max_sessions
            ):
                raise CapacityError
            db.execute(
                "INSERT INTO sessions VALUES (?,?)",
                (
                    hashlib.sha256(token.encode()).hexdigest(),
                    time.time() + self.config.session_seconds,
                ),
            )
        return token

    def reserve(self, owner: str, submission_key: str) -> tuple[str, bool]:
        uid = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT uid FROM jobs WHERE owner=? AND submission_key=?", (owner, submission_key)
            ).fetchone()
            if previous:
                return previous["uid"], False
            total = db.execute("SELECT count(*) FROM jobs WHERE expires>?", (now,)).fetchone()[0]
            owned = db.execute(
                "SELECT count(*) FROM jobs WHERE owner=? AND expires>?", (owner, now)
            ).fetchone()[0]
            pending = db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('uploading','queued','processing') AND expires>?",
                (now,),
            ).fetchone()[0]
            if (
                total >= self.config.max_jobs
                or owned >= self.config.max_owner_jobs
                or pending >= self.config.max_pending_jobs
            ):
                raise CapacityError
            db.execute(
                "INSERT INTO jobs(uid,owner,created,expires,status,submission_key) VALUES (?,?,?,?,?,?)",
                (uid, owner, now, now + self.config.retention_seconds, "uploading", submission_key),
            )
        return uid, True

    def get(self, uid: str):
        if not valid_token(uid):
            return None
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None

    def state(
        self,
        uid: str,
        status: str,
        *,
        error: str | None = None,
        has_fit: bool = False,
        has_course: bool | None = None,
    ):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,error=?,has_fit=?,"
                "has_course=COALESCE(?,has_course) WHERE uid=?",
                (status, error, has_fit, has_course, uid),
            )

    def discard(self, uid: str):
        shutil.rmtree(self.config.data_dir / uid, ignore_errors=True)
        shutil.rmtree(self.uploads_dir / uid, ignore_errors=True)
        with self.connect() as db:
            db.execute("DELETE FROM jobs WHERE uid=?", (uid,))

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT uid FROM jobs WHERE status='queued' AND expires>? ORDER BY created LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row:
                db.execute("UPDATE jobs SET status='processing' WHERE uid=?", (row["uid"],))
                return row["uid"]
        return None

    def recover(self):
        # Move still-queued inputs from the previous on-disk layout without copying
        # or overwriting any pair already stored in the new upload directory.
        with self.connect() as db:
            legacy = db.execute(
                "SELECT uid FROM jobs WHERE status IN ('queued','processing')"
            ).fetchall()
        for row in legacy:
            directory = self.uploads_dir / row["uid"]
            for name in ("original.fit", "course.gpx"):
                source = self.config.data_dir / row["uid"] / name
                if source.is_file():
                    directory.mkdir(mode=0o700, exist_ok=True)
                    target = directory / name
                    if target.exists():
                        raise RuntimeError("Conflicting legacy upload pair; refusing to overwrite")
                    source.replace(target)
                    self.events.write("upload_migrated", row["uid"], file=name)
        with self.connect() as db:
            rows = db.execute(
                "SELECT uid,status FROM jobs WHERE status IN ('processing','uploading')"
            ).fetchall()
            db.execute(
                "UPDATE jobs SET status='failed',error='interrupted',has_fit=0 WHERE status IN ('processing','uploading')"
            )
        for row in rows:
            shutil.rmtree(self.config.data_dir / row["uid"], ignore_errors=True)
            if row["status"] == "uploading":
                shutil.rmtree(self.uploads_dir / row["uid"], ignore_errors=True)
            self.events.write(
                "processing_interrupted" if row["status"] == "processing" else "upload_failed",
                row["uid"],
                reason="service_restart",
            )

    def expire(self):
        now = time.time()
        with self.connect() as db:
            rows = db.execute(
                "SELECT uid FROM jobs WHERE expires<=? AND status!='expired'", (now,)
            ).fetchall()
            db.execute("UPDATE jobs SET status='expired',has_fit=0 WHERE expires<=?", (now,))
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            # Keep tombstones for one retention period, then forget the UID and owner.
            db.execute("DELETE FROM jobs WHERE expires<?", (now - self.config.retention_seconds,))
        for row in rows:
            shutil.rmtree(self.config.data_dir / row["uid"], ignore_errors=True)
            shutil.rmtree(self.uploads_dir / row["uid"], ignore_errors=True)
            self.events.write("pair_expired", row["uid"])
