"""Explicit deployment and resource limits, separate from detector thresholds."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# Shared by the processor and its invocation log so reported policy matches execution.
REPAIR_POLICY = {
    "fill_missing_from_course": True,
    "minimum_invalidation_confidence": "medium",
    "minimum_confidence": "medium",
}


@dataclass(frozen=True)
class WebConfig:
    data_dir: Path = Path(".warpbuster-web")
    static_dir: Path = Path(__file__).resolve().parents[2] / "dist"
    public_origin: str = "http://127.0.0.1:8000"
    file_limit_bytes: int = 20 * 1024 * 1024  # Maximum bytes per FIT/GPX file.
    body_limit_bytes: int = 41 * 1024 * 1024  # Both files plus multipart framing.
    upload_timeout_seconds: int = 60  # Total time to receive and persist one upload.
    process_timeout_seconds: int = 180  # Hard subprocess deadline for one job.
    record_limit: int = 100_000  # Maximum normalized FIT records / course points.
    retention_seconds: int = 7 * 24 * 3600  # Public report and corrected FIT lifetime.
    session_seconds: int = 30 * 24 * 3600  # Original browser's ownership lifetime.
    max_jobs: int = 100  # Stored, unexpired jobs across all owners.
    max_owner_jobs: int = 20  # Unexpired jobs owned by one anonymous session.
    max_pending_jobs: int = 16  # Queued + processing jobs across all owners.
    max_sessions: int = 10_000  # Bound anonymous-session storage.
    log_max_bytes: int = 5 * 1024 * 1024  # Rotate each local event journal at 5 MiB.
    log_backups: int = 3  # Retain this many previous journal files.
    worker_poll_seconds: float = 1.0  # Idle queue scan interval.

    def __post_init__(self) -> None:
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in {"http", "https"}
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path
            or origin.query
            or origin.fragment
        ):
            raise ValueError("public_origin must be a bare http(s) origin without a trailing slash")
        if origin.scheme != "https" and origin.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Non-loopback deployments require HTTPS")
        if self.data_dir.resolve().is_relative_to(self.static_dir.resolve()):
            raise ValueError("Private data must be outside the static directory")
        for name in (
            "file_limit_bytes",
            "body_limit_bytes",
            "upload_timeout_seconds",
            "process_timeout_seconds",
            "record_limit",
            "retention_seconds",
            "session_seconds",
            "max_jobs",
            "max_owner_jobs",
            "max_pending_jobs",
            "max_sessions",
            "log_max_bytes",
            "log_backups",
            "worker_poll_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def secure_cookie(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-warpbuster-owner" if self.secure_cookie else "warpbuster-owner-local"

    @classmethod
    def from_environment(cls) -> WebConfig:
        defaults = cls()
        return cls(
            data_dir=Path(os.environ.get("WARPBUSTER_WEB_DATA", str(defaults.data_dir))),
            static_dir=Path(os.environ.get("WARPBUSTER_WEB_STATIC", str(defaults.static_dir))),
            public_origin=os.environ.get("WARPBUSTER_WEB_ORIGIN", defaults.public_origin),
        )
