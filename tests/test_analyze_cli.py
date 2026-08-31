"""Analyze CLI integration tests."""

import json
from pathlib import Path

from tests.fit_factory import write_trajectory_activity
from warpbuster.cli import main


def test_analyze_console_reports_impossible_transition(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Console analyze reports local corruption and uses anomaly exit code one."""
    fit_path = tmp_path / "spike.fit"
    write_trajectory_activity(
        fit_path,
        [
            (0, 55.0, 37.0),
            (1, 55.0, 37.00005),
            (2, 55.01, 37.00005),
            (3, 55.0, 37.00010),
            (4, 55.0, 37.00015),
            (5, 55.0, 37.00020),
            (6, 55.0, 37.00025),
        ],
    )

    assert main(["analyze", str(fit_path)]) == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WarpBuster FIT analyze" in captured.out
    assert "Status: CORRUPTED" in captured.out
    assert "impossible=2" in captured.out
    assert "absolute_speed_and_distance_exceeded" in captured.out


def test_analyze_json_contains_machine_readable_reasons(
    tmp_path: Path,
    capsys: object,
) -> None:
    """JSON analyze exposes stable classifications, thresholds, and reasons."""
    fit_path = tmp_path / "spike.fit"
    write_trajectory_activity(
        fit_path,
        [
            (0, 55.0, 37.0),
            (1, 55.0, 37.00005),
            (2, 55.01, 37.00005),
            (3, 55.0, 37.00010),
            (4, 55.0, 37.00015),
            (5, 55.0, 37.00020),
            (6, 55.0, 37.00025),
        ],
    )

    assert main(["analyze", str(fit_path), "--json"]) == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    report = json.loads(captured.out)
    assert report["schema_version"] == "0.1"
    assert report["scope"] == "local_transitions"
    assert report["activity"] == {"sport": "running", "sub_sport": None}
    assert report["status"] == "corrupted"
    assert report["summary"]["classifications"]["impossible"] == 2
    assert report["findings"][0]["classification"] == "impossible"
    assert report["findings"][0]["reasons"] == ["absolute_speed_and_distance_exceeded"]
    assert report["config"]["profile"] == "running"
    assert report["config"]["absolute_impossible_speed_mps"] == 25.0


def test_analyze_clean_activity_returns_exit_code_zero(
    tmp_path: Path,
    capsys: object,
) -> None:
    """A clean analysis succeeds without an anomaly exit code."""
    fit_path = tmp_path / "clean.fit"
    write_trajectory_activity(
        fit_path,
        [(index, 55.0, 37.0 + index * 0.00005) for index in range(7)],
    )

    assert main(["analyze", str(fit_path)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "Status: CLEAN" in captured.out
    assert "Findings: none" in captured.out


def test_analyze_invalid_input_returns_exit_code_2(tmp_path: Path, capsys: object) -> None:
    """Unreadable analyze input follows the CLI invalid-input contract."""
    fit_path = tmp_path / "invalid.fit"
    fit_path.write_bytes(b"invalid")

    assert main(["analyze", str(fit_path)]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "error:" in captured.err
