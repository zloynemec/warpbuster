"""Vendor-neutral domain models for WarpBuster Core."""

from warpbuster.models.activity import (
    ActivityData,
    ActivityRecord,
    CoordinateBounds,
    DeveloperFieldDefinition,
    FitPreservationData,
    SourceMessage,
    SourceRecordRef,
    UnknownFieldSummary,
)
from warpbuster.models.integrity import (
    BaselineStats,
    BridgeResult,
    CorruptedInterval,
    IntegrityConfidence,
    IntegrityReport,
    IntegrityStatus,
    IntervalReason,
    TransitionClassification,
    TransitionReason,
    TransitionResult,
)

__all__ = [
    "ActivityData",
    "ActivityRecord",
    "BaselineStats",
    "BridgeResult",
    "CoordinateBounds",
    "CorruptedInterval",
    "DeveloperFieldDefinition",
    "FitPreservationData",
    "IntegrityConfidence",
    "IntegrityReport",
    "IntegrityStatus",
    "IntervalReason",
    "SourceMessage",
    "SourceRecordRef",
    "TransitionClassification",
    "TransitionReason",
    "TransitionResult",
    "UnknownFieldSummary",
]
