"""Task 020D: filtered DEM results, GPX/audit and read-only altitude comparison."""

from __future__ import annotations

import ast
import gzip
import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig
from warpbuster_osm_routing.dem_coverage import DemCoveragePlan
from warpbuster_osm_routing.elevation_comparison import (
    AltitudeObservation,
    compare_altitudes,
)
from warpbuster_osm_routing.elevation_filter import (
    ElevationFilteringPolicy,
    filter_elevations,
)
from warpbuster_osm_routing.elevation_gpx import ElevationExportConfig, ElevationGpxWriter
from warpbuster_osm_routing.elevation_service import ElevationSamplingConfig, ElevationService
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint

GPX = "{http://www.topografix.com/GPX/1/1}"


class Response(io.BytesIO):
    status = 200

    def __init__(self, data: bytes, url: str) -> None:
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}
        self.url = url

    def geturl(self) -> str:
        return self.url


def _cache(tmp_path: Path) -> tuple[DemCache, str]:
    data = gzip.compress(bytes(25_934_402), mtime=0)
    cache = DemCache(
        DemCacheConfig(cache_directory=tmp_path / "dem"),
        response_factory=lambda url, _: Response(data, url),
    )
    snapshot = cache.ensure(DemCoveragePlan(("N44E033",), 2, 30.0), "auto")
    assert snapshot.snapshot_id is not None
    return cache, snapshot.snapshot_id


def test_filter_fixtures_and_policy_identity() -> None:
    policy = ElevationFilteringPolicy()
    assert policy.sha256() == ElevationFilteringPolicy().sha256()
    assert policy.sha256() != ElevationFilteringPolicy(window_radius_m=0).sha256()
    flat = filter_elevations((0, 30, 60), (100.0, 100.0, 100.0), policy)
    assert flat.values == (100.0, 100.0, 100.0)
    assert flat.raw_totals.as_dict() == {"ascent_m": 0, "descent_m": 0}
    noise = filter_elevations((0, 30, 60), (100.0, 101.0, 100.0), policy)
    assert noise.values == (100.0, 100.5, 100.0)
    assert noise.raw_totals.as_dict() == {"ascent_m": 1, "descent_m": 1}
    assert noise.filtered_totals.as_dict() == {"ascent_m": 0, "descent_m": 0}
    hill = filter_elevations((0, 30, 60), (0.0, 10.0, 0.0), policy)
    assert hill.values == (0.0, 5.0, 0.0)
    assert hill.raw_totals.as_dict() == {"ascent_m": 10, "descent_m": 10}
    assert hill.filtered_totals.as_dict() == {"ascent_m": 5, "descent_m": 5}
    slow_climb = filter_elevations(
        (0, 30, 60, 90, 120), (0.0, 1.0, 2.0, 3.0, 4.0), ElevationFilteringPolicy(window_radius_m=0)
    )
    assert slow_climb.filtered_totals.ascent_m == 4
    small_reversal = filter_elevations(
        (0, 30, 60, 90), (0.0, 5.0, 4.0, 10.0), ElevationFilteringPolicy(window_radius_m=0)
    )
    assert small_reversal.raw_totals.ascent_m == 11
    assert small_reversal.filtered_totals.ascent_m == 10
    assert small_reversal.filtered_totals.descent_m == 0
    short = filter_elevations((0, 30), (-10.0, -5.0), policy)
    assert short.values == (-10.0, -5.0)
    assert short.filtered_totals.ascent_m == 5


def test_filter_never_bridges_missing_segment() -> None:
    result = filter_elevations(
        (0, 30, 60, 90, 120),
        (0.0, 10.0, None, 20.0, 30.0),
        ElevationFilteringPolicy(),
    )
    assert result.values == (0.0, 10.0, None, 20.0, 30.0)
    assert result.raw_totals.as_dict() == {"ascent_m": 20, "descent_m": 0}
    assert result.filtered_totals.as_dict() == {"ascent_m": 20, "descent_m": 0}
    with pytest.raises(RoutingError):
        filter_elevations((1, 0), (1.0, 2.0), ElevationFilteringPolicy())


