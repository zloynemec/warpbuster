"""GPX-first automatic application of approximate OSM path hypotheses."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from math import isfinite
from typing import Any

from warpbuster.config import AutomaticOSMApplicationConfig, GapCandidateRankingConfig
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, IntegrityReport
from warpbuster.models.reconstruction import (
    GapRepairPlan,
    OSMDryRunResult,
    OSMGapOutcome,
    RankedGapCandidate,
    RepairPlan,
    RepairPlanStatus,
    UnresolvedGap,
)
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction.approximate_contract import (
    POLICY_ID,
    ApproximateSelectionPolicy,
    AttemptStatus,
    CandidateAttemptAudit,
    DecisionReason,
    DemEvidenceStatus,
    GapDecisionAudit,
    SelectionMode,
    assess_gap_scope,
    assess_ranked_osm_candidate,
)
from warpbuster.reconstruction.candidate_ranking import rank_gap_candidates
from warpbuster.reconstruction.dem_choice import DemChoice, DemSampler, compare_dem_candidates
from warpbuster.reconstruction.gaps import (
    CONFIDENCE_RANK,
    coordinate_mask,
    inventory_gaps,
    position_fields_patchable,
)
from warpbuster.reconstruction.osm import osm_gap_anchors
from warpbuster.reconstruction.osm_application import _allocate, _json, _snapshot
from warpbuster.reconstruction.selection import select_repair_intervals


def _order(candidate: RankedGapCandidate) -> tuple[float, float, str]:
    evidence: Any = candidate.evidence()
    return (
        candidate.score if candidate.score is not None else float("inf"),
        float(evidence.get("length", {}).get("total_m", float("inf"))),
        candidate.source_candidate_id,
    )


def _preflight(activity: ActivityData, plan: RepairPlan, candidate: GapRepairPlan) -> bool:
    # Use the writer's exact quantized-edge check for dry-run and write alike.
    from warpbuster.fit.writer import FitWriteError, _validate_composed_geometry

    isolated = replace(plan, interval_plans=(candidate,), unresolved_gaps=())
    try:
        _validate_composed_geometry(
            activity, isolated, select_repair_intervals(isolated, IntegrityConfidence.MEDIUM)
        )
    except FitWriteError:
        return False
    return True


def _preflight_composed(
    activity: ActivityData, plan: RepairPlan, candidates: dict[str, GapRepairPlan]
) -> bool:
    """Check already selected gaps together, not just the candidate in isolation."""
    from warpbuster.fit.writer import FitWriteError, _validate_composed_geometry

    composed = replace(
        plan,
        interval_plans=tuple(candidates[g.gap_id] for g in plan.gaps if g.gap_id in candidates),
        unresolved_gaps=(),
    )
    try:
        _validate_composed_geometry(
            activity, composed, select_repair_intervals(composed, IntegrityConfidence.MEDIUM)
        )
    except FitWriteError:
        return False
    return True


def _approximate_mode(reasons: tuple[DecisionReason, ...]) -> SelectionMode:
    if any(
        reason
        in {
            DecisionReason.INSUFFICIENT_2D_EVIDENCE,
            DecisionReason.SCORE_ABOVE_RECOMMENDATION,
            DecisionReason.OBSERVED_COMPONENTS_BELOW_RECOMMENDATION,
        }
        for reason in reasons
    ):
        return SelectionMode.APPROXIMATE_LOW_EVIDENCE
    if DecisionReason.AMBIGUOUS_2D in reasons:
        return SelectionMode.APPROXIMATE_TIE_BREAK
    return SelectionMode.RANKED


def apply_automatic_osm_routes(
    activity: ActivityData,
    integrity: IntegrityReport,
    plan: RepairPlan,
    discovery: OSMDryRunResult,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.MEDIUM,
    config: AutomaticOSMApplicationConfig | None = None,
    ranking_config: GapCandidateRankingConfig | None = None,
    approximate_policy: ApproximateSelectionPolicy | None = None,
    dem_sampler: DemSampler | None = None,
    dem_snapshot_id: str | None = None,
) -> RepairPlan:
    """Return a final plan without writing or asserting confirmed route identity."""
    if approximate_policy is None and (dem_sampler is not None or dem_snapshot_id is not None):
        raise ValueError("DEM comparison requires approximate_policy")
    if approximate_policy is not None and (config is not None or ranking_config is not None):
        raise ValueError("approximate_policy cannot be combined with legacy config overrides")
    cfg = (
        approximate_policy.application
        if approximate_policy
        else config or AutomaticOSMApplicationConfig()
    )
    ranking_cfg = (
        approximate_policy.ranking
        if approximate_policy
        else ranking_config or GapCandidateRankingConfig()
    )
    mask = coordinate_mask(activity, integrity, plan.minimum_invalidation_confidence)
    if (
        plan.activity_path != activity.preservation.source_path
        or plan.coordinate_mask != mask
        or plan.gaps != inventory_gaps(activity, mask)
        or tuple(e.interval for e in discovery.evaluations) != plan.gaps
        or any(not isinstance(c, GapRepairPlan) for c in plan.interval_plans)
    ):
        raise ValueError("automatic OSM requires the same independent source/mask/gap snapshot")
    candidates = {c.interval.gap_id: c for c in plan.interval_plans if isinstance(c, GapRepairPlan)}
    if len(candidates) != len(plan.interval_plans):
        raise ValueError("duplicate gap candidates")
    failures = {f.interval.gap_id: f for f in plan.unresolved_gaps}
    selected = select_repair_intervals(plan, minimum_confidence).selected_interval_plans
    ranking = rank_gap_candidates(
        activity,
        replace(plan, interval_plans=()),
        discovery,
        config=ranking_cfg,
        integrity_config=integrity.config,
    )
    snapshot = _snapshot(activity, plan)
    decisions: list[dict[str, Any]] = []
    for evaluation, ranked in zip(discovery.evaluations, ranking.rankings, strict=True):
        gap = evaluation.interval
        scope = assess_gap_scope(gap, minimum_confidence) if approximate_policy else None
        approximate = scope is not None and scope.eligible_for_attempt
        typed_attempts: list[CandidateAttemptAudit] = []
        accepted_for_dem: list[tuple[RankedGapCandidate, GapRepairPlan, dict[str, Any]]] = []
        dem_status = DemEvidenceStatus.NOT_REQUESTED
        dem_profile_ids: tuple[str, ...] = ()
        dem_collect = False
        if approximate and (dem_sampler is not None or dem_snapshot_id is not None):
            if dem_sampler is None or not dem_snapshot_id:
                dem_status = DemEvidenceStatus.UNAVAILABLE
            elif approximate_policy is not None:
                observed_count = sum(
                    record.altitude is not None and isfinite(record.altitude)
                    for record in activity.records[
                        gap.start_record_index : gap.end_record_index + 1
                    ]
                )
                if observed_count >= approximate_policy.dem.minimum_aligned_samples:
                    dem_collect = True
                else:
                    dem_status = DemEvidenceStatus.UNINFORMATIVE
        decision: dict[str, Any] = {"gap_id": gap.gap_id, "status": "unresolved", "attempts": []}
        decisions.append(decision)
        if scope is not None and scope.hard_reasons:
            decision["approximate_scope_reasons"] = [reason.value for reason in scope.hard_reasons]
        existing = candidates.get(gap.gap_id)
        if existing is not None and existing in selected:
            if _preflight(activity, plan, existing):
                decision.update(status="gpx_selected", provider="gpx", reason="gpx_first")
                if approximate_policy is not None:
                    decision["selection_mode"] = SelectionMode.GPX_FIRST.value
                    decision["approximate_audit"] = GapDecisionAudit(
                        gap.gap_id,
                        approximate_policy.policy_hash,
                        SelectionMode.GPX_FIRST,
                        None,
                        ranked.search_complete,
                        graph_id=discovery.graph_id,
                    ).as_dict()
                continue
            decision["gpx_rejection"] = "candidate_transition_implausible"
            candidates.pop(gap.gap_id)
        anchors, anchor_reason = osm_gap_anchors(activity, plan, gap)
        reason = Reason.OSM_NO_ACCEPTABLE_ROUTE
        blocked: str | None = None
        if anchors is None or anchor_reason is not None or gap.reasons:
            blocked = "unusable_gap_or_anchors"
            reason = gap.reasons[0] if gap.reasons else Reason.NO_TRUSTED_LOCAL_ANCHOR
        elif (evaluation.anchor_before, evaluation.anchor_after) != anchors:
            blocked = "anchor_mismatch"
            reason = Reason.NO_TRUSTED_LOCAL_ANCHOR
        elif gap.record_count > cfg.maximum_gap_records:
            blocked = "gap_record_limit"
            reason = Reason.SEARCH_LIMIT_REACHED
        elif not position_fields_patchable(activity, gap):
            blocked = "position_fields_unpatchable"
            reason = Reason.POSITION_FIELDS_UNPATCHABLE
        elif evaluation.outcome is not OSMGapOutcome.CANDIDATES_AVAILABLE:
            blocked = "osm_discovery_unresolved"
            reason = Reason.OSM_DISCOVERY_UNRESOLVED
        elif len({r.route_id for r in evaluation.candidates}) != len(evaluation.candidates):
            blocked = "duplicate_route_id"
        elif CONFIDENCE_RANK[minimum_confidence] > CONFIDENCE_RANK[IntegrityConfidence.MEDIUM]:
            blocked = "automatic_medium_below_threshold"
        elif not ranked.candidates:
            blocked = ",".join(ranked.reasons) or ranked.status.value
        if blocked is None:
            routes = {r.route_id: r for r in evaluation.candidates}
            for index, candidate in enumerate(sorted(ranked.candidates, key=_order)):
                if (
                    accepted_for_dem
                    and approximate_policy is not None
                    and len(accepted_for_dem) > approximate_policy.dem.maximum_profiles_per_gap
                ):
                    break
                if (
                    accepted_for_dem
                    and approximate_policy is not None
                    and (
                        candidate.score is None
                        or candidate.score
                        > (accepted_for_dem[0][0].score or 0)
                        + approximate_policy.ranking.near_best_score_delta
                    )
                ):
                    break
                if index >= cfg.maximum_candidate_attempts:
                    blocked = "candidate_attempt_limit"
                    break
                attempt: dict[str, Any] = {
                    "route_id": candidate.source_candidate_id,
                    "score": candidate.score,
                    "total_length_m": _order(candidate)[1]
                    if isfinite(_order(candidate)[1])
                    else None,
                    "status": "rejected",
                }
                decision["attempts"].append(attempt)
                route = routes[candidate.source_candidate_id]
                audit = route.as_dict().get("audit")
                if not isinstance(audit, dict) or audit.get("status") not in {"PASS", "WARN"}:
                    attempt["reason"] = "routing_audit_unusable"
                    if approximate:
                        typed_attempts.append(
                            CandidateAttemptAudit(
                                route.route_id,
                                AttemptStatus.REJECTED,
                                candidate.score,
                                (DecisionReason.ROUTING_AUDIT_UNUSABLE,),
                            )
                        )
                    continue
                assessment = (
                    assess_ranked_osm_candidate(
                        ranked, candidate, approximate_policy, gap_id=gap.gap_id
                    )
                    if approximate and approximate_policy is not None
                    else None
                )
                if (
                    bool(assessment and not assessment.eligible_for_attempt)
                    or not candidate.eligible
                    or candidate.score is None
                    or (
                        not approximate
                        and (
                            candidate.score > ranking_cfg.maximum_recommended_score
                            or candidate.observed_components
                            < ranking_cfg.minimum_observed_evidence_components
                        )
                    )
                ):
                    attempt["reason"] = "insufficient_route_quality"
                    attempt["ranking_reasons"] = candidate.reasons
                    if approximate and assessment is not None:
                        typed_attempts.append(
                            CandidateAttemptAudit(
                                route.route_id,
                                AttemptStatus.REJECTED,
                                candidate.score,
                                assessment.hard_reasons,
                            )
                        )
                    continue
                typed_reason = DecisionReason.ALLOCATION_REJECTED
                result = _allocate(
                    activity,
                    plan,
                    evaluation,
                    route,
                    None,
                    discovery.graph_id,
                    cfg,
                    integrity.config,
                    snapshot,
                )
                if isinstance(result, GapRepairPlan):
                    if not _preflight(activity, plan, result) or (
                        approximate
                        and not _preflight_composed(
                            activity, plan, {**candidates, gap.gap_id: result}
                        )
                    ):
                        result = Reason.CANDIDATE_TRANSITION_IMPLAUSIBLE
                        typed_reason = DecisionReason.WRITER_PREFLIGHT_REJECTED
                    else:
                        if dem_collect:
                            attempt["status"] = AttemptStatus.PREFLIGHT_ACCEPTED.value
                            accepted_for_dem.append((candidate, result, attempt))
                            assert assessment is not None
                            typed_attempts.append(
                                CandidateAttemptAudit(
                                    route.route_id,
                                    AttemptStatus.PREFLIGHT_ACCEPTED,
                                    candidate.score,
                                    assessment.soft_reasons,
                                )
                            )
                            continue
                        candidates[gap.gap_id] = result
                        failures.pop(gap.gap_id, None)
                        attempt["status"] = "selected"
                        assert result.osm_provenance is not None
                        attempt["allocation_method"] = result.osm_provenance.allocation_method.value
                        attempt["signal_diagnostics"] = result.osm_provenance.signal_diagnostics
                        decision.update(
                            status="osm_selected", provider="osm", selected_route_id=route.route_id
                        )
                        if approximate and assessment is not None:
                            mode = _approximate_mode(assessment.soft_reasons)
                            decision["selection_mode"] = mode.value
                            decision["approximate"] = True
                            typed_attempts.append(
                                CandidateAttemptAudit(
                                    route.route_id,
                                    AttemptStatus.SELECTED,
                                    candidate.score,
                                    assessment.soft_reasons,
                                )
                            )
                        break
                reason = result
                attempt["reason"] = reason.value
                if approximate:
                    typed_attempts.append(
                        CandidateAttemptAudit(
                            route.route_id,
                            AttemptStatus.REJECTED,
                            candidate.score,
                            (typed_reason,),
                        )
                    )
            if (
                accepted_for_dem
                and approximate_policy is not None
                and dem_sampler is not None
                and dem_snapshot_id
            ):
                dem_choice = (
                    DemChoice(DemEvidenceStatus.UNAVAILABLE)
                    if blocked == "candidate_attempt_limit"
                    else compare_dem_candidates(
                        activity,
                        gap,
                        tuple((item[0].source_candidate_id, item[1]) for item in accepted_for_dem),
                        dem_sampler,
                        dem_snapshot_id,
                        approximate_policy.dem,
                    )
                )
                dem_status = dem_choice.status
                dem_profile_ids = dem_choice.profile_ids
                selected_id = (
                    dem_choice.preferred_route_id or accepted_for_dem[0][0].source_candidate_id
                )
                selected_candidate, selected_plan, selected_attempt = next(
                    item for item in accepted_for_dem if item[0].source_candidate_id == selected_id
                )
                candidates[gap.gap_id] = selected_plan
                failures.pop(gap.gap_id, None)
                selected_attempt["status"] = AttemptStatus.SELECTED.value
                assert selected_plan.osm_provenance is not None
                selected_attempt["allocation_method"] = (
                    selected_plan.osm_provenance.allocation_method.value
                )
                selected_attempt["signal_diagnostics"] = (
                    selected_plan.osm_provenance.signal_diagnostics
                )
                mode = _approximate_mode(
                    assess_ranked_osm_candidate(
                        ranked, selected_candidate, approximate_policy, gap_id=gap.gap_id
                    ).soft_reasons
                )
                decision.update(
                    status="osm_selected",
                    provider="osm",
                    selected_route_id=selected_id,
                    selection_mode=mode.value,
                    approximate=True,
                )
                by_route = {item.route_id: item for item in dem_choice.candidates}
                typed_attempts = [
                    replace(
                        item,
                        status=AttemptStatus.SELECTED
                        if item.route_id == selected_id
                        else item.status,
                        dem_profile_id=by_route[item.route_id].profile_id,
                        dem_shape_error_m=by_route[item.route_id].shape_error_m,
                        dem_aligned_samples=by_route[item.route_id].aligned_samples,
                    )
                    if item.status is AttemptStatus.PREFLIGHT_ACCEPTED and item.route_id in by_route
                    else replace(item, status=AttemptStatus.SELECTED)
                    if item.status is AttemptStatus.PREFLIGHT_ACCEPTED
                    and item.route_id == selected_id
                    else item
                    for item in typed_attempts
                ]
                decision["dem_evidence"] = {
                    "status": dem_status.value,
                    "selected_by_dem": dem_choice.preferred_route_id is not None,
                }
        if decision["status"] != "osm_selected":
            decision["reason"] = blocked or reason.value
            # A rejected GPX candidate may remain visible, but must not duplicate a gap decision.
            if gap.gap_id not in candidates:
                previous = failures.get(gap.gap_id)
                failures[gap.gap_id] = (
                    replace(previous, reasons=tuple(dict.fromkeys((*previous.reasons, reason))))
                    if previous is not None
                    else UnresolvedGap(gap, (reason,))
                )
        if approximate and approximate_policy is not None:
            if dem_sampler is not None or dem_snapshot_id is not None:
                decision.setdefault(
                    "dem_evidence",
                    {"status": dem_status.value, "selected_by_dem": False},
                )
            selected_mode = (
                SelectionMode(decision["selection_mode"]) if "selection_mode" in decision else None
            )
            decision["approximate_audit"] = GapDecisionAudit(
                gap.gap_id,
                approximate_policy.policy_hash,
                selected_mode,
                decision.get("selected_route_id"),
                ranked.search_complete,
                graph_id=discovery.graph_id,
                dem_status=dem_status,
                dem_snapshot_id=dem_snapshot_id if dem_profile_ids else None,
                dem_profile_ids=dem_profile_ids,
                attempts=tuple(typed_attempts),
                reasons=(
                    DecisionReason.ATTEMPT_LIMIT_REACHED
                    if blocked == "candidate_attempt_limit"
                    else DecisionReason.NO_ACCEPTABLE_ROUTE,
                )
                if selected_mode is None
                else (DecisionReason.DEM_UNAVAILABLE,)
                if dem_status is DemEvidenceStatus.UNAVAILABLE
                else (DecisionReason.DEM_UNINFORMATIVE,)
                if dem_status is DemEvidenceStatus.UNINFORMATIVE
                else (),
            ).as_dict()
    final = replace(
        plan,
        interval_plans=tuple(candidates[g.gap_id] for g in plan.gaps if g.gap_id in candidates),
        unresolved_gaps=tuple(failures[g.gap_id] for g in plan.gaps if g.gap_id in failures),
    )
    selection = select_repair_intervals(final, minimum_confidence)
    unresolved = any(d["status"] == "unresolved" for d in decisions)
    status = (
        RepairPlanStatus.PARTIAL
        if selection.has_changes and unresolved
        else RepairPlanStatus.READY
        if selection.has_changes
        else RepairPlanStatus.REFUSED
        if plan.gaps
        else RepairPlanStatus.NOT_NEEDED
    )
    return replace(
        final,
        status=status,
        confidence=min(
            (c.confidence for c in selection.selected_interval_plans),
            key=CONFIDENCE_RANK.__getitem__,
            default=plan.confidence,
        ),
        reasons=(Reason.SOME_INTERVALS_UNRESOLVED if unresolved else Reason.ALL_INTERVALS_READY,),
        automatic_osm_json=_json(
            {
                "policy": POLICY_ID
                if approximate_policy is not None
                else "gpx-first-automatic-osm-v2",
                **(
                    {"policy_hash": approximate_policy.policy_hash}
                    if approximate_policy is not None
                    else {}
                ),
                "graph_id": discovery.graph_id,
                "confidence": "medium",
                "identity_confirmed": False,
                "config": asdict(cfg),
                "ranking_config": json.loads(ranking.config_json),
                "decisions": decisions,
            }
        ),
    )
