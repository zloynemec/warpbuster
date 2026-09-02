"""Vendor-neutral course and dry-run reconstruction models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from warpbuster.models.integrity import (
    CorruptedInterval,
    IntegrityConfidence,
    TransitionClassification,
)


class CourseDirection(StrEnum):
    """Traversal direction through one GPX course segment."""

    FORWARD = "forward"
    REVERSE = "reverse"


class AnchorDirection(StrEnum):
    """Side of a reconstruction interval validated without course geometry."""

    BEFORE = "before"
    AFTER = "after"


class AllocationMethod(StrEnum):
    """Evidence selected to distribute records along a matched course span."""

    RECORDED_DISTANCE = "recorded_distance"
    RECORDED_SPEED = "recorded_speed"
    TIMESTAMPS = "timestamps"
    RECORD_ORDER = "record_order"


class ReconstructionReason(StrEnum):
    """Stable reasons supporting or refusing one reconstruction candidate."""

    INTERVAL_HIGH_CONFIDENCE = "interval_high_confidence"
    INTERVAL_MEDIUM_CONFIDENCE = "interval_medium_confidence"
    ANCHORS_MATCHED = "anchors_matched"
    UNIQUE_COURSE_MATCH = "unique_course_match"
    TEMPORAL_ORDER_PRESERVED = "temporal_order_preserved"
    COURSE_SPEED_PLAUSIBLE = "course_speed_plausible"
    ANCHOR_CONNECTORS_PLAUSIBLE = "anchor_connectors_plausible"
    RECORDED_DISTANCE_ALLOCATION = "recorded_distance_allocation"
    RECORDED_SPEED_ALLOCATION = "recorded_speed_allocation"
    TIMESTAMP_ALLOCATION = "timestamp_allocation"
    RECORD_ORDER_ALLOCATION = "record_order_allocation"
    INTERVAL_CONFIDENCE_INSUFFICIENT = "interval_confidence_insufficient"
    ANCHOR_BEFORE_NOT_MATCHED = "anchor_before_not_matched"
    ANCHOR_AFTER_NOT_MATCHED = "anchor_after_not_matched"
    COURSE_MATCH_AMBIGUOUS = "course_match_ambiguous"
    COURSE_TRAVERSAL_IMPLAUSIBLE = "course_traversal_implausible"
    CANDIDATE_TRANSITION_IMPLAUSIBLE = "candidate_transition_implausible"
    RECONSTRUCTION_INTERVAL_LIMIT_EXCEEDED = "reconstruction_interval_limit_exceeded"
    NO_CORRUPTED_INTERVALS = "no_corrupted_intervals"
    DETECTION_HAS_NO_RECONSTRUCTABLE_INTERVAL = "detection_has_no_reconstructable_interval"
    ALL_INTERVALS_READY = "all_intervals_ready"
    SOME_INTERVALS_UNRESOLVED = "some_intervals_unresolved"
    NO_INTERVAL_READY = "no_interval_ready"
    ANCHOR_BEFORE_UNSTABLE = "anchor_before_unstable"
    ANCHOR_AFTER_UNSTABLE = "anchor_after_unstable"
    INSUFFICIENT_NORMAL_CONTEXT = "insufficient_normal_context"
    NON_NORMAL_TRANSITION_CONTEXT = "non_normal_transition_context"
    MISSING_POSITION_CONTEXT = "missing_position_context"
    CONTINUITY_BOUNDARY_CONTEXT = "continuity_boundary_context"
    ACTIVITY_BOUNDARY_CONTEXT = "activity_boundary_context"
    MIXED_GNSS_REGION = "mixed_gnss_region"
    CLUSTERED_ABNORMAL_TRANSITIONS = "clustered_abnormal_transitions"
    MISSING_GNSS_EVIDENCE = "missing_gnss_evidence"
    STABLE_OUTER_ANCHORS = "stable_outer_anchors"
    PLAUSIBLE_OUTER_BRIDGE = "plausible_outer_bridge"
    MIXED_REGION_REQUIRES_REVIEW = "mixed_region_requires_review"
    ONE_SIDED_BOUNDARIES_REFINED = "one_sided_boundaries_refined"
    ONE_SIDED_BOUNDARY_BEFORE_NOT_FOUND = "one_sided_boundary_before_not_found"
    ONE_SIDED_BOUNDARY_AFTER_NOT_FOUND = "one_sided_boundary_after_not_found"
    STABLE_COURSE_CORRIDOR = "stable_course_corridor"
    ALL_POSITIONED_COMPONENTS_TAINTED = "all_positioned_components_tainted"
    UNTAINTED_POSITION_COMPONENT = "untainted_position_component"
    COMPOSITE_REGION_RECONSTRUCTABLE = "composite_region_reconstructable"
    COMPOSITE_REGION_EXPANDED = "composite_region_expanded"
    MISSING_COORDINATES_INFERRED = "missing_coordinates_inferred"
    COMPONENT_WISE_RECONSTRUCTION_REQUIRED = "component_wise_reconstruction_required"
    MISSING_COMPLETION_ENABLED = "missing_completion_enabled"
    MISSING_COMPLETION_CANDIDATE = "missing_completion_candidate"
    MISSING_PREFIX = "missing_prefix"
    MISSING_SUFFIX = "missing_suffix"
    OBSERVED_COURSE_ALIGNMENT = "observed_course_alignment"
    OBSERVED_DISTANCE_CONSISTENT = "observed_distance_consistent"
    COURSE_ENDPOINT_USED = "course_endpoint_used"
    RECORDED_DISTANCE_PRESERVED = "recorded_distance_preserved"
    NO_STABLE_POSITION_RUN = "no_stable_position_run"
    OBSERVED_COURSE_ALIGNMENT_AMBIGUOUS = "observed_course_alignment_ambiguous"
    OBSERVED_DISTANCE_INCONSISTENT = "observed_distance_inconsistent"
    MISSING_RUN_TOO_LARGE = "missing_run_too_large"
    MISSING_POSITION_FIELDS_UNAVAILABLE = "missing_position_fields_unavailable"
    MISSING_CANDIDATE_TRANSITION_IMPLAUSIBLE = "missing_candidate_transition_implausible"
    OVERLAPS_PRIMARY_RECONSTRUCTION = "overlaps_primary_reconstruction"


class GnssComponentKind(StrEnum):
    """Whether one contiguous composite-region component has positions."""

    POSITIONED = "positioned"
    MISSING = "missing"


class GnssComponentState(StrEnum):
    """Course-independent evidence state for one composite-region component."""

    PROVEN_CORRUPTED = "proven_corrupted"
    TAINTED = "tainted"
    PLAUSIBLE = "plausible"
    UNKNOWN = "unknown"
    MISSING = "missing"


class GnssComponentReason(StrEnum):
    """Stable evidence explaining a composite-region component state."""

    POSITION_UNAVAILABLE = "position_unavailable"
    COVERED_BY_DETECTED_CORE = "covered_by_detected_core"
    OVERLAPS_DETECTED_CORE = "overlaps_detected_core"
    CONTAINS_IMPOSSIBLE_TRANSITION = "contains_impossible_transition"
    CONTAINS_SUSPICIOUS_TRANSITION = "contains_suspicious_transition"
    SUFFICIENT_NORMAL_CONTEXT = "sufficient_normal_context"
    INSUFFICIENT_COMPONENT_EVIDENCE = "insufficient_component_evidence"


class RepairPlanStatus(StrEnum):
    """Whether a complete dry-run plan is safe for a future writer."""

    NOT_NEEDED = "not_needed"
    READY = "ready"
    PARTIAL = "partial"
    REFUSED = "refused"


class MissingCourseRunKind(StrEnum):
    """Supported endpoint morphology for explicit missing-position completion."""

    PREFIX = "prefix"
    SUFFIX = "suffix"


class RepairIntervalAction(StrEnum):
    """Whether one detected interval is selected for this repair invocation."""

    APPLIED = "applied"
    SKIPPED = "skipped"


class RepairSelectionReason(StrEnum):
    """Stable reasons for applying or skipping one reconstruction interval."""

    CONFIDENCE_AT_OR_ABOVE_THRESHOLD = "confidence_at_or_above_threshold"
    BELOW_MINIMUM_CONFIDENCE = "below_minimum_confidence"
    NO_RECONSTRUCTION_CANDIDATE = "no_reconstruction_candidate"


@dataclass(frozen=True, slots=True)
class CoursePoint:
    """One point in a continuous GPX course segment."""

    index: int
    segment_index: int
    point_index: int
    latitude: float
    longitude: float
    elevation_m: float | None
    cumulative_distance_m: float


@dataclass(frozen=True, slots=True)
class CourseSegment:
    """One physically continuous course polyline."""

    index: int
    points: tuple[CoursePoint, ...]
    length_m: float


@dataclass(frozen=True, slots=True)
class CourseData:
    """Parsed GPX reference geometry kept separate from activity input."""

    source_path: Path
    raw_bytes: bytes
    version: str | None
    creator: str | None
    segments: tuple[CourseSegment, ...]
    point_count: int
    total_distance_m: float


@dataclass(frozen=True, slots=True)
class CourseAnchorMatch:
    """Projection of one trusted activity anchor onto a course segment."""

    course_segment_index: int
    segment_start_point_index: int
    segment_end_point_index: int
    segment_fraction: float
    course_distance_m: float
    latitude: float
    longitude: float
    anchor_distance_m: float


@dataclass(frozen=True, slots=True)
class CandidateCoordinate:
    """One proposed coordinate replacement; timestamp remains informational."""

    record_index: int
    timestamp: datetime | None
    original_latitude: float | None
    original_longitude: float | None
    candidate_latitude: float
    candidate_longitude: float
    course_distance_m: float


@dataclass(frozen=True, slots=True)
class AnchorStabilityDiagnostic:
    """Course-independent local evidence around one proposed trusted anchor."""

    anchor_record_index: int
    direction: AnchorDirection
    stable: bool
    required_normal_transition_count: int
    consecutive_normal_transition_count: int
    inspected_start_record_index: int
    inspected_end_record_index: int
    blocking_record_index: int | None
    blocking_classification: TransitionClassification | None
    reasons: tuple[ReconstructionReason, ...]


@dataclass(frozen=True, slots=True)
class GnssRegionComponent:
    """One contiguous positioned or missing part of a composite GNSS region."""

    start_record_index: int
    end_record_index: int
    record_count: int
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    duration_seconds: float | None
    kind: GnssComponentKind
    state: GnssComponentState
    confidence: IntegrityConfidence
    positioned_record_count: int
    missing_position_record_count: int
    suspicious_transition_count: int
    impossible_transition_count: int
    detected_core_record_count: int
    reasons: tuple[GnssComponentReason, ...]


@dataclass(frozen=True, slots=True)
class MixedGnssRegion:
    """Bounded component-level evidence cluster around unsafe local anchors."""

    start_record_index: int
    end_record_index: int
    record_count: int
    proposed_trusted_before_record_index: int | None
    proposed_trusted_after_record_index: int | None
    missing_position_record_count: int
    suspicious_transition_count: int
    impossible_transition_count: int
    outer_anchor_before: AnchorStabilityDiagnostic | None
    outer_anchor_after: AnchorStabilityDiagnostic | None
    bridge_elapsed_seconds: float | None
    bridge_distance_m: float | None
    bridge_speed_mps: float | None
    bridge_speed_limit_mps: float
    bridge_plausible: bool
    confidence: IntegrityConfidence
    repair_eligible: bool
    reasons: tuple[ReconstructionReason, ...]
    components: tuple[GnssRegionComponent, ...] = ()
    detected_core_ranges: tuple[tuple[int, int], ...] = ()
    all_positioned_components_tainted: bool = False
    reconstructable: bool = False


@dataclass(frozen=True, slots=True)
class CourseBoundaryRefinement:
    """Course-stage expansion from a detected core to stable outer drift boundaries."""

    detected_start_record_index: int
    detected_end_record_index: int
    original_trusted_before_record_index: int
    original_trusted_after_record_index: int
    refined_start_record_index: int
    refined_end_record_index: int
    refined_trusted_before_record_index: int
    refined_trusted_after_record_index: int
    expanded_before_record_count: int
    expanded_after_record_count: int
    corridor_tolerance_m: float
    required_stable_record_count: int
    reasons: tuple[ReconstructionReason, ...]


@dataclass(frozen=True, slots=True)
class IntervalRepairPlan:
    """Declarative coordinate candidate for one corrupted interval."""

    interval: CorruptedInterval
    anchor_before: CourseAnchorMatch
    anchor_after: CourseAnchorMatch
    direction: CourseDirection
    course_span_distance_m: float
    course_apparent_speed_mps: float
    anchor_connector_distance_m: float
    reconstruction_path_distance_m: float
    allocation_method: AllocationMethod
    coordinate_updates: tuple[CandidateCoordinate, ...]
    fields_to_change: tuple[str, ...]
    dependent_fields_to_recalculate: tuple[str, ...]
    confidence: IntegrityConfidence
    repair_eligible: bool
    reasons: tuple[ReconstructionReason, ...]
    anchor_before_stability: AnchorStabilityDiagnostic
    anchor_after_stability: AnchorStabilityDiagnostic
    boundary_refinement: CourseBoundaryRefinement | None = None
    composite_region: MixedGnssRegion | None = None
    reconstruction_scope_ranges: tuple[tuple[int, int], ...] = ()
    preserve_recorded_distance: bool = False


@dataclass(frozen=True, slots=True)
class MissingCourseRun:
    """Inclusive missing-position target; it is not a detected corrupted interval."""

    start_record_index: int
    end_record_index: int
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    kind: MissingCourseRunKind

    @property
    def record_count(self) -> int:
        """Return the number of missing records in the inclusive run."""
        return self.end_record_index - self.start_record_index + 1


@dataclass(frozen=True, slots=True)
class MissingCourseCompletionPlan:
    """Course-backed coordinates proposed only for an endpoint missing run."""

    interval: MissingCourseRun
    observed_run_start_record_index: int
    observed_run_end_record_index: int
    anchor_before_record_index: int | None
    anchor_after_record_index: int | None
    anchor_before: CourseAnchorMatch | None
    anchor_after: CourseAnchorMatch | None
    direction: CourseDirection
    course_span_distance_m: float
    course_apparent_speed_mps: float
    anchor_connector_distance_m: float
    reconstruction_path_distance_m: float
    observed_distance_m: float
    observed_course_span_distance_m: float
    observed_distance_ratio_error: float
    allocation_method: AllocationMethod
    coordinate_updates: tuple[CandidateCoordinate, ...]
    fields_to_change: tuple[str, ...]
    dependent_fields_to_recalculate: tuple[str, ...]
    confidence: IntegrityConfidence
    repair_eligible: bool
    reasons: tuple[ReconstructionReason, ...]
    reconstruction_scope_ranges: tuple[tuple[int, int], ...]
    preserve_recorded_distance: bool = True


@dataclass(frozen=True, slots=True)
class UnresolvedMissingCourseRun:
    """Missing endpoint run rejected by the explicit completion planner."""

    interval: MissingCourseRun
    confidence: IntegrityConfidence
    reasons: tuple[ReconstructionReason, ...]


type RepairTarget = CorruptedInterval | MissingCourseRun
type RepairCandidate = IntervalRepairPlan | MissingCourseCompletionPlan


@dataclass(frozen=True, slots=True)
class UnresolvedInterval:
    """A corrupted interval deliberately omitted from automatic reconstruction."""

    interval: CorruptedInterval
    confidence: IntegrityConfidence
    reasons: tuple[ReconstructionReason, ...]
    anchor_before_candidate_count: int
    anchor_after_candidate_count: int
    anchor_before_stability: AnchorStabilityDiagnostic | None
    anchor_after_stability: AnchorStabilityDiagnostic | None
    mixed_region: MixedGnssRegion | None


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """Complete dry-run result containing available candidates and unresolved intervals."""

    activity_path: Path
    course_path: Path
    status: RepairPlanStatus
    confidence: IntegrityConfidence
    detected_interval_count: int
    interval_plans: tuple[RepairCandidate, ...]
    unresolved_intervals: tuple[UnresolvedInterval, ...]
    reasons: tuple[ReconstructionReason, ...]
    timestamps_unchanged: bool
    trusted_records_unchanged: bool
    output_written: bool
    unresolved_missing_runs: tuple[UnresolvedMissingCourseRun, ...] = ()
    missing_completion_enabled: bool = False


@dataclass(frozen=True, slots=True)
class RepairIntervalDecision:
    """One applied/skipped decision under a selected confidence threshold."""

    interval: RepairTarget
    confidence: IntegrityConfidence
    action: RepairIntervalAction
    candidate_available: bool
    coordinate_update_count: int
    selection_reasons: tuple[RepairSelectionReason, ...]
    reconstruction_reasons: tuple[ReconstructionReason, ...]


@dataclass(frozen=True, slots=True)
class RepairSelection:
    """Deterministic subset of available candidates selected for one write."""

    minimum_confidence: IntegrityConfidence
    detected_interval_count: int
    selected_interval_plans: tuple[RepairCandidate, ...]
    decisions: tuple[RepairIntervalDecision, ...]

    @property
    def applied_interval_count(self) -> int:
        """Return how many detected intervals will be changed."""
        return sum(decision.action is RepairIntervalAction.APPLIED for decision in self.decisions)

    @property
    def skipped_interval_count(self) -> int:
        """Return how many detected intervals will remain untouched."""
        return self.detected_interval_count - self.applied_interval_count

    @property
    def is_partial(self) -> bool:
        """Return whether at least one detected interval remains untouched."""
        return self.skipped_interval_count > 0
