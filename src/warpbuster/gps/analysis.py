"""Explicit mapping from existing integrity proofs to source-point decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from warpbuster.config import GpsAnalysisConfig
from warpbuster.gps.reader import GpsInput, read_locations
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntegrityReport,
    TransitionClassification,
    TransitionResult,
)


@dataclass(frozen=True, slots=True)
class GpsPointResult:
    """Decision for exactly one original seq; transitions remain source evidence."""

    seq: int
    status: Literal["trusted", "rejected", "uncertain"]
    confidence: IntegrityConfidence
    reasons: tuple[str, ...]
    transitions: tuple[TransitionResult, ...]
    interval_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class GpsAnalysisResult:
    """Shared library/CLI analysis, including unmodified source observations."""

    source: GpsInput
    config: GpsAnalysisConfig
    integrity: IntegrityReport
    points: tuple[GpsPointResult, ...]


def analyze_gps(
    path: str | Path,
    config: GpsAnalysisConfig | None = None,
) -> GpsAnalysisResult:
    """Analyze recorder CSV offline; no reconstruction or external geometry."""
    source = read_locations(path)
    policy = config or GpsAnalysisConfig()
    integrity = analyze_integrity(source.activity, policy.detector)
    count = len(source.observations)
    adjacent: list[list[TransitionResult]] = [[] for _ in range(count)]
    intervals: list[list[int]] = [[] for _ in range(count)]
    rejected: set[int] = set()
    uncertain_tail: set[int] = set()
    for transition in integrity.transitions:
        adjacent[transition.from_record_index].append(transition)
        adjacent[transition.to_record_index].append(transition)
    for interval_index, interval in enumerate(integrity.corrupted_intervals):
        for index in range(interval.start_record_index, interval.end_record_index + 1):
            intervals[index].append(interval_index)
            if interval.confidence is IntegrityConfidence.HIGH:
                rejected.add(index)
        proof = interval.reachability
        if (
            proof is not None
            and proof.uncertain_start_record_index is not None
            and proof.uncertain_end_record_index is not None
        ):
            uncertain_tail.update(
                range(
                    proof.uncertain_start_record_index,
                    proof.uncertain_end_record_index + 1,
                )
            )

    points: list[GpsPointResult] = []
    for index, observation in enumerate(source.observations):
        reasons: list[str] = []
        for interval_index in intervals[index]:
            reasons.extend(integrity.corrupted_intervals[interval_index].reasons)
        if index in rejected:
            status: Literal["trusted", "rejected", "uncertain"] = "rejected"
            confidence = IntegrityConfidence.HIGH
        else:
            if intervals[index]:
                reasons.append("insufficient_interval_confidence")
            if index in uncertain_tail:
                reasons.append("uncertain_tail_recovery")
            accuracy = observation.horizontal_accuracy_m
            if accuracy is None:
                reasons.append("horizontal_accuracy_unavailable")
            elif accuracy > policy.max_horizontal_accuracy_m:
                reasons.append("horizontal_accuracy_exceeds_trust_limit")
            supported = False
            long_gap = False
            for transition in adjacent[index]:
                other = (
                    transition.to_record_index
                    if transition.from_record_index == index
                    else transition.from_record_index
                )
                # A proven bad point must not taint its normal outside neighbours.
                if other in rejected:
                    continue
                if transition.classification is not TransitionClassification.NORMAL:
                    reasons.extend(transition.reasons)
                    reasons.append(f"unresolved_{transition.classification.value}_transition")
                elif (
                    transition.elapsed_seconds is not None
                    and transition.elapsed_seconds > policy.max_gap_seconds
                ):
                    long_gap = True
                else:
                    supported = True
            if not supported:
                reasons.append("no_normal_neighbour_support")
                if long_gap:
                    reasons.append("long_time_gap")
            status = "uncertain" if reasons else "trusted"
            confidence = IntegrityConfidence.LOW if reasons else IntegrityConfidence.MEDIUM
            if status == "trusted":
                reasons.extend(("acceptable_horizontal_accuracy", "normal_neighbour_support"))
        points.append(
            GpsPointResult(
                seq=observation.seq,
                status=status,
                confidence=confidence,
                reasons=tuple(dict.fromkeys(reasons)),
                transitions=tuple(adjacent[index]),
                interval_indices=tuple(intervals[index]),
            )
        )
    return GpsAnalysisResult(source, policy, integrity, tuple(points))
