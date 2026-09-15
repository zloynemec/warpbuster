"""Shared OSM acquisition and application through the companion package APIs."""

from __future__ import annotations

import json
import os
import pickle
import re
import shutil
import signal
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityReport
from warpbuster.models.reconstruction import OSMDryRunResult, RepairPlan
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes
from warpbuster.reconstruction.osm import OSMReconstructionProvider, osm_gap_anchors
from warpbuster.report.osm import osm_reconstruction_report

from .config import DEFAULT_REPAIR_POLICY, OSMMode, PipelineConfig, RepairPolicy
from .osm_worker import child_main

SAFE_ERROR_CODES = frozenset(
    {
        "coverage_limit",
        "offline_cache_miss",
        "acquisition_failed",
        "cache_corrupt",
        "cache_quota",
        "engine_mismatch",
        "prepare_failed",
        "routing_failed",
        "osm_timeout",
        "resource_limit",
        "isolation_unavailable",
        "dependency_unavailable",
        "ipc_invalid",
    }
)


@dataclass(frozen=True, slots=True)
class OSMMetrics:
    """Coordinate-free operational counters safe to copy into the event journal."""

    coverage_cells: int
    coverage_area_km2: float
    coverage_seconds: float
    acquisition_seconds: float
    prepare_seconds: float
    routing_seconds: float
    snapshot_cache_hit: bool
    snapshot_stale: bool
    graph_cache_hit: bool
    routing_queries: int
    candidate_gaps: int
    candidates: int


@dataclass(frozen=True, slots=True)
class OSMResult:
    plan: RepairPlan
    status: str
    stage: str | None = None
    error_code: str | None = None
    private_audit: dict[str, Any] | None = None
    metrics: OSMMetrics | None = None
    discovery: OSMDryRunResult | None = None
    warning: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"not_needed", "disabled", "complete", "partial", "unavailable"}:
            raise ValueError("invalid OSM pipeline status")
        if self.stage not in {None, "setup", "coverage", "acquisition", "prepare", "routing"}:
            raise ValueError("invalid OSM pipeline stage")
        if self.error_code is not None and self.error_code not in SAFE_ERROR_CODES:
            raise ValueError("invalid public OSM error code")


def eligible_gap_count(activity: ActivityData, plan: RepairPlan) -> int:
    """Count gaps using the Core provider's exact anchor contract."""
    return sum(osm_gap_anchors(activity, plan, gap)[0] is not None for gap in plan.gaps)


