"""Standalone CLI, management commands, and protocol v1 output."""

import json
from pathlib import Path

import pytest
from conftest import osm_xml

from warpbuster_osm_manager.cli import main
from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.coverage import bounds_for_cell, cell_for_point
from warpbuster_osm_manager.models import GeoPoint


def test_capabilities_is_machine_readable(capsys: object) -> None:
    assert main(["capabilities", "--json"]) == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    report = json.loads(captured.out)
    assert report["protocol_version"] == 1
    assert report["coverage_inputs"] == ["gpx", "geojson", "bbox", "protocol_geometry"]
    assert "ensure" in report["commands"]


def test_import_list_inspect_remove_work_through_cli(tmp_path: Path, capsys: object) -> None:
    cache = tmp_path / "cache"
    source = tmp_path / "region.osm"
    cell = cell_for_point(GeoPoint(33.60, 44.40), OsmManagerConfig.defaults().cache_grid_zoom)
    source.write_text(osm_xml(bounds_for_cell(cell)), encoding="utf-8")
    common = ["--cache-dir", str(cache)]
    assert main([*common, "import", str(source), "--json"]) == 0
    imported = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    snapshot_id = imported["snapshot_id"]

    assert main([*common, "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert listed["snapshot_count"] == 1

    assert main([*common, "inspect", snapshot_id, "--json"]) == 0
    inspected = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert inspected["verified_data_file_count"] == 1

    assert main([*common, "remove", snapshot_id, "--json"]) == 0
    removed = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert removed["removed_manifest"] is True


def test_offline_miss_and_invalid_request_emit_json_errors(tmp_path: Path, capsys: object) -> None:
    common = ["--cache-dir", str(tmp_path / "cache")]
    code = main([*common, "ensure", "--bbox", "33.60,44.40,33.61,44.41", "--offline", "--json"])
    assert code == 3
    missing = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert missing["error_code"] == "OFFLINE_CACHE_MISS"

    request = tmp_path / "request.json"
    request.write_text('{"protocol_version":999,"coverage":{}}', encoding="utf-8")
    assert main([*common, "ensure", "--request", str(request), "--json"]) == 2
    invalid = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert invalid["error_code"] == "PROTOCOL_UNSUPPORTED"


def test_cli_policy_overrides_protocol_request_policy(tmp_path: Path, capsys: object) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "protocol_version": 1,
                "coverage": {"bbox": [33.60, 44.40, 33.61, 44.41]},
                "policy": {"offline": False},
            }
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "--cache-dir",
            str(tmp_path / "cache"),
            "ensure",
            "--request",
            str(request),
            "--offline",
            "--json",
        ]
    )
    assert code == 3
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["error_code"] == "OFFLINE_CACHE_MISS"


def test_explicit_config_file_controls_cache(tmp_path: Path, capsys: object) -> None:
    config = tmp_path / "manager.toml"
    cache = tmp_path / "configured-cache"
    config.write_text(
        f'[osm_manager]\ncache_directory = "{cache}"\nmaximum_retry_count = 0\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config), "doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert Path(report["cache_directory"]) == cache


def test_default_config_is_loaded_from_current_directory(
    tmp_path: Path,
    capsys: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "automatic-cache"
    (tmp_path / "osm-manager.toml").write_text(
        f'[osm_manager]\ncache_directory = "{cache}"\nmaximum_retry_count = 0\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert Path(report["cache_directory"]) == cache


def test_prune_defaults_to_non_destructive_dry_run(tmp_path: Path, capsys: object) -> None:
    assert main(["--cache-dir", str(tmp_path / "cache"), "prune", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert report["applied"] is False