def test_gpx_export_is_deterministic_and_audited(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path)
    profile = ElevationService(cache, ElevationSamplingConfig(maximum_spacing_m=200_000)).sample(
        snapshot_id, (GeoPoint(44.5, 33.5), GeoPoint(44.5, 34.5))
    )
    assert profile.status == "PARTIAL"
    graph_id = "sha256:" + "a" * 64
    writer = ElevationGpxWriter(cache)
    first = writer.write(profile, tmp_path / "first.gpx", graph_id=graph_id)
    second = writer.write(profile, tmp_path / "second.gpx", graph_id=graph_id)
    assert first.gpx_sha256 == second.gpx_sha256
    assert first.audit_sha256 == second.audit_sha256
    root = ET.fromstring(first.gpx_path.read_bytes())
    assert root.attrib["version"] == "1.1"
    route_points = root.findall(f"{GPX}rte/{GPX}rtept")
    assert len(route_points) == 2
    assert float(route_points[0].attrib["lat"]) == 44.5
    assert float(route_points[0].attrib["lon"]) == 33.5
    assert route_points[0].find(f"{GPX}ele") is not None
    assert route_points[1].find(f"{GPX}ele") is None
    assert root.find(f".//{GPX}time") is None
    audit = json.loads(first.audit_path.read_text())
    assert audit["graph_id"] == graph_id
    assert audit["profile"]["dem_snapshot_id"] == snapshot_id
    assert audit["tiles"][0]["name"] == "N44E033"
    assert audit["dataset_profile"]["attributions"]
    assert audit["diagnostics"]["missing_by_reason"]["TILE_NOT_IN_SNAPSHOT"] == 1
    assert "first.gpx" not in first.audit_path.read_text()
    with pytest.raises(RoutingError) as exists:
        writer.write(profile, tmp_path / "first.gpx")
    assert exists.value.code == "OUTPUT_EXISTS"
    limited = ElevationGpxWriter(cache, ElevationExportConfig(maximum_gpx_bytes=1))
    with pytest.raises(RoutingError) as limit:
        limited.write(profile, tmp_path / "limited.gpx")
    assert limit.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert not (tmp_path / "limited.gpx").exists()


def test_filter_policy_changes_profile_not_snapshot_or_raw(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path)
    points = (GeoPoint(44.5, 33.5), GeoPoint(44.5001, 33.5))
    first = ElevationService(cache).sample(snapshot_id, points)
    second = ElevationService(
        cache, filtering_policy=ElevationFilteringPolicy(window_radius_m=0)
    ).sample(snapshot_id, points)
    assert first.dem_snapshot_id == second.dem_snapshot_id == snapshot_id
    assert first.elevation_profile_id != second.elevation_profile_id
    assert [item.raw_elevation_m for item in first.samples] == [
        item.raw_elevation_m for item in second.samples
    ]


def test_cli_export_writes_gpx_and_sidecar(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache, snapshot_id = _cache(tmp_path)
    route = tmp_path / "route.json"
    route.write_text(json.dumps({"points": [{"latitude": 44.5, "longitude": 33.5}]}))
    target = tmp_path / "route.gpx"
    assert (
        main(
            [
                "dem",
                "export",
                snapshot_id,
                str(route),
                "--output",
                str(target),
                "--cache-dir",
                str(cache.root),
                "--json",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "READY"
    assert document["dem_snapshot_id"] == snapshot_id
    assert target.exists()
    assert Path(document["audit_path"]).exists()
    assert (
        main(
            [
                "dem",
                "export",
                snapshot_id,
                str(route),
                "--output",
                str(target),
                "--cache-dir",
                str(cache.root),
                "--json",
            ]
        )
        == 2
    )
    error = json.loads(capsys.readouterr().out)
    assert error["error"]["code"] == "OUTPUT_EXISTS"


def test_comparison_requires_declared_compatible_datum(tmp_path: Path) -> None:
    cache, snapshot_id = _cache(tmp_path)
    profile = ElevationService(cache).sample(snapshot_id, (GeoPoint(44.5, 33.5),))
    chainage = profile.samples[0].chainage_m
    known = AltitudeObservation("FIT", 0, chainage, 5.0, "record.altitude", "WGS84/EGM96 geoid")
    unknown = AltitudeObservation("GPX_COURSE", 0, chainage, 7.0, "trkpt.ele", None)
    comparison = compare_altitudes(profile, (known, unknown)).as_dict()
    assert comparison["purpose"] == "diagnostic_only"
    assert comparison["sources"][0]["status"] == "READY"
    assert comparison["sources"][0]["residual_m"]["mean_signed"] == 5.0
    assert comparison["sources"][1]["status"] == "DATUM_UNKNOWN"
    assert comparison["sources"][1]["residual_m"] is None
    mismatch = AltitudeObservation("FIT", 0, chainage, 5.0, "record.altitude", "ellipsoid")
    result = compare_altitudes(profile, (mismatch,)).as_dict()
    assert result["sources"][0]["status"] == "DATUM_MISMATCH"
    assert result["sources"][0]["residual_m"] is None
    with pytest.raises(RoutingError):
        compare_altitudes(
            profile, (AltitudeObservation("FIT", 0, chainage + 1, 5.0, "record.altitude", None),)
        )


def test_integrity_detector_has_no_dem_imports() -> None:
    root = Path(__file__).parents[3] / "src" / "warpbuster" / "integrity"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any("warpbuster_osm_routing" in name for name in imports), path
