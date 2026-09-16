"""Task 021B: opt-in approximate OSM choice without DEM or entry-point changes."""

import json
from dataclasses import replace

import pytest

from tests.local_reconstruction_factory import local_fixture
from tests.test_automatic_osm import AuditedRoutes, discover
from tests.test_osm_application import GRAPH, Routes, fixture
from warpbuster.config import AutomaticOSMApplicationConfig, GapCandidateRankingConfig
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import GapOrigin
from warpbuster.reconstruction import apply_automatic_osm_routes, build_repair_plan
from warpbuster.reconstruction import automatic_osm as automatic
from warpbuster.reconstruction.approximate_contract import POLICY_ID, ApproximateSelectionPolicy
from warpbuster.reconstruction.osm import OSMReconstructionProvider


def audit(plan):
    return json.loads(plan.automatic_osm_json)


def test_low_2d_evidence_becomes_soft_only_with_opt_in(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(
        activity,
        base,
        transform=lambda a, b: ((a[0] + 40 / 111195, a[1]), (b[0] + 40 / 111195, b[1])),
    )
    strict = GapCandidateRankingConfig(maximum_recommended_score=0)
    legacy = apply_automatic_osm_routes(activity, integrity, base, discovery, ranking_config=strict)
    assert not legacy.interval_plans
    policy = ApproximateSelectionPolicy(ranking=strict)
    selected = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=policy
    )
    assert (
        selected.interval_plans
        and selected.interval_plans[0].confidence is IntegrityConfidence.MEDIUM
    )
    decision = audit(selected)["decisions"][0]
    assert audit(selected)["policy"] == POLICY_ID
    assert decision["approximate"] is True
    assert decision["selection_mode"] == "approximate_low_evidence"
    assert "score_above_recommendation" in decision["approximate_audit"]["attempts"][0]["reasons"]
    written = write_repaired_fit(activity, selected, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert written.validation.valid and written.diff.unexpected_changed_field_count == 0


def test_ambiguous_alternatives_select_stably_and_keep_search_incomplete(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    policy = ApproximateSelectionPolicy()
    first = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=policy
    )
    evaluation = discovery.evaluations[0]
    reverse = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=tuple(reversed(evaluation.candidates))),),
    )
    second = apply_automatic_osm_routes(
        activity, integrity, base, reverse, approximate_policy=policy
    )
    assert first.interval_plans[0].coordinate_updates == second.interval_plans[0].coordinate_updates
    assert audit(first) == audit(second)
    decision = audit(first)["decisions"][0]
    assert decision["selection_mode"] == "approximate_tie_break"
    assert decision["approximate_audit"]["search_complete"] is False
    assert decision["approximate_audit"]["dem_status"] == "not_requested"


def test_failed_allocation_or_preflight_try_next_hard_safe_route(tmp_path, monkeypatch):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    allocate = automatic._allocate

    def fail_first(*args):
        if args[3].route_id == "route-0":
            from warpbuster.models.reconstruction import ReconstructionReason

            return ReconstructionReason.LOCAL_DISTANCE_INCONSISTENT
        return allocate(*args)

    monkeypatch.setattr(automatic, "_allocate", fail_first)
    selected = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    attempts = audit(selected)["decisions"][0]["approximate_audit"]["attempts"]
    assert [item["status"] for item in attempts] == ["rejected", "selected"]
    assert attempts[0]["reasons"] == ["allocation_rejected"]
    assert audit(selected)["decisions"][0]["selected_route_id"] == "route-1"

    monkeypatch.setattr(automatic, "_allocate", allocate)
    preflight = automatic._preflight

    def fail_first_preflight(activity, plan, candidate):
        if candidate.osm_provenance and candidate.osm_provenance.route_id == "route-0":
            return False
        return preflight(activity, plan, candidate)

    monkeypatch.setattr(automatic, "_preflight", fail_first_preflight)
    selected = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    attempts = audit(selected)["decisions"][0]["approximate_audit"]["attempts"]
    assert attempts[0]["reasons"] == ["writer_preflight_rejected"]
    assert attempts[1]["status"] == "selected"


def test_attempt_budget_and_high_threshold_remain_hard(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    policy = ApproximateSelectionPolicy(
        application=AutomaticOSMApplicationConfig(maximum_candidate_attempts=1)
    )
    high = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        minimum_confidence=IntegrityConfidence.HIGH,
        approximate_policy=policy,
    )
    assert not high.interval_plans
    assert (
        "confidence_threshold_too_high" in audit(high)["decisions"][0]["approximate_scope_reasons"]
    )
    with pytest.raises(ValueError):
        apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            approximate_policy=policy,
            config=AutomaticOSMApplicationConfig(),
        )


def test_attempt_limit_is_not_relaxed_by_opt_in(tmp_path, monkeypatch):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    from warpbuster.models.reconstruction import ReconstructionReason

    monkeypatch.setattr(
        automatic,
        "_allocate",
        lambda *_args: ReconstructionReason.LOCAL_DISTANCE_INCONSISTENT,
    )
    policy = ApproximateSelectionPolicy(
        application=AutomaticOSMApplicationConfig(maximum_candidate_attempts=1)
    )
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=policy
    )
    assert not plan.interval_plans
    decision = audit(plan)["decisions"][0]
    assert len(decision["attempts"]) == 1
    assert decision["approximate_audit"]["reasons"] == ["attempt_limit_reached"]


def test_gpx_first_is_unchanged_by_approximate_opt_in(tmp_path):
    activity, course, integrity, _, _ = fixture(tmp_path)
    base = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    discovery = discover(activity, base)
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    assert plan.interval_plans == base.interval_plans
    decision = audit(plan)["decisions"][0]
    assert decision["selection_mode"] == "gpx_first"
    assert decision["approximate_audit"]["selected_route_id"] is None


def test_routing_audit_and_connector_hard_gates_still_refuse(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    unverified = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    no_audit = apply_automatic_osm_routes(
        activity, integrity, base, unverified, approximate_policy=ApproximateSelectionPolicy()
    )
    assert not no_audit.interval_plans
    assert audit(no_audit)["decisions"][0]["approximate_audit"]["attempts"][0]["reasons"] == [
        "routing_audit_unusable"
    ]
    distant = discover(
        activity,
        base,
        transform=lambda a, b: ((a[0] + 150 / 111195, a[1]), (b[0] + 150 / 111195, b[1])),
    )
    refused = apply_automatic_osm_routes(
        activity, integrity, base, distant, approximate_policy=ApproximateSelectionPolicy()
    )
    assert not refused.interval_plans


@pytest.mark.parametrize(
    ("missing", "origin"),
    [(((201, 210),), GapOrigin.MIXED), ((), GapOrigin.INVALIDATED)],
)
def test_other_gap_origins_keep_legacy_path_even_with_opt_in(tmp_path, missing, origin):
    activity, _ = local_fixture(tmp_path, missing=missing, spikes=(200,))
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    assert base.gaps[0].origin is origin
    discovery = OSMReconstructionProvider(AuditedRoutes()).discover(activity, base, GRAPH)
    legacy = apply_automatic_osm_routes(activity, integrity, base, discovery)
    opt_in = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    assert opt_in.interval_plans == legacy.interval_plans
    assert opt_in.unresolved_gaps == legacy.unresolved_gaps
    assert "approximate" not in audit(opt_in)["decisions"][0]
    assert "gap_origin_unsupported" in audit(opt_in)["decisions"][0]["approximate_scope_reasons"]
