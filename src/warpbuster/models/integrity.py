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


class IntervalReason(StrEnum):
    """Stable evidence establishing a corrupted interval."""

    IMPOSSIBLE_TRANSITION_IN = "impossible_transition_in"
    IMPOSSIBLE_TRANSITION_OUT = "impossible_transition_out"
    PLAUSIBLE_BRIDGE = "plausible_bridge"
    MISSING_EXIT_BOUNDARY = "missing_exit_boundary"
    STABLE_OUTER_ANCHORS = "stable_outer_anchors"
    TAINTED_POSITION_COMPONENTS = "tainted_position_components"


class IntervalDetectionKind(StrEnum):
    """Course-independent proof rule that established an interval."""

    CLASSIC_ISLAND = "classic_island"
    ONE_SIDED_CLUSTER = "one_sided_cluster"
    COMPOSITE_REGION = "composite_region"


class OneSidedClusterReason(StrEnum):
    """Evidence or rejection reason recorded for one one-sided candidate."""

    IMPOSSIBLE_ENTRY = "impossible_entry"
    MISSING_POSITION_EVIDENCE = "missing_position_evidence"
    MISSING_EXIT_BOUNDARY = "missing_exit_boundary"
    STABLE_OUTER_ANCHORS = "stable_outer_anchors"
    PLAUSIBLE_OUTER_BRIDGE = "plausible_outer_bridge"
    ALL_POSITION_COMPONENTS_TAINTED = "all_position_components_tainted"
    ENTRY_NOT_ADJACENT = "entry_not_adjacent"
    NO_NEARBY_MISSING_POSITION = "no_nearby_missing_position"
    CLUSTER_NOT_MISSING_TERMINATED = "cluster_not_missing_terminated"
    TRUSTED_ANCHOR_UNAVAILABLE = "trusted_anchor_unavailable"
    CONTINUITY_BOUNDARY = "continuity_boundary"
    ANCHOR_BEFORE_UNSTABLE = "anchor_before_unstable"
    ANCHOR_AFTER_UNSTABLE = "anchor_after_unstable"
    BRIDGE_UNAVAILABLE = "bridge_unavailable"
    BRIDGE_TOO_FAST = "bridge_too_fast"
    UNTAINTED_POSITION_COMPONENT = "untainted_position_component"


class BridgeCandidateOutcome(StrEnum):
    """Machine-readable outcome of one bounded exit-candidate evaluation."""

    ACCEPTED = "accepted"
    INVALID_ELAPSED_TIME = "invalid_elapsed_time"
    OUTSIDE_SEARCH_WINDOW = "outside_search_window"
    UNUSABLE_ANCHORS = "unusable_anchors"
    BRIDGE_TOO_FAST = "bridge_too_fast"
    EMPTY_INTERVAL = "empty_interval"


class GeometryWarningKind(StrEnum):
    """Non-authoritative geometry patterns that merit human inspection."""

    POSSIBLE_INTERPOLATED_GNSS_GAP = "possible_interpolated_gnss_gap"


class GeometryWarningReason(StrEnum):
    """Machine-readable evidence for a geometry warning."""

    LONG_NEAR_COLLINEAR_RUN = "long_near_collinear_run"
    PATH_NEAR_CHORD = "path_near_chord"
    NARROW_CORRIDOR = "narrow_corridor"


class VerticalWarningReason(StrEnum):
    """Course-independent altitude evidence that merits inspection."""

    SUSTAINED_VERTICAL_RATE = "sustained_vertical_rate"
    SINGLE_EXTREME_VERTICAL_RATE = "single_extreme_vertical_rate"


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
class BridgeResult:
    """Direct physical reachability between trusted anchors around an interval."""

    from_record_index: int
    to_record_index: int
    elapsed_seconds: float
    distance_m: float
    apparent_speed_mps: float
    maximum_plausible_speed_mps: float


@dataclass(frozen=True, slots=True)
class CorruptedInterval:
    """Inclusive record range bounded by strong local and bridge evidence."""

    start_record_index: int
    end_record_index: int
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    trusted_before_record_index: int
    trusted_after_record_index: int
    entry_transition: TransitionResult
    exit_transition: TransitionResult | None
    bridge: BridgeResult
    confidence: IntegrityConfidence
    reasons: tuple[IntervalReason, ...]
    detection_kind: IntervalDetectionKind = IntervalDetectionKind.CLASSIC_ISLAND

    @property
    def record_count(self) -> int:
        """Return the number of records in the inclusive affected range."""
        return self.end_record_index - self.start_record_index + 1


