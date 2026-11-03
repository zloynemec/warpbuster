"""Trail Companion CSV and conservative per-observation trust policy."""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from warpbuster.cli import main
from warpbuster.config import GpsAnalysisConfig, IntegrityConfig
from warpbuster.gps import GpsReadError, analyze_gps, gps_json, read_locations, trusted_gpx
from warpbuster.models.integrity import IntegrityConfidence

HEADER = (
    "seq",
    "fix_timestamp_utc",
    "lat",
    "lon",
    "horizontal_accuracy_m",
    "speed_mps",
    "speed_accuracy_mps",
    "course_deg",
    "course_accuracy_deg",
)


def write_locations(tmp_path: Path, rows: list[list[str]]) -> Path:
    path = tmp_path / "locations.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def row(seq: int, seconds: int, lon: str = "33.670000") -> list[str]:
    return [
        str(seq),
        f"2026-11-03T05:00:{seconds:02d}.000Z",
        "44.541000",
        lon,
        "4",
        "2.8",
        "0.8",
        "90",
        "20",
    ]


def test_reader_orders_by_seq_and_keeps_timestamp_and_unavailable_values(tmp_path: Path) -> None:
    first, second = row(10, 2), row(20, 0)
    first[4:] = ["-1"] * 5
    data = read_locations(write_locations(tmp_path, [second, first]))
    assert [point.seq for point in data.observations] == [10, 20]
    assert data.observations[0].horizontal_accuracy_m is None
    assert data.activity.records[0].speed is None
    assert data.activity.records[0].source.message_index == 10
    assert data.activity.records[0].timestamp > data.activity.records[1].timestamp


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        (0, "x", "seq"),
        (1, "", "fix_timestamp_utc"),
        (1, "2026-11-03T05:00:00", "UTC"),
        (2, "91", "lat"),
        (3, "nan", "lon"),
        (4, "inf", "horizontal_accuracy_m"),
        (5, "abc", "speed_mps"),
        (7, "360", "course_deg"),
    ],
)
def test_invalid_input_is_explicit(
    tmp_path: Path,
    column: int,
    value: str,
    message: str,
) -> None:
    point = row(1, 0)
    point[column] = value
    with pytest.raises(GpsReadError, match=message):
        read_locations(write_locations(tmp_path, [point]))


def test_duplicate_seq_and_empty_input(tmp_path: Path) -> None:
    with pytest.raises(GpsReadError, match="duplicate seq"):
        read_locations(write_locations(tmp_path, [row(1, 0), row(1, 2)]))
    with pytest.raises(GpsReadError, match="no observations"):
        read_locations(write_locations(tmp_path, []))


def test_extra_columns_are_preserved_without_driving_classification(tmp_path: Path) -> None:
    path = write_locations(tmp_path, [row(1, 0)])
    text = path.read_text().splitlines()
    path.write_text(text[0] + ",quality_flags,extra\n" + text[1] + ",bad,something\n")
    data = read_locations(path)
    assert data.observations[0].quality_flags == "bad"
    assert data.activity.preservation.raw_bytes == path.read_bytes()


def test_missing_header_and_malformed_row(tmp_path: Path) -> None:
    path = tmp_path / "locations.csv"
    path.write_text("seq,lat\n1,44\n")
    with pytest.raises(GpsReadError, match="missing columns"):
        read_locations(path)
    path = write_locations(tmp_path, [row(1, 0)[:-1]])
    with pytest.raises(GpsReadError, match="column count"):
        read_locations(path)


def teleport_rows() -> list[list[str]]:
    rows = [row(i + 1, i * 2, f"{33.67 + i * 0.00007:.6f}") for i in range(5)]
    rows[2][2:5] = ["55.755800", "37.617300", "300"]
    rows[2][5:] = ["-1"] * 4
    return rows


def test_teleport_only_rejects_proven_island_and_is_deterministic(tmp_path: Path) -> None:
    source = write_locations(tmp_path, teleport_rows())
    result = analyze_gps(source)
    assert [point.seq for point in result.points] == [1, 2, 3, 4, 5]
    assert [point.status for point in result.points] == [
        "trusted",
        "trusted",
        "rejected",
        "trusted",
        "trusted",
    ]
    assert "plausible_bridge" in result.points[2].reasons
    assert result.integrity.corrupted_intervals[0].bridge is not None
    assert result.points[2].transitions[0].apparent_speed_mps > 1000
    assert result == analyze_gps(source)


@pytest.mark.parametrize(
    "longitudes",
    [
        ["33.670000", "33.670000", "33.670000"],  # stop
        ["33.670000", "33.670070", "33.670000"],  # turn
    ],
)
def test_stops_turns_and_unavailable_speed_are_trusted(
    tmp_path: Path,
    longitudes: list[str],
) -> None:
    rows = [row(i + 1, i * 2, lon) for i, lon in enumerate(longitudes)]
    for point in rows:
        point[5:] = ["-1"] * 4
    result = analyze_gps(write_locations(tmp_path, rows))
    assert all(point.status == "trusted" for point in result.points)


