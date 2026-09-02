"""Course-based repair dry-run CLI integration tests."""

import json
from pathlib import Path
from typing import Any

import pytest

from tests.activity_factory import eastward_observations
from tests.fit_factory import write_repairable_activity, write_trajectory_activity
from tests.gpx_factory import GpxPoint, write_gpx_activity
from warpbuster.cli import build_parser, main
from warpbuster.models.integrity import IntegrityConfidence


def test_repair_dry_run_json_builds_plan_without_output(
    tmp_path: Path,
    capsys: object,
) -> None:
    """A unique course match produces explicit updates but no modified FIT."""
    fit_path, course_path = _repairable_fixture(tmp_path)

    assert main(["repair", str(fit_path), "--course", str(course_path), "--dry-run", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert report["scope"] == "course_reconstruction_dry_run"
    assert report["status"] == "ready"
    assert report["confidence"] == "high"
    assert report["repair_eligible"] is True
    assert report["output_written"] is False
    assert report["safety"] == {
        "detection_used_course": False,
        "timestamps_unchanged": True,
        "trusted_records_unchanged": True,
    }
    assert report["summary"] == {
        "candidate_coordinate_update_count": 1,
        "detected_interval_count": 1,
        "eligible_interval_count": 1,
        "missing_completion_candidate_count": 0,
        "missing_completion_enabled": False,
        "planned_interval_count": 1,
        "selected_coordinate_update_count": 1,
        "unresolved_interval_count": 0,
    }
    selection = report["selection"]
    assert selection["application_status"] == "full"
    assert selection["minimum_confidence"] == "high"
    assert selection["applied_interval_count"] == 1
    assert selection["skipped_interval_count"] == 0
    decision = selection["intervals"][0]
    assert decision["start_record_index"] == 16
    assert decision["end_record_index"] == 16
    assert decision["confidence"] == "high"
    assert decision["action"] == "applied"
    assert decision["candidate_available"] is True
    assert decision["coordinate_update_count"] == 1
    assert decision["selection_reasons"] == ["confidence_at_or_above_threshold"]
    interval = report["interval_plans"][0]
    assert interval["fields_to_change"] == ["position_lat", "position_long"]
    assert interval["repair_eligible"] is True
    assert interval["coordinate_updates"][0]["record_index"] == 16
    assert interval["coordinate_updates"][0]["timestamp"] == "2026-01-01T08:00:16+00:00"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["course.gpx", "original.fit"]


def test_repair_writes_default_output_and_refuses_overwrite(
    tmp_path: Path,
    capsys: object,
) -> None:
    """M6 writes a validated default output once and never overwrites it."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    output_path = tmp_path / "original.fixed.fit"

    assert main(["repair", str(fit_path), "--course", str(course_path)]) == 0
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Status: WRITTEN" in output
    assert "Application: FULL (minimum=HIGH, applied=1, skipped=0)" in output
    assert "records 16..16: APPLIED" in output
    assert output_path.exists()

    original_output = output_path.read_bytes()
    assert main(["repair", str(fit_path), "--course", str(course_path)]) == 3
    assert "output already exists" in capsys.readouterr().err  # type: ignore[attr-defined]
    assert output_path.read_bytes() == original_output


def test_repair_overwrite_atomically_replaces_fit_and_html(
    tmp_path: Path,
    capsys: object,
) -> None:
    """The explicit flag replaces both outputs while preserving the source FIT."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    source_bytes = fit_path.read_bytes()
    output_path = tmp_path / "original.fixed.fit"
    html_path = tmp_path / "repair.html"
    output_path.write_bytes(b"stale fit")
    html_path.write_text("stale html", encoding="utf-8")

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--html",
                str(html_path),
                "--overwrite",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "Status: WRITTEN" in output
    assert output_path.read_bytes() != b"stale fit"
    assert '<script id="warpbuster-report-data"' in html_path.read_text(encoding="utf-8")
    assert fit_path.read_bytes() == source_bytes


def test_repair_rejects_non_fit_activity_and_invalid_course(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Repair input roles remain explicit and invalid files return input error."""
    activity_gpx = tmp_path / "activity.gpx"
    write_gpx_activity(
        activity_gpx,
        [[(55.0, 37.0, None, None), (55.0, 37.001, None, None)]],
    )
    invalid_course = tmp_path / "invalid.gpx"
    invalid_course.write_text("<gpx>", encoding="utf-8")
    fit_path, course_path = _repairable_fixture(tmp_path)

    assert (
        main(
            [
                "repair",
                str(activity_gpx),
                "--course",
                str(course_path),
                "--dry-run",
            ]
        )
        == 2
    )
    assert "original FIT" in capsys.readouterr().err  # type: ignore[attr-defined]
    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(invalid_course),
                "--dry-run",
            ]
        )
        == 2
    )
    assert "cannot decode GPX course" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_repair_verbose_console_explains_matching_and_safety(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Human output exposes the decision without dumping every coordinate."""
    fit_path, course_path = _repairable_fixture(tmp_path)

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "-v",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "WarpBuster repair dry-run" in output
    assert "Status: READY" in output
    assert "Output written: no" in output
    assert "Safety: timestamps unchanged" in output
    assert "Matching thresholds:" in output


def test_repair_json_writes_explicit_output(tmp_path: Path, capsys: object) -> None:
    """Machine-readable write mode reports the chosen output and validation result."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    output_path = tmp_path / "chosen.fit"

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--output",
                str(output_path),
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["scope"] == "fit_repair_write"
    assert report["output_path"] == str(output_path)
    assert report["output_written"] is True
    assert report["validation"]["valid"] is True
    assert report["diff"]["unexpected_changed_field_count"] == 0
    assert report["selection"]["minimum_confidence"] == "high"
    assert report["selection"]["intervals"][0]["action"] == "applied"


def test_repair_minimum_confidence_argument_is_case_insensitive() -> None:
    """The CLI exposes LOW, MEDIUM, and HIGH without making casing significant."""
    args = build_parser().parse_args(
        [
            "repair",
            "original.fit",
            "--course",
            "course.gpx",
            "--min-confidence",
            "MeDiUm",
        ]
    )

    assert args.min_confidence is IntegrityConfidence.MEDIUM


def test_fill_missing_from_course_is_explicit_and_requires_medium(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Endpoint completion is disabled by default and remains an explicit MEDIUM opt-in."""
    fit_path, course_path = _missing_endpoint_fixture(tmp_path)

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    disabled = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert disabled["status"] == "not_needed"
    assert disabled["summary"]["missing_completion_enabled"] is False
    assert disabled["interval_plans"] == []

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--fill-missing-from-course",
                "--dry-run",
                "--min-confidence",
                "medium",
                "--json",
            ]
        )
        == 0
    )
    enabled = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert enabled["status"] == "partial"
    assert enabled["summary"]["missing_completion_enabled"] is True
    assert enabled["summary"]["missing_completion_candidate_count"] == 2
    assert [item["missing_run_kind"] for item in enabled["interval_plans"]] == [
        "prefix",
        "suffix",
    ]
    assert all(item["preserve_recorded_distance"] for item in enabled["interval_plans"])


