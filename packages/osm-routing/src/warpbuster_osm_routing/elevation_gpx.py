"""Deterministic GPX 1.1 route export with complete DEM provenance sidecar."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from warpbuster_osm_routing.dem_cache import DemCache
from warpbuster_osm_routing.dem_profile import MAPZEN_SKADI_EGM96_V1
from warpbuster_osm_routing.elevation_service import ElevationProfile
from warpbuster_osm_routing.errors import RoutingError

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
GRAPH_ID_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ElevationExportConfig:
    maximum_gpx_bytes: int = 8 * 1024 * 1024
    maximum_audit_bytes: int = 16 * 1024 * 1024

    def validated(self) -> ElevationExportConfig:
        for name in ("maximum_gpx_bytes", "maximum_audit_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RoutingError("INVALID_REQUEST", f"{name} must be a positive byte limit")
        return self


@dataclass(frozen=True, slots=True)
class ElevationGpxArtifact:
    gpx_path: Path
    audit_path: Path
    gpx_sha256: str
    audit_sha256: str
    dem_snapshot_id: str
    elevation_profile_id: str
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gpx_path": str(self.gpx_path),
            "audit_path": str(self.audit_path),
            "gpx_sha256": self.gpx_sha256,
            "audit_sha256": self.audit_sha256,
            "dem_snapshot_id": self.dem_snapshot_id,
            "elevation_profile_id": self.elevation_profile_id,
        }


class ElevationGpxWriter:
    def __init__(self, cache: DemCache, config: ElevationExportConfig | None = None) -> None:
        self.cache = cache
        self.config = (config or ElevationExportConfig()).validated()

    def write(
        self, profile: ElevationProfile, output_path: Path, *, graph_id: str | None = None
    ) -> ElevationGpxArtifact:
        if graph_id is not None and not GRAPH_ID_PATTERN.fullmatch(graph_id):
            raise RoutingError("INVALID_REQUEST", "invalid graph ID for DEM export")
        if output_path.suffix.lower() != ".gpx" or not output_path.parent.is_dir():
            raise RoutingError(
                "INVALID_REQUEST", "DEM output must be a GPX in an existing directory"
            )
        audit_path = output_path.with_name(output_path.name + ".audit.json")
        if (
            output_path.exists()
            or output_path.is_symlink()
            or audit_path.exists()
            or audit_path.is_symlink()
        ):
            raise RoutingError("OUTPUT_EXISTS", "DEM GPX or audit sidecar already exists")
        with self.cache.lease(profile.dem_snapshot_id) as snapshot:
            gpx = _encode_gpx(profile, graph_id)
            audit = _encode_audit(profile, graph_id, snapshot.document, gpx)
            if (
                len(gpx) > self.config.maximum_gpx_bytes
                or len(audit) > self.config.maximum_audit_bytes
            ):
                raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM export exceeds byte limit")
            try:
                gpx_stage = _stage(output_path.parent, gpx)
                try:
                    audit_stage = _stage(output_path.parent, audit)
                    try:
                        try:
                            os.link(gpx_stage, output_path)
                            try:
                                os.link(audit_stage, audit_path)
                            except OSError:
                                output_path.unlink()
                                raise
                        except FileExistsError as error:
                            raise RoutingError(
                                "OUTPUT_EXISTS", "DEM export target already exists"
                            ) from error
                    finally:
                        audit_stage.unlink(missing_ok=True)
                finally:
                    gpx_stage.unlink(missing_ok=True)
            except OSError as error:
                raise RoutingError("DEM_EXPORT_FAILED", "DEM GPX export failed") from error
        return ElevationGpxArtifact(
            output_path,
            audit_path,
            _sha256(gpx),
            _sha256(audit),
            profile.dem_snapshot_id,
            profile.elevation_profile_id,
            profile.status,
        )


def _encode_gpx(profile: ElevationProfile, graph_id: str | None) -> bytes:
    ET.register_namespace("", GPX_NAMESPACE)
    root = ET.Element(f"{{{GPX_NAMESPACE}}}gpx", version="1.1", creator="WarpBuster")
    metadata = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}metadata")
    ET.SubElement(metadata, f"{{{GPX_NAMESPACE}}}name").text = "WarpBuster DEM route"
    description = f"DEM {profile.dem_snapshot_id}; profile {profile.elevation_profile_id}"
    if graph_id is not None:
        description += f"; graph {graph_id}"
    ET.SubElement(metadata, f"{{{GPX_NAMESPACE}}}desc").text = description
    route = ET.SubElement(root, f"{{{GPX_NAMESPACE}}}rte")
    ET.SubElement(route, f"{{{GPX_NAMESPACE}}}name").text = "DEM route"
    for item in profile.samples:
        point = ET.SubElement(
            route,
            f"{{{GPX_NAMESPACE}}}rtept",
            lat=_decimal(item.point.latitude),
            lon=_decimal(item.point.longitude),
        )
        if item.filtered_elevation_m is not None:
            ET.SubElement(point, f"{{{GPX_NAMESPACE}}}ele").text = _decimal(
                item.filtered_elevation_m
            )
    return cast(
        bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    )


def _encode_audit(
    profile: ElevationProfile,
    graph_id: str | None,
    snapshot: dict[str, Any],
    gpx: bytes,
) -> bytes:
    dataset = MAPZEN_SKADI_EGM96_V1.inspection_document()
    document = {
        "schema_version": 1,
        "artifact_kind": "warpbuster_dem_route_gpx",
        "graph_id": graph_id,
        "gpx_sha256": _sha256(gpx),
        "dataset_profile": dataset,
        "source_origin": dataset["source_origin"],
        "horizontal_crs": dataset["horizontal_crs"],
        "vertical_datum": dataset["vertical_datum"],
        "elevation_unit": dataset["elevation_unit"],
        "tiles": [
            {"name": item["name"], "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in snapshot["tiles"]
        ],
        "profile": profile.as_dict(),
        "diagnostics": {
            "sample_count": len(profile.samples),
            "missing_count": sum(item.raw_elevation_m is None for item in profile.samples),
            "missing_by_reason": {
                reason: sum(item.missing_reason == reason for item in profile.samples)
                for reason in ("TILE_NOT_IN_SNAPSHOT", "VOID")
            },
        },
    }
    return (
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        + b"\n"
    )


def _stage(directory: Path, content: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".dem-export-", dir=directory)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _decimal(value: float) -> str:
    """A schema-valid decimal lexical form that round-trips the finite float."""
    return format(Decimal(str(value)), "f")