def execute_osm_pipeline(
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    config: PipelineConfig,
    *,
    policy: RepairPolicy = DEFAULT_REPAIR_POLICY,
    stage_callback: Callable[[str], None] | None = None,
) -> OSMResult:
    """Acquire one bounded snapshot, prepare an exact graph and apply Core 012E."""
    if config.osm_mode is OSMMode.DISABLED:
        return OSMResult(base_plan, "disabled")
    lines = []
    for gap in base_plan.gaps:
        anchors, _ = osm_gap_anchors(activity, base_plan, gap)
        if anchors is not None:
            before, after = anchors
            lines.append(((before.longitude, before.latitude), (after.longitude, after.latitude)))
    if not lines:
        return OSMResult(base_plan, "not_needed")
    coverage_started = time.monotonic()
    try:
        from warpbuster_osm_manager import OsmManager, OsmManagerConfig
        from warpbuster_osm_manager.coverage import ParsedGeometry, plan_from_geometry
        from warpbuster_osm_manager.errors import OsmManagerError
        from warpbuster_osm_manager.models import GeoPoint
        from warpbuster_osm_routing import GraphCache, RoutingCacheConfig
        from warpbuster_osm_routing.errors import RoutingError
    except ImportError:
        return OSMResult(base_plan, "unavailable", "setup", "dependency_unavailable")

    cache_root = config.data_dir / "osm"
    manager_cache = cache_root / "datasets"
    routing_cache = cache_root / "routing"
    temporary = cache_root / "tmp"
    for directory in (manager_cache, routing_cache, temporary):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    if shutil.disk_usage(config.data_dir).free < config.osm_minimum_free_bytes:
        return OSMResult(base_plan, "unavailable", "coverage", "cache_quota")
    if not _ensure_cache_quota(cache_root, config.osm_cache_quota_bytes):
        return OSMResult(base_plan, "unavailable", "coverage", "cache_quota")

    try:
        manager_config = replace(
            OsmManagerConfig.defaults(),
            cache_directory=manager_cache,
            overpass_url=config.osm_overpass_url or OsmManagerConfig.defaults().overpass_url,
            gpx_corridor_buffer_m=config.osm_coverage_buffer_m,
            maximum_requested_area_km2=config.osm_maximum_area_km2,
            maximum_ensure_cells=config.osm_maximum_cells,
            maximum_cells_per_overpass_request=min(
                config.osm_maximum_cells, config.osm_maximum_requests
            ),
            maximum_overpass_requests=config.osm_maximum_requests,
            maximum_download_bytes=config.osm_maximum_download_bytes,
            maximum_ensure_download_bytes=config.osm_maximum_download_bytes,
            network_timeout_seconds=float(config.osm_acquisition_timeout_seconds),
        )
        geometry = ParsedGeometry(
            tuple(
                tuple(GeoPoint(longitude, latitude) for longitude, latitude in line)
                for line in lines
            )
        )
        coverage = plan_from_geometry(
            geometry,
            manager_config,
            source_kind="repair_gap_anchors",
            buffer_m=config.osm_coverage_buffer_m,
        )
    except Exception as error:
        return OSMResult(base_plan, "unavailable", "coverage", _safe_code(error, "coverage_limit"))

    coverage_seconds = time.monotonic() - coverage_started
    started = time.monotonic()
    if stage_callback:
        stage_callback("acquisition")
    try:
        snapshot = OsmManager(manager_config).ensure(
            coverage, offline=config.osm_mode is OSMMode.OFFLINE
        )
    except OsmManagerError as error:
        return OSMResult(
            base_plan, "unavailable", "acquisition", _safe_code(error, "acquisition_failed")
        )
    if time.monotonic() - started > config.osm_acquisition_timeout_seconds:
        return OSMResult(base_plan, "unavailable", "acquisition", "osm_timeout")
    if _tree_size(cache_root, config.osm_cache_quota_bytes) > config.osm_cache_quota_bytes:
        return OSMResult(base_plan, "unavailable", "acquisition", "cache_quota")
    acquisition_seconds = time.monotonic() - started

    started = time.monotonic()
    try:
        if stage_callback:
            stage_callback("prepare")
        routing_config = replace(
            RoutingCacheConfig.defaults(),
            cache_directory=routing_cache,
            build_timeout_seconds=float(config.osm_prepare_timeout_seconds),
        ).validated()
        with _lease(cache_root, f"snapshot-{snapshot.manifest.snapshot_id}"):
            graph = GraphCache(routing_config).prepare(snapshot.manifest_path)
    except RoutingError as error:
        return OSMResult(base_plan, "unavailable", "prepare", _safe_code(error, "prepare_failed"))
    prepare_seconds = time.monotonic() - started

    started = time.monotonic()
    try:
        if stage_callback:
            stage_callback("routing")
        if _tree_size(cache_root, config.osm_cache_quota_bytes) > config.osm_cache_quota_bytes:
            return OSMResult(base_plan, "unavailable", "prepare", "cache_quota")
        with (
            _lease(cache_root, f"snapshot-{snapshot.manifest.snapshot_id}"),
            _lease(cache_root, f"graph-{graph.graph_id}"),
        ):
            final_plan, discovery = _discover_and_apply(
                activity,
                integrity,
                base_plan,
                graph.graph_id,
                policy,
                cache_directory=routing_cache,
            )
    except Exception as error:
        return OSMResult(base_plan, "unavailable", "routing", _safe_code(error, "routing_failed"))
    routing_seconds = time.monotonic() - started
    decisions = _automatic_decisions(final_plan)
    unresolved = any(item.get("status") == "unresolved" for item in decisions)
    metrics = OSMMetrics(
        coverage_cells=len(coverage.cells),
        coverage_area_km2=coverage.area_km2,
        coverage_seconds=coverage_seconds,
        acquisition_seconds=acquisition_seconds,
        prepare_seconds=prepare_seconds,
        routing_seconds=routing_seconds,
        snapshot_cache_hit=not snapshot.downloaded,
        snapshot_stale=snapshot.stale,
        graph_cache_hit=graph.status == "CACHED",
        routing_queries=discovery.query_count,
        candidate_gaps=discovery.candidate_gap_count,
        candidates=discovery.candidate_count,
    )
    return OSMResult(
        final_plan,
        "partial" if unresolved else "complete",
        private_audit={
            "schema_version": 1,
            "coverage": {"cell_count": len(coverage.cells), "area_km2": coverage.area_km2},
            "snapshot": snapshot.as_dict(),
            "graph": graph.as_dict(),
            "discovery": osm_reconstruction_report(discovery),
            "application": {"decisions": decisions},
        },
        metrics=metrics,
        discovery=discovery,
    )


