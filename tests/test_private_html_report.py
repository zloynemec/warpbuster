"""Optional M7 HTML smoke and performance checks with ignored private data."""

import json
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.report.html import write_analyze_html

_ORIGINAL = Path("tests/private/tracks/Andromeda_Taras.fit")
_FIXED = Path("tests/private/tracks/Andromeda_Taras_FIXED.fit")
_DZHURLA = Path("tests/private/tracks/CWT_Dzhurla_2025_Taras.fit")
_DZHURLA_COURSE = Path("tests/private/tracks/CWT_Dzhurla_2025.gpx")
_MAX_ANALYZE_AND_RENDER_SECONDS = 5.0
_MAX_REPORT_SIZE_BYTES = 5 * 1024 * 1024


@pytest.mark.private
@pytest.mark.skipif(
    not _ORIGINAL.exists(),
    reason="private Andromeda FIT fixture is unavailable",
)
def test_private_andromeda_html_is_bounded_and_exposes_residual_finding(
    tmp_path: Path,
) -> None:
    """A roughly 20k-record report stays compact and does not hide Task 006B evidence."""
    activity = read_fit(_ORIGINAL)
    output_path = tmp_path / "andromeda.html"

    started = perf_counter()
    integrity = analyze_integrity(activity)
    write_analyze_html(activity, integrity, output_path)
    elapsed = perf_counter() - started

    rendered = output_path.read_text(encoding="utf-8")
    assert elapsed < _MAX_ANALYZE_AND_RENDER_SECONDS
    assert output_path.stat().st_size < _MAX_REPORT_SIZE_BYTES
    assert '"from_record_index":3626' in rendered
    assert '"to_record_index":3627' in rendered
    assert '"classification":"impossible"' in rendered
    assert 'src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"' in rendered
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in rendered
    assert "currentContinuity !== continuity" in rendered
    assert 'addOverlay("Missing-data bridges", missingBridges(primaryRows)' in rendered
    assert 'overlayLayers["Distance markers (km)"]' in rendered


@pytest.mark.private
@pytest.mark.skipif(
    not _FIXED.exists(),
    reason="private repaired Andromeda FIT fixture is unavailable",
)
def test_private_repaired_andromeda_lists_remaining_missing_runs(tmp_path: Path) -> None:
    """The report retains a stable audit table for every remaining coordinate gap."""
    activity = read_fit(_FIXED)
    output_path = tmp_path / "andromeda-fixed.html"

    write_analyze_html(activity, analyze_integrity(activity), output_path)

    payload = _html_payload(output_path.read_text(encoding="utf-8"))
    runs = payload["missing_position_runs"]
    assert [
        (item["start_record_index"], item["end_record_index"], item["missing_record_count"])
        for item in runs
    ] == [
        (1768, 1773, 6),
        (4381, 4399, 19),
        (8410, 8631, 222),
    ]
    assert runs[-1]["anchor_elapsed_seconds"] == 223.0
    assert runs[-1]["straight_line_distance_m"] == pytest.approx(286.53, abs=0.1)


@pytest.mark.private
@pytest.mark.skipif(
    not (_DZHURLA.exists() and _DZHURLA_COURSE.exists()),
    reason="private Dzhurla FIT/course fixtures are unavailable",
)
def test_private_dzhurla_numbered_detector_report_matches_known_evidence(
    tmp_path: Path,
) -> None:
    """The real track exposes one suspicious run and two independent signal gaps."""
    activity = read_fit(_DZHURLA)
    integrity = analyze_integrity(activity)
    output_path = tmp_path / "dzhurla-detector.html"

    write_analyze_html(
        activity,
        integrity,
        output_path,
        course=read_gpx_course(_DZHURLA_COURSE),
    )

    payload = _html_payload(output_path.read_text(encoding="utf-8"))
    assert payload["report_kind"] == "analyze"
    assert payload["analysis"]["status"] == "suspicious"
    assert payload["analysis"]["confidence"] == "low"
    assert payload["analysis"]["corrupted_intervals"] == []
    assert [
        (
            item["display_id"],
            item["kind"],
            item["start_record_index"],
            item["end_record_index"],
            item["status"],
        )
        for item in payload["diagnostic_regions"]
    ] == [
        (1, "abnormal_transition_run", 5879, 5881, "suspicious"),
        (2, "missing_position_run", 6364, 8031, "missing"),
        (3, "missing_position_run", 9859, 10410, "missing"),
    ]
    assert payload["tracks"]["course"]["reference_only"] is True
    assert payload["tracks"]["candidate"] is None
    assert payload["tracks"]["repaired"] is None
    assert payload["repair"] is None
    assert payload["write_result"] is None
    performance = payload["activity_performance"]
    assert performance["source_label"] == "Original FIT"
    assert performance["average_pace_seconds_per_km"] is not None
    assert performance["timer_duration_seconds"] is not None
    assert performance["total_ascent_m"] is not None
    assert performance["total_descent_m"] is not None
    assert performance["split_count"] > 0
    regions = payload["diagnostic_regions"]
    assert [
        (item["from_record_index"], item["to_record_index"], item["classification"])
        for item in regions[0]["evidence"]
    ] == [
        (5879, 5880, "suspicious"),
        (5880, 5881, "suspicious"),
    ]
    assert regions[1]["map"]["geometry_ranges"] == []
    assert regions[2]["map"]["geometry_ranges"] == []
    assert len(regions[1]["map"]["bridge_points"]) == 2
    assert len(regions[2]["map"]["bridge_points"]) == 2


def _html_payload(rendered: str) -> dict[str, Any]:
    prefix = '<script id="warpbuster-report-data" type="application/json">'
    encoded = rendered.split(prefix, maxsplit=1)[1].split("</script>", maxsplit=1)[0]
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise TypeError("HTML payload must be an object")
    return payload
