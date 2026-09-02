"""Manifest boundary tests for Task 010A."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.manifest import load_snapshot


def test_load_snapshot_verifies_public_manager_contract(snapshot_manifest: Path) -> None:
    snapshot = load_snapshot(snapshot_manifest)

    assert snapshot.dataset_profile == "pedestrian-routing-v1"
    assert snapshot.manager_version == "test"
    assert len(snapshot.data_files) == 1
    assert snapshot.data_files[0].path.is_absolute()


def test_load_snapshot_rejects_hash_mismatch(snapshot_manifest: Path) -> None:
    document = json.loads(snapshot_manifest.read_text())
    document["data_files"][0]["sha256"] = "0" * 64
    snapshot_manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RoutingSpikeError, match="hash mismatch") as caught:
        load_snapshot(snapshot_manifest)

    assert caught.value.code == "data_hash_mismatch"


def test_load_snapshot_rejects_relative_data_path(snapshot_manifest: Path) -> None:
    document = json.loads(snapshot_manifest.read_text())
    document["data_files"][0]["path"] = "source.osm"
    snapshot_manifest.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RoutingSpikeError, match="must be absolute") as caught:
        load_snapshot(snapshot_manifest)

    assert caught.value.code == "manifest_invalid"
