"""Interactive HTML report renderer tests."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.activity_factory import make_activity
from warpbuster.integrity import analyze_integrity
from warpbuster.report.html import HtmlReportError, write_analyze_html

_DATA_PREFIX = '<script id="warpbuster-report-data" type="application/json">'
_DATA_SUFFIX = "</script>"


def test_analyze_html_is_deterministic_uses_leaflet_and_preserves_gaps(tmp_path: Path) -> None:
    """One local file embeds data and uses only the declared map dependencies."""
    activity = make_activity(
        [
            (0.0, 55.0, 37.0),
            (1.0, None, None),
            (2.0, 55.0, 37.001),
        ]
    )
    activity = replace(
        activity,
        manufacturer='</script><script src="https://example.invalid/x.js">',
    )
    integrity = analyze_integrity(activity)
    first_path = tmp_path / "first.html"
    second_path = tmp_path / "second.html"

    write_analyze_html(activity, integrity, first_path)
    write_analyze_html(activity, integrity, second_path)

    first = first_path.read_text(encoding="utf-8")
    assert first == second_path.read_text(encoding="utf-8")
    assert "__WARPBUSTER_REPORT_DATA__" not in first
    assert 'src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"' in first
    assert 'href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"' in first
    assert 'integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="' in first
    assert 'L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png"' in first
    assert "OpenStreetMap</a> contributors" in first
    assert 'L.map("map"' in first
    assert "scrollWheelZoom: true" in first
    assert 'typeof L === "undefined"' in first
    assert "canvas.chart {" in first
    assert "\n    canvas {" not in first
    assert "currentContinuity !== continuity" in first
    assert 'addOverlay("Missing-data bridges", missingBridges(primaryRows)' in first
    assert 'overlayLayers["Distance markers (km)"]' in first
    assert "const distanceMarkerStepMetres = 1000" in first
    assert "Math.floor(row[5] / distanceMarkerStepMetres)" in first
    assert "`${bucket * 2}`" not in first
    assert "collapsed: true" in first
    assert ".leaflet-tile-pane { filter:" in first
    assert 'id="metrics-comparison"' in first
    assert 'id="missing-runs"' in first
    assert "connect-src https://unpkg.com" in first
    assert "\\u003c/script\\u003e" in first
    payload = _embedded_payload(first)
    assert payload["inspect"]["device"]["manufacturer"] == (
        '</script><script src="https://example.invalid/x.js">'
    )
    assert payload["report_kind"] == "analyze"
    assert payload["chart_axis"]["mode"] == "elapsed_time"
    assert payload["tracks"]["original"]["records"][1][2:4] == [None, None]
    comparison = payload["metrics_comparison"]["rows"][0]
    assert comparison["id"] == "original"
    assert comparison["embedded_distance_m"] is None
    assert comparison["map_geometry_distance_m"] == pytest.approx(63.78, abs=0.1)
    assert comparison["solid_geometry_distance_m"] is None
    assert payload["missing_position_runs"] == [
        {
            "anchor_after_record_index": 2,
            "anchor_before_record_index": 0,
            "anchor_elapsed_seconds": 2.0,
            "continuity_id": 0,
            "end_record_index": 1,
            "missing_record_count": 1,
            "recorded_distance_delta_m": None,
            "start_record_index": 1,
            "straight_line_distance_m": pytest.approx(63.78, abs=0.1),
            "straight_line_speed_mps": pytest.approx(31.89, abs=0.1),
        }
    ]
    assert payload["repair"] is None
    assert payload["write_result"] is None


def test_missing_run_report_never_bridges_a_continuity_boundary(tmp_path: Path) -> None:
    """Presentation diagnostics preserve explicit GPX/FIT continuity boundaries."""
    activity = make_activity([(0.0, 55.0, 37.0), (1.0, None, None), (2.0, 55.0, 37.001)])
    activity = replace(
        activity,
        records=(
            activity.records[0],
            activity.records[1],
            replace(activity.records[2], continuity_id=1),
        ),
    )
    output_path = tmp_path / "continuity.html"

    write_analyze_html(activity, analyze_integrity(activity), output_path)

    runs = _embedded_payload(output_path.read_text(encoding="utf-8"))["missing_position_runs"]
    assert len(runs) == 1
    assert runs[0]["anchor_before_record_index"] == 0
    assert runs[0]["anchor_after_record_index"] is None
    assert runs[0]["straight_line_distance_m"] is None


def test_html_report_refuses_overwrite_and_missing_parent(tmp_path: Path) -> None:
    """HTML output follows the same explicit no-overwrite policy as repaired FIT."""
    activity = make_activity([(0.0, 55.0, 37.0), (1.0, 55.0, 37.0001)])
    integrity = analyze_integrity(activity)
    output_path = tmp_path / "report.html"
    output_path.write_text("keep", encoding="utf-8")

    with pytest.raises(HtmlReportError, match="already exists"):
        write_analyze_html(activity, integrity, output_path)
    assert output_path.read_text(encoding="utf-8") == "keep"

    with pytest.raises(HtmlReportError, match="directory does not exist"):
        write_analyze_html(activity, integrity, tmp_path / "missing" / "report.html")


def test_html_without_complete_timestamps_uses_record_index_axis(tmp_path: Path) -> None:
    """A partially timed activity never invents elapsed time for visualization."""
    activity = make_activity([(0.0, 55.0, 37.0), (None, 55.0, 37.0001), (2.0, 55.0, 37.0002)])
    output_path = tmp_path / "report.html"

    write_analyze_html(activity, analyze_integrity(activity), output_path)

    payload = _embedded_payload(output_path.read_text(encoding="utf-8"))
    assert payload["chart_axis"] == {
        "label": "Record index",
        "mode": "record_index",
    }
    assert [row[1] for row in payload["tracks"]["original"]["records"]] == [
        0.0,
        1.0,
        2.0,
    ]


def _embedded_payload(rendered: str) -> dict[str, Any]:
    encoded = rendered.split(_DATA_PREFIX, maxsplit=1)[1].split(_DATA_SUFFIX, maxsplit=1)[0]
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise TypeError("HTML payload must be an object")
    return payload
