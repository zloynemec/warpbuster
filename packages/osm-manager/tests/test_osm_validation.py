"""Raw OSM integrity and reference-completeness validation."""

from pathlib import Path

import pytest
from conftest import osm_xml, write_pbf

from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import BoundingBox
from warpbuster_osm_manager.osm_validation import validate_osm_pbf, validate_osm_xml


def test_valid_osm_extracts_metadata(tmp_path: Path, manager_config: OsmManagerConfig) -> None:
    path = tmp_path / "data.osm"
    path.write_text(osm_xml(BoundingBox(33.6, 44.4, 33.7, 44.5)), encoding="utf-8")
    result = validate_osm_xml(path, manager_config)
    assert result.node_count == 2
    assert result.way_count == 1
    assert result.object_count == 3
    assert result.osm_base_timestamp == "2026-09-02T00:00:00Z"
    assert result.bounds is not None
    assert result.bounds_are_declared is True


def test_missing_way_reference_is_rejected(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    path = tmp_path / "broken.osm"
    path.write_text(
        '<osm version="0.6"><way id="1"><nd ref="404"/><tag k="highway" v="path"/></way></osm>',
        encoding="utf-8",
    )
    with pytest.raises(OsmManagerError) as raised:
        validate_osm_xml(path, manager_config)
    assert raised.value.code is ErrorCode.OSM_DATA_INVALID
    assert raised.value.details == {"missing_node_reference_count": 1}


def test_osm_entities_and_object_limit_are_rejected(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    entity = tmp_path / "entity.osm"
    entity.write_text("<!DOCTYPE osm [<!ENTITY x 'x'>]><osm>&x;</osm>", encoding="utf-8")
    with pytest.raises(OsmManagerError, match="entities"):
        validate_osm_xml(entity, manager_config)

    limited = tmp_path / "limited.osm"
    limited.write_text(osm_xml(BoundingBox(33.6, 44.4, 33.7, 44.5)), encoding="utf-8")
    from dataclasses import replace

    with pytest.raises(OsmManagerError) as raised:
        validate_osm_xml(limited, replace(manager_config, maximum_osm_objects=2))
    assert raised.value.code is ErrorCode.RESPONSE_LIMIT_EXCEEDED


def test_overpass_error_remark_is_not_accepted_as_empty_osm(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    path = tmp_path / "remark.osm"
    path.write_text(
        '<osm version="0.6"><remark>runtime error: query timed out</remark></osm>',
        encoding="utf-8",
    )
    with pytest.raises(OsmManagerError) as raised:
        validate_osm_xml(path, manager_config)
    assert raised.value.code is ErrorCode.OSM_DATA_INVALID


def test_pbf_header_bounds_and_references_are_validated(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    path = tmp_path / "region.osm.pbf"
    bounds = BoundingBox(33.5, 44.3, 33.8, 44.6)
    write_pbf(path, bounds)
    result = validate_osm_pbf(path, manager_config)
    assert result.bounds_are_declared is True
    assert result.bounds is not None
    assert result.object_count == 3
    assert result.way_count == 1