def _discover_and_apply(
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    graph_id: str,
    policy: RepairPolicy,
    *,
    routing_config: Path | None = None,
    cache_directory: Path | None = None,
) -> tuple[RepairPlan, OSMDryRunResult]:
    """One discovery/application contract for acquired and explicitly prepared graphs."""
    from warpbuster.reconstruction.osm import ValhallaRoutingClient

    discovery = OSMReconstructionProvider(
        ValhallaRoutingClient(routing_config, cache_directory)
    ).discover(activity, base_plan, graph_id)
    plan = apply_automatic_osm_routes(
        activity, integrity, base_plan, discovery, minimum_confidence=policy.minimum_confidence
    )
    return plan, discovery


def run_osm_pipeline(
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    config: PipelineConfig,
    *,
    policy: RepairPolicy = DEFAULT_REPAIR_POLICY,
) -> OSMResult:
    """Resolve either a supplied graph or the full Manager → Routing workflow."""
    if config.osm_graph_id is not None:
        from warpbuster.reconstruction.osm import OSMReconstructionError

        try:
            plan, discovery = _discover_and_apply(
                activity,
                integrity,
                base_plan,
                config.osm_graph_id,
                policy,
                routing_config=config.osm_routing_config,
                cache_directory=config.osm_cache_dir,
            )
        except OSMReconstructionError as error:
            # Preserve the existing CLI's detailed private diagnostics. The web
            # projects only allowlisted status/error codes from the returned result.
            failed = replace(
                base_plan,
                automatic_osm_json=json.dumps(
                    {
                        "policy": "gpx-first-automatic-osm-v2",
                        "status": "unavailable",
                        "graph_id": config.osm_graph_id,
                        "error": {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details,
                        },
                        "decisions": [],
                    }
                ),
            )
            return OSMResult(
                failed,
                "unavailable",
                "routing",
                "routing_failed",
                warning=f"OSM [{error.code}]: {error.message}; retaining GPX and cleaning",
            )
        decisions = _automatic_decisions(plan)
        return OSMResult(
            plan,
            "partial" if any(d.get("status") == "unresolved" for d in decisions) else "complete",
            discovery=discovery,
        )
    runner = run_osm_pipeline_isolated if config.isolate_osm else execute_osm_pipeline
    return runner(activity, integrity, base_plan, config, policy=policy)


