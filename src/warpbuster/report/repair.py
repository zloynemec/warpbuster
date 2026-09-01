"""Console and JSON renderers for course-based dry-run repair plans."""

from __future__ import annotations

import json
from dataclasses import asdict

from warpbuster.config import CourseReconstructionConfig
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    AnchorStabilityDiagnostic,
    CandidateCoordinate,
    CourseAnchorMatch,
    CourseBoundaryRefinement,
    CourseData,
    GnssRegionComponent,
    IntervalRepairPlan,
    MixedGnssRegion,
    RepairIntervalAction,
    RepairIntervalDecision,
    RepairPlan,
    RepairSelection,
    UnresolvedInterval,
)
from warpbuster.reconstruction.selection import select_repair_intervals


def repair_report(
    plan: RepairPlan,
    course: CourseData,
    config: CourseReconstructionConfig,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> dict[str, object]:
    """Build the stable machine-readable dry-run reconstruction report."""
    selection = select_repair_intervals(plan, minimum_confidence)
    candidate_coordinate_update_count = sum(
        len(interval.coordinate_updates) for interval in plan.interval_plans
    )
    selected_coordinate_update_count = sum(
        len(interval.coordinate_updates) for interval in selection.selected_interval_plans
    )
    decisions = {
        (
            decision.interval.start_record_index,
            decision.interval.end_record_index,
        ): decision
        for decision in selection.decisions
    }
    return {
        "schema_version": "0.1",
        "scope": "course_reconstruction_dry_run",
        "activity": {"format": "fit", "path": str(plan.activity_path)},
        "course": {
            "path": str(course.source_path),
            "version": course.version,
            "creator": course.creator,
            "segment_count": len(course.segments),
            "point_count": course.point_count,
            "total_distance_m": course.total_distance_m,
        },
        "status": plan.status.value,
        "confidence": plan.confidence.value,
        "repair_eligible": bool(selection.selected_interval_plans),
        "output_written": plan.output_written,
        "reasons": [reason.value for reason in plan.reasons],
        "summary": {
            "detected_interval_count": plan.detected_interval_count,
            "planned_interval_count": len(plan.interval_plans),
            "eligible_interval_count": selection.applied_interval_count,
            "unresolved_interval_count": len(plan.unresolved_intervals),
            "candidate_coordinate_update_count": candidate_coordinate_update_count,
            "selected_coordinate_update_count": selected_coordinate_update_count,
        },
        "selection": _selection_report(selection),
        "safety": {
            "timestamps_unchanged": plan.timestamps_unchanged,
            "trusted_records_unchanged": plan.trusted_records_unchanged,
            "detection_used_course": False,
        },
        "config": asdict(config),
        "interval_plans": [
            _interval_report(
                interval,
                decisions[
                    (
                        interval.interval.start_record_index,
                        interval.interval.end_record_index,
                    )
                ],
            )
            for interval in plan.interval_plans
        ],
        "unresolved_intervals": [
            _unresolved_report(interval) for interval in plan.unresolved_intervals
        ],
    }


def repair_json(
    plan: RepairPlan,
    course: CourseData,
    config: CourseReconstructionConfig,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
) -> str:
    """Render deterministic JSON for a dry-run RepairPlan."""
    return json.dumps(
        repair_report(
            plan,
            course,
            config,
            minimum_confidence=minimum_confidence,
        ),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def repair_console(
    plan: RepairPlan,
    course: CourseData,
    config: CourseReconstructionConfig,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
    verbosity: int = 0,
) -> str:
    """Render a compact human-readable dry-run report."""
    selection = select_repair_intervals(plan, minimum_confidence)
    candidate_coordinate_update_count = sum(
        len(interval.coordinate_updates) for interval in plan.interval_plans
    )
    selected_coordinate_update_count = sum(
        len(interval.coordinate_updates) for interval in selection.selected_interval_plans
    )
    lines = [
        "WarpBuster repair dry-run",
        f"Activity: {plan.activity_path}",
        f"Course: {course.source_path}",
        (
            f"Course geometry: segments={len(course.segments)}, points={course.point_count}, "
            f"distance={course.total_distance_m:.2f} m"
        ),
        f"Status: {plan.status.value.upper()}",
        f"Confidence: {plan.confidence.value.upper()}",
        f"Minimum confidence: {minimum_confidence.value.upper()}",
        (
            f"Application preview: {_application_status(selection)} "
            f"(apply={selection.applied_interval_count}, "
            f"skip={selection.skipped_interval_count})"
        ),
        f"Repair eligible: {'yes' if selection.selected_interval_plans else 'no'}",
        "Output written: no",
        (
            f"Intervals: detected={plan.detected_interval_count}, "
            f"planned={len(plan.interval_plans)}, "
            f"unresolved={len(plan.unresolved_intervals)}"
        ),
        f"Candidate coordinate updates: {candidate_coordinate_update_count}",
        f"Selected coordinate updates: {selected_coordinate_update_count}",
    ]
    decisions = {
        (
            decision.interval.start_record_index,
            decision.interval.end_record_index,
        ): decision
        for decision in selection.decisions
    }
    lines.extend(
        _interval_console(
            interval,
            decisions[
                (
                    interval.interval.start_record_index,
                    interval.interval.end_record_index,
                )
            ],
            verbosity,
        )
        for interval in plan.interval_plans
    )
    lines.extend(
        _unresolved_console(
            interval,
            decisions[
                (
                    interval.interval.start_record_index,
                    interval.interval.end_record_index,
                )
            ],
        )
        for interval in plan.unresolved_intervals
    )
    if verbosity >= 1:
        lines.extend(
            [
                "Safety: timestamps unchanged; trusted records unchanged; course excluded from detection",
                (
                    "Matching thresholds: "
                    f"anchor<={config.anchor_match_tolerance_m:.2f} m, "
                    f"one_sided_anchor<={config.one_sided_anchor_match_tolerance_m:.2f} m, "
                    f"HIGH<={config.high_confidence_anchor_distance_m:.2f} m, "
                    f"ambiguity_margin={config.ambiguity_score_margin_m:.2f} m"
                ),
            ]
        )
    return "\n".join(lines)


def _selection_report(selection: RepairSelection) -> dict[str, object]:
    return {
        "minimum_confidence": selection.minimum_confidence.value,
        "application_status": _application_status(selection).casefold(),
        "applied_interval_count": selection.applied_interval_count,
        "skipped_interval_count": selection.skipped_interval_count,
        "intervals": [_decision_report(decision) for decision in selection.decisions],
    }


def _decision_report(decision: RepairIntervalDecision) -> dict[str, object]:
    return {
        "start_record_index": decision.interval.start_record_index,
        "end_record_index": decision.interval.end_record_index,
        "confidence": decision.confidence.value,
        "action": decision.action.value,
        "candidate_available": decision.candidate_available,
        "coordinate_update_count": decision.coordinate_update_count,
        "selection_reasons": [reason.value for reason in decision.selection_reasons],
        "reconstruction_reasons": [reason.value for reason in decision.reconstruction_reasons],
    }


def _application_status(selection: RepairSelection) -> str:
    if not selection.selected_interval_plans:
        return "NONE"
    return "PARTIAL" if selection.is_partial else "FULL"


def _interval_report(
    plan: IntervalRepairPlan,
    decision: RepairIntervalDecision,
) -> dict[str, object]:
    missing_coordinate_update_count = sum(
        coordinate.original_latitude is None or coordinate.original_longitude is None
        for coordinate in plan.coordinate_updates
    )
    return {
        "start_record_index": plan.interval.start_record_index,
        "end_record_index": plan.interval.end_record_index,
        "record_count": plan.interval.record_count,
        "trusted_before_record_index": plan.interval.trusted_before_record_index,
        "trusted_after_record_index": plan.interval.trusted_after_record_index,
        "detection_kind": plan.interval.detection_kind.value,
        "confidence": plan.confidence.value,
        "repair_eligible": decision.action is RepairIntervalAction.APPLIED,
        "default_high_confidence_eligible": plan.repair_eligible,
        "action": decision.action.value,
        "direction": plan.direction.value,
        "course_span_distance_m": plan.course_span_distance_m,
        "course_apparent_speed_mps": plan.course_apparent_speed_mps,
        "anchor_connector_distance_m": plan.anchor_connector_distance_m,
        "reconstruction_path_distance_m": plan.reconstruction_path_distance_m,
        "allocation_method": plan.allocation_method.value,
        "anchor_before": _anchor_report(plan.anchor_before),
        "anchor_after": _anchor_report(plan.anchor_after),
        "anchor_before_stability": _stability_report(plan.anchor_before_stability),
        "anchor_after_stability": _stability_report(plan.anchor_after_stability),
        "boundary_refinement": (
            _boundary_refinement_report(plan.boundary_refinement)
            if plan.boundary_refinement is not None
            else None
        ),
        "composite_gnss_region": (
            _mixed_region_report(plan.composite_region)
            if plan.composite_region is not None
            else None
        ),
        "reconstruction_scope_ranges": [
            list(bounds) for bounds in plan.reconstruction_scope_ranges
        ],
        "existing_coordinate_update_count": (
            len(plan.coordinate_updates) - missing_coordinate_update_count
        ),
        "missing_coordinate_update_count": missing_coordinate_update_count,
        "fields_to_change": list(plan.fields_to_change),
        "dependent_fields_to_recalculate": list(plan.dependent_fields_to_recalculate),
        "reasons": [reason.value for reason in plan.reasons],
        "coordinate_updates": [
            _coordinate_report(coordinate) for coordinate in plan.coordinate_updates
        ],
    }


def _boundary_refinement_report(
    refinement: CourseBoundaryRefinement,
) -> dict[str, object]:
    return {
        "detected_start_record_index": refinement.detected_start_record_index,
        "detected_end_record_index": refinement.detected_end_record_index,
        "original_trusted_before_record_index": (refinement.original_trusted_before_record_index),
        "original_trusted_after_record_index": refinement.original_trusted_after_record_index,
        "refined_start_record_index": refinement.refined_start_record_index,
        "refined_end_record_index": refinement.refined_end_record_index,
        "refined_trusted_before_record_index": (refinement.refined_trusted_before_record_index),
        "refined_trusted_after_record_index": refinement.refined_trusted_after_record_index,
        "expanded_before_record_count": refinement.expanded_before_record_count,
        "expanded_after_record_count": refinement.expanded_after_record_count,
        "corridor_tolerance_m": refinement.corridor_tolerance_m,
        "required_stable_record_count": refinement.required_stable_record_count,
        "reasons": [reason.value for reason in refinement.reasons],
    }


def _anchor_report(match: CourseAnchorMatch) -> dict[str, object]:
    return {
        "course_segment_index": match.course_segment_index,
        "segment_start_point_index": match.segment_start_point_index,
        "segment_end_point_index": match.segment_end_point_index,
        "segment_fraction": match.segment_fraction,
        "course_distance_m": match.course_distance_m,
        "latitude": match.latitude,
        "longitude": match.longitude,
        "anchor_distance_m": match.anchor_distance_m,
    }


def _coordinate_report(coordinate: CandidateCoordinate) -> dict[str, object]:
    return {
        "record_index": coordinate.record_index,
        "timestamp": (
            coordinate.timestamp.isoformat() if coordinate.timestamp is not None else None
        ),
        "original": {
            "latitude": coordinate.original_latitude,
            "longitude": coordinate.original_longitude,
        },
        "candidate": {
            "latitude": coordinate.candidate_latitude,
            "longitude": coordinate.candidate_longitude,
        },
        "course_distance_m": coordinate.course_distance_m,
    }


def _unresolved_report(interval: UnresolvedInterval) -> dict[str, object]:
    return {
        "start_record_index": interval.interval.start_record_index,
        "end_record_index": interval.interval.end_record_index,
        "record_count": interval.interval.record_count,
        "detection_kind": interval.interval.detection_kind.value,
        "confidence": interval.confidence.value,
        "repair_eligible": False,
        "reasons": [reason.value for reason in interval.reasons],
        "anchor_before_candidate_count": interval.anchor_before_candidate_count,
        "anchor_after_candidate_count": interval.anchor_after_candidate_count,
        "anchor_before_stability": _optional_stability_report(interval.anchor_before_stability),
        "anchor_after_stability": _optional_stability_report(interval.anchor_after_stability),
        "mixed_gnss_region": (
            _mixed_region_report(interval.mixed_region)
            if interval.mixed_region is not None
            else None
        ),
    }


def _stability_report(diagnostic: AnchorStabilityDiagnostic) -> dict[str, object]:
    return {
        "anchor_record_index": diagnostic.anchor_record_index,
        "direction": diagnostic.direction.value,
        "stable": diagnostic.stable,
        "required_normal_transition_count": diagnostic.required_normal_transition_count,
        "consecutive_normal_transition_count": (diagnostic.consecutive_normal_transition_count),
        "inspected_start_record_index": diagnostic.inspected_start_record_index,
        "inspected_end_record_index": diagnostic.inspected_end_record_index,
        "blocking_record_index": diagnostic.blocking_record_index,
        "blocking_classification": (
            diagnostic.blocking_classification.value
            if diagnostic.blocking_classification is not None
            else None
        ),
        "reasons": [reason.value for reason in diagnostic.reasons],
    }


def _optional_stability_report(
    diagnostic: AnchorStabilityDiagnostic | None,
) -> dict[str, object] | None:
    return _stability_report(diagnostic) if diagnostic is not None else None


def _mixed_region_report(region: MixedGnssRegion) -> dict[str, object]:
    return {
        "start_record_index": region.start_record_index,
        "end_record_index": region.end_record_index,
        "record_count": region.record_count,
        "proposed_trusted_before_record_index": (region.proposed_trusted_before_record_index),
        "proposed_trusted_after_record_index": region.proposed_trusted_after_record_index,
        "missing_position_record_count": region.missing_position_record_count,
        "suspicious_transition_count": region.suspicious_transition_count,
        "impossible_transition_count": region.impossible_transition_count,
        "outer_anchor_before": _optional_stability_report(region.outer_anchor_before),
        "outer_anchor_after": _optional_stability_report(region.outer_anchor_after),
        "bridge_elapsed_seconds": region.bridge_elapsed_seconds,
        "bridge_distance_m": region.bridge_distance_m,
        "bridge_speed_mps": region.bridge_speed_mps,
        "bridge_speed_limit_mps": region.bridge_speed_limit_mps,
        "bridge_plausible": region.bridge_plausible,
        "confidence": region.confidence.value,
        "repair_eligible": region.repair_eligible,
        "reconstructable": region.reconstructable,
        "all_positioned_components_tainted": region.all_positioned_components_tainted,
        "detected_core_ranges": [list(bounds) for bounds in region.detected_core_ranges],
        "components": [_component_report(component) for component in region.components],
        "reasons": [reason.value for reason in region.reasons],
    }


def _component_report(component: GnssRegionComponent) -> dict[str, object]:
    return {
        "start_record_index": component.start_record_index,
        "end_record_index": component.end_record_index,
        "record_count": component.record_count,
        "start_timestamp": (
            component.start_timestamp.isoformat() if component.start_timestamp is not None else None
        ),
        "end_timestamp": (
            component.end_timestamp.isoformat() if component.end_timestamp is not None else None
        ),
        "duration_seconds": component.duration_seconds,
        "kind": component.kind.value,
        "state": component.state.value,
        "confidence": component.confidence.value,
        "positioned_record_count": component.positioned_record_count,
        "missing_position_record_count": component.missing_position_record_count,
        "suspicious_transition_count": component.suspicious_transition_count,
        "impossible_transition_count": component.impossible_transition_count,
        "detected_core_record_count": component.detected_core_record_count,
        "reasons": [reason.value for reason in component.reasons],
    }


def _interval_console(
    plan: IntervalRepairPlan,
    decision: RepairIntervalDecision,
    verbosity: int,
) -> str:
    details = (
        f"anchors={plan.anchor_before.anchor_distance_m:.2f}/"
        f"{plan.anchor_after.anchor_distance_m:.2f} m, "
        if verbosity >= 1
        else ""
    )
    refinement = ""
    if plan.boundary_refinement is not None:
        detected = plan.boundary_refinement
        refinement = (
            f", detected_core={detected.detected_start_record_index}.."
            f"{detected.detected_end_record_index}, corridor_refined=yes"
        )
    composite = ""
    if plan.composite_region is not None:
        scope = ";".join(f"{start}..{end}" for start, end in plan.reconstruction_scope_ranges)
        composite = (
            f", composite_components={len(plan.composite_region.components)}, "
            f"detected_cores={len(plan.composite_region.detected_core_ranges)}, "
            f"scope={scope or 'none'}"
        )
    missing_update_count = sum(
        update.original_latitude is None or update.original_longitude is None
        for update in plan.coordinate_updates
    )
    return (
        f"  - records {plan.interval.start_record_index}..{plan.interval.end_record_index}: "
        f"{decision.action.value.upper()}, confidence={plan.confidence.value.upper()}, "
        f"kind={plan.interval.detection_kind.value}, "
        f"default_high_eligible={'yes' if plan.repair_eligible else 'no'}, "
        f"{details}course={plan.course_span_distance_m:.2f} m, "
        f"connectors={plan.anchor_connector_distance_m:.2f} m, "
        f"path={plan.reconstruction_path_distance_m:.2f} m, "
        f"direction={plan.direction.value}, allocation={plan.allocation_method.value}, "
        f"updates={len(plan.coordinate_updates)} "
        f"(existing={len(plan.coordinate_updates) - missing_update_count}, "
        f"missing={missing_update_count}), "
        "anchor_stability="
        f"{plan.anchor_before_stability.consecutive_normal_transition_count}/"
        f"{plan.anchor_after_stability.consecutive_normal_transition_count}"
        f"{refinement}{composite}"
    )


def _unresolved_console(
    interval: UnresolvedInterval,
    decision: RepairIntervalDecision,
) -> str:
    reasons = ",".join(reason.value for reason in interval.reasons)
    line = (
        f"  - unresolved records {interval.interval.start_record_index}.."
        f"{interval.interval.end_record_index}: {decision.action.value.upper()}, "
        f"confidence={interval.confidence.value.upper()}, reasons={reasons}, "
        f"kind={interval.interval.detection_kind.value}, "
        f"anchor_candidates={interval.anchor_before_candidate_count}/"
        f"{interval.anchor_after_candidate_count}"
    )
    if interval.anchor_before_stability is not None and interval.anchor_after_stability is not None:
        line += (
            ", anchor_stability="
            f"{interval.anchor_before_stability.consecutive_normal_transition_count}/"
            f"{interval.anchor_after_stability.consecutive_normal_transition_count}"
        )
    if interval.mixed_region is not None:
        region = interval.mixed_region
        bridge = (
            f"{region.bridge_speed_mps:.3f} m/s"
            if region.bridge_speed_mps is not None
            else "unavailable"
        )
        line += (
            f"\n    mixed GNSS region {region.start_record_index}..{region.end_record_index}: "
            f"{region.confidence.value.upper()}, eligible=no, "
            f"outer_anchors={region.proposed_trusted_before_record_index}/"
            f"{region.proposed_trusted_after_record_index}, "
            f"evidence=missing:{region.missing_position_record_count},"
            f"suspicious:{region.suspicious_transition_count},"
            f"impossible:{region.impossible_transition_count}, bridge={bridge}, "
            f"components={len(region.components)}, "
            f"reconstructable={'yes' if region.reconstructable else 'no'}"
        )
        for component in region.components:
            line += (
                f"\n      component {component.start_record_index}.."
                f"{component.end_record_index}: {component.kind.value}, "
                f"state={component.state.value}, confidence={component.confidence.value.upper()}, "
                f"evidence=missing:{component.missing_position_record_count},"
                f"suspicious:{component.suspicious_transition_count},"
                f"impossible:{component.impossible_transition_count},"
                f"core:{component.detected_core_record_count}"
            )
    return line
