"""CLI behavior tests for the feasibility command."""

from __future__ import annotations

import json
from pathlib import Path

from warpbuster_osm_routing.cli import main


def test_cli_returns_machine_readable_manifest_error(tmp_path: Path, capsys: object) -> None:
    status = main(
        [
            "spike",
            str(tmp_path / "missing.json"),
            "--work-dir",
            str(tmp_path / "work"),
            "--from",
            "44,33",
            "--to",
            "44.1,33.1",
            "--json",
        ]
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    document = json.loads(captured.out)

    assert status == 2
    assert document["status"] == "error"
    assert document["error"]["code"] == "manifest_unreadable"


def test_prepare_and_list_json_commands(
    snapshot_manifest: Path, tmp_path: Path, capsys: object
) -> None:
    cache = tmp_path / "cache"
    status = main(["prepare", str(snapshot_manifest), "--cache-dir", str(cache), "--json"])
    prepared = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]

    assert status == 0
    assert prepared["status"] == "READY"
    assert prepared["graph_id"].startswith("sha256:")

    status = main(["list", "--cache-dir", str(cache), "--json"])
    listing = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert status == 0
    assert listing["count"] == 1
    assert listing["graphs"][0]["status"] == "READY"

    status = main(
        [
            "route",
            prepared["graph_id"],
            "--from",
            "45,34",
            "--to",
            "44,33",
            "--cache-dir",
            str(cache),
            "--json",
        ]
    )
    route = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert status == 1
    assert route["status"] == "OUTSIDE_COVERAGE"
