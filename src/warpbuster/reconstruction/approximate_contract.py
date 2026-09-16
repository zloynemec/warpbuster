"""Task 021A: inert, typed contract for approximate original-missing OSM selection.

These assessments are preliminary. They never grant permission to edit FIT records:
allocation, physical checks and the writer's quantized preflight remain mandatory.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

from warpbuster.config import AutomaticOSMApplicationConfig, GapCandidateRankingConfig
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    CandidateProvider,
    CandidateRankingStatus,
    GapCandidateRanking,
    GapOrigin,
    RankedGapCandidate,
    ReconstructionGap,
)

POLICY_ID = "approximate-original-missing-osm-v1"


class DecisionReason(StrEnum):
    """Stable, privacy-safe reasons; hard and soft are separate namespaces."""

    # Hard preconditions, never waived by an approximate selection policy.
    GAP_ORIGIN_UNSUPPORTED = "gap_origin_unsupported"
    GAP_NOT_FULLY_ORIGINAL_MISSING = "gap_not_fully_original_missing"
    GAP_HAS_REASONS = "gap_has_reasons"
    TWO_ANCHORS_REQUIRED = "two_anchors_required"
    CONFIDENCE_THRESHOLD_TOO_HIGH = "confidence_threshold_too_high"
    GAP_ID_MISMATCH = "gap_id_mismatch"
    CANDIDATE_NOT_IN_RANKING = "candidate_not_in_ranking"
    PROVIDER_UNSUPPORTED = "provider_unsupported"
    RANKING_INELIGIBLE = "ranking_ineligible"
    RANKING_SCORE_MISSING = "ranking_score_missing"
    ROUTING_AUDIT_UNUSABLE = "routing_audit_unusable"
    ALLOCATION_REJECTED = "allocation_rejected"
    WRITER_PREFLIGHT_REJECTED = "writer_preflight_rejected"
    NO_ACCEPTABLE_ROUTE = "no_acceptable_route"
    ATTEMPT_LIMIT_REACHED = "attempt_limit_reached"
    # Soft uncertainty never authorizes a hard-gate bypass or forces refusal.
    AMBIGUOUS_2D = "ambiguous_2d"
    INSUFFICIENT_2D_EVIDENCE = "insufficient_2d_evidence"
    INCOMPLETE_SEARCH = "incomplete_search"
    SCORE_ABOVE_RECOMMENDATION = "score_above_recommendation"
    OBSERVED_COMPONENTS_BELOW_RECOMMENDATION = "observed_components_below_recommendation"
    DEM_UNINFORMATIVE = "dem_uninformative"
    DEM_UNAVAILABLE = "dem_unavailable"


HARD_REASONS = frozenset(
    (
        DecisionReason.GAP_ORIGIN_UNSUPPORTED,
        DecisionReason.GAP_NOT_FULLY_ORIGINAL_MISSING,
        DecisionReason.GAP_HAS_REASONS,
        DecisionReason.TWO_ANCHORS_REQUIRED,
        DecisionReason.CONFIDENCE_THRESHOLD_TOO_HIGH,
        DecisionReason.GAP_ID_MISMATCH,
        DecisionReason.CANDIDATE_NOT_IN_RANKING,
        DecisionReason.PROVIDER_UNSUPPORTED,
        DecisionReason.RANKING_INELIGIBLE,
        DecisionReason.RANKING_SCORE_MISSING,
        DecisionReason.ROUTING_AUDIT_UNUSABLE,
        DecisionReason.ALLOCATION_REJECTED,
        DecisionReason.WRITER_PREFLIGHT_REJECTED,
        DecisionReason.NO_ACCEPTABLE_ROUTE,
        DecisionReason.ATTEMPT_LIMIT_REACHED,
    )
)
SOFT_REASONS = frozenset(DecisionReason) - HARD_REASONS


class SelectionMode(StrEnum):
    GPX_FIRST = "gpx_first"
    RANKED = "ranked"
    APPROXIMATE_TIE_BREAK = "approximate_tie_break"
    APPROXIMATE_LOW_EVIDENCE = "approximate_low_evidence"


class AttemptStatus(StrEnum):
    REJECTED = "rejected"
    PREFLIGHT_ACCEPTED = "preflight_accepted"
    SELECTED = "selected"


class DemEvidenceStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    USABLE = "usable"
    UNINFORMATIVE = "uninformative"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DemComparisonConfig:
    """Named bounds for optional, offset-neutral comparison of FIT/DEM shapes.

    Defaults allow a modest ~30 m vertical-evidence span while requiring most
    observed records to be covered. They are policy inputs, not FIT-write gates.
    """

    minimum_aligned_samples: int = 5  # Distinct original FIT records.
    minimum_span_m: float = 30.0  # Route distance across aligned observations.
    minimum_coverage_fraction: float = 0.8  # Shared valid DEM/FIT observations.
    minimum_dem_advantage_m: float = 5.0  # Required reduction in median shape error.
    maximum_profiles_per_gap: int = 8  # Bounded DEM calls across near-best plans.

    def __post_init__(self) -> None:
        for name in ("minimum_aligned_samples", "maximum_profiles_per_gap"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("minimum_span_m", "minimum_dem_advantage_m"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{name} must be finite and positive metres")
        coverage = self.minimum_coverage_fraction
        if (
            isinstance(coverage, bool)
            or not isinstance(coverage, int | float)
            or not math.isfinite(coverage)
            or not 0 < coverage <= 1
        ):
            raise ValueError("minimum_coverage_fraction must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ApproximateSelectionPolicy:
    """Explicit opt-in policy identity; defaults reuse existing named limits."""

    ranking: GapCandidateRankingConfig = field(default_factory=GapCandidateRankingConfig)
    application: AutomaticOSMApplicationConfig = field(
        default_factory=AutomaticOSMApplicationConfig
    )
    dem: DemComparisonConfig = field(default_factory=DemComparisonConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.ranking, GapCandidateRankingConfig):
            raise TypeError("ranking must be GapCandidateRankingConfig")
        if not isinstance(self.application, AutomaticOSMApplicationConfig):
            raise TypeError("application must be AutomaticOSMApplicationConfig")
        if not isinstance(self.dem, DemComparisonConfig):
            raise TypeError("dem must be DemComparisonConfig")

    @property
    def policy_hash(self) -> str:
        document = {"policy_id": POLICY_ID, "config": asdict(self)}
        canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "sha256:" + sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PreliminaryAssessment:
    """Contract boundary only; ``eligible`` is not a FIT-write decision."""

    hard_reasons: tuple[DecisionReason, ...] = ()
    soft_reasons: tuple[DecisionReason, ...] = ()

    def __post_init__(self) -> None:
        if any(reason not in HARD_REASONS for reason in self.hard_reasons):
            raise ValueError("hard_reasons contains a soft reason")
        if any(reason not in SOFT_REASONS for reason in self.soft_reasons):
            raise ValueError("soft_reasons contains a hard reason")

    @property
    def eligible_for_attempt(self) -> bool:
        return not self.hard_reasons


def assess_gap_scope(
    gap: ReconstructionGap, minimum_confidence: IntegrityConfidence
) -> PreliminaryAssessment:
    """Check only independently established gap scope and requested threshold."""
    if not isinstance(minimum_confidence, IntegrityConfidence):
        raise TypeError("minimum_confidence must be IntegrityConfidence")
    reasons: list[DecisionReason] = []
    if gap.origin is not GapOrigin.ORIGINAL_MISSING:
        reasons.append(DecisionReason.GAP_ORIGIN_UNSUPPORTED)
    if gap.original_missing_count != gap.record_count or gap.invalidated_count != 0:
        reasons.append(DecisionReason.GAP_NOT_FULLY_ORIGINAL_MISSING)
    if gap.reasons:
        reasons.append(DecisionReason.GAP_HAS_REASONS)
    if gap.anchor_before_record_index is None or gap.anchor_after_record_index is None:
        reasons.append(DecisionReason.TWO_ANCHORS_REQUIRED)
    if minimum_confidence is IntegrityConfidence.HIGH:
        reasons.append(DecisionReason.CONFIDENCE_THRESHOLD_TOO_HIGH)
    return PreliminaryAssessment(tuple(reasons))


def assess_ranked_osm_candidate(
    ranking: GapCandidateRanking,
    candidate: RankedGapCandidate,
    policy: ApproximateSelectionPolicy,
    *,
    gap_id: str,
) -> PreliminaryAssessment:
    """Separate ranking hard refusal from soft advisory uncertainty.

    This does not assess routing audit, allocation or writer preflight; 021B must
    perform those independent hard gates before selecting a route.
    """
    hard: list[DecisionReason] = []
    soft: list[DecisionReason] = []
    if ranking.gap_id != gap_id:
        hard.append(DecisionReason.GAP_ID_MISMATCH)
    if candidate not in ranking.candidates:
        hard.append(DecisionReason.CANDIDATE_NOT_IN_RANKING)
    if candidate.provider is not CandidateProvider.OSM:
        hard.append(DecisionReason.PROVIDER_UNSUPPORTED)
    if not candidate.eligible:
        hard.append(DecisionReason.RANKING_INELIGIBLE)
    if candidate.score is None:
        hard.append(DecisionReason.RANKING_SCORE_MISSING)
    if ranking.status is CandidateRankingStatus.AMBIGUOUS:
        soft.append(DecisionReason.AMBIGUOUS_2D)
    if ranking.status is CandidateRankingStatus.INSUFFICIENT_EVIDENCE:
        soft.append(DecisionReason.INSUFFICIENT_2D_EVIDENCE)
    if not ranking.search_complete:
        soft.append(DecisionReason.INCOMPLETE_SEARCH)
    if candidate.score is not None and candidate.score > policy.ranking.maximum_recommended_score:
        soft.append(DecisionReason.SCORE_ABOVE_RECOMMENDATION)
    if candidate.observed_components < policy.ranking.minimum_observed_evidence_components:
        soft.append(DecisionReason.OBSERVED_COMPONENTS_BELOW_RECOMMENDATION)
    return PreliminaryAssessment(tuple(hard), tuple(soft))


@dataclass(frozen=True, slots=True)
class CandidateAttemptAudit:
    """No route geometry, FIT records or altitude samples in public audit."""

    route_id: str
    status: AttemptStatus
    score: int | None
    reasons: tuple[DecisionReason, ...] = ()
    dem_profile_id: str | None = None
    dem_shape_error_m: float | None = None
    dem_aligned_samples: int | None = None

    def __post_init__(self) -> None:
        if not self.route_id:
            raise ValueError("route_id must not be empty")
        if self.score is not None and (type(self.score) is not int or self.score < 0):
            raise ValueError("score must be a non-negative integer or None")
        if self.status is AttemptStatus.SELECTED and any(
            reason in HARD_REASONS for reason in self.reasons
        ):
            raise ValueError("selected route cannot carry a hard refusal")
        if self.dem_shape_error_m is not None and (
            not math.isfinite(self.dem_shape_error_m) or self.dem_shape_error_m < 0
        ):
            raise ValueError("DEM shape error must be finite non-negative metres")
        if self.dem_aligned_samples is not None and (
            type(self.dem_aligned_samples) is not int or self.dem_aligned_samples < 0
        ):
            raise ValueError("DEM aligned samples must be a non-negative integer")

    def as_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "status": self.status.value,
            "score": self.score,
            "reasons": [reason.value for reason in self.reasons],
            "dem_profile_id": self.dem_profile_id,
            "dem_shape_error_m": self.dem_shape_error_m,
            "dem_aligned_samples": self.dem_aligned_samples,
        }


@dataclass(frozen=True, slots=True)
class GapDecisionAudit:
    """Versioned gap-level audit schema for 021B-D to populate."""

    gap_id: str
    policy_hash: str
    selection_mode: SelectionMode | None
    selected_route_id: str | None
    search_complete: bool
    dem_status: DemEvidenceStatus = DemEvidenceStatus.NOT_REQUESTED
    graph_id: str | None = None
    dem_snapshot_id: str | None = None
    dem_profile_ids: tuple[str, ...] = ()
    attempts: tuple[CandidateAttemptAudit, ...] = ()
    reasons: tuple[DecisionReason, ...] = ()

    def __post_init__(self) -> None:
        if (
            not self.gap_id
            or len(self.policy_hash) != 71
            or not self.policy_hash.startswith("sha256:")
            or any(char not in "0123456789abcdef" for char in self.policy_hash[7:])
        ):
            raise ValueError("gap_id and policy_hash are required")
        if self.selection_mode is SelectionMode.GPX_FIRST:
            if self.selected_route_id is not None:
                raise ValueError("GPX-first has no OSM route ID")
        elif (self.selection_mode is None) != (self.selected_route_id is None):
            raise ValueError("OSM selection mode and route ID must both be set or absent")
        if any(not item for item in (self.graph_id, self.dem_snapshot_id) if item is not None):
            raise ValueError("graph and DEM snapshot IDs must not be empty")
        if any(not item for item in self.dem_profile_ids):
            raise ValueError("DEM profile IDs must not be empty")
        if self.dem_status is DemEvidenceStatus.USABLE and (
            self.dem_snapshot_id is None or not self.dem_profile_ids
        ):
            raise ValueError("usable DEM evidence requires a snapshot and profile IDs")
        if (
            self.selected_route_id is not None
            and self.selection_mode is not SelectionMode.GPX_FIRST
        ):
            selected = [
                attempt for attempt in self.attempts if attempt.status is AttemptStatus.SELECTED
            ]
            if len(selected) != 1 or selected[0].route_id != self.selected_route_id:
                raise ValueError("selected OSM route must match exactly one selected attempt")

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": POLICY_ID,
            "policy_hash": self.policy_hash,
            "gap_id": self.gap_id,
            "selection_mode": self.selection_mode.value if self.selection_mode else None,
            "selected_route_id": self.selected_route_id,
            "search_complete": self.search_complete,
            "dem_status": self.dem_status.value,
            "graph_id": self.graph_id,
            "dem_snapshot_id": self.dem_snapshot_id,
            "dem_profile_ids": list(self.dem_profile_ids),
            "attempted_count": len(self.attempts),
            "rejected_count": sum(
                attempt.status is AttemptStatus.REJECTED for attempt in self.attempts
            ),
            "attempts": [attempt.as_dict() for attempt in self.attempts],
            "reasons": [reason.value for reason in self.reasons],
        }
