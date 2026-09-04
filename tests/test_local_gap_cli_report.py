"""Unified CLI and shared report audit, including invalidation-only output."""

import json
from pathlib import Path

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.cli import main


def _payload(path: Path) -> dict:
    return json.loads(
        path.read_text()
        .split('<script id="warpbuster-report-data" type="application/json">', 1)[1]
        .split("</script>", 1)[0]
    )


def test_optional_course_cleaning_json_html_and_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    activity, _course = local_fixture(tmp_path, missing=(), spikes=(200,))
    source = activity.preservation.source_path
    args = ["repair", str(source), "--html", "--json"]
    assert main([*args, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    html = source.with_suffix(".repair.html")
    dry_html = _payload(html)
    assert not source.with_suffix(".fixed.fit").exists()
    assert dry_html["repair"]["gap_inventory"] == dry["gap_inventory"]
    assert dry_html["tracks"]["original"]["records"][200][2] is not None
    assert dry_html["tracks"]["candidate"]["records"][200][1:3] == [None, None]
    assert dry_html["gap_markers"][0]["candidate_geometry"] == []
    assert main(args) == 2  # existing HTML blocks any FIT side effect
    capsys.readouterr()
    assert not source.with_suffix(".fixed.fit").exists()
    assert main([*args, "--overwrite"]) == 0
    written = json.loads(capsys.readouterr().out)
    rendered = _payload(html)
    assert rendered["repair"]["coordinate_coverage"] == written["coordinate_coverage"]
    assert rendered["repair"]["distance"]["quality"] == "uncertain"
    assert rendered["tracks"]["repaired"]["records"][200][2:4] == [None, None]
    assert rendered["write_result"]["diff"]["unexpected_changed_field_count"] == 0
    assert rendered["repair"]["gap_inventory"][0]["invalidation_action"] == "applied"
    assert source.read_bytes() == activity.preservation.raw_bytes


def test_all_gap_numbers_and_selection_reasons_match_html(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    activity, course = local_fixture(tmp_path)
    source = activity.preservation.source_path
    args = [
        "repair",
        str(source),
        "--course",
        str(course.source_path),
        "--fill-missing-from-course",
        "--dry-run",
        "--html",
        "--json",
    ]
    assert main(args) == 3  # MEDIUM paths do not bypass the default HIGH threshold
    report = json.loads(capsys.readouterr().out)
    payload = _payload(source.with_suffix(".repair.html"))
    assert [g["number"] for g in report["gap_inventory"]] == [1, 2, 3]
    assert payload["repair"]["gap_inventory"] == report["gap_inventory"]
    assert len(payload["gap_markers"]) == 3
    assert all(g["status"] == "skipped" for g in report["gap_inventory"])
    assert all(
        "below_minimum_confidence" in g["selection_reasons"] for g in report["gap_inventory"]
    )
    assert main([*args, "--overwrite", "--min-confidence", "medium"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert all(g["status"] == "planned" for g in selected["gap_inventory"])
    assert selected["coordinate_coverage"]["filled"] == 90
    assert selected["gap_inventory"][0]["endpoint_source"] == "course_assumption"
    assert selected["gap_inventory"][0]["provenance"]["source_sha256"]


@pytest.mark.parametrize("command", ["repair", "analyze"])
@pytest.mark.parametrize("target", ["source", "course"])
def test_html_overwrite_never_replaces_an_input(
    tmp_path: Path, capsys: pytest.CaptureFixture, command: str, target: str
) -> None:
    activity, course = local_fixture(tmp_path, missing=(), spikes=(200,))
    source = activity.preservation.source_path
    path = source if target == "source" else course.source_path
    before = path.read_bytes()
    assert (
        main(
            [
                command,
                str(source),
                "--course",
                str(course.source_path),
                "--html",
                str(path),
                "--overwrite",
            ]
        )
        == 2
    )
    assert "must differ" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert not source.with_suffix(".fixed.fit").exists()


def test_analyze_still_uses_original_geometry_and_fill_requires_course(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    activity, course = local_fixture(tmp_path, missing=(), spikes=(200,))
    source = activity.preservation.source_path
    assert main(["repair", str(source), "--fill-missing-from-course"]) == 2
    assert "requires --course" in capsys.readouterr().err
    assert main(["analyze", str(source), "--course", str(course.source_path), "--html"]) == 1
    capsys.readouterr()
    payload = _payload(source.with_suffix(".analyze.html"))
    assert payload["repair"] is None and payload["tracks"]["candidate"] is None
    assert payload["tracks"]["original"]["records"][200][2] == activity.records[200].latitude
    assert not source.with_suffix(".fixed.fit").exists()


def test_added_coordinate_fields_are_audited_in_json_and_shared_html(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    activity, course = local_fixture(tmp_path, position_fields=False)
    source = activity.preservation.source_path
    assert (
        main(
            [
                "repair",
                str(source),
                "--course",
                str(course.source_path),
                "--fill-missing-from-course",
                "--min-confidence",
                "medium",
                "--html",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    path = source.with_suffix(".repair.html")
    rendered = _payload(path)
    diff = rendered["write_result"]["diff"]
    assert diff == report["diff"]
    assert diff["added_coordinate_field_count"] == 180
    assert diff["definition_count_delta"] == 180
    assert not diff["definitions_unchanged"]
    assert "Added coordinate fields" in path.read_text()
    assert rendered["repair"]["coordinate_coverage"]["filled"] == 90
