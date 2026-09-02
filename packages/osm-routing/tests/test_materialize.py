"""Snapshot materialization tests for Task 010A."""

from __future__ import annotations

from pathlib import Path

import osmium

from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.materialize import materialize_pbf


class ObjectCounter(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.nodes = 0
        self.ways = 0

    def node(self, _: object) -> None:
        self.nodes += 1

    def way(self, _: object) -> None:
        self.ways += 1


def test_materialize_pbf_is_readable_and_reference_complete(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    output = tmp_path / "snapshot.osm.pbf"

    digest, size = materialize_pbf(load_snapshot(snapshot_manifest), output)
    counter = ObjectCounter()
    counter.apply_file(str(output))

    assert len(digest) == 64
    assert size == output.stat().st_size
    assert counter.nodes == 9
    assert counter.ways == 3
