#!/usr/bin/env python3
"""Run the existing repair CLI sequentially for FIT/GPX pairs from a CSV manifest."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class TrackPair:
    fit: Path
    gpx: Path


@dataclass
class BatchRow:
    filename: str
    report_name: str | None = None
    distance_after: float | None = None
    distance_before: float | None = None
    pace_after: float | None = None
    pace_before: float | None = None
    fixed: int | None = None
    found: int | None = None
    uncertain: bool = False
    error: str | None = None


def _number(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def _stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_ino, stat.st_size
    except FileNotFoundError:
        return None


def report_targets(pairs: list[TrackPair], csv_path: Path, directory: Path) -> list[Path]:
    """Preflight the HTML bundle before repair can overwrite any files."""
    counts = Counter(p.fit.stem for p in pairs)
    targets = [
        directory / f"{f'{i:03d}-' if counts[p.fit.stem] > 1 else ''}{p.fit.stem}.repair.html"
        for i, p in enumerate(pairs, 1)
    ]
    protected = {
        csv_path.resolve(),
        *(p.fit for p in pairs),
        *(p.gpx for p in pairs),
        *(p.fit.with_name(f"{p.fit.stem}.fixed{p.fit.suffix}").resolve() for p in pairs),
    }
    originals = {p.fit.with_suffix(".repair.html").resolve() for p in pairs}
    all_targets = [directory / "index.html", *targets]
    resolved = [p.resolve() for p in all_targets]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Batch report targets collide")
    for i, target in enumerate(all_targets):
        own_report = pairs[i - 1].fit.with_suffix(".repair.html").resolve() if i else None
        if target.resolve() in protected or (
            target.resolve() in originals and target.resolve() != own_report
        ):
            raise ValueError(f"Batch report collides with an input or another output: {target}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Batch report target is not a file: {target}")
    if directory.exists() and not directory.is_dir():
        raise ValueError(f"Report directory is not a directory: {directory}")
    return targets


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect_row(
    pair: TrackPair,
    target: Path,
    previous_stamp: tuple[int, int, int] | None,
    error: str | None,
) -> BatchRow:
    """Use only a fresh report from this run, never a stale successful output."""
    row = BatchRow(pair.fit.name, error=error)
    source = pair.fit.with_suffix(".repair.html")
    stamp = _stamp(source)
    if stamp is None or stamp == previous_stamp:
        row.error = error or "repair did not produce a fresh HTML report"
        return row
    content = source.read_bytes()
    try:
        payload = json.loads(
            content.decode("utf-8")
            .split('<script id="warpbuster-report-data" type="application/json">', 1)[1]
            .split("</script>", 1)[0]
        )
        if Path(payload["inspect"]["source"]["path"]).resolve() != pair.fit:
            raise ValueError("report source does not match the FIT input")
        before = payload["original_performance"]
        row.distance_before = _number(before["distance_m"])
        row.pace_before = _number(before["average_pace_seconds_per_km"])
        gaps = payload["repair"]["gap_inventory"]
        row.found = len(gaps)
        written = payload.get("write_result")
        if error is None and written and written.get("post_write_verified"):
            after = payload["repaired_performance"]
            row.distance_after = _number(after["distance_m"])
            row.pace_after = _number(after["average_pace_seconds_per_km"])
            row.fixed = sum(g["status"] == "applied" for g in gaps)
        elif error is None and payload["repair"]["status"] == "not_needed":
            row.distance_after, row.pace_after = row.distance_before, row.pace_before
            row.fixed = 0
        elif error is None:
            raise ValueError("report has no verified output or no-op result")
        row.uncertain = payload["repair"]["distance"]["quality"] == "uncertain"
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(f"cannot summarize {pair.fit.name}: {exc}") from exc
    _atomic_write(target, content)
    row.report_name = target.name
    return row


def _pace(value: float | None) -> str:
    if value is None or value <= 0:
        return "—"
    minutes, seconds = divmod(int(value + 0.5), 60)
    return f"{minutes}:{seconds:02d}"


def write_summary(rows: list[BatchRow], destination: Path, *, started_at: datetime) -> None:
    if started_at.utcoffset() is None:
        raise ValueError("batch start time must include a timezone")

    def km(value: float | None) -> str:
        return "—" if value is None else f"{value / 1000:.2f}"

    def count(value: int | None) -> str:
        return "—" if value is None else str(value)

    cells = []
    for row in rows:
        name = html.escape(row.filename)
        if row.report_name:
            name = f'<a href="{quote(row.report_name, safe="")}">{name}</a>'
        status = (
            f"Ошибка: {row.error}"
            if row.error
            else "Частично восстановлено"
            if row.fixed != row.found
            else "Проблем не найдено"
            if row.found == 0
            else "Разрывы восстановлены"
        )
        note = "Дистанция и темп остаются неопределёнными" if row.uncertain else ""
        cells.append(
            f"<tr><td>{name}<small>{html.escape(status)}</small>"
            f"<small>{note}</small></td><td>{km(row.distance_after)} / "
            f"{km(row.distance_before)}</td><td>{_pace(row.pace_after)} / "
            f"{_pace(row.pace_before)}</td><td>{count(row.fixed)} / {count(row.found)}</td></tr>"
        )
    failures = sum(row.error is not None for row in rows)
    document = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WarpBuster — сводка repair</title>
<style>body{{font:16px system-ui,sans-serif;margin:32px;color:#24313a;background:#f6f7f9}}
main{{max-width:1100px;margin:auto}}h1{{font-size:26px}}.table{{overflow:auto;background:white;border-radius:10px}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:16px;text-align:left;border-bottom:1px solid #e4e8ed}}
th{{background:#eaf0f5}}td:not(:first-child){{white-space:nowrap;font-variant-numeric:tabular-nums}}
a{{color:#176ca4}}small{{display:block;color:#63717e;font-size:13px;margin-top:5px}}p{{color:#63717e}}</style>
<main><h1>Результаты пакетного repair</h1><p>Файлов: {len(rows)} · Ошибок: {failures}</p>
<p>Время запуска: <time datetime="{started_at.isoformat(timespec="seconds")}">{started_at.isoformat(sep=" ", timespec="seconds")}</time></p>
<div class="table"><table><thead><tr><th>Файл / полный отчёт</th><th>Километры<br>Стало / было</th>
<th>Средний темп, мин/км<br>Стало / было</th><th>Проблемы<br>Исправлено / найдено</th></tr></thead>
<tbody>{"".join(cells)}</tbody></table></div>
<p>Проблема — разрыв G1, G2… из полного отчёта. Исправлено — разрыв полностью заполнен;
очистка координат без восстановления пути не считается исправлением.</p>
<p>Дистанция и средний темп взяты из исходного и выходного FIT тем же расчётом, что в полном отчёте.
Неизвестные значения обозначены «—». При ошибке старый результат не используется.</p></main></html>"""
    _atomic_write(destination, document.encode("utf-8"))


