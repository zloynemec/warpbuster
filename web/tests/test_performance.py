"""Public running metrics match the internal HTML while excluding raw private data."""

import json
from dataclasses import replace

import pytest
from tests.activity_factory import make_activity
from tests.test_repair_cli import _repairable_fixture
from warpbuster.fit.reader import read_fit
from warpbuster.report.html import _activity_performance
from warpbuster_web.performance import SPLIT_FIELDS, SUMMARY_FIELDS, public_performance
from warpbuster_web.processing import process_job
from warpbuster_web.refresh_results import refresh_report


def test_public_metrics_match_service_html_including_partial_kilometre():
    activity = make_activity([(float(i * 150), 55.0, 37 + i * 0.005) for i in range(6)])
    altitudes = [100, 400, 100, 200, 100, 80]
    activity = replace(
        activity,
        recorded_distance_m=2500.0,
        records=tuple(
            replace(
                record,
                distance=float(i * 500),
                altitude=float(altitudes[i]),
                heart_rate=100 + i * 10,
                cadence=70 + i * 2,
            )
            for i, record in enumerate(activity.records)
        ),
    )
    public = public_performance(activity, source="corrected", distance_quality="uncertain")
    internal = _activity_performance(activity)
    assert public["distance_quality"] == "uncertain"
    assert public["average_pace_seconds_per_km"] == 300
    assert public["total_ascent_m"] == 400 and public["total_descent_m"] == 420
    assert set(public) == {*SUMMARY_FIELDS, "source", "distance_quality", "cadence_unit", "splits"}
    assert all(public[key] == internal[key] for key in SUMMARY_FIELDS)
    assert len(public["splits"]) == 3
    for actual, expected in zip(public["splits"], internal["splits"], strict=True):
        assert set(actual) == {*SPLIT_FIELDS, "complete_kilometre"}
        assert all(actual[key] == expected[key] for key in actual)
    assert public["splits"][-1]["complete_kilometre"] is False
    assert public["splits"][-1]["distance_m"] == 500
    for private in ('"timestamp"', '"records"', '"source_path"', '"heart_rate"', '"serial_number"'):
        assert private not in json.dumps(public)


def test_missing_metrics_are_null_not_invented():
    activity = make_activity([(0.0, 55.0, 37.0), (10.0, 55.0, 37.0001)])
    report = public_performance(activity, source="original", distance_quality="source_unverified")
    assert report["average_pace_seconds_per_km"] is None
    assert report["total_ascent_m"] is None
    assert report["splits"] == []


def test_processed_report_includes_gpx_geometry_and_actual_corrected_metrics(tmp_path):
    _repairable_fixture(tmp_path, with_elevation=True)
    assert process_job(tmp_path, 100_000)
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["schema_version"] == 3
    assert len(report["tracks"]["course"][0]) == 33
    assert all(len(point) == 2 for point in report["tracks"]["course"][0])
    assert report["performance"]["source"] == "corrected"
    expected = _activity_performance(read_fit(tmp_path / "corrected.fit"))
    assert report["performance"]["total_ascent_m"] == pytest.approx(expected["total_ascent_m"])
    assert report["performance"]["splits"][0]["elapsed_seconds"] == 32
    assert "source_path" not in json.dumps(report)


@pytest.mark.parametrize("keep_inputs", [True, False])
def test_existing_report_enrichment_preserves_fit_and_handles_deleted_legacy_sources(
    tmp_path, keep_inputs
):
    _repairable_fixture(tmp_path, with_elevation=True)
    process_job(tmp_path, 100_000)
    path = tmp_path / "result.json"
    current = json.loads(path.read_text())
    old = {**current, "schema_version": 1}
    old.pop("performance")
    old["tracks"] = {key: value for key, value in current["tracks"].items() if key != "course"}
    path.write_text(json.dumps(old))
    fit_bytes = (tmp_path / "corrected.fit").read_bytes()
    if not keep_inputs:
        (tmp_path / "original.fit").unlink()
        (tmp_path / "course.gpx").unlink()
    assert refresh_report(tmp_path, tmp_path)
    enriched = json.loads(path.read_text())
    assert (tmp_path / "corrected.fit").read_bytes() == fit_bytes
    assert enriched["fit_diff"] == current["fit_diff"]
    assert enriched["performance"]["total_ascent_m"] == current["performance"]["total_ascent_m"]
    assert bool(enriched["tracks"]["course"]) is keep_inputs
    assert enriched["performance"]["distance_quality"] == (
        current["performance"]["distance_quality"] if keep_inputs else "unknown"
    )
    assert not refresh_report(tmp_path, tmp_path)
