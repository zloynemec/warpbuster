"""One persistent SQLite queue consumer with a bounded subprocess per activity."""

import fcntl
import json
import subprocess
import sys
import threading
import time

from .config import REPAIR_POLICY
from .store import Store

ERRORS = {
    "invalid_fit",
    "invalid_gpx",
    "empty_activity",
    "too_many_records",
    "repair_refused",
    "processing_failed",
    "timeout",
    "interrupted",
}


class Worker:
    def __init__(self, store: Store):
        self.store = store
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = None

    def start(self):
        self.lock = (self.store.config.data_dir / "worker.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Use one web server process per data directory") from None
        self.store.recover()
        self.store.expire()
        self.thread = threading.Thread(target=self.run, daemon=True, name="warpbuster-jobs")
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            self.store.expire()
            uid = self.store.claim()
            if uid:
                self.process(uid)
            else:
                self.stop_event.wait(self.store.config.worker_poll_seconds)

    def process(self, uid):
        config = self.store.config
        directory = config.data_dir.resolve() / uid
        inputs = self.store.uploads_dir.resolve() / uid
        command = [
            sys.executable,
            "-m",
            "warpbuster_web.processing",
            str(directory),
            str(config.record_limit),
            "--inputs",
            str(inputs),
        ]
        started = time.monotonic()
        return_code = None
        self.store.events.write(
            "processing_started",
            uid,
            command=command,
            timeout_seconds=config.process_timeout_seconds,
            **REPAIR_POLICY,
        )
        error = None
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=config.process_timeout_seconds,
                check=False,
            )
            return_code = completed.returncode
            if completed.returncode:
                error = "processing_failed"
                marker = directory / "failure.json"
                if marker.is_file():
                    candidate = json.loads(marker.read_text())["code"]
                    if candidate in ERRORS:
                        error = candidate
            elif not (directory / "result.json").is_file():
                error = "processing_failed"
        except subprocess.TimeoutExpired:
            error = "timeout"
        except OSError, ValueError, KeyError:
            error = "processing_failed"
        finally:
            # The private input pair is retained separately until the job expires.
            for filename in ("failure.json", "result.json.tmp"):
                (directory / filename).unlink(missing_ok=True)
        if error:
            (directory / "corrected.fit").unlink(missing_ok=True)
            (directory / "result.json").unlink(missing_ok=True)
            self.store.state(uid, "failed", error=error)
        else:
            self.store.state(uid, "ready", has_fit=(directory / "corrected.fit").is_file())
        self.store.events.write(
            "processing_failed" if error else "processing_completed",
            uid,
            duration_seconds=round(time.monotonic() - started, 3),
            return_code=return_code,
            error=error,
            has_fit=not error and (directory / "corrected.fit").is_file(),
        )

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=self.store.config.process_timeout_seconds + 5)
        if self.lock:
            self.lock.close()
