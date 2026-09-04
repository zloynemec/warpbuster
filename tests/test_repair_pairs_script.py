"""Batch wrapper contract, preflight safety and a synthetic real CLI smoke test."""

import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import repair_pairs
from tests.local_reconstruction_factory import local_fixture

_STARTED_AT = datetime(2026, 9, 4, 12, 30, 15, tzinfo=timezone(timedelta(hours=3)))


def _detail_report(source: Path, *, written: bool = True, no_op: bool = False) -> Path:
    payload = {
        "inspect": {"source": {"path": str(source)}},
        "original_performance": {"distance_m": 12000, "average_pace_seconds_per_km": 300},
        "repaired_performance": {"distance_m": 10000, "average_pace_seconds_per_km": 360},
        "write_result": {"post_write_verified": True} if written else None,
        "repair": {
            "status": "not_needed" if no_op else "partial",
            "gap_inventory": [] if no_op else [{"status": "applied"}, {"status": "unresolved"}],
            "distance": {"quality": "uncertain"},
        },
    }
    path = source.with_suffix(".repair.html")
    path.write_text(
        '<script id="warpbuster-report-data" type="application/json">'
        + json.dumps(payload)
        + "</script>"
    )
    return path


def _manifest(path: Path, rows: list[tuple[str, str]], *, bom: bool = False) -> Path:
    with path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["fit", "gpx"])
        writer.writerows(rows)
    return path


def test_manifest_resolves_csv_relative_and_absolute_paths_with_bom(tmp_path: Path) -> None:
    absolute = tmp_path / "absolute.fit"
    manifest = _manifest(
        tmp_path / "pairs.csv",
        [
            ("folder/track, name.fit", "folder/route name.gpx"),
            (str(absolute), "another.gpx"),
        ],
        bom=True,
    )
    pairs = repair_pairs.read_pairs(manifest)
    assert pairs[0].fit == tmp_path / "folder/track, name.fit"
    assert pairs[1].fit == absolute
    assert pairs[1].gpx == tmp_path / "another.gpx"


