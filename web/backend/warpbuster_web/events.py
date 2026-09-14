"""Local rotating JSON Lines journal; no cookies, filenames supplied by users or telemetry."""

import json
import logging
import os
import threading
from datetime import UTC, datetime


class EventLog:
    def __init__(self, config):
        self.directory = config.data_dir / "logs"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.directory.chmod(0o700)
        self.path = self.directory / "events.jsonl"
        self.max_bytes = config.log_max_bytes
        self.backups = config.log_backups
        self.lock = threading.Lock()

    def write(self, event: str, pair_id: str, **details):
        line = (
            json.dumps(
                {
                    "time": datetime.now(UTC).isoformat(),
                    "event": event,
                    "pair_id": pair_id,
                    **details,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        try:
            with self.lock:
                if (
                    self.path.exists()
                    and self.path.stat().st_size + len(line.encode()) > self.max_bytes
                ):
                    for number in range(self.backups, 0, -1):
                        source = (
                            self.path
                            if number == 1
                            else self.directory / f"events.jsonl.{number - 1}"
                        )
                        if source.exists():
                            source.replace(self.directory / f"events.jsonl.{number}")
                descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(line)
        except OSError:
            # A logging failure must not strand a processing job. Surface it locally.
            logging.getLogger(__name__).error(
                "Local event log unavailable: event=%s pair_id=%s", event, pair_id
            )
