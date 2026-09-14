"""Provider-neutral ranking stays advisory, coarse and deterministic."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig, GapCandidateRankingConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.reconstruction import (
    CandidateProvider,
    CandidateRankingStatus,
    OSMAnchor,
    OSMDryRunResult,
    OSMDryRunStatus,
    OSMGapEvaluation,
    OSMGapOutcome,
    OSMPathPoint,
    OSMRouteCandidate,
)
from warpbuster.reconstruction import build_repair_plan, rank_gap_candidates
from warpbuster.report.repair import repair_report


def _fixture(tmp_path: Path):
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        fill_missing_from_course=True,
    )
    return activity, course, plan


def _osm(plan, points, *, route_id="osm-1", role="primary", exhaustive=False):
    candidate = OSMRouteCandidate(
        route_id,
        role,
        tuple(OSMPathPoint(*point) for point in points),
        json.dumps({"route_id": route_id, "role": role, "summary": {"length_m": 62.0}}),
    )
    gap = plan.gaps[0]
    before = plan.interval_plans[0].provenance.anchor_before
    after = plan.interval_plans[0].provenance.anchor_after
    routing = json.dumps({"search": {"exhaustive": exhaustive}})
    evaluation = OSMGapEvaluation(
        gap,
        OSMGapOutcome.CANDIDATES_AVAILABLE,
        True,
        OSMAnchor(gap.anchor_before_record_index, before.latitude, before.longitude),
        OSMAnchor(gap.anchor_after_record_index, after.latitude, after.longitude),
        (candidate,),
        route_status="READY",
        _routing_document_json=routing,
    )
    return OSMDryRunResult(
        "graph", OSMDryRunStatus.CANDIDATES_AVAILABLE, (evaluation,), 2, 32, 100_000
    )


def _gpx_points(plan):
    item = plan.interval_plans[0]
    provenance = item.provenance
    return (
        (provenance.anchor_before.latitude, provenance.anchor_before.longitude),
        *(
            (point.candidate_latitude, point.candidate_longitude)
            for point in item.coordinate_updates
        ),
        (provenance.anchor_after.latitude, provenance.anchor_after.longitude),
    )


def test_single_good_gpx_is_recommended_without_changing_plan(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    original = plan
    result = rank_gap_candidates(activity, plan, None)
    ranking = result.rankings[0]
    assert ranking.status is CandidateRankingStatus.RECOMMENDED
    assert ranking.candidates[0].provider is CandidateProvider.GPX
    assert ranking.recommended_candidate_id == ranking.candidates[0].candidate_id
    assert not result.application_allowed
    assert plan == original


def test_equal_gpx_and_osm_are_ambiguous_and_provider_has_no_bonus(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    result = rank_gap_candidates(activity, plan, _osm(plan, _gpx_points(plan), exhaustive=True))
    ranking = result.rankings[0]
    assert ranking.status is CandidateRankingStatus.AMBIGUOUS
    assert ranking.recommended_candidate_id is None
    assert {candidate.provider for candidate in ranking.candidates} == {
        CandidateProvider.GPX,
        CandidateProvider.OSM,
    }
    assert len({candidate.score for candidate in ranking.candidates}) == 1


def test_role_and_input_order_do_not_change_ranking(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    points = _gpx_points(plan)
    first = _osm(plan, points, route_id="a", role="primary", exhaustive=True)
    second_candidate = replace(first.evaluations[0].candidates[0], route_id="b", role="alternative")
    evaluation = replace(
        first.evaluations[0],
        candidates=(first.evaluations[0].candidates[0], second_candidate),
    )
    a = rank_gap_candidates(activity, plan, replace(first, evaluations=(evaluation,)))
    swapped = replace(
        evaluation,
        candidates=(
            replace(second_candidate, role="primary"),
            replace(evaluation.candidates[0], role="alternative"),
        ),
    )
    b = rank_gap_candidates(activity, plan, replace(first, evaluations=(swapped,)))

    def view(result):
        return sorted(
            (item.candidate_id, item.score, item.provider.value)
            for item in result.rankings[0].candidates
        )

    assert view(a) == view(b)
    assert a.rankings[0].status == b.rankings[0].status


def test_connector_over_100_metres_is_ineligible(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    far = tuple((latitude + 0.01, longitude) for latitude, longitude in _gpx_points(plan))
    result = rank_gap_candidates(activity, replace(plan, interval_plans=()), _osm(plan, far))
    candidate = result.rankings[0].candidates[0]
    assert not candidate.eligible
    assert "connector_too_long" in candidate.reasons
    assert result.rankings[0].status is CandidateRankingStatus.NO_ELIGIBLE_CANDIDATES


def test_scope_mismatch_and_failed_routing_audit_are_ineligible(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    osm = _osm(plan, _gpx_points(plan))
    candidate = replace(
        osm.evaluations[0].candidates[0],
        _document_json=json.dumps({"audit": {"status": "FAIL"}}),
    )
    failed = replace(osm.evaluations[0], candidates=(candidate,))
    result = rank_gap_candidates(
        activity, replace(plan, interval_plans=()), replace(osm, evaluations=(failed,))
    )
    assert result.rankings[0].candidates[0].reasons == ("routing_audit_failed",)

    wrong_scope = replace(
        osm.evaluations[0],
        interval=replace(osm.evaluations[0].interval, end_record_index=178),
    )
    result = rank_gap_candidates(
        activity, replace(plan, interval_plans=()), replace(osm, evaluations=(wrong_scope,))
    )
    assert result.rankings[0].candidates[0].reasons == ("scope_mismatch",)


def test_small_coordinate_changes_within_bands_do_not_change_score(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    points = _gpx_points(plan)
    shifted = tuple((latitude + 0.000001, longitude) for latitude, longitude in points)
    one = rank_gap_candidates(activity, replace(plan, interval_plans=()), _osm(plan, points))
    two = rank_gap_candidates(activity, replace(plan, interval_plans=()), _osm(plan, shifted))
    assert one.rankings[0].candidates[0].score == two.rankings[0].candidates[0].score


def test_candidate_and_point_limits_refuse_arbitrary_truncation(tmp_path: Path) -> None:
    activity, _, plan = _fixture(tmp_path)
    osm = _osm(plan, _gpx_points(plan))
    candidate = osm.evaluations[0].candidates[0]
    many = replace(osm.evaluations[0], candidates=(candidate, replace(candidate, route_id="two")))
    limited = rank_gap_candidates(
        activity,
        replace(plan, interval_plans=()),
        replace(osm, evaluations=(many,)),
        config=GapCandidateRankingConfig(maximum_candidates_per_gap=1),
    )
    assert limited.rankings[0].status is CandidateRankingStatus.RESOURCE_LIMIT
    assert not limited.rankings[0].search_complete


@pytest.mark.parametrize(
    "values",
    [
        {"maximum_connector_m": 0},
        {"connector_good_m": 60, "connector_fair_m": 50},
        {"direction_fair_degrees": 181},
        {"minimum_observed_evidence_components": 5},
        {"distance_relative_tolerance": 1.1},
    ],
)
def test_invalid_ranking_config_is_rejected(values) -> None:
    with pytest.raises(ValueError):
        GapCandidateRankingConfig(**values)


def test_report_is_additive_and_reproducible(tmp_path: Path) -> None:
    activity, course, plan = _fixture(tmp_path)
    result = rank_gap_candidates(activity, plan, _osm(plan, _gpx_points(plan)))
    report = repair_report(
        plan,
        course,
        CourseReconstructionConfig(),
        osm_result=_osm(plan, _gpx_points(plan)),
        ranking_result=result,
    )
    assert report["candidate_ranking"]["application_allowed"] is False
    assert report["candidate_ranking"]["rankings"][0]["status"] == "ambiguous"
    assert result == rank_gap_candidates(activity, plan, _osm(plan, _gpx_points(plan)))


def test_20k_record_ranking_stays_bounded(tmp_path: Path) -> None:
    missing = tuple((1000 + index * 1800, 1029 + index * 1800) for index in range(10))
    activity, course = local_fixture(tmp_path, count=20_000, missing=missing)
    plan = build_repair_plan(
        activity,
        analyze_integrity(activity),
        course,
        fill_missing_from_course=True,
    )
    started = perf_counter()
    result = rank_gap_candidates(activity, plan, None)
    elapsed = perf_counter() - started
    assert len(result.rankings) == len(missing)
    assert elapsed < 5.0
