"""Offline behavioral matrix for the pinned Valhalla trail profile."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
import valhalla

from tests.helpers import make_manifest
from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.profiles import apply_profile

ROW_SPACING_DEGREES = 0.01
ROUTE_SEARCH_RADIUS_METRES = 15


@pytest.fixture(scope="module")
def profile_actor(tmp_path_factory: pytest.TempPathFactory) -> Iterator[valhalla.Actor]:
    root = tmp_path_factory.mktemp("profile-matrix")
    manifest = make_manifest(root, [_matrix_osm()])
    result = GraphCache(RoutingCacheConfig(cache_directory=root / "cache")).prepare(manifest)
    config_path = result.manifest_path.parent / "valhalla.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    yield valhalla.Actor(config)


@pytest.fixture(scope="module")
def preference_actor(tmp_path_factory: pytest.TempPathFactory) -> Iterator[valhalla.Actor]:
    root = tmp_path_factory.mktemp("profile-preferences")
    manifest = make_manifest(root, [_preference_osm()])
    result = GraphCache(RoutingCacheConfig(cache_directory=root / "cache")).prepare(manifest)
    config_path = result.manifest_path.parent / "valhalla.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    yield valhalla.Actor(config)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("row", "label"),
    [
        (0, "paved path"),
        (1, "gravel path"),
        (2, "ground path"),
        (3, "mud path"),
        (4, "steps"),
        (5, "T3 demanding mountain hiking"),
        (8, "access=no overridden by foot=yes"),
        (9, "foot=permissive"),
        (10, "private endpoint access"),
        (11, "destination endpoint access"),
        (12, "gate"),
        (13, "stile"),
        (15, "foot=designated"),
    ],
)
def test_allowed_access_and_surface_matrix(
    profile_actor: valhalla.Actor, row: int, label: str
) -> None:
    assert _can_route(profile_actor, row), label


@pytest.mark.integration
@pytest.mark.parametrize(
    ("row", "label"),
    [
        (6, "T4 alpine hiking"),
        (7, "explicit foot=no"),
        (14, "gate with access=no"),
    ],
)
def test_hard_prohibition_matrix(profile_actor: valhalla.Actor, row: int, label: str) -> None:
    assert not _can_route(profile_actor, row), label


@pytest.mark.integration
def test_track_is_preferred_only_for_a_bounded_detour(preference_actor: valhalla.Actor) -> None:
    moderate = _route_summary(preference_actor, 44.20)
    excessive = _route_summary(preference_actor, 44.22)

    assert moderate["max_lat"] > 44.2002
    assert excessive["max_lat"] < 44.221


@pytest.mark.integration
def test_ferry_is_avoided_when_a_land_route_exists(preference_actor: valhalla.Actor) -> None:
    summary = _route_summary(preference_actor, 44.24)

    assert summary["has_ferry"] is False
    assert summary["max_lat"] > 44.2402


@pytest.mark.integration
def test_steep_but_short_path_is_not_avoided(preference_actor: valhalla.Actor) -> None:
    summary = _route_summary(preference_actor, 44.26)

    assert summary["max_lat"] < 44.2602


def _can_route(actor: valhalla.Actor, row: int) -> bool:
    latitude = 44.0 + row * ROW_SPACING_DEGREES
    locations = [
        {
            "lat": latitude,
            "lon": longitude,
            "radius": ROUTE_SEARCH_RADIUS_METRES,
            "minimum_reachability": 0,
        }
        for longitude in (33.0, 33.002)
    ]
    request: dict[str, Any] = {
        "locations": locations,
        "directions_type": "none",
        "units": "kilometers",
    }
    apply_profile(request)
    try:
        response = json.loads(actor.route(json.dumps(request, separators=(",", ":"))))
    except Exception:
        return False
    trip = response.get("trip", {})
    summary = trip.get("summary", {})
    # Valhalla can correlate to another component when the exact way is inaccessible.
    # Task 010D will expose that snap; this test counts only the requested row.
    routed_latitude = summary.get("min_lat")
    return (
        bool(trip.get("legs"))
        and isinstance(routed_latitude, int | float)
        and abs(routed_latitude - latitude) < 0.0001
    )


def _route_summary(actor: valhalla.Actor, latitude: float) -> dict[str, Any]:
    request: dict[str, Any] = {
        "locations": [
            {"lat": latitude, "lon": longitude, "minimum_reachability": 0}
            for longitude in (33.0, 33.004)
        ],
        "directions_type": "none",
        "units": "kilometers",
    }
    apply_profile(request)
    response = json.loads(actor.route(json.dumps(request, separators=(",", ":"))))
    summary = response["trip"]["summary"]
    assert isinstance(summary, dict)
    return summary


def _matrix_osm() -> bytes:
    rows = [
        (("highway", "path"), ("surface", "paved")),
        (("highway", "path"), ("surface", "gravel")),
        (("highway", "path"), ("surface", "ground")),
        (("highway", "path"), ("surface", "mud")),
        (("highway", "steps"),),
        (("highway", "path"), ("sac_scale", "demanding_mountain_hiking")),
        (("highway", "path"), ("sac_scale", "alpine_hiking")),
        (("highway", "path"), ("foot", "no")),
        (("highway", "path"), ("access", "no"), ("foot", "yes")),
        (("highway", "path"), ("foot", "permissive")),
        (("highway", "path"), ("access", "private")),
        (("highway", "path"), ("access", "destination")),
        (("highway", "path"),),
        (("highway", "path"), ("foot", "designated")),
        (("highway", "path"),),
        (("highway", "path"),),
    ]
    nodes: list[str] = []
    ways: list[str] = []
    for row, tags in enumerate(rows):
        latitude = 44.0 + row * ROW_SPACING_DEGREES
        node_ids = [row * 10 + offset + 1 for offset in range(3)]
        barrier = {12: "gate", 13: "stile", 14: "gate"}.get(row)
        for offset, (node_id, longitude) in enumerate(
            zip(node_ids, (33.0, 33.001, 33.002), strict=True)
        ):
            barrier_tag = f'<tag k="barrier" v="{barrier}"/>' if barrier and offset == 1 else ""
            if row == 14 and offset == 1:
                barrier_tag += '<tag k="access" v="no"/>'
            nodes.append(
                f'<node id="{node_id}" version="1" lat="{latitude:.4f}" '
                f'lon="{longitude:.4f}">{barrier_tag}</node>'
            )
        tag_xml = "".join(f'<tag k="{key}" v="{value}"/>' for key, value in tags)
        references = "".join(f'<nd ref="{node_id}"/>' for node_id in node_ids)
        ways.append(f'<way id="{1000 + row}" version="1">{references}{tag_xml}</way>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<osm version="0.6" generator="warpbuster-test">'
        '<bounds minlat="44.0000" minlon="33.0000" maxlat="44.1520" maxlon="33.0020"/>'
        + "".join(nodes)
        + "".join(ways)
        + "</osm>"
    )
    return xml.encode()


def _preference_osm() -> bytes:
    networks = [
        (44.20, 0.0004, "track"),
        (44.22, 0.0050, "track"),
        (44.24, 0.0005, "ferry"),
        (44.26, 0.0008, "incline"),
    ]
    nodes: list[str] = []
    ways: list[str] = []
    for index, (latitude, detour, kind) in enumerate(networks):
        first = index * 10 + 1
        coordinates = [
            (first, latitude, 33.0),
            (first + 1, latitude, 33.002),
            (first + 2, latitude, 33.004),
            (first + 3, latitude + detour, 33.002),
        ]
        nodes.extend(
            f'<node id="{node_id}" version="1" lat="{lat:.4f}" lon="{lon:.4f}"/>'
            for node_id, lat, lon in coordinates
        )
        if kind == "ferry":
            direct_tags = '<tag k="route" v="ferry"/><tag k="foot" v="yes"/>'
        elif kind == "incline":
            direct_tags = '<tag k="highway" v="path"/><tag k="incline" v="30%"/>'
        else:
            direct_tags = '<tag k="highway" v="residential"/>'
        detour_tags = (
            '<tag k="highway" v="track"/><tag k="surface" v="ground"/>'
            if kind == "track"
            else '<tag k="highway" v="path"/><tag k="surface" v="ground"/>'
        )
        ways.extend(
            [
                f'<way id="{2000 + index * 2}" version="1">'
                f'<nd ref="{first}"/><nd ref="{first + 1}"/><nd ref="{first + 2}"/>'
                f"{direct_tags}</way>",
                f'<way id="{2001 + index * 2}" version="1">'
                f'<nd ref="{first}"/><nd ref="{first + 3}"/><nd ref="{first + 2}"/>'
                f"{detour_tags}</way>",
            ]
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<osm version="0.6" generator="warpbuster-test">'
        '<bounds minlat="44.1900" minlon="33.0000" maxlat="44.2700" maxlon="33.0040"/>'
        + "".join(nodes)
        + "".join(ways)
        + "</osm>"
    )
    return xml.encode()
