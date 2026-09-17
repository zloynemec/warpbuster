"""Shared repair policy and execution limits, independent of any user interface."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields
from enum import StrEnum
from pathlib import Path

from warpbuster.models.integrity import IntegrityConfidence


@dataclass(frozen=True, slots=True)
class RepairPolicy:
    """The full two-file workflow's confidence and completion policy."""

    fill_missing_from_course: bool = True
    minimum_invalidation_confidence: IntegrityConfidence = IntegrityConfidence.MEDIUM
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.MEDIUM

    def __post_init__(self) -> None:
        if not isinstance(self.fill_missing_from_course, bool):
            raise ValueError("fill_missing_from_course must be boolean")
        if not isinstance(self.minimum_confidence, IntegrityConfidence):
            raise ValueError("minimum_confidence must be an IntegrityConfidence")
        if self.minimum_invalidation_confidence not in (
            IntegrityConfidence.HIGH,
            IntegrityConfidence.MEDIUM,
        ) or not isinstance(self.minimum_invalidation_confidence, IntegrityConfidence):
            raise ValueError("minimum_invalidation_confidence must be HIGH or MEDIUM")

    def as_dict(self) -> dict[str, bool | str]:
        """Expose exactly the policy used for execution to reports and logs."""
        return {
            "fill_missing_from_course": self.fill_missing_from_course,
            "minimum_invalidation_confidence": self.minimum_invalidation_confidence.value,
            "minimum_confidence": self.minimum_confidence.value,
        }


DEFAULT_REPAIR_POLICY = RepairPolicy()
LEGACY_REPAIR_POLICY = RepairPolicy(False, IntegrityConfidence.HIGH, IntegrityConfidence.HIGH)


class OSMMode(StrEnum):
    AUTO = "auto"
    OFFLINE = "offline"
    DISABLED = "disabled"


class DEMMode(StrEnum):
    AUTO = "auto"
    OFFLINE = "offline"
    DISABLED = "disabled"


EGM96_VERTICAL_DATUM = "WGS84/EGM96 geoid"


