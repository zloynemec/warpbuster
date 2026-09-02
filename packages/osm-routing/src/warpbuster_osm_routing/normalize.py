"""Bounded deterministic OSM merge and canonical PBF writer."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

import osmium

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import (
    MaterializationResult,
    NormalizationStatistics,
    Snapshot,
)

MATERIALIZER_SCHEMA_VERSION = "osm-materializer-v1"


class _CanonicalCollector(osmium.SimpleHandler):
    def __init__(self, connection: sqlite3.Connection, config: RoutingCacheConfig) -> None:
        super().__init__()
        self.connection = connection
        self.config = config
        self.input_objects = 0
        self.exact_duplicates = 0
        self.input_node_references = 0
        self.input_tag_bytes = 0

    def node(self, node: Any) -> None:
        tags = self._tags(node.tags)
        visible = bool(node.visible)
        location: list[float] | None = None
        if visible:
            try:
                longitude = float(node.location.lon)
                latitude = float(node.location.lat)
            except Exception as error:
                raise RoutingError(
                    "OSM_INPUT_INVALID", f"visible node {node.id} has no valid location"
                ) from error
            if not (
                math.isfinite(longitude)
                and math.isfinite(latitude)
                and -180 <= longitude <= 180
                and -90 <= latitude <= 90
            ):
                raise RoutingError(
                    "OSM_INPUT_INVALID", f"node {node.id} coordinate is outside WGS84"
                )
            location = [longitude, latitude]
        self._store(
            "n",
            int(node.id),
            int(node.version),
            {"visible": visible, "location": location, "tags": tags},
        )

    def way(self, way: Any) -> None:
        references = [int(item.ref) for item in way.nodes]
        self.input_node_references += len(references)
        if self.input_node_references > self.config.maximum_total_node_references:
            self._limit(
                "maximum_total_node_references",
                self.input_node_references,
                self.config.maximum_total_node_references,
            )
        self._store(
            "w",
            int(way.id),
            int(way.version),
            {
                "visible": bool(way.visible),
                "nodes": references,
                "tags": self._tags(way.tags),
            },
        )

    def relation(self, relation: Any) -> None:
        members = [
            [str(member.type), int(member.ref), str(member.role)] for member in relation.members
        ]
        self._store(
            "r",
            int(relation.id),
            int(relation.version),
            {
                "visible": bool(relation.visible),
                "members": members,
                "tags": self._tags(relation.tags),
            },
        )

    def _tags(self, source: Any) -> list[list[str]]:
        tags = sorted([[str(tag.k), str(tag.v)] for tag in source])
        self.input_tag_bytes += sum(
            len(key.encode("utf-8")) + len(value.encode("utf-8")) for key, value in tags
        )
        if self.input_tag_bytes > self.config.maximum_total_tag_bytes:
            self._limit(
                "maximum_total_tag_bytes",
                self.input_tag_bytes,
                self.config.maximum_total_tag_bytes,
            )
        return tags

    def _store(self, kind: str, identifier: int, version: int, payload: dict[str, Any]) -> None:
        self.input_objects += 1
        if self.input_objects > self.config.maximum_osm_objects:
            self._limit("maximum_osm_objects", self.input_objects, self.config.maximum_osm_objects)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            self.connection.execute(
                "INSERT INTO objects(kind, object_id, version, payload) VALUES (?, ?, ?, ?)",
                (kind, identifier, version, encoded),
            )
        except sqlite3.IntegrityError:
            row = self.connection.execute(
                "SELECT payload FROM objects WHERE kind=? AND object_id=? AND version=?",
                (kind, identifier, version),
            ).fetchone()
            if row is None or bytes(row[0]) != encoded:
                raise RoutingError(
                    "OBJECT_VERSION_CONFLICT",
                    f"OSM {kind}{identifier} version {version} has conflicting payloads",
                    {"object_type": kind, "object_id": identifier, "version": version},
                ) from None
            self.exact_duplicates += 1

    @staticmethod
    def _limit(name: str, actual: int, limit: int) -> None:
        raise RoutingError(
            "RESOURCE_LIMIT_EXCEEDED",
            f"OSM input exceeds {name}",
            {"limit_name": name, "actual": actual, "limit": limit},
        )


def normalize_snapshot(
    snapshot: Snapshot,
    output: Path,
    database_path: Path,
    config: RoutingCacheConfig,
) -> MaterializationResult:
    """Select canonical OSM versions, validate references, and write one PBF."""
    output_path = output.resolve()
    database = database_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        _initialize_database(connection)
        collector = _CanonicalCollector(connection, config)
        try:
            for item in snapshot.data_files:
                _reject_history_file(item.path)
                collector.apply_file(str(item.path))
        except RoutingError:
            raise
        except Exception as error:
            raise RoutingError(
                "OSM_INPUT_INVALID", f"cannot parse OSM source data: {error}"
            ) from error
        connection.commit()
        _select_latest_versions(connection)
        unresolved_relation_members = _validate_references(connection, config)
        semantic_digest = _semantic_digest(connection, config.io_chunk_bytes)
        _write_canonical_pbf(connection, output_path)
        size, digest = _hash_file(output_path, config.io_chunk_bytes)
        if size > config.maximum_output_pbf_bytes:
            raise RoutingError(
                "RESOURCE_LIMIT_EXCEEDED",
                "canonical PBF exceeds maximum_output_pbf_bytes",
                {
                    "limit_name": "maximum_output_pbf_bytes",
                    "actual": size,
                    "limit": config.maximum_output_pbf_bytes,
                },
            )
        statistics = _statistics(connection, collector, unresolved_relation_members)
        return MaterializationResult(
            path=output_path,
            sha256=digest,
            size_bytes=size,
            semantic_object_sha256=semantic_digest,
            statistics=statistics,
        )
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()


def _initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=FILE;
        CREATE TABLE objects (
            kind TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            payload BLOB NOT NULL,
            PRIMARY KEY(kind, object_id, version)
        ) WITHOUT ROWID;
        """
    )


