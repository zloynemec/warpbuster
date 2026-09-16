"""Ignored six-pair web-policy acceptance; private source files are never modified."""

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from warpbuster.fit.reader import read_fit
from warpbuster_web.config import DEMMode, OSMMode, WebConfig
from warpbuster_web.processing import process_job

TRACKS = Path("tests/private/tracks")
PAIRS = (
    ("Andromeda_Taras.fit", "Andromeda_2026.gpx"),
    ("Balaklava_20260913_Taras.fit", "Balaklava_20260913.gpx"),
    ("CHR_KayaBayu_22_Taras.fit", "CHR_KayaBayu_22.gpx"),
    ("m87_home_run.fit", "m87_home_run.gpx"),
    ("BST2025_TezBair_55_Taras.fit", "BST2025_TezBair_55.gpx"),
    ("Tridcatka_Taras.fit", "Tridcatka.gpx"),
)


@pytest.mark.private
@pytest.mark.parametrize(("fit_name", "gpx_name"), PAIRS)
def test_six_private_pairs_reach_schema_three_without_mutating_sources(
    tmp_path, fit_name, gpx_name
):
    fit = TRACKS / fit_name
    gpx = TRACKS / gpx_name
    if not fit.is_file() or not gpx.is_file():
        pytest.skip("private FIT/GPX pair is unavailable")
    source_hashes = (
        hashlib.sha256(fit.read_bytes()).digest(),
        hashlib.sha256(gpx.read_bytes()).digest(),
    )
    shutil.copyfile(fit, tmp_path / "original.fit")
    shutil.copyfile(gpx, tmp_path / "course.gpx")
    config = replace(
        WebConfig(),
        data_dir=tmp_path / "data",
        osm_mode=OSMMode.DISABLED,
        approximate_osm=False,
        dem_mode=DEMMode.DISABLED,
        complete_missing_altitude=False,
    )
    process_job(tmp_path, 100_000, config=config)
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["schema_version"] == 3
    assert report["osm"]["status"] == "disabled"
    assert report["summary"]["applied_osm_gaps"] == 0
    if fit_name == "Andromeda_Taras.fit":
        assert report["summary"]["applied_gpx_gaps"] >= 3
        assert any(gap["estimated"] for gap in report["gaps"] if gap["provider"] == "gpx")
    assert hashlib.sha256(fit.read_bytes()).digest() == source_hashes[0]
    assert hashlib.sha256(gpx.read_bytes()).digest() == source_hashes[1]
    if (tmp_path / "corrected.fit").is_file():
        assert read_fit(tmp_path / "corrected.fit").records
        assert report["fit_diff"]["timestamps_unchanged"]
        assert report["fit_diff"]["sensors_unchanged"]