def run_osm_pipeline_isolated(
    activity: ActivityData,
    integrity: IntegrityReport,
    base_plan: RepairPlan,
    config: PipelineConfig,
    *,
    policy: RepairPolicy = DEFAULT_REPAIR_POLICY,
) -> OSMResult:
    """Run native OSM work in a killable process group with bounded trusted IPC."""
    if config.osm_mode is OSMMode.DISABLED:
        return OSMResult(base_plan, "disabled")
    if eligible_gap_count(activity, base_plan) == 0:
        return OSMResult(base_plan, "not_needed")
    if sys.platform != "linux" or not hasattr(os, "setsid"):
        return OSMResult(base_plan, "unavailable", "setup", "isolation_unavailable")
    context = get_context("fork")
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(
        target=child_main,
        args=(sending, activity, integrity, base_plan, config, policy),
        name="warpbuster-osm",
    )
    process.start()
    sending.close()
    previous_handler = signal.getsignal(signal.SIGTERM)

    def terminate_from_parent(signum, frame):
        _terminate_group(process.pid)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate_from_parent)
    try:
        total_deadline = time.monotonic() + config.osm_total_timeout_seconds
        stage = "setup"
        stage_deadline = total_deadline
        received_bytes = 0
        while True:
            deadline = min(total_deadline, stage_deadline)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_group(process.pid)
                process.join(timeout=5)
                _cleanup_job_temporary(config)
                return OSMResult(base_plan, "unavailable", stage, "osm_timeout")
            if not receiving.poll(min(0.25, remaining)):
                cache_root = config.data_dir / "osm"
                if (
                    _osm_temporary_size(cache_root, config.osm_job_temp_quota_bytes)
                    > config.osm_job_temp_quota_bytes
                    or _tree_size(cache_root, config.osm_cache_quota_bytes)
                    > config.osm_cache_quota_bytes
                    or shutil.disk_usage(config.data_dir).free < config.osm_minimum_free_bytes
                ):
                    _terminate_group(process.pid)
                    process.join(timeout=5)
                    _cleanup_job_temporary(config)
                    return OSMResult(base_plan, "unavailable", stage, "cache_quota")
                if _group_resources_exceeded(process.pid, config):
                    _terminate_group(process.pid)
                    process.join(timeout=5)
                    _cleanup_job_temporary(config)
                    return OSMResult(base_plan, "unavailable", stage, "resource_limit")
                continue
            try:
                payload = receiving.recv_bytes(config.osm_ipc_maximum_bytes)
                received_bytes += len(payload)
                if received_bytes > config.osm_ipc_maximum_bytes:
                    raise ValueError("cumulative IPC limit exceeded")
                # Payload comes only from our forked, resource-limited child process.
                kind, value = pickle.loads(payload)
            except EOFError, OSError, pickle.PickleError, ValueError, TypeError:
                return OSMResult(base_plan, "unavailable", stage, "ipc_invalid")
            if kind == "stage" and value in {"acquisition", "prepare", "routing"}:
                stage = value
                seconds = {
                    "acquisition": config.osm_acquisition_timeout_seconds,
                    "prepare": config.osm_prepare_timeout_seconds,
                    "routing": config.osm_routing_timeout_seconds,
                }[stage]
                stage_deadline = time.monotonic() + seconds
                continue
            if kind == "result" and isinstance(value, OSMResult):
                return value
            return OSMResult(base_plan, "unavailable", stage, "ipc_invalid")
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        receiving.close()
        if process.is_alive():
            _terminate_group(process.pid)
        process.join(timeout=5)


def _terminate_group(pid: int | None) -> None:
    if pid:
        with suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


def _safe_code(error: BaseException, fallback: str) -> str:
    raw = getattr(error, "code", None)
    value = getattr(raw, "value", raw)
    aliases = {
        "OFFLINE_CACHE_MISS": "offline_cache_miss",
        "REQUEST_LIMIT_EXCEEDED": "coverage_limit",
        "RESPONSE_LIMIT_EXCEEDED": "resource_limit",
        "OSM_DATA_INVALID": "cache_corrupt",
        "CACHE_IO_ERROR": "cache_corrupt",
        "CACHE_LOCK_TIMEOUT": "acquisition_failed",
        "OVERPASS_UNAVAILABLE": "acquisition_failed",
        "CACHE_CORRUPT": "cache_corrupt",
        "GRAPH_ENGINE_MISMATCH": "engine_mismatch",
        "RESOURCE_LIMIT_EXCEEDED": "resource_limit",
    }
    return aliases.get(str(value), fallback)


def _automatic_decisions(plan: RepairPlan) -> list[dict[str, Any]]:
    if plan.automatic_osm_json is None:
        return []
    import json

    value = json.loads(plan.automatic_osm_json)
    decisions = value.get("decisions", [])
    return decisions if isinstance(decisions, list) else []


