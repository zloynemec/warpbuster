"""Interactive HTML report renderer tests."""

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any

import pytest

from tests.activity_factory import eastward_observations, make_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.config import IntegrityConfig
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.report.html import (
    HtmlReportError,
    _repaired_performance,
    write_analyze_html,
)

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
    assert 'id="one-sided-clusters"' in first
    assert 'id="composite-regions"' in first
    assert 'addOverlay("Composite tainted"' in first
    assert "component.state === state" in first
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
    assert payload["activity_performance"]["source_label"] == "Original FIT"
    assert payload["activity_performance"]["timer_duration_seconds"] == 2.0
    assert payload["activity_performance"]["average_pace_seconds_per_km"] is None
    assert payload["activity_performance"]["split_count"] == 0
    assert payload["repaired_performance"] is None


def test_numbered_regions_group_adjacent_transitions_and_keep_missing_separate(
    tmp_path: Path,
) -> None:
    """Presentation grouping preserves detector evidence and independent missing runs."""
    observations = eastward_observations(
        [0, 1, 2, 3, 4, 6],
        [0, 5, 35, 65, 70, 75],
    )
    observations.insert(5, (5, None, None))
    activity = make_activity(observations)
    config = replace(IntegrityConfig.running(), minimum_baseline_samples=100)
    integrity = analyze_integrity(activity, config)
    output_path = tmp_path / "regions.html"

    write_analyze_html(activity, integrity, output_path)

    rendered = output_path.read_text(encoding="utf-8")
    payload = _embedded_payload(rendered)
    regions = payload["diagnostic_regions"]
    assert [(item["display_id"], item["kind"]) for item in regions] == [
        (1, "abnormal_transition_run"),
        (2, "missing_position_run"),
    ]
    transition_region = regions[0]
    assert (transition_region["start_record_index"], transition_region["end_record_index"]) == (
        1,
        3,
    )
    assert transition_region["repair_eligible"] is False
    assert [item["classification"] for item in transition_region["evidence"]] == [
        "suspicious",
        "suspicious",
    ]
    assert transition_region["map"]["geometry_ranges"] == [[1, 3]]
    missing_region = regions[1]
    assert (missing_region["start_record_index"], missing_region["end_record_index"]) == (5, 5)
    assert missing_region["status"] == "missing"
    assert missing_region["map"]["geometry_ranges"] == []
    assert len(missing_region["map"]["bridge_points"]) == 2
    assert 'overlayLayers["Numbered detector regions"]' in rendered
    assert 'label: "Suspicious / impossible transitions"' in rendered
    assert 'label: "One-sided diagnostics"' in rendered
    assert 'label: "Geometry warnings"' in rendered
    assert 'label: "Vertical warnings"' in rendered


def test_interval_and_one_sided_regions_keep_evidence_without_duplicate_numbers(
    tmp_path: Path,
) -> None:
    """Covered abnormal edges stay inside their authoritative top-level diagnostic."""
    spike = make_activity(
        [
            (0, 55.0, 37.0),
            (1, 55.0, 37.00005),
            (2, 55.01, 37.00005),
            (3, 55.0, 37.00010),
            (4, 55.0, 37.00015),
            (5, 55.0, 37.00020),
            (6, 55.0, 37.00025),
        ]
    )
    spike_path = tmp_path / "spike.html"
    write_analyze_html(spike, analyze_integrity(spike), spike_path)

    spike_regions = _embedded_payload(spike_path.read_text(encoding="utf-8"))["diagnostic_regions"]
    assert [region["kind"] for region in spike_regions] == ["corrupted_interval"]
    assert [item["classification"] for item in spike_regions[0]["evidence"]] == [
        "impossible",
        "impossible",
    ]

    observations = eastward_observations(
        [float(index) for index in range(6)],
        [float(index * 3) for index in range(6)],
    )
    observations.extend(eastward_observations([6.0, 7.0], [85.0, 115.0]))
    observations.extend([(8.0, None, None), (9.0, None, None)])
    observations.extend(eastward_observations([10.0, 11.0, 12.0], [90.0, 93.0, 96.0]))
    observations.extend((float(index), None, None) for index in range(13, 18))
    observations.extend(
        eastward_observations(
            [float(index) for index in range(18, 23)],
            [float(index * 3) for index in range(18, 23)],
        )
    )
    one_sided = make_activity(observations)
    config = replace(
        IntegrityConfig.running(),
        one_sided_search_max_records=64,
        one_sided_max_clean_gap_records=5,
        one_sided_anchor_min_normal_transitions=3,
        one_sided_anchor_scan_max_records=5,
    )
    one_sided_path = tmp_path / "one-sided.html"
    write_analyze_html(
        one_sided,
        analyze_integrity(one_sided, config),
        one_sided_path,
    )

    one_sided_regions = _embedded_payload(one_sided_path.read_text(encoding="utf-8"))[
        "diagnostic_regions"
    ]
    assert [region["kind"] for region in one_sided_regions] == [
        "one_sided_diagnostic",
        "missing_position_run",
        "missing_position_run",
    ]
    assert [item["entity"] for item in one_sided_regions[0]["evidence"]] == [
        "one_sided_cluster",
        "transition",
        "transition",
    ]
    assert one_sided_regions[0]["overlaps_display_ids"] == [2, 3]
    assert one_sided_regions[1]["overlaps_display_ids"] == [1]
    assert one_sided_regions[2]["overlaps_display_ids"] == [1]


