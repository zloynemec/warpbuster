"""Enrich existing public reports from retained files without rewriting any FIT."""

import json
import os
import time

from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.reconstruction.local import build_repair_plan
from warpbuster.reconstruction.selection import select_repair_intervals
from warpbuster.report.gaps import distance_policy

from .config import REPAIR_POLICY, WebConfig
from .performance import public_performance
from .store import Store


def refresh_report(directory, inputs):
    path = directory / "result.json"
    report = json.loads(path.read_text())
    if report.get("schema_version", 1) >= 2:
        return False
    original = read_fit(inputs / "original.fit") if (inputs / "original.fit").is_file() else None
    fixed = (
        read_fit(directory / "corrected.fit") if (directory / "corrected.fit").is_file() else None
    )
    course = read_gpx_course(inputs / "course.gpx") if (inputs / "course.gpx").is_file() else None
    quality = "unknown"
    if original and course:
        plan = build_repair_plan(
            original,
            analyze_integrity(original),
            course,
            CourseReconstructionConfig(),
            fill_missing_from_course=REPAIR_POLICY["fill_missing_from_course"],
            minimum_invalidation_confidence=IntegrityConfidence(
                REPAIR_POLICY["minimum_invalidation_confidence"]
            ),
        )
        quality = distance_policy(
            select_repair_intervals(plan, IntegrityConfidence(REPAIR_POLICY["minimum_confidence"]))
        )["quality"]
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
            "SELECT uid FROM jobs WHERE status='ready' AND expires>?", (time.time(),)
        ).fetchall()
    refreshed = 0
    failed = 0
    for row in rows:
        uid = row["uid"]
        try:
            changed = refresh_report(store.config.data_dir / uid, store.uploads_dir / uid)
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
