"""Shared, provider-neutral gap and coordinate-cleaning audit for all reports."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import cast

from warpbuster.models.reconstruction import (
    CoordinateState,
    GapRepairPlan,
    ReconstructionGap,
    ReconstructionReason,
    RepairIntervalAction,
    RepairIntervalDecision,
    RepairPlan,
    RepairSelection,
)


def json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    return value


def gap_candidate_report(
    candidate: GapRepairPlan, decision: RepairIntervalDecision
) -> dict[str, object]:
    provenance = candidate.provenance
    gap = candidate.interval
    return {
        "gap_id": gap.gap_id,
        "start_record_index": gap.start_record_index,
        "end_record_index": gap.end_record_index,
        "record_count": gap.record_count,
        "detection_kind": "gap_reconstruction",
        "missing_run_kind": gap.kind.value,
        "origin": gap.origin.value,
        "confidence": candidate.confidence.value,
        "repair_eligible": decision.action is RepairIntervalAction.APPLIED,
        "default_high_confidence_eligible": candidate.repair_eligible,
        "action": decision.action.value,
        "direction": provenance.direction.value if provenance else None,
        "course_span_distance_m": provenance.course_span_distance_m if provenance else None,
        "anchor_connector_distance_m": provenance.connector_distance_m if provenance else None,
        "reconstruction_path_distance_m": candidate.reconstruction_path_distance_m,
        "allocation_method": provenance.allocation_method.value if provenance else None,
        "anchor_before_record_index": gap.anchor_before_record_index,
        "anchor_after_record_index": gap.anchor_after_record_index,
        "provenance": json_value(asdict(provenance)) if provenance else None,
        "coordinate_updates": [json_value(asdict(item)) for item in candidate.coordinate_updates],
        "reconstruction_scope_ranges": [
            list(bounds) for bounds in candidate.reconstruction_scope_ranges
        ],
        "preserve_recorded_distance": candidate.preserve_recorded_distance,
        "existing_coordinate_update_count": gap.invalidated_count,
        "missing_coordinate_update_count": gap.original_missing_count,
        "fields_to_change": list(candidate.fields_to_change),
        "dependent_fields_to_recalculate": list(candidate.dependent_fields_to_recalculate),
        "reasons": [reason.value for reason in candidate.reasons],
    }


def distance_policy(selection: RepairSelection) -> dict[str, object]:
    unresolved_geometry = any(
        isinstance(decision.interval, ReconstructionGap)
        and decision.action is not RepairIntervalAction.APPLIED
        for decision in selection.decisions
    )
    unresolved_signal = any(
        isinstance(candidate, GapRepairPlan)
        and candidate.provenance is not None
        and candidate.preserve_recorded_distance
        and (
            candidate.provenance.distance_signal_status != "plausible"
            or "distance_path_mismatch" in candidate.provenance.signal_diagnostics
        )
        for candidate in selection.selected_interval_plans
    )
    uncertain = (
        unresolved_geometry or unresolved_signal or bool(selection.unresolved_invalidated_indices)
    )
    recalculated = any(
        not candidate.preserve_recorded_distance for candidate in selection.selected_interval_plans
    )
    return {
        "policy": "preserved" if not recalculated else "coordinate_dependent_correction",
        "status": "partially_corrected"
        if recalculated and uncertain
        else "corrected"
        if recalculated
        else "unresolved"
        if uncertain
        else "preserved_source_unverified",
        "quality": "uncertain"
        if uncertain
        else "estimated"
        if recalculated
        else "source_unverified",
        "reason": "unresolved_geometry_or_signal"
        if uncertain
        else "no_independent_distance_source_claim",
        "correction_skipped": uncertain and not recalculated,
        "unresolved_geometry": unresolved_geometry,
        "unresolved_distance_signal": unresolved_signal,
        "unresolved_invalidated_record_count": len(selection.unresolved_invalidated_indices),
    }


def gap_audit(plan: RepairPlan, selection: RepairSelection) -> dict[str, object]:
    candidates = {
        item.interval.gap_id: item
        for item in plan.interval_plans
        if isinstance(item, GapRepairPlan)
    }
    selected = {
        item.interval.gap_id
        for item in selection.selected_interval_plans
        if isinstance(item, GapRepairPlan)
    }
    unresolved = {item.interval.gap_id: item for item in plan.unresolved_gaps}
    decisions = {
        item.interval.gap_id: item
        for item in selection.decisions
        if isinstance(item.interval, ReconstructionGap)
    }
    gaps = []
    for number, gap in enumerate(plan.gaps, 1):
        candidate = candidates.get(gap.gap_id)
        failure = unresolved.get(gap.gap_id)
        is_selected = gap.gap_id in selected
        status = (
            ("applied" if plan.output_written else "planned")
            if is_selected
            else "skipped"
            if candidate
            or (failure and ReconstructionReason.MISSING_COMPLETION_DISABLED in failure.reasons)
            else "unresolved"
        )
        timing = (
            candidate.provenance.timing
            if candidate and candidate.provenance
            else (failure.timing if failure else None)
        )
        gaps.append(
            {
                **asdict(gap),
                "number": number,
                "timing": asdict(timing) if timing else None,
                "status": status,
                "candidate_available": candidate is not None,
                "path_confidence": candidate.confidence.value if candidate else None,
                "filled_count": gap.record_count if is_selected else 0,
                "unresolved_count": 0 if is_selected else gap.record_count,
                "context_ranges": candidate.provenance.context_ranges
                if candidate and candidate.provenance
                else failure.context_ranges
                if failure
                else (),
                "reasons": failure.reasons if failure else candidate.reasons if candidate else (),
                "selection_reasons": decisions[gap.gap_id].selection_reasons
                if gap.gap_id in decisions
                else (),
                "provenance": asdict(candidate.provenance)
                if candidate and candidate.provenance
                else None,
                "endpoint_source": candidate.provenance.endpoint_source
                if candidate and candidate.provenance
                else None,
                "distance_action": ("corrected" if plan.output_written else "correction_planned")
                if is_selected and candidate and not candidate.preserve_recorded_distance
                else "preserved",
                "distance_signal_status": candidate.provenance.distance_signal_status
                if candidate and candidate.provenance
                else "unassessed",
                "invalidation_action": ("applied" if plan.output_written else "planned")
                if gap.invalidated_count
                else "not_needed",
            }
        )
    filled = sum(
        len(candidate.coordinate_updates)
        for candidate in selection.selected_interval_plans
        if isinstance(candidate, GapRepairPlan)
    )
    return {
        "geometry_status": "complete"
        if gaps and all(g["filled_count"] for g in gaps)
        else "partial"
        if filled
        else "unresolved"
        if gaps
        else "unchanged",
        "gap_inventory": [json_value(gap) for gap in gaps],
        "coordinate_dispositions": [
            json_value(asdict(item))
            for item in plan.coordinate_mask
            if item.proof_ranges
            or (item.original_latitude is None) != (item.original_longitude is None)
        ],
        "coordinate_coverage": {
            "original_missing": sum(
                item.state is CoordinateState.ORIGINAL_MISSING for item in plan.coordinate_mask
            ),
            "invalidated": len(selection.invalidations),
            "filled": filled,
            "unresolved": sum(gap.record_count for gap in plan.gaps) - filled,
            "untouched_positioned": sum(
                item.state is CoordinateState.PRESERVED for item in plan.coordinate_mask
            ),
        },
        "minimum_invalidation_confidence": plan.minimum_invalidation_confidence.value,
        "distance": distance_policy(selection),
    }


def gap_console(plan: RepairPlan, selection: RepairSelection) -> list[str]:
    """Use the same G-numbers, counts and decisions as JSON and the shared HTML."""
    audit = gap_audit(plan, selection)
    gaps = cast(list[dict[str, object]], audit["gap_inventory"])
    return [
        f"Coordinate coverage: {audit['coordinate_coverage']}",
        *(
            f"  G{g['number']} ({g['gap_id']}): {g['status']}; "
            f"missing={g['original_missing_count']}, invalidated={g['invalidated_count']}, "
            f"filled={g['filled_count']}, unresolved={g['unresolved_count']}; "
            f"reasons={g['reasons']}; timing={g['timing']}; "
            f"allocation={g['provenance'].get('allocation_method') if isinstance(g['provenance'], dict) else None}"
            for g in gaps
        ),
    ]