def read_pairs(csv_path: Path) -> list[TrackPair]:
    """Resolve CSV-relative paths and reject ambiguous/destructive batch definitions."""
    csv_path = csv_path.resolve()
    pairs = []
    seen_fits: set[Path] = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        if next(reader, None) != ["fit", "gpx"]:
            raise ValueError("CSV header must be exactly: fit,gpx")
        for row in reader:
            if not row or all(not cell.strip() for cell in row):
                continue
            if len(row) != 2 or not all(cell.strip() for cell in row):
                raise ValueError(f"CSV line {reader.line_num}: expected non-empty fit and gpx")
            fit, gpx = ((csv_path.parent / cell.strip()).resolve() for cell in row)
            if fit.suffix.lower() != ".fit" or gpx.suffix.lower() != ".gpx":
                raise ValueError(f"CSV line {reader.line_num}: expected .fit and .gpx files")
            if fit in seen_fits:
                raise ValueError(f"CSV line {reader.line_num}: duplicate FIT input: {fit}")
            seen_fits.add(fit)
            pairs.append(TrackPair(fit, gpx))
    if not pairs:
        raise ValueError("CSV contains no FIT/GPX pairs")
    protected = {csv_path, *(p.fit for p in pairs), *(p.gpx for p in pairs)}
    outputs: set[Path] = set()
    for pair in pairs:
        for path in (
            pair.fit.with_name(f"{pair.fit.stem}.fixed{pair.fit.suffix}").resolve(),
            pair.fit.with_name(f"{pair.fit.stem}.repair.html").resolve(),
        ):
            if path in protected or path in outputs:
                raise ValueError(f"Batch output collides with an input or another output: {path}")
            outputs.add(path)
    return pairs


def repair_command(pair: TrackPair) -> list[str]:
    """Use this interpreter's installed WarpBuster, without shell interpolation."""
    return [
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


def main(argv: list[str] | None = None) -> int:
    started_at = datetime.now().astimezone()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="CSV with fit,gpx paths relative to the CSV")
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="HTML bundle directory (default: <CSV stem>.reports next to CSV)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="check input paths and print commands only; do not run repair or write FIT/HTML",
    )
    args = parser.parse_args(argv)
    try:
        pairs = read_pairs(args.csv_file)
        directory = (args.report_dir or args.csv_file.resolve().with_suffix(".reports")).resolve()
        targets = report_targets(pairs, args.csv_file, directory)
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    failures = []
    rows = []
    for number, (pair, target) in enumerate(zip(pairs, targets, strict=True), 1):
        detail = None
        previous_stamp = _stamp(pair.fit.with_suffix(".repair.html"))
        command = repair_command(pair)
        print(f"[{number}/{len(pairs)}] {shlex.join(command)}", flush=True)
        missing = [str(path) for path in (pair.fit, pair.gpx) if not path.is_file()]
        if missing:
            detail = f"input file not found: {', '.join(missing)}"
        elif args.dry_run:
            continue
        else:
            try:
                result = subprocess.run(command, check=False)
            except OSError as error:
                detail = str(error)
            else:
                if result.returncode != 0:
                    detail = f"repair exit code {result.returncode}"
        if not args.dry_run:
            try:
                row = collect_row(pair, target, previous_stamp, detail)
            except (OSError, ValueError) as error:
                row = BatchRow(pair.fit.name, error=str(error))
            rows.append(row)
            detail = row.error
        if detail:
            failures.append((pair.fit, detail))
            print(f"FAILED {pair.fit.name}: {detail}", file=sys.stderr, flush=True)
    action = "checked" if args.dry_run else "repair commands succeeded"
    print(f"\nBatch: {len(pairs) - len(failures)}/{len(pairs)} {action}; failed={len(failures)}")
    for fit, detail in failures:
        print(f"  {fit.name}: {detail}")
    if not args.dry_run:
        try:
            write_summary(rows, directory / "index.html", started_at=started_at)
        except OSError as error:
            print(f"cannot write batch report: {error}", file=sys.stderr)
            return 1
        print(f"Batch HTML report: {directory / 'index.html'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nBatch interrupted.", file=sys.stderr)
        raise SystemExit(130) from None
