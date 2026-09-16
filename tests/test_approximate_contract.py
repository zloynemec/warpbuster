"""Synthetic contract checks for Task 021A; application remains unchanged."""

from dataclasses import replace

import pytest

from warpbuster.config import GapCandidateRankingConfig
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    CandidateProvider,
    CandidateRankingStatus,
    GapCandidateRanking,
    GapOrigin,
    MissingCourseRunKind,
    OSMPathPoint,
    RankedGapCandidate,
    ReconstructionGap,
)
from warpbuster.reconstruction.approximate_contract import (
    POLICY_ID,
    ApproximateSelectionPolicy,
    AttemptStatus,
    CandidateAttemptAudit,
    DecisionReason,
    DemEvidenceStatus,
    GapDecisionAudit,
    PreliminaryAssessment,
    SelectionMode,
    assess_gap_scope,
    assess_ranked_osm_candidate,
)


def gap() -> ReconstructionGap:
    return ReconstructionGap(
        gap_id="synthetic-gap",
        start_record_index=1,
        end_record_index=2,
        start_timestamp=None,
        end_timestamp=None,
        kind=MissingCourseRunKind.INTERNAL,
        origin=GapOrigin.ORIGINAL_MISSING,
        continuity_id=0,
        anchor_before_record_index=0,
        anchor_after_record_index=3,
        original_missing_count=2,
        invalidated_count=0,
        invalidation_confidence=None,
    )


def candidate(**changes: object) -> RankedGapCandidate:
    value = RankedGapCandidate(
        candidate_id="synthetic-candidate",
        provider=CandidateProvider.OSM,
        eligible=True,
        score=3,
        observed_components=1,
        component_scores=(),
        evidence_json="{}",
        reasons=(),
        coordinates=(OSMPathPoint(0.0, 0.0), OSMPathPoint(0.0, 0.01)),
        source_candidate_id="route-a",
    )
    return replace(value, **changes)


def ranking(**changes: object) -> GapCandidateRanking:
    value = GapCandidateRanking(
        gap_id="synthetic-gap",
        status=CandidateRankingStatus.AMBIGUOUS,
        candidates=(candidate(),),
        search_complete=False,
    )
    return replace(value, **changes)


def test_policy_identity_is_stable_and_config_sensitive() -> None:
    base = ApproximateSelectionPolicy()
    assert POLICY_ID == "approximate-original-missing-osm-v1"
    assert base.policy_hash == ApproximateSelectionPolicy().policy_hash
    changed = ApproximateSelectionPolicy(
        ranking=GapCandidateRankingConfig(maximum_recommended_score=6)
    )
    assert changed.policy_hash != base.policy_hash
    with pytest.raises(TypeError):
        ApproximateSelectionPolicy(ranking="invalid")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changed", "confidence", "expected"),
    [
        ({}, IntegrityConfidence.MEDIUM, ()),
        (
            {"origin": GapOrigin.INVALIDATED},
            IntegrityConfidence.MEDIUM,
            (DecisionReason.GAP_ORIGIN_UNSUPPORTED,),
        ),
        (
            {"origin": GapOrigin.MIXED, "invalidated_count": 1},
            IntegrityConfidence.MEDIUM,
            (DecisionReason.GAP_ORIGIN_UNSUPPORTED, DecisionReason.GAP_NOT_FULLY_ORIGINAL_MISSING),
        ),
        (
            {"original_missing_count": 1},
            IntegrityConfidence.MEDIUM,
            (DecisionReason.GAP_NOT_FULLY_ORIGINAL_MISSING,),
        ),
        (
            {"anchor_after_record_index": None},
            IntegrityConfidence.MEDIUM,
            (DecisionReason.TWO_ANCHORS_REQUIRED,),
        ),
        ({}, IntegrityConfidence.HIGH, (DecisionReason.CONFIDENCE_THRESHOLD_TOO_HIGH,)),
    ],
)
def test_scope_hard_gates_are_independent_of_route_quality(changed, confidence, expected) -> None:
    assessment = assess_gap_scope(replace(gap(), **changed), confidence)
    assert assessment.hard_reasons == expected
    assert assessment.eligible_for_attempt is (not expected)


