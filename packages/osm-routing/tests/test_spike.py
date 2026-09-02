"""Local end-to-end Valhalla feasibility acceptance."""

from __future__ import annotations

from pathlib import Path

import pytest

from warpbuster_osm_routing.errors import RoutingSpikeError
from warpbuster_osm_routing.models import GeoPoint
from warpbuster_osm_routing.spike import run_spike


@pytest.mark.integration
def test_valhalla_build_route_and_osm_provenance(snapshot_manifest: Path, tmp_path: Path) -> None:
    result = run_spike(
        snapshot_manifest,
        tmp_path / "derived",
        GeoPoint(44.0, 33.0),
        GeoPoint(44.002, 33.002),
        alternates=1,
    ).as_dict()

    assert result["status"] == "ready"
    assert result["verdict"] == "go"
    assert result["probe"]["returned_routes"] >= 1
    assert result["probe"]["routes"][0]["way_ids"]
    assert result["probe"]["start_snap"]["candidates"][0]["distance_m"] < 1
    assert result["artifacts"]["tile_files"] > 0


@pytest.mark.integration
def test_valhalla_outside_coverage_is_structured_failure(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    with pytest.raises(RoutingSpikeError) as caught:
        run_spike(
            snapshot_manifest,
            tmp_path / "outside",
            GeoPoint(45.0, 34.0),
            GeoPoint(45.001, 34.001),
        )

    assert caught.value.code in {"snap_failed", "no_route"}
