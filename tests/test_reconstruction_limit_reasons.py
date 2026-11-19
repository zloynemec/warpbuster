"""Specific budget reasons retain the existing report and legacy-read contracts."""

import json
from dataclasses import replace

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.reconstruction import ReconstructionReason, UnresolvedGap
from warpbuster.reconstruction import build_repair_plan
from warpbuster.report.html import write_repair_html
from warpbuster.report.repair import repair_json


def test_legacy_search_limit_reason_remains_readable():
    assert ReconstructionReason("search_limit_reached") is ReconstructionReason.SEARCH_LIMIT_REACHED


@pytest.mark.parametrize(
    "reason",
    [
        "reconstruction_interval_limit_exceeded",
        "gap_record_limit",
        "anchor_candidate_limit",
        "path_evaluation_limit",
    ],
)
def test_specific_limits_survive_json_and_html(tmp_path, reason):
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    integrity = analyze_integrity(activity)
    config = CourseReconstructionConfig()
    base = build_repair_plan(activity, integrity, None)
    plan = replace(
        base, unresolved_gaps=(UnresolvedGap(base.gaps[0], (ReconstructionReason(reason),)),)
    )
    report = json.loads(repair_json(plan, course, config))
    assert report["schema_version"] == "0.1"
    assert report["gap_inventory"][0]["reasons"] == [reason]
    path = write_repair_html(activity, integrity, course, plan, config, tmp_path / "report.html")
    payload = json.loads(
        path.read_text()
        .split('<script id="warpbuster-report-data" type="application/json">', 1)[1]
        .split("</script>", 1)[0]
    )
    assert payload["repair"]["gap_inventory"][0]["reasons"] == [reason]
    assert not list(tmp_path.glob("*.fixed.fit"))
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes
