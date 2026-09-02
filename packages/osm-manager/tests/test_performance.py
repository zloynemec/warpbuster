"""Bounded local performance regression without network access."""

from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

from warpbuster_osm_manager.cache import CacheStore
from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.osm_validation import validate_osm_xml


@pytest.mark.performance
def test_validate_hash_and_publish_one_hundred_thousand_objects(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    source = tmp_path / "large.osm"
    nodes = "".join(
        f'<node id="{identifier}" lat="44.5" lon="33.5" />' for identifier in range(1, 100_001)
    )
    source.write_text(
        '<osm version="0.6"><bounds minlat="44" minlon="33" '
        f'maxlat="45" maxlon="34" />{nodes}</osm>',
        encoding="utf-8",
    )
    config = replace(manager_config, maximum_osm_objects=100_001)

    started = perf_counter()
    validation = validate_osm_xml(source, config)
    published = CacheStore(config).publish_import(source)
    elapsed = perf_counter() - started

    assert validation.object_count == 100_000
    assert published.path.is_file()
    assert published.size_bytes == source.stat().st_size
    assert elapsed < 5.0
