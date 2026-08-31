"""Console and JSON renderers for local integrity analysis."""

from __future__ import annotations

import json
from dataclasses import asdict

from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import (
    IntegrityReport,
    TransitionClassification,
    TransitionResult,
)

_MAX_CONSOLE_FINDINGS = 20
_CLASSIFICATIONS = tuple(TransitionClassification)


def analyze_report(activity: ActivityData, integrity: IntegrityReport) -> dict[str, object]:
    """Build the stable v0.1 machine-readable local-analysis report."""
    baseline = integrity.baseline
    return {
        "schema_version": "0.1",
        "scope": "local_transitions",
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


def analyze_console(activity: ActivityData, integrity: IntegrityReport) -> str:
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
        "WarpBuster FIT analyze — local transitions",
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
    ]
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
