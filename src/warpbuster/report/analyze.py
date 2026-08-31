"""Console and JSON renderers for local integrity analysis."""

from __future__ import annotations

import json
from dataclasses import asdict

from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import (
    BridgeCandidateDiagnostic,
    CorruptedInterval,
    IntegrityReport,
    IslandSearchDiagnostics,
    TransitionClassification,
    TransitionResult,
)

_MAX_CONSOLE_FINDINGS = 20
_MAX_CONSOLE_CANDIDATE_DIAGNOSTICS = 20
_CLASSIFICATIONS = tuple(TransitionClassification)


def analyze_report(activity: ActivityData, integrity: IntegrityReport) -> dict[str, object]:
    """Build the stable v0.1 machine-readable local-analysis report."""
    baseline = integrity.baseline
    return {
        "schema_version": "0.1",
        "scope": "integrity_detection",
        "stages": ["local_transitions", "spoofing_islands"],
        "source": {"path": str(activity.preservation.source_path)},
        "activity": {
            "sport": activity.sport,
            "sub_sport": activity.sub_sport,
        },
        "status": integrity.status.value,
        "confidence": integrity.confidence.value,
        "summary": {
            "record_count": integrity.record_count,
            "position_record_count": integrity.position_record_count,
            "missing_position_record_count": integrity.missing_position_record_count,
            "transition_count": len(integrity.transitions),
            "corrupted_interval_count": len(integrity.corrupted_intervals),
            "classifications": {
                classification.value: integrity.count(classification)
                for classification in _CLASSIFICATIONS
            },
        },
        "baseline": {
            "sample_count": baseline.sample_count,
            "median_speed_mps": baseline.median_speed_mps,
            "percentile_95_speed_mps": baseline.percentile_95_speed_mps,
            "median_absolute_deviation_mps": baseline.median_absolute_deviation_mps,
            "relative_suspicious_threshold_mps": (baseline.relative_suspicious_threshold_mps),
        },
        "config": asdict(integrity.config),
        "island_search_diagnostics": _island_diagnostics_report(
            integrity.island_search_diagnostics
        ),
        "corrupted_intervals": [
            _interval_report(interval) for interval in integrity.corrupted_intervals
        ],
        "findings": [
            _transition_report(transition)
            for transition in integrity.transitions
            if transition.classification is not TransitionClassification.NORMAL
        ],
    }