@pytest.mark.parametrize("accuracy", ["-1", "", "300"])
def test_bad_accuracy_is_uncertain_not_rejected(tmp_path: Path, accuracy: str) -> None:
    rows = [row(1, 0), row(2, 2), row(3, 4)]
    rows[1][4] = accuracy
    result = analyze_gps(write_locations(tmp_path, rows))
    assert result.points[1].status == "uncertain"
    assert result.points[0].status == result.points[2].status == "trusted"


def test_lone_point_and_unresolved_jump_are_uncertain(tmp_path: Path) -> None:
    assert analyze_gps(write_locations(tmp_path, [row(1, 0)])).points[0].status == "uncertain"
    rows = [row(1, 0), row(2, 2, "37.617300")]
    result = analyze_gps(write_locations(tmp_path, rows))
    assert all(point.status == "uncertain" for point in result.points)


def test_bad_time_and_long_gap_are_not_positive_trust_evidence(tmp_path: Path) -> None:
    rows = [row(1, 2), row(2, 0), row(3, 4)]
    result = analyze_gps(write_locations(tmp_path, rows))
    assert [point.status for point in result.points] == ["uncertain", "uncertain", "trusted"]
    assert "non_positive_time_delta" in result.points[0].reasons
    rows[1][1] = "2026-11-03T05:02:31.000Z"
    result = analyze_gps(write_locations(tmp_path, rows[:2]))
    assert all(point.status == "uncertain" for point in result.points)


def test_generic_profile_leaves_teleport_uncertain(tmp_path: Path) -> None:
    result = analyze_gps(
        write_locations(tmp_path, teleport_rows()),
        GpsAnalysisConfig(detector=IntegrityConfig()),
    )
    assert result.points[2].status == "uncertain"
    assert not any(point.status == "rejected" for point in result.points)


def test_policy_defaults_and_threshold_boundaries(tmp_path: Path) -> None:
    config = GpsAnalysisConfig()
    assert config.max_horizontal_accuracy_m == 25
    assert config.max_gap_seconds == 60
    assert config.detector == IntegrityConfig.running()
    rows = [row(1, 0), row(2, 0)]
    rows[1][1] = "2026-11-03T05:01:00Z"
    for point in rows:
        point[4] = "25"
    path = write_locations(tmp_path, rows)
    assert all(point.status == "trusted" for point in analyze_gps(path).points)
    for config in (
        GpsAnalysisConfig(max_horizontal_accuracy_m=24),
        GpsAnalysisConfig(max_gap_seconds=59),
    ):
        assert all(point.status == "uncertain" for point in analyze_gps(path, config).points)
    for value in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            GpsAnalysisConfig(max_horizontal_accuracy_m=value)
        with pytest.raises(ValueError):
            GpsAnalysisConfig(max_gap_seconds=value)


def test_gpx_preserves_source_values_and_breaks_at_rejected_or_uncertain(tmp_path: Path) -> None:
    rows = teleport_rows()
    path = write_locations(tmp_path, rows)
    result = analyze_gps(path)
    document = ElementTree.fromstring(trusted_gpx(result))
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    segments = document.findall("g:trk/g:trkseg", ns)
    assert [len(segment) for segment in segments] == [2, 2]
    points = document.findall(".//g:trkpt", ns)
    for point, source in zip(points, [rows[i] for i in (0, 1, 3, 4)], strict=True):
        assert point.attrib == {"lat": source[2], "lon": source[3]}
        assert point.findtext("g:time", namespaces=ns) == source[1]
    rows[2] = row(3, 4)
    rows[2][4] = "300"
    result = analyze_gps(write_locations(tmp_path, rows))
    assert result.points[2].status == "uncertain"
    document = ElementTree.fromstring(trusted_gpx(result))
    assert [len(segment) for segment in document.findall(".//g:trkseg", ns)] == [2, 2]


def test_long_gap_splits_supported_points_into_segments(tmp_path: Path) -> None:
    rows = [row(i + 1, i * 2) for i in range(4)]
    rows[2][1] = "2026-11-03T05:02:31.000Z"
    rows[3][1] = "2026-11-03T05:02:33.000Z"
    result = analyze_gps(write_locations(tmp_path, rows))
    assert all(point.status == "trusted" for point in result.points)
    document = ElementTree.fromstring(trusted_gpx(result))
    assert [len(segment) for segment in document.findall(".//{*}trkseg")] == [2, 2]


