"""Explicit deployment and resource limits, separate from detector thresholds."""

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

# Shared by the processor and its invocation log so reported policy matches execution.
REPAIR_POLICY = {
    "fill_missing_from_course": True,
    "minimum_invalidation_confidence": "medium",
    "minimum_confidence": "medium",
}


def positive_integer_environment(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class OSMMode(StrEnum):
    AUTO = "auto"
    OFFLINE = "offline"
    DISABLED = "disabled"


def _environment(name: str, default: int | float) -> int | float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw) if isinstance(default, float) else int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a positive number") from None
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


@dataclass(frozen=True)
class WebConfig:
    data_dir: Path = Path(".warpbuster-web")
    static_dir: Path = Path(__file__).resolve().parents[2] / "dist"
    public_origin: str = "http://127.0.0.1:8000"
    file_limit_bytes: int = 20 * 1024 * 1024  # Maximum bytes per FIT/GPX file.
    body_limit_bytes: int = 41 * 1024 * 1024  # Both files plus multipart framing.
    upload_timeout_seconds: int = 60  # Total time to receive and persist one upload.
    process_timeout_seconds: int = 600  # Hard deadline for the complete job.
    base_plan_timeout_seconds: int = 180
    osm_total_timeout_seconds: int = 360
    osm_acquisition_timeout_seconds: int = 90
    osm_prepare_timeout_seconds: int = 180
    osm_routing_timeout_seconds: int = 90
    publish_reserve_seconds: int = 60
    osm_mode: OSMMode = OSMMode.AUTO
    osm_coverage_buffer_m: float = 1_000.0
    osm_maximum_area_km2: float = 250.0
    osm_maximum_cells: int = 64
    osm_maximum_requests: int = 8
    osm_maximum_download_bytes: int = 128 * 1024 * 1024
    osm_cache_quota_bytes: int = 10 * 1024 * 1024 * 1024
    osm_job_temp_quota_bytes: int = 2 * 1024 * 1024 * 1024
    osm_minimum_free_bytes: int = 1 * 1024 * 1024 * 1024
    osm_child_memory_limit_bytes: int = 2 * 1024 * 1024 * 1024
    osm_child_cpu_seconds: int = 300
    maximum_parallel_osm_jobs: int = 1
    osm_ipc_maximum_bytes: int = 64 * 1024 * 1024
    osm_overpass_url: str | None = None
    record_limit: int = 100_000  # Maximum normalized FIT records / course points.
    retention_seconds: int = 7 * 24 * 3600  # Public report and corrected FIT lifetime.
    session_seconds: int = 30 * 24 * 3600  # Original browser's ownership lifetime.
    max_jobs: int = 1_000  # Stored, unexpired jobs across all owners.
    max_owner_jobs: int = 100  # Unexpired jobs owned by one anonymous session.
    max_pending_jobs: int = 50  # Queued + processing jobs across all owners.
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
            "base_plan_timeout_seconds",
            "osm_total_timeout_seconds",
            "osm_acquisition_timeout_seconds",
            "osm_prepare_timeout_seconds",
            "osm_routing_timeout_seconds",
            "publish_reserve_seconds",
            "osm_coverage_buffer_m",
            "osm_maximum_area_km2",
            "osm_maximum_cells",
            "osm_maximum_requests",
            "osm_maximum_download_bytes",
            "osm_cache_quota_bytes",
            "osm_job_temp_quota_bytes",
            "osm_minimum_free_bytes",
            "osm_child_memory_limit_bytes",
            "osm_child_cpu_seconds",
            "maximum_parallel_osm_jobs",
            "osm_ipc_maximum_bytes",
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
        if not isinstance(self.osm_mode, OSMMode):
            raise ValueError("osm_mode must be auto, offline or disabled")
        if self.maximum_parallel_osm_jobs != 1:
            raise ValueError("maximum_parallel_osm_jobs must remain 1 for the single worker")
        if (
            self.osm_acquisition_timeout_seconds
            + self.osm_prepare_timeout_seconds
            + self.osm_routing_timeout_seconds
            < self.osm_total_timeout_seconds
        ):
            raise ValueError("OSM stage timeouts must cover osm_total_timeout_seconds")

    @property
    def secure_cookie(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def cookie_name(self) -> str:
        return "__Host-warpbuster-owner" if self.secure_cookie else "warpbuster-owner-local"

    def processor_environment(self) -> dict[str, str]:
        """Serialize only the settings consumed by the isolated processor."""
        values: dict[str, object] = {
            "WARPBUSTER_WEB_DATA": self.data_dir,
            "WARPBUSTER_WEB_PROCESS_TIMEOUT_SECONDS": self.process_timeout_seconds,
            "WARPBUSTER_WEB_BASE_PLAN_TIMEOUT_SECONDS": self.base_plan_timeout_seconds,
            "WARPBUSTER_WEB_OSM_TOTAL_TIMEOUT_SECONDS": self.osm_total_timeout_seconds,
            "WARPBUSTER_WEB_OSM_ACQUISITION_TIMEOUT_SECONDS": self.osm_acquisition_timeout_seconds,
            "WARPBUSTER_WEB_OSM_PREPARE_TIMEOUT_SECONDS": self.osm_prepare_timeout_seconds,
            "WARPBUSTER_WEB_OSM_ROUTING_TIMEOUT_SECONDS": self.osm_routing_timeout_seconds,
            "WARPBUSTER_WEB_PUBLISH_RESERVE_SECONDS": self.publish_reserve_seconds,
            "WARPBUSTER_WEB_OSM_MODE": self.osm_mode.value,
            "WARPBUSTER_WEB_OSM_COVERAGE_BUFFER_M": self.osm_coverage_buffer_m,
            "WARPBUSTER_WEB_OSM_MAXIMUM_AREA_KM2": self.osm_maximum_area_km2,
            "WARPBUSTER_WEB_OSM_MAXIMUM_CELLS": self.osm_maximum_cells,
            "WARPBUSTER_WEB_OSM_MAXIMUM_REQUESTS": self.osm_maximum_requests,
            "WARPBUSTER_WEB_OSM_MAXIMUM_DOWNLOAD_BYTES": self.osm_maximum_download_bytes,
            "WARPBUSTER_WEB_OSM_CACHE_QUOTA_BYTES": self.osm_cache_quota_bytes,
            "WARPBUSTER_WEB_OSM_JOB_TEMP_QUOTA_BYTES": self.osm_job_temp_quota_bytes,
            "WARPBUSTER_WEB_OSM_MINIMUM_FREE_BYTES": self.osm_minimum_free_bytes,
            "WARPBUSTER_WEB_OSM_CHILD_MEMORY_LIMIT_BYTES": self.osm_child_memory_limit_bytes,
            "WARPBUSTER_WEB_OSM_CHILD_CPU_SECONDS": self.osm_child_cpu_seconds,
            "WARPBUSTER_WEB_OSM_IPC_MAXIMUM_BYTES": self.osm_ipc_maximum_bytes,
        }
        if self.osm_overpass_url is not None:
            values["WARPBUSTER_WEB_OSM_OVERPASS_URL"] = self.osm_overpass_url
        return {name: str(value) for name, value in values.items()}

    @classmethod
    def from_environment(cls) -> WebConfig:
        defaults = cls()
        return cls(
            data_dir=Path(os.environ.get("WARPBUSTER_WEB_DATA", str(defaults.data_dir))),
            static_dir=Path(os.environ.get("WARPBUSTER_WEB_STATIC", str(defaults.static_dir))),
            public_origin=os.environ.get("WARPBUSTER_WEB_ORIGIN", defaults.public_origin),
            process_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_PROCESS_TIMEOUT_SECONDS", defaults.process_timeout_seconds
            ),
            base_plan_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_BASE_PLAN_TIMEOUT_SECONDS", defaults.base_plan_timeout_seconds
            ),
            osm_total_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_TOTAL_TIMEOUT_SECONDS", defaults.osm_total_timeout_seconds
            ),
            osm_acquisition_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_ACQUISITION_TIMEOUT_SECONDS",
                defaults.osm_acquisition_timeout_seconds,
            ),
            osm_prepare_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_PREPARE_TIMEOUT_SECONDS", defaults.osm_prepare_timeout_seconds
            ),
            osm_routing_timeout_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_ROUTING_TIMEOUT_SECONDS", defaults.osm_routing_timeout_seconds
            ),
            publish_reserve_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_PUBLISH_RESERVE_SECONDS", defaults.publish_reserve_seconds
            ),
            osm_mode=OSMMode(os.environ.get("WARPBUSTER_WEB_OSM_MODE", defaults.osm_mode.value)),
            osm_coverage_buffer_m=_environment(
                "WARPBUSTER_WEB_OSM_COVERAGE_BUFFER_M", defaults.osm_coverage_buffer_m
            ),
            osm_maximum_area_km2=_environment(
                "WARPBUSTER_WEB_OSM_MAXIMUM_AREA_KM2", defaults.osm_maximum_area_km2
            ),
            osm_maximum_cells=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_MAXIMUM_CELLS", defaults.osm_maximum_cells
            ),
            osm_maximum_requests=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_MAXIMUM_REQUESTS", defaults.osm_maximum_requests
            ),
            osm_maximum_download_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_MAXIMUM_DOWNLOAD_BYTES", defaults.osm_maximum_download_bytes
            ),
            osm_cache_quota_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_CACHE_QUOTA_BYTES", defaults.osm_cache_quota_bytes
            ),
            osm_job_temp_quota_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_JOB_TEMP_QUOTA_BYTES", defaults.osm_job_temp_quota_bytes
            ),
            osm_minimum_free_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_MINIMUM_FREE_BYTES", defaults.osm_minimum_free_bytes
            ),
            osm_child_memory_limit_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_CHILD_MEMORY_LIMIT_BYTES",
                defaults.osm_child_memory_limit_bytes,
            ),
            osm_child_cpu_seconds=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_CHILD_CPU_SECONDS", defaults.osm_child_cpu_seconds
            ),
            maximum_parallel_osm_jobs=positive_integer_environment(
                "WARPBUSTER_WEB_MAXIMUM_PARALLEL_OSM_JOBS",
                defaults.maximum_parallel_osm_jobs,
            ),
            osm_ipc_maximum_bytes=positive_integer_environment(
                "WARPBUSTER_WEB_OSM_IPC_MAXIMUM_BYTES", defaults.osm_ipc_maximum_bytes
            ),
            osm_overpass_url=os.environ.get("WARPBUSTER_WEB_OSM_OVERPASS_URL"),
            max_jobs=positive_integer_environment("WARPBUSTER_WEB_MAX_JOBS", defaults.max_jobs),
            max_owner_jobs=positive_integer_environment(
                "WARPBUSTER_WEB_MAX_OWNER_JOBS", defaults.max_owner_jobs
            ),
            max_pending_jobs=positive_integer_environment(
                "WARPBUSTER_WEB_MAX_PENDING_JOBS", defaults.max_pending_jobs
            ),
        )
