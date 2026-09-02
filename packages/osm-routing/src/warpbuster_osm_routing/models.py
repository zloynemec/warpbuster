"""Typed boundary models for OSM snapshot routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """One validated WGS84 point."""

    latitude: float
    longitude: float

    def as_valhalla(self) -> dict[str, float]:
        return {"lat": self.latitude, "lon": self.longitude}


@dataclass(frozen=True, slots=True)
class SnapshotDataFile:
    """One verified data file from an OSM Manager manifest."""

    path: Path
    media_type: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The verified subset of the OSM Manager contract consumed by routing."""

    manifest_path: Path
    snapshot_id: str
    dataset_profile: str
    manager_version: str
    osm_base_timestamp: str | None
    data_files: tuple[SnapshotDataFile, ...]
    manifest_sha256: str
    attribution: str
    copyright_url: str
    license_url: str


@dataclass(frozen=True, slots=True)
class NormalizationStatistics:
    """Auditable counts produced while selecting canonical OSM objects."""

    input_objects: int
    selected_nodes: int
    selected_ways: int
    selected_relations: int
    exact_duplicates: int
    older_versions_replaced: int
    tombstones: int
    unresolved_relation_members: int
    input_node_references: int
    input_tag_bytes: int

    def as_dict(self) -> dict[str, int]:
        return {
            "input_objects": self.input_objects,
            "selected_nodes": self.selected_nodes,
            "selected_ways": self.selected_ways,
            "selected_relations": self.selected_relations,
            "exact_duplicates": self.exact_duplicates,
            "older_versions_replaced": self.older_versions_replaced,
            "tombstones": self.tombstones,
            "unresolved_relation_members": self.unresolved_relation_members,
            "input_node_references": self.input_node_references,
            "input_tag_bytes": self.input_tag_bytes,
        }


@dataclass(frozen=True, slots=True)
class MaterializationResult:
    """Canonical PBF identity and merge diagnostics."""

    path: Path
    sha256: str
    size_bytes: int
    semantic_object_sha256: str
    statistics: NormalizationStatistics


@dataclass(frozen=True, slots=True)
class GraphResult:
    """One verified READY or CACHED graph cache result."""

    status: str
    graph_id: str
    manifest_path: Path
    document: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": 1,
            "operation": "prepare",
            "status": self.status,
            "graph_id": self.graph_id,
            "manifest_path": str(self.manifest_path),
            "graph": self.document,
        }


@dataclass(frozen=True, slots=True)
class SpikeResult:
    """Serializable result of one local Valhalla build and route probe."""

    document: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.document
