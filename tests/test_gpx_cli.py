"""GPX inspect/analyze CLI integration tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.gpx_factory import GpxPoint, write_gpx_activity
from warpbuster.cli import main


def test_inspect_gpx_console_and_json(tmp_path: Path, capsys: object) -> None:
    """Inspect identifies GPX metadata without pretending FIT metadata exists."""
    gpx_path = tmp_path / "activity.GPX"
    write_gpx_activity(gpx_path, [_clean_points(3)])

    assert main(["inspect", str(gpx_path)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WarpBuster GPX inspect" in captured.out
    assert "GPX version: 1.1" in captured.out
    assert "tracks: 1; segments: 1" in captured.out
    assert "Records: 3" in captured.out

    assert main(["inspect", str(gpx_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["source"] == {
        "creator": "WarpBuster tests",
        "format": "gpx",
        "gpx_version": "1.1",
        "path": str(gpx_path),
        "segment_count": 1,
        "size_bytes": gpx_path.stat().st_size,
        "track_count": 1,
    }
    assert report["fields"]["position"] is True
    assert report["fields"]["distance"] is False


def test_analyze_clean_gpx_uses_running_profile(tmp_path: Path, capsys: object) -> None:
    """A normal GPX running activity uses the existing detector unchanged."""
    gpx_path = tmp_path / "clean.gpx"
    write_gpx_activity(gpx_path, [_clean_points(8)])

    assert main(["analyze", str(gpx_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["source"]["format"] == "gpx"
    assert report["activity"] == {"sport": "running", "sub_sport": None}
    assert report["config"]["profile"] == "running"
    assert report["status"] == "clean"


def test_analyze_gpx_writes_self_contained_html(tmp_path: Path, capsys: object) -> None:
    """The format-neutral HTML path works for GPX input through the CLI."""
    gpx_path = tmp_path / "clean.gpx"
    html_path = tmp_path / "clean-report.html"
    write_gpx_activity(gpx_path, [_clean_points(8)])

    assert main(["analyze", str(gpx_path), "--html", str(html_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    rendered = html_path.read_text(encoding="utf-8")

    assert report["source"]["format"] == "gpx"
    assert "WarpBuster activity report" in rendered
    assert '"report_kind":"analyze"' in rendered
    assert '"format":"gpx"' in rendered
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in rendered
    assert "https://unpkg.com/leaflet@1.9.4/" in rendered


def test_analyze_gpx_detects_impossible_running_teleport(tmp_path: Path, capsys: object) -> None:
    """GPX input reaches the same absolute running detector as FIT input."""
    gpx_path = tmp_path / "teleport.gpx"
    write_gpx_activity(
        gpx_path,
        [
            [
                (55.0, 37.0, "2026-01-01T08:00:00Z", None),
                (56.0, 37.0, "2026-01-01T08:00:01Z", None),
                (55.0, 37.00005, "2026-01-01T08:00:02Z", None),
            ]
        ],
    )

    assert main(["analyze", str(gpx_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["status"] == "corrupted"
    assert report["confidence"] == "high"
    assert report["summary"]["classifications"]["impossible"] == 2
    assert report["summary"]["corrupted_interval_count"] == 1


def test_analyze_does_not_bridge_separate_gpx_segments(tmp_path: Path, capsys: object) -> None:
    """A large spatial gap between explicit segments is not a teleport."""
    gpx_path = tmp_path / "segments.gpx"
    write_gpx_activity(
        gpx_path,
        [
            [
                (55.0, 37.0, "2026-01-01T08:00:00Z", None),
                (55.0, 37.00005, "2026-01-01T08:00:01Z", None),
            ],
            [
                (56.0, 38.0, "2026-01-01T08:00:02Z", None),
                (56.0, 38.00005, "2026-01-01T08:00:03Z", None),
            ],
        ],
    )

    assert main(["analyze", str(gpx_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["status"] == "clean"
    assert report["summary"]["transition_count"] == 2
    assert report["summary"]["classifications"]["impossible"] == 0


def test_island_search_does_not_pair_edges_from_different_segments(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Impossible edges in separate segments cannot form one corrupted island."""
    gpx_path = tmp_path / "separate-edges.gpx"
    write_gpx_activity(
        gpx_path,
        [
            [
                (55.0, 37.0, "2026-01-01T08:00:00Z", None),
                (56.0, 37.0, "2026-01-01T08:00:01Z", None),
            ],
            [
                (56.0, 37.0, "2026-01-01T08:00:02Z", None),
                (55.0, 37.00005, "2026-01-01T08:00:03Z", None),
            ],
        ],
    )

    assert main(["analyze", str(gpx_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["summary"]["classifications"]["impossible"] == 2
    assert report["summary"]["corrupted_interval_count"] == 0
    assert report["island_search_diagnostics"]["continuity_pruned_count"] == 1


def test_analyze_gpx_without_time_is_unknown(tmp_path: Path, capsys: object) -> None:
    """Missing GPX timestamps remain unknown rather than corrupted."""
    gpx_path = tmp_path / "without-time.gpx"
    write_gpx_activity(
        gpx_path,
        [[(55.0, 37.0, None, None), (56.0, 38.0, None, None)]],
    )

    assert main(["analyze", str(gpx_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["status"] == "unknown"
    assert report["summary"]["classifications"]["unknown"] == 1


def test_geometry_warning_is_advisory_in_json(tmp_path: Path, capsys: object) -> None:
    """A suspiciously perfect chord is reported without changing clean status."""
    gpx_path = tmp_path / "interpolated-gap.gpx"
    start = datetime(2026, 1, 1, 8, tzinfo=UTC)
    points: list[GpxPoint] = []
    for index in range(401):
        timestamp = (start + timedelta(seconds=index)).isoformat().replace("+00:00", "Z")
        points.append((55.0, 37.0 + index * 0.00005, timestamp, 100.0))
    write_gpx_activity(gpx_path, [points])

    assert main(["analyze", str(gpx_path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["status"] == "clean"
    assert report["summary"]["corrupted_interval_count"] == 0
    assert report["summary"]["geometry_warning_count"] == 1
    warning = report["geometry_warnings"][0]
    assert warning["kind"] == "possible_interpolated_gnss_gap"
    assert warning["confidence"] == "low"
    assert warning["repair_eligible"] is False
    assert warning["timestamps_available"] is True


def test_invalid_and_unsupported_activity_inputs_return_two(
    tmp_path: Path,
    capsys: object,
) -> None:
    """The generic activity reader exposes explicit CLI input errors."""
    invalid_gpx = tmp_path / "invalid.gpx"
    invalid_gpx.write_text("<gpx>", encoding="utf-8")
    unsupported = tmp_path / "activity.tcx"
    unsupported.write_text("unsupported", encoding="utf-8")

    assert main(["inspect", str(invalid_gpx)]) == 2
    assert "error:" in capsys.readouterr().err  # type: ignore[attr-defined]
    assert main(["analyze", str(unsupported)]) == 2
    assert "expected .fit or .gpx" in capsys.readouterr().err  # type: ignore[attr-defined]


def _clean_points(count: int) -> list[GpxPoint]:
    return [
        (
            55.0,
            37.0 + index * 0.00005,
            f"2026-01-01T08:00:{index:02d}Z",
            100.0 + index,
        )
        for index in range(count)
    ]
