"""Materialize overlapping manager blobs into one Valhalla input PBF."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import osmium

from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.models import Snapshot


def materialize_pbf(snapshot: Snapshot, output: Path) -> tuple[str, int]:
    """Merge a verified snapshot in stable file order and return hash and byte size."""
    destination = output.resolve()
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp.pbf")
    reader = osmium.MergeInputReader()
    try:
        for item in snapshot.data_files:
            reader.add_file(str(item.path))
        writer = osmium.SimpleWriter(str(temporary), overwrite=True)
        try:
            reader.apply(writer, simplify=True)
        finally:
            writer.close()
        temporary.replace(destination)
    except Exception as error:
        temporary.unlink(missing_ok=True)
        raise RoutingSpikeError(
            "materialization_failed", f"cannot materialize snapshot PBF: {error}"
        ) from error
    digest = hashlib.sha256()
    size = 0
    with destination.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            size += len(chunk)
            digest.update(chunk)
    if size == 0:
        raise RoutingSpikeError("materialization_failed", "materialized PBF is empty")
    return digest.hexdigest(), size
