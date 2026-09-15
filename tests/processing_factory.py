"""Synthetic offline inputs shared by Core and web pipeline integration tests."""

from dataclasses import replace
from math import cos, radians

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.test_osm_reconstruction_native import _forked_osm
from warpbuster.fit.reader import read_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.pipeline import OSMMode, PipelineConfig
from warpbuster.reconstruction import build_repair_plan


def processing_fixture(tmp_path, *, seed_cache=True):
    manager = pytest.importorskip("warpbuster_osm_manager")
    pytest.importorskip("warpbuster_osm_routing")
    pytest.importorskip("valhalla")
    coverage_module = __import__("warpbuster_osm_manager.coverage", fromlist=["ParsedGeometry"])
    scale = 111_195.0 * cos(radians(44.0))
    path = tmp_path / "original.fit"
    write_trajectory_activity(
        path,
        [
            (i, None, None) if 21 <= i <= 379 else (i, 44.0, 33.0 + i * 2 / scale)
            for i in range(401)
        ],
        distances_m=[float(i * 2) for i in range(401)],
        speeds_mps=[2.0] * 401,
        altitudes_m=[100.0] * 401,
    )
    activity = read_fit(path)
    integrity = analyze_integrity(activity)
    plan = build_repair_plan(activity, integrity)
    config = replace(
        PipelineConfig(),
        data_dir=tmp_path / "web",
        osm_mode=OSMMode.OFFLINE,
        osm_minimum_free_bytes=1,
    )
    manager_config = replace(
        manager.OsmManagerConfig.defaults(),
        cache_directory=config.data_dir / "osm" / "datasets",
        gpx_corridor_buffer_m=config.osm_coverage_buffer_m,
        maximum_requested_area_km2=config.osm_maximum_area_km2,
        maximum_ensure_cells=config.osm_maximum_cells,
    )
    geometry = coverage_module.ParsedGeometry(
        ((coverage_module.GeoPoint(33.0, 44.0), coverage_module.GeoPoint(33.01, 44.0)),)
    )
    coverage = coverage_module.plan_from_geometry(
        geometry,
        manager_config,
        source_kind="web_gap_anchors",
        buffer_m=config.osm_coverage_buffer_m,
    )
    bounds = [coverage_module.bounds_for_cell(cell) for cell in coverage.cells]
    bounds_tag = (
        f'<bounds minlat="{min(item.south for item in bounds)}" '
        f'minlon="{min(item.west for item in bounds)}" '
        f'maxlat="{max(item.north for item in bounds)}" '
        f'maxlon="{max(item.east for item in bounds)}"/>'
    ).encode()
    source = tmp_path / "source.osm"
    source.write_bytes(
        _forked_osm().replace(b'<osm version="0.6">', b'<osm version="0.6">' + bounds_tag)
    )
    if seed_cache:
        manager.OsmManager(manager_config).import_file(source)
    (tmp_path / "course.gpx").write_text(
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg><trkpt lat="45" lon="34"/><trkpt lat="45.001" lon="34.001"/>'
        "</trkseg></trk></gpx>"
    )
    return activity, integrity, plan, config