def _tree_size(root: Path, stop_after: int) -> int:
    total = 0
    if not root.exists():
        return 0
    for directory, _, filenames in os.walk(root, followlinks=False):
        for filename in filenames:
            try:
                total += (Path(directory) / filename).stat(follow_symlinks=False).st_size
            except OSError:
                continue
            if total > stop_after:
                return total
    return total


def _ensure_cache_quota(cache_root: Path, quota_bytes: int) -> bool:
    """Evict only complete old graphs while no pipeline cache lease is active."""
    if _tree_size(cache_root, quota_bytes) <= quota_bytes:
        return True
    import fcntl

    lock_directory = cache_root / "leases"
    lock_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (lock_directory / "eviction.lock").open("a+b") as eviction_lock:
        os.fchmod(eviction_lock.fileno(), 0o600)
        try:
            fcntl.flock(eviction_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        try:
            graphs = cache_root / "routing" / "graphs"
            candidates = (
                sorted(
                    (
                        item
                        for item in graphs.iterdir()
                        if item.is_dir() and re.fullmatch(r"[0-9a-f]{64}", item.name)
                    ),
                    key=lambda item: item.stat().st_mtime,
                )
                if graphs.is_dir()
                else []
            )
            trash = cache_root / "tmp"
            trash.mkdir(parents=True, exist_ok=True, mode=0o700)
            for candidate in candidates:
                target = trash / f"evict-{candidate.name}"
                candidate.replace(target)
                shutil.rmtree(target)
                if _tree_size(cache_root, quota_bytes) <= quota_bytes:
                    return True
            return _tree_size(cache_root, quota_bytes) <= quota_bytes
        except OSError:
            return False
        finally:
            fcntl.flock(eviction_lock, fcntl.LOCK_UN)


def _osm_temporary_size(cache_root: Path, stop_after: int) -> int:
    total = 0
    for root in (
        cache_root / "tmp",
        cache_root / "datasets" / "tmp",
        cache_root / "routing" / "staging",
    ):
        total += _tree_size(root, max(0, stop_after - total))
        if total > stop_after:
            break
    return total


def _cleanup_job_temporary(config: PipelineConfig) -> None:
    cache_root = config.data_dir / "osm"
    for root in (
        cache_root / "tmp",
        cache_root / "datasets" / "tmp",
        cache_root / "routing" / "staging",
    ):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)


def _group_resources_exceeded(pgid: int | None, config: PipelineConfig) -> bool:
    """Account aggregate Linux RSS and CPU for the isolated OSM process group."""
    if not pgid or not Path("/proc").is_dir():
        return False
    total_rss = 0
    total_ticks = 0
    page_size = os.sysconf("SC_PAGE_SIZE")
    clock_ticks = os.sysconf("SC_CLK_TCK")
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            if os.getpgid(pid) != pgid:
                continue
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            statm = (entry / "statm").read_text().split()
            total_ticks += int(stat[11]) + int(stat[12])
            total_rss += int(statm[1]) * page_size
        except OSError, ProcessLookupError, ValueError, IndexError:
            continue
    return (
        total_rss > config.osm_child_memory_limit_bytes
        or total_ticks / clock_ticks > config.osm_child_cpu_seconds
    )


@contextmanager
def _lease(cache_root: Path, identity: str) -> Iterator[None]:
    """Hold a process-backed shared lease while a verified cache entry is in use."""
    import fcntl
    import hashlib

    directory = cache_root / "leases"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = hashlib.sha256(identity.encode()).hexdigest() + ".lease"
    with (
        (directory / "eviction.lock").open("a+b") as eviction_lock,
        (directory / name).open("a+b") as stream,
    ):
        os.fchmod(eviction_lock.fileno(), 0o600)
        os.fchmod(stream.fileno(), 0o600)
        fcntl.flock(eviction_lock, fcntl.LOCK_SH)
        fcntl.flock(stream, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
            fcntl.flock(eviction_lock, fcntl.LOCK_UN)