def _reject_history_file(path: Path) -> None:
    try:
        reader = osmium.io.Reader(str(path))
        try:
            multiple_versions = bool(reader.header().has_multiple_object_versions)
        finally:
            reader.close()
    except Exception as error:
        raise RoutingError(
            "OSM_INPUT_INVALID", f"cannot inspect OSM source header: {error}"
        ) from error
    if multiple_versions:
        raise RoutingError(
            "UNSUPPORTED_OSM_FORMAT", "OSM history/change datasets are not supported"
        )


def _select_latest_versions(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE selected AS
        SELECT source.kind, source.object_id, source.version, source.payload
        FROM objects AS source
        JOIN (
            SELECT kind, object_id, MAX(version) AS version
            FROM objects
            GROUP BY kind, object_id
        ) AS newest
          ON source.kind = newest.kind
         AND source.object_id = newest.object_id
         AND source.version = newest.version;
        CREATE UNIQUE INDEX selected_identity ON selected(kind, object_id);
        CREATE TABLE visible_ids AS
        SELECT kind, object_id
        FROM selected
        WHERE json_extract(CAST(payload AS TEXT), '$.visible') = 1;
        CREATE UNIQUE INDEX visible_identity ON visible_ids(kind, object_id);
        """
    )


def _validate_references(connection: sqlite3.Connection, config: RoutingCacheConfig) -> int:
    missing_ways: list[dict[str, int]] = []
    for way_id, payload in connection.execute(
        "SELECT object_id, payload FROM selected WHERE kind='w' ORDER BY object_id"
    ):
        document = json.loads(bytes(payload))
        if not document["visible"]:
            continue
        for node_id in document["nodes"]:
            exists = connection.execute(
                "SELECT 1 FROM visible_ids WHERE kind='n' AND object_id=?", (node_id,)
            ).fetchone()
            if exists is None and len(missing_ways) < config.maximum_diagnostic_items:
                missing_ways.append({"way_id": int(way_id), "node_id": int(node_id)})
    if missing_ways:
        raise RoutingError(
            "UNRESOLVED_WAY_REFERENCE",
            "visible OSM way references a missing or invisible node",
            {"examples": missing_ways, "retained": len(missing_ways)},
        )
    unresolved_relations = 0
    for (payload,) in connection.execute(
        "SELECT payload FROM selected WHERE kind='r' ORDER BY object_id"
    ):
        document = json.loads(bytes(payload))
        if not document["visible"]:
            continue
        for kind, reference, _role in document["members"]:
            exists = connection.execute(
                "SELECT 1 FROM visible_ids WHERE kind=? AND object_id=?", (kind, reference)
            ).fetchone()
            unresolved_relations += exists is None
    return unresolved_relations


def _semantic_digest(connection: sqlite3.Connection, chunk_bytes: int) -> str:
    del chunk_bytes  # The rows are already bounded by the source and tag limits.
    digest = hashlib.sha256()
    for kind, identifier, version, payload in connection.execute(
        """
        SELECT kind, object_id, version, payload
        FROM selected
        ORDER BY CASE kind WHEN 'n' THEN 0 WHEN 'w' THEN 1 ELSE 2 END, object_id
        """
    ):
        document = {
            "type": str(kind),
            "id": int(identifier),
            "version": int(version),
            "payload": json.loads(bytes(payload)),
        }
        digest.update(json.dumps(document, sort_keys=True, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def _write_canonical_pbf(connection: sqlite3.Connection, output: Path) -> None:
    writer = osmium.SimpleWriter(str(output), overwrite=True)
    try:
        for kind, identifier, version, payload in connection.execute(
            """
            SELECT kind, object_id, version, payload
            FROM selected
            WHERE json_extract(CAST(payload AS TEXT), '$.visible') = 1
            ORDER BY CASE kind WHEN 'n' THEN 0 WHEN 'w' THEN 1 ELSE 2 END, object_id
            """
        ):
            document = json.loads(bytes(payload))
            tags = [tuple(item) for item in document["tags"]]
            if kind == "n":
                writer.add_node(
                    osmium.osm.mutable.Node(
                        id=int(identifier),
                        version=int(version),
                        visible=True,
                        tags=tags,
                        location=tuple(document["location"]),
                    )
                )
            elif kind == "w":
                writer.add_way(
                    osmium.osm.mutable.Way(
                        id=int(identifier),
                        version=int(version),
                        visible=True,
                        tags=tags,
                        nodes=document["nodes"],
                    )
                )
            else:
                writer.add_relation(
                    osmium.osm.mutable.Relation(
                        id=int(identifier),
                        version=int(version),
                        visible=True,
                        tags=tags,
                        members=[tuple(item) for item in document["members"]],
                    )
                )
    except Exception as error:
        raise RoutingError(
            "PBF_MATERIALIZATION_FAILED", f"cannot write canonical PBF: {error}"
        ) from error
    finally:
        writer.close()


def _statistics(
    connection: sqlite3.Connection,
    collector: _CanonicalCollector,
    unresolved_relation_members: int,
) -> NormalizationStatistics:
    counts = {
        str(kind): int(count)
        for kind, count in connection.execute(
            """
            SELECT kind, COUNT(*) FROM selected
            WHERE json_extract(CAST(payload AS TEXT), '$.visible') = 1
            GROUP BY kind
            """
        )
    }
    selected_total = int(connection.execute("SELECT COUNT(*) FROM selected").fetchone()[0])
    stored_versions = int(connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0])
    tombstones = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM selected
            WHERE json_extract(CAST(payload AS TEXT), '$.visible') = 0
            """
        ).fetchone()[0]
    )
    return NormalizationStatistics(
        input_objects=collector.input_objects,
        selected_nodes=counts.get("n", 0),
        selected_ways=counts.get("w", 0),
        selected_relations=counts.get("r", 0),
        exact_duplicates=collector.exact_duplicates,
        older_versions_replaced=stored_versions - selected_total,
        tombstones=tombstones,
        unresolved_relation_members=unresolved_relation_members,
        input_node_references=collector.input_node_references,
        input_tag_bytes=collector.input_tag_bytes,
    )


def _hash_file(path: Path, chunk_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_bytes):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()
