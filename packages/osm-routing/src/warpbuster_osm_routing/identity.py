"""Path-independent identity for Task 010B graph artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import valhalla
from osmium import version as osmium_version

from warpbuster_osm_routing.models import Snapshot
from warpbuster_osm_routing.normalize import MATERIALIZER_SCHEMA_VERSION
from warpbuster_osm_routing.valhalla_backend import VALHALLA_BUILD_PROFILE_ID, build_config

CACHE_KEY_SCHEMA_VERSION = "graph-cache-key-v2"


def runtime_versions() -> dict[str, str]:
    """Return the runtime components whose semantics affect graph materialization."""
    return {
        "pyosmium": str(osmium_version.pyosmium_release),
        "libosmium": str(osmium_version.libosmium_version),
        "valhalla": str(valhalla.__version__),
    }


def semantic_build_config_hash() -> str:
    """Hash the pinned Valhalla config without embedding a local tile path."""
    return build_config(Path.cwd().resolve())[1]


def graph_cache_key(snapshot: Snapshot) -> tuple[dict[str, Any], str]:
    """Build and hash the canonical, path-independent graph key document."""
    document: dict[str, Any] = {
        "cache_key_schema": CACHE_KEY_SCHEMA_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "dataset_profile": snapshot.dataset_profile,
        "source_sha256": sorted(item.sha256 for item in snapshot.data_files),
        "coverage": snapshot.coverage.as_dict(),
        "materializer_schema": MATERIALIZER_SCHEMA_VERSION,
        "runtime": runtime_versions(),
        "build_profile": VALHALLA_BUILD_PROFILE_ID,
        "build_config_sha256": semantic_build_config_hash(),
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return document, hashlib.sha256(encoded).hexdigest()


def graph_id_for_key(document: dict[str, Any]) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
