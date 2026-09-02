"""Versioned trail-running profile contract tests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.identity import graph_cache_key
from warpbuster_osm_routing.manifest import load_snapshot
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, apply_profile
from warpbuster_osm_routing.valhalla_backend import (
    VALHALLA_BUILD_PROFILE_ID,
    build_config,
    build_profile_options,
)

EXPECTED_PROFILE_SHA256 = "f8af97fd29f4cfb9afc435679c18da5c85812e847242318556119b0dc0e842a3"


def test_profile_v1_has_stable_complete_request_document() -> None:
    assert TRAIL_RUNNING_V1.canonical_document() == {
        "profile_schema": 1,
        "profile_id": "warpbuster-trail-running-v1",
        "engine": {"name": "Valhalla", "compatibility": ">=3.8.3,<3.9"},
        "costing": "pedestrian",
        "costing_options": {
            "pedestrian": {
                "type": "foot",
                "walking_speed": 5.1,
                "max_hiking_difficulty": 3,
                "use_tracks": 1.0,
                "walkway_factor": 0.8,
                "use_hills": 1.0,
                "exclude_unpaved": False,
                "use_ferry": 0.0,
                "step_penalty": 30.0,
                "alley_factor": 2.0,
                "driveway_factor": 5.0,
                "use_living_streets": 0.6,
            }
        },
    }
    assert TRAIL_RUNNING_V1.sha256() == EXPECTED_PROFILE_SHA256


def test_request_application_is_fresh_and_does_not_replace_other_fields() -> None:
    request: dict[str, object] = {"locations": [{"lat": 44.0, "lon": 33.0}]}
    apply_profile(request)
    assert request["costing"] == "pedestrian"
    assert request["costing_options"] == TRAIL_RUNNING_V1.costing_options()
    assert "locations" in request

    request_options = request["costing_options"]
    assert isinstance(request_options, dict)
    pedestrian = request_options["pedestrian"]
    assert isinstance(pedestrian, dict)
    pedestrian["walking_speed"] = 99
    assert TRAIL_RUNNING_V1.costing_options()["pedestrian"]["walking_speed"] == 5.1


@pytest.mark.parametrize("version", ["3.8.3", "3.8.4", "3.8.99-dev"])
def test_profile_accepts_pinned_engine_family(version: str) -> None:
    assert TRAIL_RUNNING_V1.supports_engine(version)


@pytest.mark.parametrize("version", ["3.8.2", "3.9.0", "4.0.0", "unknown"])
def test_profile_rejects_unverified_engine_versions(version: str) -> None:
    assert not TRAIL_RUNNING_V1.supports_engine(version)


def test_incompatible_profile_fails_before_request_mutation() -> None:
    incompatible = replace(
        TRAIL_RUNNING_V1,
        minimum_engine_version=(99, 0, 0),
        maximum_engine_version_exclusive=(100, 0, 0),
    )
    request: dict[str, object] = {"sentinel": True}
    with pytest.raises(RoutingError, match="does not support") as caught:
        apply_profile(request, incompatible)
    assert caught.value.code == "PROFILE_ENGINE_INCOMPATIBLE"
    assert request == {"sentinel": True}


def test_build_and_routing_profile_identities_are_separate(
    snapshot_manifest: Path, tmp_path: Path
) -> None:
    key, _ = graph_cache_key(load_snapshot(snapshot_manifest))
    tiles = tmp_path / "tiles"
    tiles.mkdir()
    config, _ = build_config(tiles)

    assert key["build_profile"] == VALHALLA_BUILD_PROFILE_ID
    assert "routing_profile" not in key
    assert "warpbuster-trail-running" not in json.dumps(key, sort_keys=True)
    assert config["mjolnir"]["include_pedestrian"] is True
    assert config["mjolnir"]["pedestrian_areas"] is False
    assert build_profile_options() == {
        "include_pedestrian": True,
        "keep_all_osm_node_ids": True,
        "keep_osm_node_ids": True,
        "pedestrian_areas": False,
    }


def test_profile_show_json_exposes_identity_and_compatibility(capsys: object) -> None:
    status = main(["profile", "show", "--json"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    document = json.loads(captured.out)

    assert status == 0
    assert document["status"] == "OK"
    assert document["profile"]["profile_sha256"] == EXPECTED_PROFILE_SHA256
    assert document["profile"]["engine_compatible"] is True
    assert document["profile"]["hard_rules"]
    assert document["profile"]["limitations"]
