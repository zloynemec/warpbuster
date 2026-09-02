"""GPX, GeoJSON, bbox, corridor, and antimeridian coverage planning."""

from dataclasses import replace
from pathlib import Path

import pytest
from conftest import write_gpx

from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.coverage import (
    parse_bbox,
    parse_gpx,
    plan_from_bbox,
    plan_from_geojson,
    plan_from_gpx,
)
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError


def test_gpx_track_and_route_produce_deterministic_corridor_cells(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    track = write_gpx(
        tmp_path / "track.gpx",
        [[(33.60, 44.40), (33.62, 44.41)], [(33.62, 44.41), (33.64, 44.42)]],
    )
    route = write_gpx(
        tmp_path / "route.gpx",
        [[(33.60, 44.40), (33.62, 44.41)]],
        route=True,
        version="1.0",
    )

    first = plan_from_gpx(track, manager_config)
    second = plan_from_gpx(track, manager_config)
    route_plan = plan_from_gpx(route, manager_config)

    assert first.cells == second.cells
    assert first.request_fingerprint == second.request_fingerprint
    assert first.source_kind == "gpx"
    assert 1 <= len(route_plan.cells) < len(first.cells) + 1
    assert first.buffer_m == manager_config.gpx_corridor_buffer_m


def test_gpx_ignores_waypoints_telemetry_and_names(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    path = tmp_path / "course.gpx"
    path.write_text(
        """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <wpt lat="0" lon="0"><name>private name</name></wpt>
  <trk><name>secret</name><trkseg>
    <trkpt lat="44.4" lon="33.6"><ele>100</ele><time>2026-01-01T00:00:00Z</time></trkpt>
    <trkpt lat="44.41" lon="33.61"><extensions><private>x</private></extensions></trkpt>
  </trkseg></trk>
</gpx>""",
        encoding="utf-8",
    )
    geometry = parse_gpx(path, manager_config)
    assert geometry.lines == (((geometry.lines[0][0]), geometry.lines[0][1]),)
    assert geometry.lines[0][0].longitude == 33.6
    assert all(point.longitude != 0 for line in geometry.lines for point in line)


@pytest.mark.parametrize(
    "body",
    [
        "<gpx>",
        "<!DOCTYPE gpx [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><gpx>&x;</gpx>",
        "<gpx><wpt lat='1' lon='2'/></gpx>",
        "<gpx><trk><trkseg><trkpt lat='NaN' lon='2'/><trkpt lat='1' lon='3'/></trkseg></trk></gpx>",
    ],
)
def test_invalid_or_unsafe_gpx_is_rejected(
    tmp_path: Path, manager_config: OsmManagerConfig, body: str
) -> None:
    path = tmp_path / "invalid.gpx"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(OsmManagerError) as raised:
        plan_from_gpx(path, manager_config)
    assert raised.value.code is ErrorCode.INVALID_GPX


def test_outlier_and_area_bounds_are_explicit(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    outlier = write_gpx(tmp_path / "outlier.gpx", [[(33.6, 44.4), (36.0, 50.0)]])
    with pytest.raises(OsmManagerError) as raised:
        plan_from_gpx(outlier, manager_config)
    assert raised.value.code is ErrorCode.REQUEST_LIMIT_EXCEEDED
    assert "maximum_gpx_segment_length_m" in raised.value.message

    tiny_limit = replace(manager_config, maximum_requested_area_km2=0.01)
    normal = write_gpx(tmp_path / "normal.gpx", [[(33.6, 44.4), (33.61, 44.41)]])
    with pytest.raises(OsmManagerError, match="maximum_requested_area_km2"):
        plan_from_gpx(normal, tiny_limit)


def test_antimeridian_track_does_not_select_the_world(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    path = write_gpx(tmp_path / "date-line.gpx", [[(179.99, 10.0), (-179.99, 10.01)]])
    plan = plan_from_gpx(path, manager_config, buffer_m=100)
    assert len(plan.cells) < 20
    dimension = 1 << manager_config.cache_grid_zoom
    assert {cell.x for cell in plan.cells} & {0, dimension - 1}


def test_geojson_and_explicit_bbox_inputs(tmp_path: Path, manager_config: OsmManagerConfig) -> None:
    path = tmp_path / "area.geojson"
    path.write_text(
        '{"type":"LineString","coordinates":[[33.6,44.4],[33.61,44.41]]}',
        encoding="utf-8",
    )
    geojson = plan_from_geojson(path, manager_config, buffer_m=250)
    bbox = plan_from_bbox(parse_bbox("33.60,44.40,33.61,44.41"), manager_config)
    assert geojson.source_kind == "geojson"
    assert bbox.source_kind == "bbox"
    assert geojson.cells
    assert bbox.cells


def test_long_sparse_course_uses_corridor_not_full_bbox(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    diagonal = write_gpx(
        tmp_path / "diagonal.gpx",
        [[(33.60, 44.40), (34.00, 44.70)]],
    )
    permissive = replace(manager_config, maximum_gpx_segment_length_m=100_000)
    corridor = plan_from_gpx(diagonal, permissive, buffer_m=100)
    full_bbox = plan_from_bbox(parse_bbox("33.60,44.40,34.00,44.70"), permissive)
    assert len(corridor.cells) < len(full_bbox.cells)