def test_json_has_source_seq_evidence_policy_and_empty_track_notice(tmp_path: Path) -> None:
    result = analyze_gps(write_locations(tmp_path, teleport_rows()))
    report = json.loads(gps_json(result))
    assert report["policy"]["detector"]["profile"] == "running"
    assert report["summary"] == {"trusted": 4, "rejected": 1, "uncertain": 0}
    assert report["points"][2]["seq"] == 3
    assert report["points"][2]["transitions"][0]["from_seq"] == 2
    assert report["points"][2]["transitions"][0]["elapsed_seconds"] == 2
    assert report["detector"]["corrupted_intervals"]
    empty = analyze_gps(write_locations(tmp_path, [row(1, 0)]))
    assert json.loads(gps_json(empty))["track_status"] == "no_trusted_points"
    assert not ElementTree.fromstring(trusted_gpx(empty)).findall(".//{*}trkpt")


def test_cli_writes_the_api_result_and_does_not_overwrite(tmp_path: Path) -> None:
    source = write_locations(tmp_path, teleport_rows())
    original = source.read_bytes()
    report, gpx = tmp_path / "analysis.json", tmp_path / "trusted.gpx"
    args = ["analyze-gps", str(source), "--json", str(report), "--gpx", str(gpx)]
    assert main(args) == 0
    assert report.read_text() == gps_json(analyze_gps(source))
    assert gpx.read_text() == trusted_gpx(analyze_gps(source))
    report.write_text("do not overwrite")
    gpx.unlink()
    assert main(args) == 2
    assert report.read_text() == "do not overwrite"
    assert not gpx.exists()
    assert main([*args, "--overwrite"]) == 0
    assert source.read_bytes() == original
    assert main([*args, "--profile", "generic", "--overwrite"]) == 0
    assert json.loads(report.read_text())["policy"]["detector"]["profile"] == "generic"


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_cli_never_overwrites_input_even_with_permission(tmp_path: Path, alias: str) -> None:
    source = write_locations(tmp_path, teleport_rows())
    original = source.read_bytes()
    target = source
    if alias != "same":
        target = tmp_path / "alias.json"
        if alias == "symlink":
            target.symlink_to(source)
        else:
            target.hardlink_to(source)
    assert (
        main(
            [
                "analyze-gps",
                str(source),
                "--json",
                str(target),
                "--gpx",
                str(tmp_path / "trusted.gpx"),
                "--overwrite",
            ]
        )
        == 2
    )
    assert source.read_bytes() == original


def test_cli_rejects_colliding_outputs(tmp_path: Path) -> None:
    source = write_locations(tmp_path, teleport_rows())
    target = tmp_path / "output"
    args = ["analyze-gps", str(source), "--json", str(target), "--gpx", str(target)]
    assert main(args) == 2
    assert not target.exists()


def test_medium_interval_remains_uncertain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_locations(tmp_path, teleport_rows())
    original = analyze_gps(path).integrity
    medium = replace(original.corrupted_intervals[0], confidence=IntegrityConfidence.MEDIUM)
    report = replace(original, corrupted_intervals=(medium,))
    monkeypatch.setattr("warpbuster.gps.analysis.analyze_integrity", lambda *args: report)
    result = analyze_gps(path)
    assert result.points[2].status == "uncertain"
    assert "insufficient_interval_confidence" in result.points[2].reasons
    assert not any(point.status == "rejected" for point in result.points)


def test_quality_flags_do_not_override_wb_decisions(tmp_path: Path) -> None:
    path = write_locations(tmp_path, [row(1, 0), row(2, 2)])
    lines = path.read_text().splitlines()
    path.write_text(
        lines[0]
        + ",quality_flags\n"
        + "\n".join(line + ",recorder_says_bad" for line in lines[1:])
        + "\n"
    )
    assert all(point.status == "trusted" for point in analyze_gps(path).points)


def test_gpx_exports_scientific_coordinates_as_exact_decimals(tmp_path: Path) -> None:
    rows = [row(1, 0), row(2, 2)]
    for point in rows:
        point[3] = "3.367000012345678901234e1"
    result = analyze_gps(write_locations(tmp_path, rows))
    points = ElementTree.fromstring(trusted_gpx(result)).findall(".//{*}trkpt")
    assert [point.attrib["lon"] for point in points] == ["33.67000012345678901234"] * 2
    rows[0][3] = "1e-999999999"
    with pytest.raises(GpsReadError, match="lon"):
        analyze_gps(write_locations(tmp_path, rows))


def test_cli_staging_failure_leaves_no_artifacts(tmp_path: Path) -> None:
    path = write_locations(tmp_path, teleport_rows())
    report = tmp_path / "analysis.json"
    assert (
        main(
            [
                "analyze-gps",
                str(path),
                "--json",
                str(report),
                "--gpx",
                str(tmp_path / "missing" / "trusted.gpx"),
            ]
        )
        == 2
    )
    assert not report.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_cli_rejects_bad_input_before_writing(tmp_path: Path) -> None:
    source = tmp_path / "locations.csv"
    target = tmp_path / "analysis.json"
    source.write_text("invalid")
    assert (
        main(
            [
                "analyze-gps",
                str(source),
                "--json",
                str(target),
                "--gpx",
                str(tmp_path / "trusted.gpx"),
            ]
        )
        == 2
    )
    assert not target.exists()
