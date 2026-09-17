"""Task 022C FIT-only CLI and private report integration."""

import json
from math import cos, radians
from pathlib import Path

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.processing_factory import processing_fixture
from warpbuster.cli import main
from warpbuster.fit.reader import read_fit


def _html_payload(path: Path) -> dict:
    return json.loads(
        path.read_text(encoding="utf-8")
        .split('<script id="warpbuster-report-data" type="application/json">', 1)[1]
        .split("</script>", 1)[0]
    )


def _fit(path: Path, *, missing: range) -> None:
    scale = 111_195.0 * cos(radians(44.0))
    write_trajectory_activity(
        path,
        [
            (index, None, None) if index in missing else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )


@pytest.mark.integration
def test_fit_only_cli_writes_endpoint_and_consistent_json_html(tmp_path, capsys):
    _, _, _, config = processing_fixture(tmp_path)
    fit = tmp_path / "original.fit"
    _fit(fit, missing=range(51))
    output, html = tmp_path / "fixed.fit", tmp_path / "private.html"
    code = main(
        [
            "process",
            str(fit),
            "--start",
            "44,33",
            "--osm-mode",
            "offline",
            "--dem-mode",
            "disabled",
            "--work-dir",
            str(config.data_dir),
            "--output",
            str(output),
            "--html",
            str(html),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    embedded = _html_payload(html)
    assert code == 0
    assert report["pipeline"]["mode"] == "fit_only"
    assert report["repair_plan"]["mode"] == "fit_only"
    assert report["pipeline"]["observed_gps_coverage"]["status"] == "passed"
    assert (
        report["repair_plan"]["observed_gps_coverage"]
        == embedded["repair"]["observed_gps_coverage"]
    )
    assert report["repair_plan"]["endpoint_audit"] == embedded["repair"]["endpoint_audit"]
    assert report["repair_plan"]["endpoint_audit"][0]["status"] == "osm_selected"
    assert report["repair_plan"]["geometry_status"] == "complete"
    assert report["diff"]["unexpected_changed_field_count"] == 0
    assert embedded["write_result"]["post_write_verified"] is True
    assert read_fit(output).records[0].latitude == pytest.approx(44.0, abs=1e-7)


def test_fit_only_cli_gate_refusal_has_report_without_output(tmp_path, capsys):
    fit = tmp_path / "mostly-missing.fit"
    _fit(fit, missing=range(250))
    output, html = tmp_path / "fixed.fit", tmp_path / "refused.html"
    code = main(
        [
            "process",
            str(fit),
            "--start",
            "44,33",
            "--osm-mode",
            "disabled",
            "--output",
            str(output),
            "--html",
            str(html),
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    assert not output.exists()
    assert report["pipeline"]["observed_gps_coverage"]["status"] == "below_threshold"
    assert report["pipeline"]["observed_gps_coverage"]["observed_gps_coverage_percent"] < 51
    assert _html_payload(html)["repair"]["observed_gps_coverage"] == report["observed_gps_coverage"]


def test_cli_rejects_conflicting_points_and_keeps_gpx_gate_not_applicable(tmp_path, capsys):
    fit = tmp_path / "original.fit"
    _fit(fit, missing=range(250))
    course = tmp_path / "course.gpx"
    course.write_text(
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg><trkpt lat="44" lon="33"/><trkpt lat="44" lon="33.01"/></trkseg></trk></gpx>'
    )
    assert main(["process", str(fit), str(course), "--start", "44,33"]) == 2
    assert "FIT-only" in capsys.readouterr().err
    assert main(["process", str(fit), "--start", "44,33", "--loop-point", "44,33"]) == 2
    assert "loop point" in capsys.readouterr().err
    code = main(["process", str(fit), str(course), "--osm-mode", "disabled", "--dry-run", "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code in {0, 3}
    assert report["pipeline"]["mode"] == "fit_with_course"
    assert report["pipeline"]["observed_gps_coverage"]["status"] == "not_applicable"


def test_cli_configured_threshold_and_unused_point(tmp_path, capsys):
    fit = tmp_path / "mostly-preserved.fit"
    _fit(fit, missing=range(100, 190))
    code = main(
        [
            "process",
            str(fit),
            "--start",
            "45,34",
            "--osm-mode",
            "disabled",
            "--min-gps-coverage-percent",
            "80",
            "--dry-run",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 3
    gate = report["observed_gps_coverage"]
    assert gate["status"] == "below_threshold"
    assert gate["minimum_observed_gps_coverage_percent"] == 80
    assert report["endpoint_audit"][0]["status"] == "unused"
    assert report["endpoint_audit"][0]["endpoint"] == [45.0, 34.0]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("option", "missing", "expected_kinds"),
    [
        ("--finish", range(350, 401), {"suffix"}),
        ("--loop-point", range(0, 51), {"prefix"}),
    ],
)
def test_cli_accepts_finish_and_loop_point(tmp_path, capsys, option, missing, expected_kinds):
    _, _, _, config = processing_fixture(tmp_path)
    fit = tmp_path / "original.fit"
    _fit(fit, missing=missing)
    point = "44,33.00999" if option == "--finish" else "44,33"
    code = main(
        [
            "process",
            str(fit),
            option,
            point,
            "--osm-mode",
            "offline",
            "--dem-mode",
            "disabled",
            "--work-dir",
            str(config.data_dir),
            "--dry-run",
            "--json",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert {
        item["kind"] for item in report["endpoint_audit"] if item["status"] == "osm_selected"
    } == expected_kinds


def test_invalid_supplied_gpx_does_not_switch_to_fit_only(tmp_path, capsys):
    fit = tmp_path / "original.fit"
    _fit(fit, missing=range(250))
    bad_gpx = tmp_path / "bad.gpx"
    bad_gpx.write_text("not GPX")
    assert main(["process", str(fit), str(bad_gpx), "--osm-mode", "disabled", "--json"]) == 2
    assert "error:" in capsys.readouterr().err


def test_console_coverage_refusal_and_invalid_endpoint_args(tmp_path, capsys):
    fit = tmp_path / "mostly-missing.fit"
    _fit(fit, missing=range(250))
    assert main(["process", str(fit), "--osm-mode", "disabled", "--start", "44,33"]) == 3
    captured = capsys.readouterr()
    assert "observed GPS gate: below_threshold" in captured.out
    assert "below" in captured.err and "51.0000%" in captured.err
    assert main(["process", str(fit), "--start", "44,33", "--no-approximate-missing-osm"]) == 2
    assert "approximate" in capsys.readouterr().err
    with pytest.raises(SystemExit) as invalid:
        main(["process", str(fit), "--start", "91,33"])
    assert invalid.value.code == 2
