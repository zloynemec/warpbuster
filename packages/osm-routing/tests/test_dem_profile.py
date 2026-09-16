"""Task 020A: source, format, datum, attribution and inspection contract."""

from __future__ import annotations

import json

import pytest

from warpbuster_osm_routing.cli import main
from warpbuster_osm_routing.dem_profile import MAPZEN_SKADI_EGM96_V1


def test_skadi_format_and_datum_are_explicit() -> None:
    profile = MAPZEN_SKADI_EGM96_V1.canonical_document()
    assert profile["profile_id"] == "mapzen-skadi-egm96-v1"
    assert profile["horizontal_crs"] == "EPSG:4326"
    assert profile["vertical_datum"] == "WGS84/EGM96 geoid"
    assert profile["elevation_unit"] == "metre"
    assert profile["tile_extent_degrees"] == 1
    assert profile["grid_samples_per_side"] == 3601
    assert profile["nominal_grid_spacing_arcseconds"] == 1
    assert profile["uncompressed_tile_bytes"] == 25_934_402
    assert profile["sample_encoding"] == "signed-int16-big-endian"
    assert profile["compression"] == "gzip"
    assert profile["void_value"] == -32768
    assert profile["includes_bathymetry"] is True
    assert profile["source_origin"].startswith("https://")


def test_attribution_bundle_covers_all_published_provider_groups() -> None:
    profile = MAPZEN_SKADI_EGM96_V1.canonical_document()
    sources = {item["source"] for item in profile["attributions"]}
    assert sources == {
        "Mapzen/Tilezen",
        "3DEP / GMTED2010 / SRTM",
        "ArcticDEM",
        "Australia",
        "Austria",
        "Canada",
        "ETOPO1",
        "EU-DEM",
        "INEGI",
        "LINZ",
        "Norway",
        "United Kingdom",
    }
    assert all(item["credit"] for item in profile["attributions"])
    inspection = MAPZEN_SKADI_EGM96_V1.inspection_document()
    assert inspection["documentation"]["attribution_and_terms"].endswith("/attribution.md")
    assert any("not a license" in note for note in inspection["limitations"])
    assert any("not a guarantee" in note for note in inspection["limitations"])


def test_profile_identity_is_stable_and_documents_are_detached() -> None:
    original = MAPZEN_SKADI_EGM96_V1.sha256()
    first = MAPZEN_SKADI_EGM96_V1.canonical_document()
    first["attributions"][0]["credit"] = "tampered"
    second = MAPZEN_SKADI_EGM96_V1.canonical_document()
    assert second["attributions"][0]["credit"] != "tampered"
    assert MAPZEN_SKADI_EGM96_V1.sha256() == original
    assert len(original) == 64
    assert second["attribution_bundle_version"] == 1


def test_cli_exposes_machine_and_human_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["dem", "profile", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["operation"] == "dem_profile_show"
    assert document["status"] == "OK"
    assert document["profile"]["profile_sha256"] == MAPZEN_SKADI_EGM96_V1.sha256()
    assert main(["dem", "profile"]) == 0
    output = capsys.readouterr().out
    assert "mapzen-skadi-egm96-v1" in output
    assert "WGS84/EGM96 geoid" in output