def test_geometry_and_vertical_warnings_are_numbered_advisory_regions(
    tmp_path: Path,
) -> None:
    """Advisory detector outputs are mappable but never become repair authority."""
    geometry_activity = make_activity(
        eastward_observations(
            [float(index) for index in range(401)],
            [float(index * 5) for index in range(401)],
        )
    )
    geometry_path = tmp_path / "geometry.html"
    write_analyze_html(
        geometry_activity,
        analyze_integrity(geometry_activity),
        geometry_path,
    )
    geometry_regions = _embedded_payload(geometry_path.read_text(encoding="utf-8"))[
        "diagnostic_regions"
    ]
    assert [region["kind"] for region in geometry_regions] == ["geometry_warning"]
    assert geometry_regions[0]["repair_eligible"] is False
    assert geometry_regions[0]["map"]["mappable"] is True

    vertical_activity = make_activity(
        eastward_observations(
            [float(index) for index in range(6)],
            [float(index * 2) for index in range(6)],
        )
    )
    altitudes = [100.0, 100.0, 105.0, 110.0, 115.0, 115.0]
    vertical_activity = replace(
        vertical_activity,
        records=tuple(
            replace(record, altitude=altitudes[record.index])
            for record in vertical_activity.records
        ),
    )
    vertical_path = tmp_path / "vertical.html"
    write_analyze_html(
        vertical_activity,
        analyze_integrity(vertical_activity),
        vertical_path,
    )
    vertical_regions = _embedded_payload(vertical_path.read_text(encoding="utf-8"))[
        "diagnostic_regions"
    ]
    assert [region["kind"] for region in vertical_regions] == ["vertical_warning"]
    assert vertical_regions[0]["repair_eligible"] is False
    assert vertical_regions[0]["map"]["mappable"] is True


def test_twenty_thousand_record_analysis_html_stays_bounded(tmp_path: Path) -> None:
    """Report projection remains practical for the documented MVP activity size."""
    count = 20_000
    activity = make_activity(
        eastward_observations(
            [float(index) for index in range(count)],
            [float(index * 3) for index in range(count)],
        )
    )
    output_path = tmp_path / "large.html"

    started = perf_counter()
    write_analyze_html(activity, analyze_integrity(activity), output_path)
    elapsed = perf_counter() - started

    assert elapsed < 5.0
    assert output_path.stat().st_size < 5 * 1024 * 1024


