"""Inspect CLI tests."""

import json
from pathlib import Path

from tests.fit_factory import write_synthetic_activity
from warpbuster.cli import main


def test_inspect_console(tmp_path: Path, capsys: object) -> None:
    """Console inspect exposes the required activity summary."""
    fit_path = tmp_path / "activity.fit"
    write_synthetic_activity(fit_path)

    assert main(["inspect", str(fit_path)]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WarpBuster FIT inspect" in captured.out
    assert "Records: 4" in captured.out
    assert "Manufacturer: garmin" in captured.out
    assert "position=yes" in captured.out
    assert "Developer fields: 1" in captured.out


def test_inspect_json(tmp_path: Path, capsys: object) -> None:
    """JSON inspect emits structured data rather than console decoration."""
    fit_path = tmp_path / "activity.fit"
    write_synthetic_activity(fit_path)

    assert main(["inspect", str(fit_path), "--json"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    report = json.loads(captured.out)
    assert report["schema_version"] == "0.1"
    assert report["record_count"] == 4
    assert report["fields"]["position"] is True
    assert report["developer_fields"][0]["name"] == "synthetic_metric"


def test_inspect_invalid_input_returns_exit_code_2(
    tmp_path: Path,
    capsys: object,
) -> None:
    """Unreadable FIT input follows the CLI invalid-input contract."""
    fit_path = tmp_path / "invalid.fit"
    fit_path.write_bytes(b"invalid")

    assert main(["inspect", str(fit_path)]) == 2
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "error:" in captured.err
