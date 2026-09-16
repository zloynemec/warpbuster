"""Optional, killable DEM stage shared by CLI and Web after safe 2D OSM selection."""

from __future__ import annotations

import copyreg
import os
import pickle
import sys
import time
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from multiprocessing import get_context
from types import MappingProxyType
from typing import Any

from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityReport
from warpbuster.models.reconstruction import GapOrigin, GapRepairPlan, OSMDryRunResult, RepairPlan
from warpbuster.reconstruction.altitude_completion import (
    DISABLED_ALTITUDE_COMPLETION,
    AltitudeCompletionPlan,
    plan_altitude_completion,
)
from warpbuster.reconstruction.approximate_contract import ApproximateSelectionPolicy
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes

from .config import DEMMode, PipelineConfig, RepairPolicy


def _reduce_mapping_proxy(value: MappingProxyType) -> tuple[type[dict], tuple[dict]]:
    """Transfer immutable FIT metadata as a detached child-only dictionary."""
    return dict, (dict(value),)


@dataclass(frozen=True, slots=True)
class DEMResult:
    """Public-safe stage summary; the plan is always a validated 2D fallback or better."""

    plan: RepairPlan
    status: str
    snapshot_id: str | None = None
    error_code: str | None = None
    duration_seconds: float = 0.0
    altitude: AltitudeCompletionPlan = DISABLED_ALTITUDE_COMPLETION

    def __post_init__(self) -> None:
        if self.status not in {"disabled", "not_needed", "complete", "unavailable"}:
            raise ValueError("invalid DEM stage status")
        if self.error_code not in {
            None,
            "timeout",
            "dependency_unavailable",
            "cache_unavailable",
            "resource_limit",
            "sampling_failed",
            "isolation_unavailable",
        }:
            raise ValueError("invalid DEM stage error code")


def run_dem_stage(
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    fallback_plan: RepairPlan,
    discovery: OSMDryRunResult | None,
    config: PipelineConfig,
    policy: RepairPolicy,
    *,
    budget_seconds: float,
) -> DEMResult:
    """Reapply the same Core selection with DEM; never lose the completed 2D plan."""
    if config.dem_mode is DEMMode.DISABLED:
        return DEMResult(fallback_plan, "disabled")
    if discovery is None or not any(
        isinstance(item, GapRepairPlan) and item.osm_provenance is not None
        for item in fallback_plan.interval_plans
    ):
        return DEMResult(fallback_plan, "not_needed")
    if budget_seconds <= 0:
        return DEMResult(fallback_plan, "unavailable", error_code="timeout")
    try:
        # macOS proxy lookup initializes Objective-C; resolve it before fork and
        # pass the exact mapping so the child never queries SystemConfiguration.
        proxy_mapping = urllib.request.getproxies() if sys.platform == "darwin" else None
        if sys.platform == "darwin":
            copyreg.pickle(MappingProxyType, _reduce_mapping_proxy)
        context = get_context("spawn" if sys.platform == "darwin" else "fork")
    except ValueError:
        return DEMResult(fallback_plan, "unavailable", error_code="isolation_unavailable")
    # Keep the child in the processor's process group: the Web worker's hard
    # deadline then terminates both processes, while this stage can kill its
    # own child earlier without disturbing the reserved FIT publication time.
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(
        target=_dem_child,
        args=(
            sending,
            activity,
            integrity,
            base_plan,
            fallback_plan,
            discovery,
            config,
            policy,
            proxy_mapping,
        ),
        name="warpbuster-dem",
    )
    started = time.monotonic()
    try:
        process.start()
    except OSError, RuntimeError:
        sending.close()
        receiving.close()
        return DEMResult(fallback_plan, "unavailable", error_code="isolation_unavailable")
    sending.close()
    try:
        if not receiving.poll(min(budget_seconds, config.dem_timeout_seconds)):
            return DEMResult(
                fallback_plan,
                "unavailable",
                error_code="timeout",
                duration_seconds=time.monotonic() - started,
            )
        try:
            payload = receiving.recv_bytes(config.osm_ipc_maximum_bytes)
            status, value = pickle.loads(payload)
        except EOFError, OSError, ValueError, TypeError, pickle.PickleError:
            return DEMResult(
                fallback_plan,
                "unavailable",
                error_code="sampling_failed",
                duration_seconds=time.monotonic() - started,
            )
        if status == "ok" and isinstance(value, tuple) and len(value) == 3:
            plan, snapshot_id, altitude = value
            if (
                isinstance(plan, RepairPlan)
                and isinstance(snapshot_id, str)
                and plan.activity_path == base_plan.activity_path
                and plan.coordinate_mask == base_plan.coordinate_mask
                and plan.gaps == base_plan.gaps
                and isinstance(altitude, AltitudeCompletionPlan)
            ):
                return DEMResult(
                    plan,
                    "complete",
                    snapshot_id=snapshot_id,
                    duration_seconds=time.monotonic() - started,
                    altitude=altitude,
                )
        if status == "error" and value in {
            "dependency_unavailable",
            "cache_unavailable",
            "resource_limit",
            "sampling_failed",
        }:
            code = value
        else:
            code = "sampling_failed"
        return DEMResult(
            fallback_plan,
            "unavailable",
            error_code=code,
            duration_seconds=time.monotonic() - started,
        )
    finally:
        receiving.close()
        if process.is_alive():
            process.kill()
        process.join(timeout=5)
        if process.exitcode is not None and process.pid is not None:
            _cleanup_dead_child_locks(config, process.pid)


