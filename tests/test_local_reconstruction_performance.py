"""Reproducible 20k-record normalize/detect/plan/report baseline (pytest -s)."""

from pathlib import Path
from time import perf_counter

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.reader import read_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.reconstruction import build_repair_plan
from warpbuster.report.html import write_repair_html


def test_many_local_gaps_20k_baseline(tmp_path: Path) -> None:
    missing = ((0, 19), *((i, i + 29) for i in range(1000, 19000, 2000)), (19980, 19999))
    prepared, course = local_fixture(tmp_path, count=20_000, missing=missing)
    start = perf_counter()
    activity = read_fit(prepared.preservation.source_path)
    normalized = perf_counter()
    integrity = analyze_integrity(activity)
    detected = perf_counter()
    config = CourseReconstructionConfig()
    plan = build_repair_plan(activity, integrity, course, config, fill_missing_from_course=True)
    planned = perf_counter()
    write_repair_html(
        activity,
        integrity,
        course,
        plan,
        config,
        tmp_path / "baseline.html",
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    reported = perf_counter()
    print(
        f"20k records / {len(missing)} gaps: normalize={normalized - start:.3f}s detect={detected - normalized:.3f}s plan={planned - detected:.3f}s html={reported - planned:.3f}s"
    )
    assert detected - normalized < 5.0
    assert len(plan.interval_plans) == len(missing)
    assert not plan.unresolved_gaps
    assert all(
        end - start + 1 <= config.local_alignment_max_context_records
        for c in plan.interval_plans
        for start, end in c.provenance.context_ranges
    )