@pytest.mark.parametrize(
    "contents",
    [
        "",
        "fit,gpx\n",
        "gpx,fit\na.gpx,a.fit\n",
        "fit,gpx\na.fit\n",
        "fit,gpx\na.fit,b.gpx,extra\n",
        "fit,gpx\na.fit,\n",
        "fit,gpx\na.txt,b.gpx\n",
        'fit,gpx\n"unterminated',
    ],
)
def test_invalid_csv_is_rejected_before_any_repair(
    tmp_path: Path, contents: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "invalid.csv"
    path.write_text(contents)
    monkeypatch.setattr(
        repair_pairs.subprocess, "run", lambda *a, **kw: pytest.fail("must not run")
    )
    assert repair_pairs.main([str(path)]) == 2


@pytest.mark.parametrize("second_fit", ["a.fit", "./a.fit", "a.fixed.fit"])
def test_duplicate_and_output_input_collision_are_refused(tmp_path: Path, second_fit: str) -> None:
    path = _manifest(tmp_path / "pairs.csv", [("a.fit", "a.gpx"), (second_fit, "b.gpx")])
    with pytest.raises(ValueError, match=r"duplicate|collides"):
        repair_pairs.read_pairs(path)


def test_commands_have_exact_flags_and_no_shell_interpolation(tmp_path: Path) -> None:
    pair = repair_pairs.TrackPair(tmp_path / "a; injected.fit", tmp_path / "course with spaces.gpx")
    assert repair_pairs.repair_command(pair) == [
        sys.executable,
        "-m",
        "warpbuster",
        "repair",
        str(pair.fit),
        "--course",
        str(pair.gpx),
        "--overwrite",
        "--html",
        "--fill-missing-from-course",
        "--min-invalidation-confidence",
        "medium",
        "--min-confidence",
        "medium",
    ]


def test_dry_run_checks_files_without_invoking_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    activity, course = local_fixture(tmp_path)
    path = _manifest(
        tmp_path / "pairs.csv", [(activity.preservation.source_path.name, course.source_path.name)]
    )
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(
        repair_pairs.subprocess, "run", lambda *a, **kw: pytest.fail("must not run")
    )
    assert repair_pairs.main([str(path), "--dry-run"]) == 0
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_missing_input_and_failed_repair_do_not_stop_other_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    path = _manifest(
        tmp_path / "pairs.csv",
        [(f"{name}.fit", f"{name}.gpx") for name in ["missing", "bad", "good"]],
    )
    for name in ["bad", "good"]:
        (tmp_path / f"{name}.fit").touch()
        (tmp_path / f"{name}.gpx").touch()
    calls = []

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess:
        assert check is False
        calls.append(command)
        if "good.fit" in command[4]:
            _detail_report(Path(command[4]))
        return subprocess.CompletedProcess(command, 3 if "bad.fit" in command[4] else 0)

    monkeypatch.setattr(repair_pairs.subprocess, "run", run)
    assert repair_pairs.main([str(path)]) == 1
    assert len(calls) == 2
    output = capsys.readouterr()
    assert "1/3 repair commands succeeded; failed=2" in output.out
    assert "input file not found" in output.err and "repair exit code 3" in output.err
    summary = (tmp_path / "pairs.reports/index.html").read_text()
    assert "missing.fit" in summary and "bad.fit" in summary and "good.fit" in summary
    assert '<a href="good.repair.html">good.fit</a>' in summary
    assert '<a href="bad.repair.html">' not in summary
    assert "Ошибок: 2" in summary


def test_checked_in_manifest_has_exact_requested_pairs() -> None:
    path = Path(__file__).with_name("repair_pairs.csv")
    pairs = repair_pairs.read_pairs(path)
    assert [(p.fit.name, p.gpx.name) for p in pairs] == [
        ("Andromeda_Taras.fit", "Andromeda_2026.gpx"),
        ("BST2025_TezBair_55_Taras.fit", "BST2025_TezBair_55.gpx"),
        ("CHR_KayaBayu_22_Taras.fit", "CHR_KayaBayu_22.gpx"),
        ("CHR_KayaBayu_22_Nikita.fit", "CHR_KayaBayu_22.gpx"),
        ("CHR_KayaBayu_22_Andrey.fit", "CHR_KayaBayu_22.gpx"),
        ("CHR_KayaBayu_22_Ivan.fit", "CHR_KayaBayu_22.gpx"),
        ("CWT_Dzhurla_2025_Taras.fit", "CWT_Dzhurla_2025.gpx"),
        ("m87_home_run.fit", "m87_home_run.gpx"),
    ]


def test_real_batch_cli_writes_html_and_overwrites_synthetic_outputs_from_other_cwd(
    tmp_path: Path,
) -> None:
    activity, course = local_fixture(tmp_path)
    path = _manifest(
        tmp_path / "pairs.csv", [(activity.preservation.source_path.name, course.source_path.name)]
    )
    script = Path(repair_pairs.__file__).resolve()
    other = tmp_path / "other-working-directory"
    other.mkdir()
    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(script), str(path)],
            cwd=other,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "activity.fixed.fit").exists()
    assert 'id="warpbuster-report-data"' in (tmp_path / "activity.repair.html").read_text()
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes
    bundle = tmp_path / "pairs.reports"
    summary = (bundle / "index.html").read_text()
    assert 'href="activity.repair.html"' in summary
    assert "3 / 3" in summary
    assert (bundle / "activity.repair.html").read_bytes() == (
        tmp_path / "activity.repair.html"
    ).read_bytes()


def test_summary_values_are_after_before_and_unknowns_are_not_zero(tmp_path: Path) -> None:
    pair = repair_pairs.TrackPair(tmp_path / "test.fit", tmp_path / "test.gpx")
    _detail_report(pair.fit)
    target = tmp_path / "reports/test.repair.html"
    row = repair_pairs.collect_row(pair, target, None, None)
    assert (row.distance_after, row.distance_before) == (10000, 12000)
    assert (row.pace_after, row.pace_before) == (360, 300)
    assert (row.fixed, row.found) == (1, 2)
    repair_pairs.write_summary(
        [row, repair_pairs.BatchRow("absent.fit", error="failed")],
        tmp_path / "index.html",
        started_at=_STARTED_AT,
    )
    rendered = (tmp_path / "index.html").read_text()
    assert "10.00 / 12.00" in rendered and "6:00 / 5:00" in rendered
    assert "1 / 2" in rendered and "— / —" in rendered
    assert "Дистанция и темп остаются неопределёнными" in rendered
    assert repair_pairs._pace(359.6) == "6:00"
    assert repair_pairs._pace(None) == "—"
    assert repair_pairs._number(float("nan")) is None


