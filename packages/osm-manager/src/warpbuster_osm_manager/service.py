"""High-level OSM snapshot acquisition and cache management service."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from warpbuster_osm_manager._version import __version__
from warpbuster_osm_manager.cache import CacheStore
from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.coverage import batch_cells, plan_from_import_bounds
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import (
    CachedCell,
    CellId,
    CoveragePlan,
    EnsureResult,
    utc_now,
)
from warpbuster_osm_manager.osm_validation import validate_osm_file
from warpbuster_osm_manager.overpass import OsmFetcher, OverpassClient


class OsmManager:
    """Coordinate cache resolution without any routing or FIT semantics."""

    def __init__(
        self,
        config: OsmManagerConfig | None = None,
        *,
        overpass_client: OsmFetcher | None = None,
    ) -> None:
        self.config = config or OsmManagerConfig.defaults()
        self.cache = CacheStore(self.config)
        self.overpass = overpass_client or OverpassClient(self.config)

    def ensure(
        self,
        plan: CoveragePlan,
        *,
        max_age_seconds: int | None = None,
        offline: bool = False,
        refresh: bool = False,
        require_fresh: bool = False,
        now: datetime | None = None,
    ) -> EnsureResult:
        """Return one complete immutable snapshot, fetching missing coverage as needed."""
        if offline and refresh:
            raise OsmManagerError(
                ErrorCode.INVALID_INPUT, "offline and refresh modes are mutually exclusive"
            )
        effective_now = now or utc_now()
        maximum_age = (
            self.config.default_max_age_seconds if max_age_seconds is None else max_age_seconds
        )
        if maximum_age <= 0:
            raise OsmManagerError(ErrorCode.INVALID_INPUT, "max_age_seconds must be positive")

        with self.cache.ensure_lock():
            current = self.cache.current_cells(plan)
            complete = len(current) == len(plan.cells)
            stale_cells = {
                cell
                for cell, cached in current.items()
                if (effective_now - cached.fetched_at).total_seconds() > maximum_age
            }
            if complete and not stale_cells and not refresh:
                return self._snapshot(plan, current, effective_now, stale=False, downloaded=False)
            if offline:
                if not complete:
                    raise OsmManagerError(
                        ErrorCode.OFFLINE_CACHE_MISS,
                        "offline cache does not completely cover the requested area",
                        {
                            "required_cell_count": len(plan.cells),
                            "available_cell_count": len(current),
                        },
                    )
                if stale_cells and require_fresh:
                    raise OsmManagerError(
                        ErrorCode.FRESH_CACHE_REQUIRED,
                        "offline cache is complete but stale",
                        {"stale_cell_count": len(stale_cells)},
                    )
                return self._snapshot(
                    plan,
                    current,
                    effective_now,
                    stale=bool(stale_cells),
                    downloaded=False,
                    warnings=("using stale OSM cache in offline mode",) if stale_cells else (),
                )

            to_fetch = (
                set(plan.cells) if refresh else (set(plan.cells) - set(current)) | stale_cells
            )
            pending: list[CachedCell] = []
            total_download_bytes = 0
            temporary_paths: list[Path] = []
            try:
                for batch in batch_cells(to_fetch, self.config):
                    temporary = self.cache.new_temporary_path(".osm")
                    temporary_paths.append(temporary)
                    downloaded = self.overpass.fetch(batch.bounds, temporary)
                    total_download_bytes += downloaded.size_bytes
                    if total_download_bytes > self.config.maximum_ensure_download_bytes:
                        raise OsmManagerError(
                            ErrorCode.RESPONSE_LIMIT_EXCEEDED,
                            "ensure exceeds maximum_ensure_download_bytes",
                            {
                                "downloaded_bytes": total_download_bytes,
                                "limit_bytes": self.config.maximum_ensure_download_bytes,
                            },
                        )
                    blob = self.cache.publish_overpass_xml(temporary)
                    pending.extend(
                        CachedCell(
                            cell=cell,
                            blob_sha256=blob.sha256,
                            fetched_at=effective_now,
                            osm_base_timestamp=downloaded.validation.osm_base_timestamp,
                            source_kind="overpass",
                            endpoint=self.config.overpass_url,
                            size_bytes=blob.size_bytes,
                            media_type=blob.media_type,
                            path=blob.path,
                        )
                        for cell in batch.cells
                    )
            except OsmManagerError as error:
                if error.code is ErrorCode.OVERPASS_UNAVAILABLE and complete and not require_fresh:
                    return self._snapshot(
                        plan,
                        current,
                        effective_now,
                        stale=True,
                        downloaded=False,
                        warnings=("Overpass unavailable; using complete stale OSM cache",),
                    )
                raise
            finally:
                for path in temporary_paths:
                    path.unlink(missing_ok=True)

            self.cache.commit_cells(pending, plan)
            updated = self.cache.current_cells(plan)
            return self._snapshot(
                plan, updated, effective_now, stale=False, downloaded=bool(pending)
            )

    def import_file(self, path: Path, *, now: datetime | None = None) -> EnsureResult:
        """Validate, content-address, and index one local OSM extract by its bounds."""
        effective_now = now or utc_now()
        try:
            size_bytes = path.stat().st_size
        except OSError as error:
            raise OsmManagerError(
                ErrorCode.INVALID_INPUT, f"cannot read imported OSM file {path}: {error}"
            ) from error
        if size_bytes > self.config.maximum_import_bytes:
            raise OsmManagerError(
                ErrorCode.REQUEST_LIMIT_EXCEEDED,
                "imported OSM file exceeds maximum_import_bytes",
                {"size_bytes": size_bytes, "limit_bytes": self.config.maximum_import_bytes},
            )
        validation = validate_osm_file(path, self.config)
        if validation.bounds is None:
            raise OsmManagerError(
                ErrorCode.OSM_DATA_INVALID, "imported OSM data contains no geographic bounds"
            )
        if not validation.bounds_are_declared:
            raise OsmManagerError(
                ErrorCode.OSM_DATA_INVALID,
                "imported OSM data must declare extract bounds in its header",
            )
        plan = plan_from_import_bounds(validation.bounds, self.config)
        with self.cache.ensure_lock():
            blob = self.cache.publish_import(path)
            cells = tuple(
                CachedCell(
                    cell=cell,
                    blob_sha256=blob.sha256,
                    fetched_at=effective_now,
                    osm_base_timestamp=validation.osm_base_timestamp,
                    source_kind="import",
                    endpoint=None,
                    size_bytes=blob.size_bytes,
                    media_type=blob.media_type,
                    path=blob.path,
                )
                for cell in plan.cells
            )
            self.cache.commit_cells(cells, plan)
            return self._snapshot(
                plan,
                {cell.cell: cell for cell in cells},
                effective_now,
                stale=False,
                downloaded=False,
            )

    def list_snapshots(self) -> tuple[dict[str, Any], ...]:
        return self.cache.list_manifests()

    def inspect_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        return self.cache.verify_manifest(snapshot_id)

    def remove_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        with self.cache.ensure_lock():
            return self.cache.remove_snapshot(snapshot_id)

    def prune(self, *, apply: bool, now: datetime | None = None) -> dict[str, Any]:
        with self.cache.ensure_lock():
            return self.cache.prune(apply=apply, now=now or utc_now())

    def doctor(self) -> dict[str, Any]:
        restored = self.cache.rebuild_index()
        return {
            "status": "ok",
            "cache_directory": str(self.cache.root),
            "coverage_index": str(self.cache.index_path),
            "coverage_index_rows_restored": restored,
            "overpass_url": self.config.overpass_url,
        }

    def _snapshot(
        self,
        plan: CoveragePlan,
        cells: dict[CellId, CachedCell],
        now: datetime,
        *,
        stale: bool,
        downloaded: bool,
        warnings: tuple[str, ...] = (),
    ) -> EnsureResult:
        manifest, path = self.cache.create_snapshot(
            plan,
            cells,
            now=now,
            stale=stale,
            manager_version=__version__,
        )
        return EnsureResult(
            manifest=manifest,
            manifest_path=path,
            downloaded=downloaded,
            stale=stale,
            warnings=warnings,
        )


def read_protocol_request(path: Path) -> dict[str, Any]:
    """Read one explicit JSON request file without accepting unknown protocol versions."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OsmManagerError(
            ErrorCode.INVALID_INPUT, f"cannot read protocol request {path}: {error}"
        ) from error
    if not isinstance(document, dict):
        raise OsmManagerError(ErrorCode.INVALID_INPUT, "protocol request must be an object")
    return document
