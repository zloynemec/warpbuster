"""Bounded deterministic OSM normalization tests for Task 010B."""

from __future__ import annotations

import gzip
from dataclasses import replace
from pathlib import Path

import osmium
import pytest

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.materialize import materialize_pbf
from warpbuster_osm_routing.normalize import normalize_snapshot

from .helpers import make_manifest


def _normalize(manifest: Path, root: Path):  # type: ignore[no-untyped-def]
    config = RoutingCacheConfig.defaults().with_cache_directory(root / "cache")
    return normalize_snapshot(
        load_snapshot(manifest, config), root / "canonical.osm.pbf", root / "objects.sqlite", config
    )


def test_exact_overlap_is_deduplicated(
    snapshot_manifest: Path, osm_xml: bytes, tmp_path: Path
) -> None:
    manifest = make_manifest(tmp_path, [osm_xml, osm_xml])

    result = _normalize(manifest, tmp_path)

    assert result.statistics.input_objects == 24
    assert result.statistics.exact_duplicates == 12
    assert result.statistics.selected_nodes == 9
    assert result.statistics.selected_ways == 3


def test_highest_version_is_selected_deterministically(tmp_path: Path) -> None:
    older = b'<osm version="0.6"><node id="1" version="1" lat="44" lon="33"/></osm>'
    newer = b'<osm version="0.6"><node id="1" version="2" lat="44.1" lon="33"/></osm>'
    manifest = make_manifest(tmp_path, [newer, older])

    result = _normalize(manifest, tmp_path)

    assert result.statistics.older_versions_replaced == 1
    versions: list[int] = []

    class Reader(osmium.SimpleHandler):
        def node(self, node: object) -> None:
            versions.append(int(node.version))  # type: ignore[attr-defined]

    Reader().apply_file(str(result.path))
    assert versions == [2]


def test_same_version_conflict_refuses_entire_materialization(tmp_path: Path) -> None:
    first = b'<osm version="0.6"><node id="1" version="1" lat="44" lon="33"/></osm>'
    second = b'<osm version="0.6"><node id="1" version="1" lat="45" lon="33"/></osm>'
    manifest = make_manifest(tmp_path, [first, second])

    with pytest.raises(RoutingError) as caught:
        _normalize(manifest, tmp_path)

    assert caught.value.code == "OBJECT_VERSION_CONFLICT"
    assert not (tmp_path / "canonical.osm.pbf").exists()


def test_missing_way_node_ref_refuses_entire_materialization(tmp_path: Path) -> None:
    source = b"""<osm version="0.6">
      <node id="1" version="1" lat="44" lon="33"/>
      <way id="2" version="1"><nd ref="1"/><nd ref="999"/><tag k="highway" v="path"/></way>
    </osm>"""
    manifest = make_manifest(tmp_path, [source])

    with pytest.raises(RoutingError) as caught:
        _normalize(manifest, tmp_path)

    assert caught.value.code == "UNRESOLVED_WAY_REFERENCE"


def test_unresolved_relation_member_is_a_warning_statistic(tmp_path: Path) -> None:
    source = b"""<osm version="0.6">
      <node id="1" version="1" lat="44" lon="33"/>
      <relation id="8" version="1">
        <member type="way" ref="999" role="outer"/><tag k="type" v="multipolygon"/>
      </relation>
    </osm>"""
    manifest = make_manifest(tmp_path, [source])

    result = _normalize(manifest, tmp_path)

    assert result.statistics.selected_relations == 1
    assert result.statistics.unresolved_relation_members == 1


def test_source_order_does_not_change_semantic_digest(tmp_path: Path) -> None:
    first = b'<osm version="0.6"><node id="1" version="1" lat="44" lon="33"/></osm>'
    second = b'<osm version="0.6"><node id="2" version="1" lat="44" lon="34"/></osm>'
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    one = _normalize(make_manifest(left, [first, second]), left)
    two = _normalize(make_manifest(right, [second, first]), right)

    assert one.semantic_object_sha256 == two.semantic_object_sha256
    assert one.sha256 == two.sha256


def test_named_object_limit_is_enforced(tmp_path: Path) -> None:
    source = b"""<osm version="0.6">
      <node id="1" version="1" lat="44" lon="33"/>
      <node id="2" version="1" lat="44" lon="34"/>
    </osm>"""
    manifest = make_manifest(tmp_path, [source])
    config = RoutingCacheConfig.defaults().with_cache_directory(tmp_path / "cache")
    config = replace(config, maximum_osm_objects=1)

    with pytest.raises(RoutingError) as caught:
        normalize_snapshot(
            load_snapshot(manifest, config),
            tmp_path / "one.pbf",
            tmp_path / "one.sqlite",
            config,
        )

    assert caught.value.code == "RESOURCE_LIMIT_EXCEEDED"
    assert caught.value.details["limit_name"] == "maximum_osm_objects"


def test_highest_version_tombstone_removes_object(tmp_path: Path) -> None:
    visible = b'<osm version="0.6"><node id="1" version="1" lat="44" lon="33"/></osm>'
    deleted = b'<osm version="0.6"><node id="1" version="2" visible="false"/></osm>'
    manifest = make_manifest(tmp_path, [visible, deleted])

    result = _normalize(manifest, tmp_path)

    assert result.statistics.selected_nodes == 0
    assert result.statistics.tombstones == 1


def test_xml_gzip_input_is_supported(osm_xml: bytes, tmp_path: Path) -> None:
    compressed = gzip.compress(osm_xml, mtime=0)
    manifest = make_manifest(
        tmp_path,
        [compressed],
        extensions=[".osm.gz"],
        media_types=["application/vnd.openstreetmap.data+xml+gzip"],
    )

    result = _normalize(manifest, tmp_path)

    assert result.statistics.selected_nodes == 9
    assert result.statistics.selected_ways == 3


def test_same_objects_in_xml_and_pbf_are_exact_duplicates(osm_xml: bytes, tmp_path: Path) -> None:
    source = tmp_path / "pbf-source"
    combined = tmp_path / "combined"
    source.mkdir()
    combined.mkdir()
    source_manifest = make_manifest(source, [osm_xml])
    pbf_path = source / "source.pbf"
    materialize_pbf(load_snapshot(source_manifest), pbf_path)
    manifest = make_manifest(
        combined,
        [osm_xml, pbf_path.read_bytes()],
        extensions=[".osm", ".osm.pbf"],
        media_types=[
            "application/vnd.openstreetmap.data+xml",
            "application/vnd.openstreetmap.data+pbf",
        ],
    )

    result = _normalize(manifest, combined)

    assert result.statistics.exact_duplicates == 12
    assert result.statistics.selected_nodes == 9
    assert result.statistics.selected_ways == 3