@dataclass(frozen=True)
class PipelineConfig:
    """Operational budgets; no detector thresholds or network activity on import.

    Library calls are offline by default. Full CLI/web entry points explicitly
    enable AUTO; OFFLINE can use a previously acquired Manager snapshot. An
    explicit osm_graph_id bypasses acquisition mode and uses that graph offline.
    """

    data_dir: Path = Path(".warpbuster")  # Shared service artifacts and OSM caches.
    process_timeout_seconds: int = 600  # Whole-job budget, seconds.
    base_plan_timeout_seconds: int = 180  # Maximum base-plan stage, seconds.
    osm_total_timeout_seconds: int = 360  # Maximum isolated OSM stage, seconds.
    osm_acquisition_timeout_seconds: int = 90  # Manager network budget, seconds.
    osm_prepare_timeout_seconds: int = 180  # Routing graph build budget, seconds.
    osm_routing_timeout_seconds: int = 90  # Route discovery budget, seconds.
    publish_reserve_seconds: int = 60  # Reserve for final FIT/report publication, seconds.
    osm_mode: OSMMode = OSMMode.DISABLED
    isolate_osm: bool = False  # Linux web deployments require process resource isolation.
    osm_coverage_buffer_m: float = 1_000.0  # Corridor around trusted gap anchors, metres.
    osm_maximum_area_km2: float = 1_000.0  # Maximum union coverage area, square kilometres.
    osm_maximum_cells: int = 64  # Maximum Manager coverage cells per job.
    osm_maximum_requests: int = 8  # Maximum Overpass requests per job.
    osm_maximum_download_bytes: int = 128 * 1024 * 1024  # Manager download byte budget.
    osm_cache_quota_bytes: int = 10 * 1024 * 1024 * 1024  # Combined cache byte budget.
    osm_job_temp_quota_bytes: int = 2 * 1024 * 1024 * 1024  # Temporary file byte budget.
    osm_minimum_free_bytes: int = 1 * 1024 * 1024 * 1024  # Required free disk bytes.
    osm_child_memory_limit_bytes: int = 2 * 1024 * 1024 * 1024  # Isolated RSS/AS bytes.
    osm_child_cpu_seconds: int = 300  # Isolated process-group CPU budget, seconds.
    osm_ipc_maximum_bytes: int = 64 * 1024 * 1024  # Cumulative isolated IPC byte budget.
    osm_overpass_url: str | None = None  # None delegates endpoint choice to Manager.
    record_limit: int = 100_000  # Maximum FIT records or GPX course points.
    minimum_observed_gps_coverage_percent: float = 51.0  # FIT-only, percent of active time.
    maximum_observed_gps_interval_seconds: float = 30.0  # FIT-only, active seconds per fix pair.
    osm_graph_id: str | None = None  # Optional exact prepared Routing graph identity.
    osm_routing_config: Path | None = None  # Prepared-graph Routing configuration.
    osm_cache_dir: Path | None = None  # Prepared-graph Routing cache override.
    approximate_osm: bool = False  # Explicit Task 021 selection opt-in.
    dem_mode: DEMMode = DEMMode.DISABLED  # Optional height evidence, disabled by default.
    dem_snapshot_id: str | None = None  # Exact verified DEM snapshot, if supplied.
    dem_cache_dir: Path | None = None  # Else data_dir/dem.
    dem_timeout_seconds: int = 60  # Separate bounded DEM stage, seconds.
    complete_missing_altitude: bool = False  # Separate Task 021E FIT-write opt-in.
    fit_altitude_datum: str | None = None  # Explicit per-file assertion, never inferred.

    def __post_init__(self) -> None:
        for item in fields(PipelineConfig):
            value = getattr(self, item.name)
            numeric = isinstance(item.default, int | float) and not isinstance(item.default, bool)
            if numeric and (
                not isinstance(value, int | float)
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{item.name} must be finite and positive")
        if self.minimum_observed_gps_coverage_percent > 100:
            raise ValueError("minimum_observed_gps_coverage_percent must not exceed 100")
        if not isinstance(self.osm_mode, OSMMode):
            raise ValueError("osm_mode must be auto, offline or disabled")
        if type(self.approximate_osm) is not bool:
            raise ValueError("approximate_osm must be boolean")
        if not isinstance(self.dem_mode, DEMMode):
            raise ValueError("dem_mode must be auto, offline or disabled")
        if self.approximate_osm and self.osm_mode is OSMMode.DISABLED and self.osm_graph_id is None:
            raise ValueError("approximate OSM requires enabled OSM or a prepared graph")
        if self.dem_mode is not DEMMode.DISABLED and not self.approximate_osm:
            raise ValueError("DEM evidence requires approximate OSM selection")
        if self.dem_snapshot_id is not None and (
            self.dem_mode is DEMMode.DISABLED
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.dem_snapshot_id) is None
        ):
            raise ValueError("DEM snapshot requires enabled DEM and a valid sha256 ID")
        if self.dem_cache_dir is not None and (
            self.dem_mode is DEMMode.DISABLED or not isinstance(self.dem_cache_dir, Path)
        ):
            raise ValueError("DEM cache directory requires enabled DEM and must be a path")
        if type(self.complete_missing_altitude) is not bool:
            raise ValueError("complete_missing_altitude must be boolean")
        if self.complete_missing_altitude and self.dem_mode is DEMMode.DISABLED:
            raise ValueError("altitude completion requires enabled DEM")
        if self.fit_altitude_datum is not None and (
            not self.complete_missing_altitude or self.fit_altitude_datum != EGM96_VERTICAL_DATUM
        ):
            raise ValueError("FIT altitude datum requires enabled completion and exact EGM96")
        if (
            self.osm_acquisition_timeout_seconds
            + self.osm_prepare_timeout_seconds
            + self.osm_routing_timeout_seconds
            < self.osm_total_timeout_seconds
        ):
            raise ValueError("OSM stage timeouts must cover osm_total_timeout_seconds")
        if self.osm_graph_id is None and (
            self.osm_routing_config is not None or self.osm_cache_dir is not None
        ):
            raise ValueError("prepared routing configuration requires osm_graph_id")