def test_repair_dry_run_writes_candidate_html_report(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Dry-run HTML contains the course, candidate geometry, and repair decisions."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    html_path = tmp_path / "preview.html"

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "--html",
                str(html_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    rendered = html_path.read_text(encoding="utf-8")
    assert f"HTML report: {html_path}" in output
    assert '"report_kind":"repair_dry_run"' in rendered
    assert '"action":"applied"' in rendered
    assert '"candidate":{"record_count":33' in rendered
    assert '"course":{"point_count":33' in rendered
    assert '"write_result":null' in rendered

    html_path.write_text("stale preview", encoding="utf-8")
    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "--html",
                str(html_path),
                "--overwrite",
            ]
        )
        == 0
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    assert '"report_kind":"repair_dry_run"' in html_path.read_text(encoding="utf-8")


def test_repair_html_without_path_uses_source_based_default(
    tmp_path: Path,
    capsys: object,
) -> None:
    """A bare repair --html derives its report path from the original FIT."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    default_html_path = tmp_path / "original.repair.html"

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "--html",
            ]
        )
        == 0
    )
    assert f"HTML report: {default_html_path}" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert '"report_kind":"repair_dry_run"' in default_html_path.read_text(encoding="utf-8")

    default_html_path.write_text("stale report", encoding="utf-8")
    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--dry-run",
                "--html",
                "--overwrite",
            ]
        )
        == 0
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    assert '"report_kind":"repair_dry_run"' in default_html_path.read_text(encoding="utf-8")

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--html",
                "--overwrite",
            ]
        )
        == 0
    )
    capsys.readouterr()  # type: ignore[attr-defined]
    assert (tmp_path / "original.fixed.fit").exists()
    assert '"report_kind":"repair_write"' in default_html_path.read_text(encoding="utf-8")


def test_repair_write_html_contains_actual_track_and_diff(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Write-mode HTML is built from the validated FIT and keeps JSON stdout valid."""
    fit_path, course_path = _repairable_fixture(tmp_path, with_elevation=True)
    fixed_path = tmp_path / "fixed.fit"
    html_path = tmp_path / "written.html"

    assert (
        main(
            [
                "repair",
                str(fit_path),
                "--course",
                str(course_path),
                "--output",
                str(fixed_path),
                "--json",
                "--html",
                str(html_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    rendered = html_path.read_text(encoding="utf-8")
    assert report["output_path"] == str(fixed_path)
    assert fixed_path.exists()
    assert '"report_kind":"repair_write"' in rendered
    assert '"repaired":{"record_count":33' in rendered
    assert '"write_result":{"bytes_written"' in rendered
    assert '"unexpected_changed_field_count":0' in rendered
    payload = _html_payload(rendered)
    comparison_rows = payload["metrics_comparison"]["rows"]
    assert [row["id"] for row in comparison_rows] == ["original", "course", "repaired"]
    assert comparison_rows[1]["elevation_gain_m"] == 32.0
    assert comparison_rows[1]["elevation_gain_source"] == (
        "GPX positive elevation deltas (unsmoothed)"
    )
    performance = payload["repaired_performance"]
    assert payload["activity_performance"] is None
    assert performance["source_label"] == "Repaired FIT"
    assert performance["average_pace_seconds_per_km"] == pytest.approx(166.67, abs=0.1)
    assert performance["timer_duration_seconds"] == 32.0
    assert performance["timer_source"] == "FIT session.total_timer_time"
    assert performance["total_ascent_m"] == 16.0
    assert performance["total_descent_m"] == 16.0
    assert performance["split_ascent_total_m"] == 16.0
    assert performance["split_descent_total_m"] == 16.0
    assert len(performance["splits"]) == 1
    assert performance["splits"][0]["complete_kilometre"] is False
    assert performance["splits"][0]["ascent_m"] == 16.0
    assert performance["splits"][0]["descent_m"] == 16.0
    assert 'id="split-pace-chart"' in rendered
    assert 'id="split-elevation-chart"' in rendered
    assert '["Time", clockDuration(repairedPerformance.timer_duration_seconds)]' in rendered
    assert '["Total ascent", metres(repairedPerformance.total_ascent_m)]' in rendered
    assert '["Total descent", metres(repairedPerformance.total_descent_m)]' in rendered
    assert "Kilometre bars ascent sum" not in rendered
    assert "Kilometre bars descent sum" not in rendered
    assert "const hours = Math.floor(rounded / 3600);" in rendered
    assert payload["missing_position_runs"] == []


def test_validate_and_diff_cli_report_success_and_unexpected_changes(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Validation and diff use stable exit codes for safe and unsafe files."""
    fit_path, course_path = _repairable_fixture(tmp_path)
    assert main(["repair", str(fit_path), "--course", str(course_path)]) == 0
    capsys.readouterr()  # type: ignore[attr-defined]
    fixed_path = tmp_path / "original.fixed.fit"

    assert main(["validate", str(fixed_path)]) == 0
    assert "Status: VALID" in capsys.readouterr().out  # type: ignore[attr-defined]
    assert main(["diff", str(fit_path), str(fixed_path)]) == 0
    diff_output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "unexpected=0" in diff_output
    assert "timestamps=100.00%" in diff_output

    changed_source = tmp_path / "sensor-original.fit"
    changed_fixed = tmp_path / "sensor-changed.fit"
    write_repairable_activity(changed_source)
    write_repairable_activity(changed_fixed, heart_rate_offset=1)
    assert main(["diff", str(changed_source), str(changed_fixed)]) == 4
    assert "unexpected=33" in capsys.readouterr().out  # type: ignore[attr-defined]

    invalid_path = tmp_path / "invalid-for-validation.fit"
    invalid_path.write_bytes(b"broken")
    assert main(["validate", str(invalid_path)]) == 4
    assert "Status: INVALID" in capsys.readouterr().out  # type: ignore[attr-defined]


def _repairable_fixture(
    tmp_path: Path,
    *,
    with_elevation: bool = False,
) -> tuple[Path, Path]:
    observations = [
        *eastward_observations(
            [float(index) for index in range(16)],
            [float(index * 6) for index in range(16)],
        ),
        (16.0, 56.0, 37.0),
        *eastward_observations(
            [float(index) for index in range(17, 33)],
            [float(index * 6) for index in range(17, 33)],
        ),
    ]
    fit_path = tmp_path / "original.fit"
    write_trajectory_activity(
        fit_path,
        [
            (int(elapsed), latitude, longitude)
            for elapsed, latitude, longitude in observations
            if elapsed is not None
        ],
        distances_m=([float(index * 6) for index in range(33)] if with_elevation else None),
        altitudes_m=(
            [100.0 + index if index <= 16 else 132.0 - index for index in range(33)]
            if with_elevation
            else None
        ),
    )
    course_observations = eastward_observations(
        [float(index) for index in range(33)],
        [float(index * 6) for index in range(33)],
    )
    course_points: list[GpxPoint] = [
        (latitude, longitude, None, 100.0 + index if with_elevation else None)
        for index, (_elapsed, latitude, longitude) in enumerate(course_observations)
        if latitude is not None and longitude is not None
    ]
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(course_path, [course_points])
    return fit_path, course_path


def _missing_endpoint_fixture(tmp_path: Path) -> tuple[Path, Path]:
    full = eastward_observations(
        [float(index * 5) for index in range(51)],
        [float(index * 10) for index in range(51)],
    )
    fit_path = tmp_path / "missing.fit"
    write_trajectory_activity(
        fit_path,
        [
            (
                int(elapsed if elapsed is not None else 0.0),
                latitude if 10 <= index <= 40 else None,
                longitude if 10 <= index <= 40 else None,
            )
            for index, (elapsed, latitude, longitude) in enumerate(full)
        ],
        retain_invalid_position_fields=True,
        distances_m=[float(index * 10) for index in range(51)],
        speeds_mps=[2.0] * 51,
    )
    course_path = tmp_path / "missing-course.gpx"
    write_gpx_activity(
        course_path,
        [
            [
                (latitude, longitude, None, None)
                for _elapsed, latitude, longitude in full
                if latitude is not None and longitude is not None
            ]
        ],
    )
    return fit_path, course_path


def _html_payload(rendered: str) -> dict[str, Any]:
    prefix = '<script id="warpbuster-report-data" type="application/json">'
    encoded = rendered.split(prefix, maxsplit=1)[1].split("</script>", maxsplit=1)[0]
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise TypeError("HTML payload must be an object")
    return payload
