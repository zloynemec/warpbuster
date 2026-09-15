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
from warpbuster.reconstruction.candidate_ranking import rank_gap_candidates
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


def apply_automatic_osm_routes(
    activity: ActivityData,
    integrity: IntegrityReport,
    plan: RepairPlan,
    discovery: OSMDryRunResult,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.MEDIUM,
    config: AutomaticOSMApplicationConfig | None = None,
    ranking_config: GapCandidateRankingConfig | None = None,
) -> RepairPlan:
    """Return a final plan without writing or asserting confirmed route identity."""
    cfg = config or AutomaticOSMApplicationConfig()
    ranking_cfg = ranking_config or GapCandidateRankingConfig()
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
        decision: dict[str, Any] = {"gap_id": gap.gap_id, "status": "unresolved", "attempts": []}
        decisions.append(decision)
        existing = candidates.get(gap.gap_id)
        if existing is not None and existing in selected:
            if _preflight(activity, plan, existing):
                decision.update(status="gpx_selected", provider="gpx", reason="gpx_first")
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
                    continue
                if (
                    not candidate.eligible
                    or candidate.score is None
                    or candidate.score > ranking_cfg.maximum_recommended_score
                    or candidate.observed_components
                    < ranking_cfg.minimum_observed_evidence_components
                ):
                    attempt["reason"] = "insufficient_route_quality"
                    attempt["ranking_reasons"] = candidate.reasons
                    continue
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
                    if not _preflight(activity, plan, result):
                        result = Reason.CANDIDATE_TRANSITION_IMPLAUSIBLE
                    else:
                        candidates[gap.gap_id] = result
                        failures.pop(gap.gap_id, None)
                        attempt["status"] = "selected"
                        assert result.osm_provenance is not None
                        attempt["allocation_method"] = result.osm_provenance.allocation_method.value
                        attempt["signal_diagnostics"] = result.osm_provenance.signal_diagnostics
                        decision.update(
                            status="osm_selected", provider="osm", selected_route_id=route.route_id
                        )
                        break
                reason = result
                attempt["reason"] = reason.value
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
                "policy": "gpx-first-automatic-osm-v2",
                "graph_id": discovery.graph_id,
                "confidence": "medium",
                "identity_confirmed": False,
                "config": asdict(cfg),
                "ranking_config": json.loads(ranking.config_json),
                "decisions": decisions,
            }
        ),
    )
