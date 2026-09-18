"""Enrich existing public reports from retained files without rewriting any FIT."""

import json
import os
import time

from warpbuster.fit.reader import read_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.pipeline import run_repair
from warpbuster.report.gaps import distance_policy

from .config import WebConfig
from .performance import public_performance
from .store import Store


def refresh_report(directory, inputs, *, has_course=True):
    path = directory / "result.json"
    report = json.loads(path.read_text())
    if report.get("schema_version", 1) >= 2:
        return False
    original = read_fit(inputs / "original.fit") if (inputs / "original.fit").is_file() else None
    fixed = (
        read_fit(directory / "corrected.fit") if (directory / "corrected.fit").is_file() else None
    )
    course = (
        read_gpx_course(inputs / "course.gpx")
        if has_course and (inputs / "course.gpx").is_file()
        else None
    )
    quality = "unknown"
    if original and (course or not has_course):
        run = run_repair(
            inputs / "original.fit", inputs / "course.gpx" if has_course else None, dry_run=True
        )
        quality = distance_policy(run.selection)["quality"]
    report["tracks"]["course"] = (
        [
            [[point.latitude, point.longitude] for point in segment.points]
            for segment in course.segments
        ]
        if course
        else []
    )
    activity = fixed if fixed else original
    report["performance"] = (
        public_performance(
            activity, source="corrected" if fixed else "original", distance_quality=quality
        )
        if activity
        else None
    )
    report["schema_version"] = 2
    temporary = directory / "result.refresh.tmp"
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def main():
    os.umask(0o077)
    store = Store(WebConfig.from_environment())
    with store.connect() as db:
        rows = db.execute(
            "SELECT uid,has_course FROM jobs WHERE status='ready' AND expires>?", (time.time(),)
        ).fetchall()
    refreshed = 0
    failed = 0
    for row in rows:
        uid = row["uid"]
        try:
            changed = refresh_report(
                store.config.data_dir / uid,
                store.uploads_dir / uid,
                has_course=bool(row["has_course"]),
            )
            if changed:
                store.events.write("report_refreshed", uid, schema_version=2)
                refreshed += 1
        except Exception:
            # Preserve the existing report and log only a non-sensitive code.
            store.events.write("report_refresh_failed", uid)
            failed += 1
    print(json.dumps({"refreshed": refreshed, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