def test_summary_shows_timezone_aware_batch_start(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    repair_pairs.write_summary([], target, started_at=_STARTED_AT)
    assert (
        'Время запуска: <time datetime="2026-09-04T12:30:15+03:00">2026-09-04 12:30:15+03:00</time>'
    ) in target.read_text()
    with pytest.raises(ValueError, match="timezone"):
        repair_pairs.write_summary([], target, started_at=_STARTED_AT.replace(tzinfo=None))


def test_start_time_is_captured_before_repair_not_at_summary_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _manifest(tmp_path / "pairs.csv", [("a.fit", "a.gpx")])
    (tmp_path / "a.fit").touch()
    (tmp_path / "a.gpx").touch()
    calls = []

    class BatchClock:
        @staticmethod
        def now():
            assert not calls
            calls.append("clock")
            return _STARTED_AT

    def run(command: list[str], *, check: bool) -> subprocess.CompletedProcess:
        assert calls == ["clock"]
        calls.append("repair")
        _detail_report(Path(command[4]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(repair_pairs, "datetime", BatchClock)
    monkeypatch.setattr(repair_pairs.subprocess, "run", run)
    assert repair_pairs.main([str(manifest)]) == 0
    summary = (tmp_path / "pairs.reports/index.html").read_text()
    assert _STARTED_AT.astimezone().isoformat(timespec="seconds") in summary
    assert calls == ["clock", "repair"]


def test_stale_report_is_never_linked_or_counted(tmp_path: Path) -> None:
    pair = repair_pairs.TrackPair(tmp_path / "a.fit", tmp_path / "a.gpx")
    source = _detail_report(pair.fit)
    row = repair_pairs.collect_row(
        pair, tmp_path / "bundle/a.html", repair_pairs._stamp(source), "repair exit code 2"
    )
    assert row.report_name is None and row.fixed is None and row.distance_after is None
    assert not (tmp_path / "bundle").exists()
    missing_output = repair_pairs.collect_row(
        pair, tmp_path / "bundle/a.html", repair_pairs._stamp(source), None
    )
    assert "fresh HTML" in missing_output.error


def test_no_op_and_failed_fresh_report_are_not_claimed_as_repairs(tmp_path: Path) -> None:
    pair = repair_pairs.TrackPair(tmp_path / "a.fit", tmp_path / "a.gpx")
    _detail_report(pair.fit, written=False, no_op=True)
    row = repair_pairs.collect_row(pair, tmp_path / "bundle/a.html", None, None)
    assert row.fixed == row.found == 0
    assert row.distance_after == row.distance_before == 12000
    assert row.pace_after == row.pace_before == 300
    _detail_report(pair.fit, written=False)
    row = repair_pairs.collect_row(pair, tmp_path / "bundle/a.html", None, "repair exit code 3")
    assert row.report_name == "a.html" and row.found == 2
    assert row.distance_after is None and row.fixed is None


def test_summary_escapes_names_errors_and_link_urls(tmp_path: Path) -> None:
    row = repair_pairs.BatchRow(
        "<script>&.fit", report_name='a #&".html', error="<script>alert(1)</script>"
    )
    repair_pairs.write_summary([row], tmp_path / "index.html", started_at=_STARTED_AT)
    result = (tmp_path / "index.html").read_text()
    assert "<script>" not in result
    assert "&lt;script&gt;&amp;.fit" in result
    assert 'href="a%20%23%26%22.html"' in result


def test_duplicate_basenames_get_distinct_bundle_links(tmp_path: Path) -> None:
    pairs = [
        repair_pairs.TrackPair(tmp_path / folder / "a.fit", tmp_path / folder / "a.gpx")
        for folder in ["one", "two"]
    ]
    targets = repair_pairs.report_targets(pairs, tmp_path / "pairs.csv", tmp_path / "bundle")
    assert [p.name for p in targets] == ["001-a.repair.html", "002-a.repair.html"]


def test_summary_cannot_overwrite_manifest_before_running_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _manifest(tmp_path / "index.html", [("a.fit", "a.gpx")])
    before = path.read_bytes()
    monkeypatch.setattr(
        repair_pairs.subprocess, "run", lambda *a, **kw: pytest.fail("must not run")
    )
    assert repair_pairs.main([str(path), "--report-dir", str(tmp_path)]) == 2
    assert path.read_bytes() == before