def test_reference_course_is_display_only_and_does_not_change_detector_payload(
    tmp_path: Path,
) -> None:
    """The optional GPX is serialized only as a reference and metrics row."""
    activity = make_activity(eastward_observations([0, 1, 2, 3], [0, 5, 10, 15]))
    integrity = analyze_integrity(activity)
    course_path = tmp_path / "reference.gpx"
    write_gpx_activity(
        course_path,
        [
            [
                (55.0, 37.0, None, 100.0),
                (55.0, 37.001, None, 110.0),
            ]
        ],
    )
    without_course_path = tmp_path / "without-course.html"
    with_course_path = tmp_path / "with-course.html"

    write_analyze_html(activity, integrity, without_course_path)
    write_analyze_html(
        activity,
        integrity,
        with_course_path,
        course=read_gpx_course(course_path),
    )

    without_course = _embedded_payload(without_course_path.read_text(encoding="utf-8"))
    with_course = _embedded_payload(with_course_path.read_text(encoding="utf-8"))
    assert with_course["analysis"] == without_course["analysis"]
    assert with_course["diagnostic_regions"] == without_course["diagnostic_regions"]
    assert without_course["tracks"]["course"] is None
    course = with_course["tracks"]["course"]
    assert course["source_path"] == str(course_path)
    assert course["source_name"] == "reference.gpx"
    assert course["segment_count"] == 1
    assert course["point_count"] == 2
    assert course["reference_only"] is True
    assert with_course["tracks"]["candidate"] is None
    assert with_course["tracks"]["repaired"] is None
    assert with_course["repair"] is None
    assert with_course["write_result"] is None
    assert [row["id"] for row in with_course["metrics_comparison"]["rows"]] == [
        "original",
        "course",
    ]


def test_numbered_missing_region_without_spatial_anchor_is_not_mappable(
    tmp_path: Path,
) -> None:
    """A complete table row remains available when a region has no map position."""
    activity = make_activity([(0, None, None), (1, None, None)])
    output_path = tmp_path / "unmappable.html"

    write_analyze_html(activity, analyze_integrity(activity), output_path)

    regions = _embedded_payload(output_path.read_text(encoding="utf-8"))["diagnostic_regions"]
    assert len(regions) == 1
    assert regions[0]["kind"] == "missing_position_run"
    assert regions[0]["map"] == {
        "bounds": None,
        "bridge_points": [],
        "geometry_ranges": [],
        "mappable": False,
        "marker": None,
        "marker_record_index": None,
    }


def test_repaired_performance_reports_pace_and_kilometre_ascent_descent() -> None:
    """A hill inside one kilometre contributes to both ascent and descent bars."""
    activity = make_activity(
        [
            (0.0, 55.0, 37.0),
            (150.0, 55.0, 37.005),
            (300.0, 55.0, 37.01),
            (450.0, 55.0, 37.015),
            (600.0, 55.0, 37.02),
            (900.0, 55.0, 37.025),
        ]
    )
    distances = (0.0, 500.0, 1_000.0, 1_500.0, 2_000.0, 2_500.0)
    altitudes = (100.0, 400.0, 100.0, 200.0, 100.0, 80.0)
    activity = replace(
        activity,
        records=tuple(
            replace(record, distance=distances[index], altitude=altitudes[index])
            for index, record in enumerate(activity.records)
        ),
        duration_seconds=900.0,
        recorded_distance_m=2_500.0,
    )

    performance = _repaired_performance(activity)

    assert performance["average_pace_seconds_per_km"] == pytest.approx(360.0)
    assert performance["timer_duration_seconds"] == pytest.approx(900.0)
    assert performance["timer_source"] == "record timestamp elapsed time"
    assert performance["total_ascent_m"] == pytest.approx(400.0)
    assert performance["total_descent_m"] == pytest.approx(420.0)
    assert performance["split_ascent_total_m"] == pytest.approx(400.0)
    assert performance["split_descent_total_m"] == pytest.approx(420.0)
    splits = performance["splits"]
    assert isinstance(splits, list)
    assert [split["distance_m"] for split in splits] == [1_000.0, 1_000.0, 500.0]
    assert [split["pace_seconds_per_km"] for split in splits] == [300.0, 300.0, 600.0]
    assert [split["ascent_m"] for split in splits] == [300.0, 100.0, 0.0]
    assert [split["descent_m"] for split in splits] == [300.0, 100.0, 20.0]
    assert [split["complete_kilometre"] for split in splits] == [True, True, False]


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

    write_analyze_html(activity, integrity, output_path, overwrite=True)
    assert _DATA_PREFIX in output_path.read_text(encoding="utf-8")

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