def test_2d_ambiguity_and_low_evidence_are_soft_not_write_permission() -> None:
    policy = ApproximateSelectionPolicy()
    result = assess_ranked_osm_candidate(ranking(), candidate(), policy, gap_id="synthetic-gap")
    assert result.eligible_for_attempt
    assert result.hard_reasons == ()
    assert result.soft_reasons == (
        DecisionReason.AMBIGUOUS_2D,
        DecisionReason.INCOMPLETE_SEARCH,
        DecisionReason.OBSERVED_COMPONENTS_BELOW_RECOMMENDATION,
    )
    low_candidate = candidate(score=policy.ranking.maximum_recommended_score + 1)
    low = assess_ranked_osm_candidate(
        ranking(status=CandidateRankingStatus.INSUFFICIENT_EVIDENCE, candidates=(low_candidate,)),
        low_candidate,
        policy,
        gap_id="synthetic-gap",
    )
    assert low.eligible_for_attempt
    assert DecisionReason.INSUFFICIENT_2D_EVIDENCE in low.soft_reasons
    assert DecisionReason.SCORE_ABOVE_RECOMMENDATION in low.soft_reasons


def test_ranking_ineligibility_and_scope_mismatch_remain_hard() -> None:
    result = assess_ranked_osm_candidate(
        ranking(gap_id="other"),
        candidate(provider=CandidateProvider.GPX, eligible=False, score=None),
        ApproximateSelectionPolicy(),
        gap_id="synthetic-gap",
    )
    assert result.hard_reasons == (
        DecisionReason.GAP_ID_MISMATCH,
        DecisionReason.CANDIDATE_NOT_IN_RANKING,
        DecisionReason.PROVIDER_UNSUPPORTED,
        DecisionReason.RANKING_INELIGIBLE,
        DecisionReason.RANKING_SCORE_MISSING,
    )
    assert not result.eligible_for_attempt
    with pytest.raises(ValueError):
        PreliminaryAssessment(hard_reasons=(DecisionReason.AMBIGUOUS_2D,))


def test_candidate_from_another_ranking_is_hard_rejected() -> None:
    result = assess_ranked_osm_candidate(
        ranking(),
        candidate(source_candidate_id="route-b"),
        ApproximateSelectionPolicy(),
        gap_id="synthetic-gap",
    )
    assert result.hard_reasons == (DecisionReason.CANDIDATE_NOT_IN_RANKING,)


def test_audit_schema_is_deterministic_privacy_safe_and_validated() -> None:
    policy_hash = ApproximateSelectionPolicy().policy_hash
    attempts = (
        CandidateAttemptAudit(
            "route-a", AttemptStatus.REJECTED, 3, (DecisionReason.WRITER_PREFLIGHT_REJECTED,)
        ),
        CandidateAttemptAudit("route-b", AttemptStatus.SELECTED, 3, (DecisionReason.AMBIGUOUS_2D,)),
    )
    audit = GapDecisionAudit(
        gap_id="synthetic-gap",
        policy_hash=policy_hash,
        selection_mode=SelectionMode.APPROXIMATE_TIE_BREAK,
        selected_route_id="route-b",
        search_complete=False,
        dem_status=DemEvidenceStatus.UNAVAILABLE,
        attempts=attempts,
        reasons=(DecisionReason.INCOMPLETE_SEARCH,),
    )
    document = audit.as_dict()
    assert document == audit.as_dict()
    assert document["policy_id"] == POLICY_ID
    assert document["selected_route_id"] == "route-b"
    assert document["attempted_count"] == 2
    assert document["rejected_count"] == 1
    assert document["attempts"][0]["reasons"] == ["writer_preflight_rejected"]
    assert "coordinates" not in str(document)
    assert "altitude" not in str(document)
    with pytest.raises(ValueError):
        replace(audit, selected_route_id="route-a")
    with pytest.raises(ValueError):
        CandidateAttemptAudit(
            "route-a", AttemptStatus.SELECTED, 3, (DecisionReason.ALLOCATION_REJECTED,)
        )
    with pytest.raises(ValueError):
        replace(audit, dem_status=DemEvidenceStatus.USABLE)
    usable = replace(
        audit,
        dem_status=DemEvidenceStatus.USABLE,
        dem_snapshot_id="dem-snapshot",
        dem_profile_ids=("profile-a", "profile-b"),
    )
    assert usable.as_dict()["dem_profile_ids"] == ["profile-a", "profile-b"]
