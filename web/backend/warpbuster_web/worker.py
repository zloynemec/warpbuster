"""One persistent SQLite queue consumer with a bounded subprocess per activity."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress

from warpbuster.pipeline import DEFAULT_REPAIR_POLICY

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
        self.active_process = None
        self.process_lock = threading.Lock()

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
            osm_mode=config.osm_mode.value,
            osm_timeout_seconds=config.osm_total_timeout_seconds,
            publish_reserve_seconds=config.publish_reserve_seconds,
            **DEFAULT_REPAIR_POLICY.as_dict(),
        )
        error = None
        try:
            return_code, execution_error = self._run_processor(
                command,
                config.process_timeout_seconds,
                config.processor_environment(),
            )
            if execution_error:
                error = execution_error
            elif return_code:
                error = "processing_failed"
                marker = directory / "failure.json"
                if marker.is_file():
                    candidate = json.loads(marker.read_text())["code"]
                    if candidate in ERRORS:
                        error = candidate
            elif not (directory / "result.json").is_file():
                error = "processing_failed"
        except OSError, ValueError, KeyError:
            error = "processing_failed"
        finally:
            # The private input pair is retained separately until the job expires.
            for filename in ("failure.json", "result.json.tmp", "private-osm-audit.json.tmp"):
                (directory / filename).unlink(missing_ok=True)
        if error:
            (directory / "corrected.fit").unlink(missing_ok=True)
            (directory / "result.json").unlink(missing_ok=True)
            self.store.state(uid, "failed", error=error)
        else:
            self.store.state(uid, "ready", has_fit=(directory / "corrected.fit").is_file())
            try:
                public = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                osm = public.get("osm", {})
                if isinstance(osm, dict):
                    summary = public.get("summary", {})
                    summary = summary if isinstance(summary, dict) else {}
                    self.store.events.write(
                        "osm_pipeline_completed",
                        uid,
                        status=osm.get("status"),
                        stage=osm.get("stage"),
                        error=osm.get("error_code"),
                        duration_seconds=osm.get("duration_seconds"),
                        eligible_gaps=osm.get("eligible_gaps"),
                        coverage_cells=osm.get("coverage_cells"),
                        coverage_area_km2=osm.get("coverage_area_km2"),
                        coverage_seconds=osm.get("coverage_seconds"),
                        acquisition_seconds=osm.get("acquisition_seconds"),
                        prepare_seconds=osm.get("prepare_seconds"),
                        routing_seconds=osm.get("routing_seconds"),
                        snapshot_cache_hit=osm.get("snapshot_cache_hit"),
                        snapshot_stale=osm.get("snapshot_stale"),
                        graph_cache_hit=osm.get("graph_cache_hit"),
                        routing_queries=osm.get("routing_queries"),
                        candidate_gaps=osm.get("candidate_gaps"),
                        candidates=osm.get("candidates"),
                        applied_gpx_gaps=summary.get("applied_gpx_gaps"),
                        applied_osm_gaps=summary.get("applied_osm_gaps"),
                        unresolved_gaps=summary.get("unresolved_gaps"),
                    )
            except OSError, ValueError:
                pass
        self.store.events.write(
            "processing_failed" if error else "processing_completed",
            uid,
            duration_seconds=round(time.monotonic() - started, 3),
            return_code=return_code,
            error=error,
            has_fit=not error and (directory / "corrected.fit").is_file(),
        )

    def _run_processor(self, command, timeout_seconds, environment):
        child_environment = os.environ.copy()
        child_environment.update(environment)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=child_environment,
        )
        with self.process_lock:
            self.active_process = process
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if self.stop_event.is_set():
                    self._terminate(process)
                    return process.returncode, "interrupted"
                if remaining <= 0:
                    self._terminate(process)
                    return process.returncode, "timeout"
                try:
                    return_code = process.wait(timeout=min(0.25, remaining))
                    return return_code, "interrupted" if self.stop_event.is_set() else None
                except subprocess.TimeoutExpired:
                    continue
        finally:
            with self.process_lock:
                if self.active_process is process:
                    self.active_process = None

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)

    def stop(self):
        self.stop_event.set()
        with self.process_lock:
            process = self.active_process
        if process is not None:
            self._terminate(process)
        if self.thread:
            self.thread.join(timeout=5)
        if self.lock:
            self.lock.close()