def _cleanup_dead_child_locks(config: PipelineConfig, child_pid: int) -> None:
    """Remove only DEM cache locks owned by this already-terminated child."""
    lock_dir = (config.dem_cache_dir or config.data_dir / "dem") / "locks"
    try:
        for path in lock_dir.glob("*.lock"):
            if path.name != "maintenance.lock" and not path.name.startswith("tile-"):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            try:
                if path.read_text() == str(child_pid):
                    path.unlink()
            except OSError:
                continue
    except OSError:
        return


def _dem_child(
    connection: Any,
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    fallback_plan: RepairPlan,
    discovery: OSMDryRunResult,
    config: PipelineConfig,
    policy: RepairPolicy,
    proxy_mapping: dict[str, str] | None,
) -> None:
    try:
        if os.name == "posix" and hasattr(os, "uname") and os.uname().sysname == "Linux":
            from .osm_worker import _apply_resource_limits

            _apply_resource_limits(config)
        from warpbuster_osm_routing import (
            DemCache,
            DemCacheConfig,
            ElevationService,
            GeoPoint,
            plan_coverage,
        )
        from warpbuster_osm_routing.dem_coverage import DemCoveragePlan
        from warpbuster_osm_routing.errors import RoutingError

        cache_config = dataclass_replace(
            DemCacheConfig.defaults(),
            cache_directory=config.dem_cache_dir or config.data_dir / "dem",
            total_deadline_seconds=float(config.dem_timeout_seconds),
        )
        cache = DemCache(cache_config, proxy_mapping=proxy_mapping)
        if config.dem_snapshot_id is not None:
            snapshot = cache.inspect(config.dem_snapshot_id)
        else:
            selected_gaps = {
                item.interval.gap_id
                for item in fallback_plan.interval_plans
                if isinstance(item, GapRepairPlan)
                and item.osm_provenance is not None
                and item.interval.origin is GapOrigin.ORIGINAL_MISSING
            }
            tiles: set[str] = set()
            point_count = 0
            for evaluation in discovery.evaluations:
                if evaluation.interval.gap_id not in selected_gaps:
                    continue
                before, after = evaluation.anchor_before, evaluation.anchor_after
                if before is None or after is None:
                    continue
                for route in evaluation.candidates:
                    audit = route.as_dict().get("audit")
                    if not isinstance(audit, dict) or audit.get("status") not in {"PASS", "WARN"}:
                        continue
                    points = (
                        GeoPoint(before.latitude, before.longitude),
                        *(GeoPoint(item.latitude, item.longitude) for item in route.coordinates),
                        GeoPoint(after.latitude, after.longitude),
                    )
                    try:
                        coverage = plan_coverage(
                            points,
                            buffer_m=cache_config.buffer_m,
                            maximum_points=cache_config.maximum_points,
                            maximum_tiles=cache_config.maximum_tiles,
                        )
                    except RoutingError:
                        continue
                    tiles.update(coverage.tile_names)
                    point_count += coverage.point_count
                    if (
                        len(tiles) > cache_config.maximum_tiles
                        or point_count > cache_config.maximum_points
                    ):
                        raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM coverage exceeds budget")
            if not tiles:
                raise RoutingError("DEM_COVERAGE_UNAVAILABLE", "no eligible DEM coverage")
            coverage_plan = DemCoveragePlan(
                tuple(sorted(tiles)), point_count, cache_config.buffer_m
            )
            snapshot = cache.ensure(coverage_plan, config.dem_mode.value)
        if snapshot.snapshot_id is None:
            raise RoutingError("DEM_SNAPSHOT_NOT_FOUND", "DEM snapshot unavailable")
        sampler = ElevationService(cache)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base_plan,
            discovery,
            minimum_confidence=policy.minimum_confidence,
            approximate_policy=ApproximateSelectionPolicy(),
            dem_sampler=sampler,
            dem_snapshot_id=snapshot.snapshot_id,
        )
        altitude = (
            plan_altitude_completion(
                activity,
                plan,
                sampler,
                snapshot.snapshot_id,
                minimum_confidence=policy.minimum_confidence,
                fit_altitude_datum=config.fit_altitude_datum,
            )
            if config.complete_missing_altitude
            else DISABLED_ALTITUDE_COMPLETION
        )
        payload = pickle.dumps(
            ("ok", (plan, snapshot.snapshot_id, altitude)), protocol=pickle.HIGHEST_PROTOCOL
        )
        if len(payload) > config.osm_ipc_maximum_bytes:
            connection.send_bytes(pickle.dumps(("error", "resource_limit")))
        else:
            connection.send_bytes(payload)
    except ImportError:
        connection.send_bytes(pickle.dumps(("error", "dependency_unavailable")))
    except Exception as error:
        code = getattr(error, "code", "")
        safe = (
            "resource_limit"
            if code == "RESOURCE_LIMIT_EXCEEDED"
            else "cache_unavailable"
            if code
            in {
                "DEM_SNAPSHOT_NOT_FOUND",
                "DEM_TILE_MISSING",
                "DEM_TILE_CORRUPT",
                "DEM_CACHE_CORRUPT",
                "DEM_COVERAGE_UNAVAILABLE",
                "DEM_DOWNLOAD_FAILED",
                "DEM_NETWORK_TIMEOUT",
                "DEM_LOCK_TIMEOUT",
            }
            else "sampling_failed"
        )
        with suppress(OSError):
            connection.send_bytes(pickle.dumps(("error", safe)))
    finally:
        connection.close()
