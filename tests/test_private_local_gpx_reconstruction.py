"""Additional available private regressions; algorithm acceptance remains synthetic."""

from pathlib import Path

import pytest

from warpbuster.fit.reader import read_fit
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.reconstruction import build_repair_plan


@pytest.mark.private
@pytest.mark.parametrize(
    "fit_name, course_name, expected_candidates",
    [
        ("m87_home_run.fit", "m87_home_run.gpx", 2),
        ("CWT_Dzhurla_2025_Taras.fit", "CWT_Dzhurla_2025.gpx", 1),
    ],
)
def test_available_private_local_candidates(
    fit_name: str, course_name: str, expected_candidates: int
) -> None:
    root = Path("tests/private/tracks")
    if not (root / fit_name).exists() or not (root / course_name).exists():
        pytest.skip("private FIT/course fixtures unavailable")
    activity = read_fit(root / fit_name)
    integrity = analyze_integrity(activity)
    no_course = build_repair_plan(activity, integrity)
    plan = build_repair_plan(
        activity, integrity, read_gpx_course(root / course_name), fill_missing_from_course=True
    )
    assert len(plan.interval_plans) == expected_candidates
    assert plan.coordinate_mask == no_course.coordinate_mask
    assert len(plan.gaps) == len(plan.interval_plans) + len(plan.unresolved_gaps)
    assert all(c.provenance.context_ranges for c in plan.interval_plans)
    assert (root / fit_name).read_bytes() == activity.preservation.raw_bytes
