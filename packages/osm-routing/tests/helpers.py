"""Shared Task 010B snapshot builders."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def make_manifest(
    root: Path,
    sources: list[bytes],
    *,
    snapshot_id: str = "sha256:test-snapshot",
    extensions: list[str] | None = None,
    media_types: list[str] | None = None,
) -> Path:
    files = []
    for index, source in enumerate(sources):
        extension = extensions[index] if extensions else ".osm"
        path = root / f"source-{index}{extension}"
        path.write_bytes(source)
        digest = hashlib.sha256(source).hexdigest()
        files.append(
            {
                "path": str(path.resolve()),
                "media_type": media_types[index]
                if media_types
                else "application/vnd.openstreetmap.data+xml",
                "sha256": digest,
                "size_bytes": len(source),
            }
        )
    manifest = {
        "protocol_version": 1,
        "manifest_version": 1,
        "manager_version": "test",
        "snapshot_id": snapshot_id,
        "dataset_profile": "pedestrian-routing-v1",
        "osm_base_timestamp": "2026-08-31T00:00:00Z",
        "attribution": "OpenStreetMap contributors",
        "copyright_url": "https://www.openstreetmap.org/copyright",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "coverage": {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000.0,
            "area_km2": 1.0,
        },
        "data_files": files,
    }
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path