def analyze_json(activity: ActivityData, integrity: IntegrityReport) -> str:
    """Render a local integrity report as deterministic JSON."""
    return json.dumps(
        analyze_report(activity, integrity),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def analyze_console(
    activity: ActivityData,
    integrity: IntegrityReport,
    *,
    verbosity: int = 0,
) -> str:
    """Render a compact local integrity report for a human reader."""
    counts = {
        classification: integrity.count(classification) for classification in _CLASSIFICATIONS
    }
    baseline = integrity.baseline
    findings = [
        transition
        for transition in integrity.transitions
        if transition.classification is not TransitionClassification.NORMAL
    ]
    lines = [
        "WarpBuster FIT analyze",
        f"File: {activity.preservation.source_path}",
        (
            f"Sport: {_display(activity.sport)} / {_display(activity.sub_sport)}; "
            f"profile: {integrity.config.profile.value}"
        ),
        f"Status: {integrity.status.value.upper()}",
        f"Confidence: {integrity.confidence.value.upper()}",
        (
            f"Records: {integrity.record_count}; positions: {integrity.position_record_count}; "
            f"missing position: {integrity.missing_position_record_count}"
        ),
        (
            f"Transitions: {len(integrity.transitions)}; "
            f"normal={counts[TransitionClassification.NORMAL]}, "
            f"suspicious={counts[TransitionClassification.SUSPICIOUS]}, "
            f"impossible={counts[TransitionClassification.IMPOSSIBLE]}, "
            f"unknown={counts[TransitionClassification.UNKNOWN]}"
        ),
        (
            "Baseline speed: "
            f"median={_number(baseline.median_speed_mps)} m/s, "
            f"p95={_number(baseline.percentile_95_speed_mps)} m/s, "
            f"MAD={_number(baseline.median_absolute_deviation_mps)} m/s"
        ),
        (
            "Relative suspicious threshold: "
            f"{_number(baseline.relative_suspicious_threshold_mps)} m/s"
        ),
        f"Corrupted intervals: {len(integrity.corrupted_intervals)}",
    ]
    lines.extend(_interval_console(interval) for interval in integrity.corrupted_intervals)
    if verbosity >= 1:
        lines.append("Pipeline: local_transitions -> spoofing_islands")
    if verbosity >= 2:
        lines.extend(_diagnostics_console(integrity))
    if not findings:
        lines.append("Findings: none")
        return "\n".join(lines)

    lines.append(f"Findings (showing up to {_MAX_CONSOLE_FINDINGS}):")
    lines.extend(_transition_console(finding) for finding in findings[:_MAX_CONSOLE_FINDINGS])
    if len(findings) > _MAX_CONSOLE_FINDINGS:
        lines.append(f"  ... {len(findings) - _MAX_CONSOLE_FINDINGS} more; use --json")
    return "\n".join(lines)


def _transition_report(transition: TransitionResult) -> dict[str, object]:
    return {
        "from_record_index": transition.from_record_index,
        "to_record_index": transition.to_record_index,
        "from_timestamp": (
            transition.from_timestamp.isoformat() if transition.from_timestamp is not None else None
        ),
        "to_timestamp": (
            transition.to_timestamp.isoformat() if transition.to_timestamp is not None else None
        ),
        "elapsed_seconds": transition.elapsed_seconds,
        "distance_m": transition.distance_m,
        "apparent_speed_mps": transition.apparent_speed_mps,
        "classification": transition.classification.value,
        "reasons": [reason.value for reason in transition.reasons],
    }


def _interval_report(interval: CorruptedInterval) -> dict[str, object]:
    return {
        "start_record_index": interval.start_record_index,
        "end_record_index": interval.end_record_index,
        "record_count": interval.record_count,
        "start_timestamp": (
            interval.start_timestamp.isoformat() if interval.start_timestamp is not None else None
        ),
        "end_timestamp": (
            interval.end_timestamp.isoformat() if interval.end_timestamp is not None else None
        ),
        "trusted_before_record_index": interval.trusted_before_record_index,
        "trusted_after_record_index": interval.trusted_after_record_index,
        "confidence": interval.confidence.value,
        "reasons": [reason.value for reason in interval.reasons],
        "entry_transition": _transition_report(interval.entry_transition),
        "exit_transition": _transition_report(interval.exit_transition),
        "bridge": {
            "from_record_index": interval.bridge.from_record_index,
            "to_record_index": interval.bridge.to_record_index,
            "elapsed_seconds": interval.bridge.elapsed_seconds,
            "distance_m": interval.bridge.distance_m,
            "apparent_speed_mps": interval.bridge.apparent_speed_mps,
            "maximum_plausible_speed_mps": interval.bridge.maximum_plausible_speed_mps,
            "plausible": True,
        },
    }


def _island_diagnostics_report(diagnostics: IslandSearchDiagnostics) -> dict[str, object]:
    return {
        "enabled": diagnostics.enabled,
        "bridge_speed_limit_mps": diagnostics.bridge_speed_limit_mps,
        "impossible_transition_count": diagnostics.impossible_transition_count,
        "entries_considered": diagnostics.entries_considered,
        "consumed_entries_skipped": diagnostics.consumed_entries_skipped,
        "candidates_considered": diagnostics.candidates_considered,
        "candidate_limit_pruned_count": diagnostics.candidate_limit_pruned_count,
        "time_window_pruned_count": diagnostics.time_window_pruned_count,
        "invalid_candidate_count": diagnostics.invalid_candidate_count,
        "implausible_bridge_count": diagnostics.implausible_bridge_count,
        "accepted_interval_count": diagnostics.accepted_interval_count,
        "retained_candidate_details": [
            _candidate_diagnostic_report(detail)
            for detail in diagnostics.retained_candidate_details
        ],
        "candidate_details_truncated_count": diagnostics.candidate_details_truncated_count,
    }


def _candidate_diagnostic_report(detail: BridgeCandidateDiagnostic) -> dict[str, object]:
    return {
        "entry_transition": {
            "from_record_index": detail.entry_from_record_index,
            "to_record_index": detail.entry_to_record_index,
        },
        "exit_transition": {
            "from_record_index": detail.exit_from_record_index,
            "to_record_index": detail.exit_to_record_index,
        },
        "search_elapsed_seconds": detail.search_elapsed_seconds,
        "bridge_distance_m": detail.bridge_distance_m,
        "bridge_speed_mps": detail.bridge_speed_mps,
        "bridge_speed_limit_mps": detail.bridge_speed_limit_mps,
        "outcome": detail.outcome.value,
    }


def _interval_console(interval: CorruptedInterval) -> str:
    reasons = ",".join(reason.value for reason in interval.reasons)
    return (
        f"  - records {interval.start_record_index}..{interval.end_record_index} "
        f"({interval.record_count}): {interval.confidence.value.upper()}, "
        f"anchors={interval.trusted_before_record_index}->{interval.trusted_after_record_index}, "
        f"bridge={interval.bridge.apparent_speed_mps:.2f} m/s, reasons={reasons}"
    )


def _diagnostics_console(integrity: IntegrityReport) -> list[str]:
    config = integrity.config
    diagnostics = integrity.island_search_diagnostics
    lines = [
        "Detector diagnostics:",
        (
            "  Local thresholds: "
            f"impossible_speed={_number(config.absolute_impossible_speed_mps)} m/s, "
            f"impossible_distance={config.absolute_impossible_distance_m:.2f} m, "
            f"relative_floor={config.relative_suspicious_speed_floor_mps:.2f} m/s"
        ),
        (
            "  Island bounds: "
            f"elapsed<={config.island_search_max_elapsed_seconds:.2f} s, "
            f"exit_candidates<={config.island_search_max_exit_candidates}, "
            f"derived_bridge_limit={_number(diagnostics.bridge_speed_limit_mps)} m/s"
        ),
        (
            "  Island search: "
            f"enabled={'yes' if diagnostics.enabled else 'no'}, "
            f"entries={diagnostics.entries_considered}, "
            f"candidates={diagnostics.candidates_considered}, "
            f"accepted={diagnostics.accepted_interval_count}, "
            f"too_fast={diagnostics.implausible_bridge_count}, "
            f"candidate_pruned={diagnostics.candidate_limit_pruned_count}, "
            f"time_pruned={diagnostics.time_window_pruned_count}"
        ),
    ]
    lines.extend(
        _candidate_diagnostic_console(detail)
        for detail in diagnostics.retained_candidate_details[:_MAX_CONSOLE_CANDIDATE_DIAGNOSTICS]
    )
    hidden_count = (
        max(
            0,
            len(diagnostics.retained_candidate_details) - _MAX_CONSOLE_CANDIDATE_DIAGNOSTICS,
        )
        + diagnostics.candidate_details_truncated_count
    )
    if hidden_count > 0:
        lines.append(f"  ... {hidden_count} candidate diagnostics omitted; use --json")
    return lines


def _candidate_diagnostic_console(detail: BridgeCandidateDiagnostic) -> str:
    return (
        f"  Candidate {detail.entry_from_record_index}->{detail.entry_to_record_index} / "
        f"{detail.exit_from_record_index}->{detail.exit_to_record_index}: "
        f"{detail.outcome.value}, bridge={_number(detail.bridge_speed_mps)} m/s"
    )


def _transition_console(transition: TransitionResult) -> str:
    reasons = ",".join(reason.value for reason in transition.reasons)
    return (
        f"  - records {transition.from_record_index}->{transition.to_record_index}: "
        f"{transition.classification.value.upper()}, "
        f"distance={transition.distance_m:.2f} m, "
        f"dt={_number(transition.elapsed_seconds)} s, "
        f"speed={_number(transition.apparent_speed_mps)} m/s, reason={reasons}"
    )


def _number(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _display(value: object) -> str:
    return "n/a" if value is None or value == "" else str(value)
