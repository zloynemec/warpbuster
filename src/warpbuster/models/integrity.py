"""Vendor-neutral integrity-analysis models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from warpbuster.config import IntegrityConfig


class TransitionClassification(StrEnum):
    """Local classification of one transition between GNSS observations."""

    NORMAL = "normal"
    SUSPICIOUS = "suspicious"
    IMPOSSIBLE = "impossible"
    UNKNOWN = "unknown"


class TransitionReason(StrEnum):
    """Stable, machine-readable evidence attached to a transition."""

    ABSOLUTE_SPEED_AND_DISTANCE_EXCEEDED = "absolute_speed_and_distance_exceeded"
    RELATIVE_SPEED_OUTLIER = "relative_speed_outlier"
    MISSING_TIMESTAMP = "missing_timestamp"
    NON_POSITIVE_TIME_DELTA = "non_positive_time_delta"


class IntegrityStatus(StrEnum):
    """Overall result of the detector stage currently available."""

    CLEAN = "clean"
    SUSPICIOUS = "suspicious"
    CORRUPTED = "corrupted"
    UNKNOWN = "unknown"


class IntegrityConfidence(StrEnum):
    """Confidence supported by the available local evidence."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class BaselineStats:
    """Robust speed statistics over positive-time local transitions."""

    sample_count: int
    median_speed_mps: float | None
    percentile_95_speed_mps: float | None
    median_absolute_deviation_mps: float | None
    relative_suspicious_threshold_mps: float | None


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Measured and classified movement between two valid-position records."""

    from_record_index: int
    to_record_index: int
    from_timestamp: datetime | None
    to_timestamp: datetime | None
    elapsed_seconds: float | None
    distance_m: float
    apparent_speed_mps: float | None
    classification: TransitionClassification
    reasons: tuple[TransitionReason, ...]


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """Deterministic report for local physical-transition analysis."""

    status: IntegrityStatus
    confidence: IntegrityConfidence
    record_count: int
    position_record_count: int
    missing_position_record_count: int
    baseline: BaselineStats
    transitions: tuple[TransitionResult, ...]
    config: IntegrityConfig

    def count(self, classification: TransitionClassification) -> int:
        """Return how many transitions have a given classification."""
        return sum(transition.classification is classification for transition in self.transitions)