@dataclass(frozen=True, slots=True)
class BridgeCandidateDiagnostic:
    """Diagnostic snapshot for one entry/exit pair considered by island search."""

    entry_from_record_index: int
    entry_to_record_index: int
    exit_from_record_index: int
    exit_to_record_index: int
    search_elapsed_seconds: float | None
    bridge_distance_m: float | None
    bridge_speed_mps: float | None
    bridge_speed_limit_mps: float | None
    outcome: BridgeCandidateOutcome


@dataclass(frozen=True, slots=True)
class IslandSearchDiagnostics:
    """Bounded-search counters and a capped sample of candidate details."""

    enabled: bool
    bridge_speed_limit_mps: float | None
    impossible_transition_count: int
    entries_considered: int
    consumed_entries_skipped: int
    candidates_considered: int
    continuity_pruned_count: int
    candidate_limit_pruned_count: int
    time_window_pruned_count: int
    invalid_candidate_count: int
    implausible_bridge_count: int
    accepted_interval_count: int
    retained_candidate_details: tuple[BridgeCandidateDiagnostic, ...]
    candidate_details_truncated_count: int


@dataclass(frozen=True, slots=True)
class OneSidedClusterDiagnostic:
    """Audit trail for a retained one-sided GNSS cluster candidate."""

    start_record_index: int
    end_record_index: int | None
    trusted_before_record_index: int
    trusted_after_record_index: int | None
    missing_position_record_count: int
    impossible_transition_count: int
    suspicious_transition_count: int
    positioned_component_count: int
    tainted_positioned_component_count: int
    anchor_before_normal_transition_count: int
    anchor_after_normal_transition_count: int
    anchor_required_normal_transition_count: int
    bridge: BridgeResult | None
    bridge_speed_limit_mps: float | None
    confidence: IntegrityConfidence
    reconstructable: bool
    reasons: tuple[OneSidedClusterReason, ...]

    @property
    def record_count(self) -> int | None:
        """Return inclusive candidate size when an end boundary was found."""
        if self.end_record_index is None:
            return None
        return self.end_record_index - self.start_record_index + 1


@dataclass(frozen=True, slots=True)
class OneSidedSearchDiagnostics:
    """Bounded one-sided cluster search counters and retained details."""

    enabled: bool
    impossible_entries_considered: int
    classic_interval_entries_skipped: int
    candidates_with_missing_evidence: int
    reconstructable_cluster_count: int
    unresolved_cluster_count: int
    records_scanned: int
    retained_clusters: tuple[OneSidedClusterDiagnostic, ...]
    clusters_truncated_count: int


@dataclass(frozen=True, slots=True)
class GeometryWarning:
    """Geometry-only warning that never establishes coordinate corruption."""

    kind: GeometryWarningKind
    start_record_index: int
    end_record_index: int
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    position_record_count: int
    chord_distance_m: float
    path_distance_m: float
    path_to_chord_ratio: float
    max_cross_track_deviation_m: float
    timestamps_available: bool
    confidence: IntegrityConfidence
    reasons: tuple[GeometryWarningReason, ...]


@dataclass(frozen=True, slots=True)
class GeometryScanDiagnostics:
    """Aggregate work and retention counters for bounded geometry scanning."""

    continuity_segment_count: int
    candidate_window_count: int
    qualifying_window_count: int
    warning_count: int
    retained_warning_count: int
    warnings_truncated_count: int


@dataclass(frozen=True, slots=True)
class VerticalWarning:
    """Altitude anomaly that does not by itself establish coordinate corruption."""

    start_record_index: int
    end_record_index: int
    start_timestamp: datetime
    end_timestamp: datetime
    transition_count: int
    elapsed_seconds: float
    altitude_delta_m: float
    maximum_absolute_vertical_speed_mps: float
    confidence: IntegrityConfidence
    reasons: tuple[VerticalWarningReason, ...]


@dataclass(frozen=True, slots=True)
class VerticalScanDiagnostics:
    """Bounded output counters for the linear altitude-consistency scan."""

    enabled: bool
    measured_transition_count: int
    warning_count: int
    retained_warning_count: int
    warnings_truncated_count: int


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
    corrupted_intervals: tuple[CorruptedInterval, ...]
    island_search_diagnostics: IslandSearchDiagnostics
    one_sided_search_diagnostics: OneSidedSearchDiagnostics
    geometry_warnings: tuple[GeometryWarning, ...]
    geometry_scan_diagnostics: GeometryScanDiagnostics
    vertical_warnings: tuple[VerticalWarning, ...]
    vertical_scan_diagnostics: VerticalScanDiagnostics
    config: IntegrityConfig

    def count(self, classification: TransitionClassification) -> int:
        """Return how many transitions have a given classification."""
        return sum(transition.classification is classification for transition in self.transitions)
