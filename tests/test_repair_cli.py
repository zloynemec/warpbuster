"""Course-based repair dry-run CLI integration tests."""

import json
from pathlib import Path

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


def _repairable_fixture(tmp_path: Path) -> tuple[Path, Path]:
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
    )
    course_observations = eastward_observations(
        [float(index) for index in range(33)],
        [float(index * 6) for index in range(33)],
    )
    course_points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in course_observations
        if latitude is not None and longitude is not None
    ]
    course_path = tmp_path / "course.gpx"
    write_gpx_activity(course_path, [course_points])
    return fit_path, course_path
