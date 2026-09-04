"""Configuration models for WarpBuster Core."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class IntegrityProfile(StrEnum):
    """Named threshold profiles selected from normalized activity semantics."""

    GENERIC = "generic"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class IntegrityConfig:
    """Named thresholds for local physical-transition analysis.

    Attributes:
        profile: Stable name of the threshold profile.
        absolute_impossible_speed_mps: Absolute human-movement ceiling in metres
            per second. ``None`` disables speed-only impossible classification for
            activity types without a justified physical ceiling.
        absolute_impossible_distance_m: Minimum transition distance in metres needed
            to classify an excessive apparent speed as physically impossible.
        relative_suspicious_speed_floor_mps: Conservative lower bound in metres per
            second for any baseline-relative suspicion.
        relative_speed_multiplier: Multiplier applied to the median local speed.
        relative_mad_multiplier: Multiplier applied to median absolute deviation and
            added to the median local speed.
        relative_suspicious_distance_m: Minimum transition distance in metres needed
            for baseline-relative suspicion.
        minimum_baseline_samples: Minimum positive-time transitions needed before
            median/MAD evidence may raise the suspicious speed floor.
        island_search_max_elapsed_seconds: Maximum elapsed time in seconds searched
            forward from an impossible entry transition.
        island_search_max_exit_candidates: Maximum later impossible transitions tested
            as exits for one entry, bounding worst-case search work.
        bridge_max_speed_mps: Maximum direct trusted-anchor speed in metres per second
            that can support a plausible bridge. ``None`` disables island detection.
        bridge_speed_floor_mps: Minimum derived bridge limit in metres per second.
        bridge_baseline_multiplier: Multiplier applied to median local speed when
            deriving the bridge limit.
        diagnostic_max_candidate_details: Maximum bridge candidate details retained
            for reports; aggregate counters are never truncated.
        one_sided_search_max_records: Maximum records inspected after an unpaired
            impossible transition when looking for missing-exit cluster evidence.
        one_sided_max_clean_gap_records: Maximum records without new suspicious or
            missing-position evidence that may join evidence into one cluster.
        one_sided_anchor_min_normal_transitions: Consecutive NORMAL transitions
            required outside each proposed one-sided cluster boundary.
        one_sided_anchor_scan_max_records: Maximum records inspected outward from
            either proposed trusted anchor.
        one_sided_max_diagnostics: Maximum candidate diagnostics retained; aggregate
            counters are never truncated.
        tail_anchor_min_normal_transitions: Consecutive adjacent NORMAL transitions
            required before a tail entry and for reachable recovery (count); default 15.
        tail_position_error_budget_m: Combined anchor/observation position uncertainty
            added to the physical reachability radius, in metres; default 50.
        vertical_warning_speed_mps: Sustained absolute vertical speed in metres per
            second that merits a sensor-consistency warning. ``None`` disables the
            scan for activity types without a justified profile.
        vertical_warning_single_transition_speed_mps: Higher absolute vertical speed
            that can create a warning from one transition.
        vertical_warning_min_delta_m: Minimum absolute altitude change per transition.
        vertical_warning_min_consecutive_transitions: Consecutive lower-threshold
            transitions required for a sustained warning.
        vertical_warning_max_count: Maximum warnings retained in a report.
        geometry_min_chord_distance_m: Minimum endpoint distance in metres for a
            near-collinear geometry warning.
        geometry_min_position_count: Minimum positioned observations in a warning.
        geometry_max_cross_track_deviation_m: Maximum perpendicular deviation in
            metres from the candidate chord.
        geometry_max_path_to_chord_ratio: Maximum sampled-path/chord ratio.
        geometry_scan_max_window_records: Maximum positioned observations inspected
            by one bounded candidate window.
        geometry_scan_stride_records: Number of positioned observations between
            candidate window starts.
        geometry_max_bearing_change_degrees: Maximum chord-bearing difference in
            degrees when merging overlapping candidate windows.
        geometry_max_warnings: Maximum warnings retained in a report; aggregate
            diagnostics still count omitted warnings.
    """

    profile: IntegrityProfile = IntegrityProfile.GENERIC
    absolute_impossible_speed_mps: float | None = None
    absolute_impossible_distance_m: float = 50.0
    relative_suspicious_speed_floor_mps: float = 20.0
    relative_speed_multiplier: float = 6.0
    relative_mad_multiplier: float = 10.0
    relative_suspicious_distance_m: float = 20.0
    minimum_baseline_samples: int = 5
    island_search_max_elapsed_seconds: float = 3_600.0
    island_search_max_exit_candidates: int = 64
    bridge_max_speed_mps: float | None = None
    bridge_speed_floor_mps: float = 5.0
    bridge_baseline_multiplier: float = 3.0
    diagnostic_max_candidate_details: int = 100
    one_sided_search_max_records: int = 512
    one_sided_max_clean_gap_records: int = 32
    one_sided_anchor_min_normal_transitions: int = 15
    one_sided_anchor_scan_max_records: int = 60
    one_sided_max_diagnostics: int = 100
    tail_anchor_min_normal_transitions: int = 15
    tail_position_error_budget_m: float = 50.0
    vertical_warning_speed_mps: float | None = None
    vertical_warning_single_transition_speed_mps: float = 10.0
    vertical_warning_min_delta_m: float = 4.0
    vertical_warning_min_consecutive_transitions: int = 3
    vertical_warning_max_count: int = 100
    geometry_min_chord_distance_m: float = 1_000.0
    geometry_min_position_count: int = 100
    geometry_max_cross_track_deviation_m: float = 0.5
    geometry_max_path_to_chord_ratio: float = 1.0005
    geometry_scan_max_window_records: int = 512
    geometry_scan_stride_records: int = 16
    geometry_max_bearing_change_degrees: float = 2.0
    geometry_max_warnings: int = 100

    @classmethod
    def running(cls) -> IntegrityConfig:
        """Return the conservative profile for running and trail running."""
        return cls(
            profile=IntegrityProfile.RUNNING,
            absolute_impossible_speed_mps=25.0,
            bridge_max_speed_mps=12.0,
            vertical_warning_speed_mps=4.0,
        )

    @classmethod
    def for_sport(cls, sport: str | int | None) -> IntegrityConfig:
        """Select a profile without depending on FIT-vendor types."""
        if isinstance(sport, str) and sport.casefold() == "running":
            return cls.running()
        return cls()

    def __post_init__(self) -> None:
        """Reject invalid threshold profiles early."""
        positive_values = {
            "absolute_impossible_distance_m": self.absolute_impossible_distance_m,
            "relative_suspicious_speed_floor_mps": self.relative_suspicious_speed_floor_mps,
            "relative_speed_multiplier": self.relative_speed_multiplier,
            "relative_mad_multiplier": self.relative_mad_multiplier,
            "relative_suspicious_distance_m": self.relative_suspicious_distance_m,
            "island_search_max_elapsed_seconds": self.island_search_max_elapsed_seconds,
            "bridge_speed_floor_mps": self.bridge_speed_floor_mps,
            "bridge_baseline_multiplier": self.bridge_baseline_multiplier,
            "geometry_min_chord_distance_m": self.geometry_min_chord_distance_m,
            "geometry_max_cross_track_deviation_m": (self.geometry_max_cross_track_deviation_m),
            "geometry_max_bearing_change_degrees": (self.geometry_max_bearing_change_degrees),
            "vertical_warning_single_transition_speed_mps": (
                self.vertical_warning_single_transition_speed_mps
            ),
            "vertical_warning_min_delta_m": self.vertical_warning_min_delta_m,
        }
        for name, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if (
            self.absolute_impossible_speed_mps is not None
            and self.absolute_impossible_speed_mps <= 0
        ):
            raise ValueError("absolute_impossible_speed_mps must be greater than zero")
        if self.bridge_max_speed_mps is not None and self.bridge_max_speed_mps <= 0:
            raise ValueError("bridge_max_speed_mps must be greater than zero")
        if self.vertical_warning_speed_mps is not None and self.vertical_warning_speed_mps <= 0:
            raise ValueError("vertical_warning_speed_mps must be greater than zero")
        if (
            self.vertical_warning_speed_mps is not None
            and self.vertical_warning_single_transition_speed_mps < self.vertical_warning_speed_mps
        ):
            raise ValueError(
                "vertical_warning_single_transition_speed_mps must not be less than "
                "vertical_warning_speed_mps"
            )
        if (
            self.bridge_max_speed_mps is not None
            and self.bridge_speed_floor_mps > self.bridge_max_speed_mps
        ):
            raise ValueError("bridge_speed_floor_mps must not exceed bridge_max_speed_mps")
        if self.minimum_baseline_samples < 1:
            raise ValueError("minimum_baseline_samples must be at least one")
        if self.island_search_max_exit_candidates < 1:
            raise ValueError("island_search_max_exit_candidates must be at least one")
        if self.diagnostic_max_candidate_details < 0:
            raise ValueError("diagnostic_max_candidate_details must not be negative")
        if self.one_sided_search_max_records < 1:
            raise ValueError("one_sided_search_max_records must be at least one")
        if self.one_sided_max_clean_gap_records < 0:
            raise ValueError("one_sided_max_clean_gap_records must not be negative")
        if self.one_sided_anchor_min_normal_transitions < 1:
            raise ValueError("one_sided_anchor_min_normal_transitions must be at least one")
        if self.one_sided_anchor_scan_max_records < self.one_sided_anchor_min_normal_transitions:
            raise ValueError(
                "one_sided_anchor_scan_max_records must not be less than "
                "one_sided_anchor_min_normal_transitions"
            )
        if self.one_sided_max_diagnostics < 0:
            raise ValueError("one_sided_max_diagnostics must not be negative")
        if self.tail_anchor_min_normal_transitions < 1:
            raise ValueError("tail_anchor_min_normal_transitions must be at least one")
        if (
            not math.isfinite(self.tail_position_error_budget_m)
            or self.tail_position_error_budget_m < 0
        ):
            raise ValueError("tail_position_error_budget_m must be finite and non-negative")
        if self.vertical_warning_min_consecutive_transitions < 2:
            raise ValueError("vertical_warning_min_consecutive_transitions must be at least two")
        if self.vertical_warning_max_count < 0:
            raise ValueError("vertical_warning_max_count must not be negative")
        if self.geometry_min_position_count < 3:
            raise ValueError("geometry_min_position_count must be at least three")
        if self.geometry_scan_max_window_records < self.geometry_min_position_count:
            raise ValueError(
                "geometry_scan_max_window_records must not be less than geometry_min_position_count"
            )
        if self.geometry_scan_stride_records < 1:
            raise ValueError("geometry_scan_stride_records must be at least one")
        if self.geometry_max_path_to_chord_ratio < 1.0:
            raise ValueError("geometry_max_path_to_chord_ratio must be at least one")
        if self.geometry_max_bearing_change_degrees > 180.0:
            raise ValueError("geometry_max_bearing_change_degrees must not exceed 180")
        if self.geometry_max_warnings < 0:
            raise ValueError("geometry_max_warnings must not be negative")


@dataclass(frozen=True, slots=True)
class CourseReconstructionConfig:
    """Named thresholds and bounds for GPX course reconstruction.

    These values affect only optional reconstruction after integrity detection.
    They are deliberately separate from ``IntegrityConfig``.

    Active local matching uses ``anchor_match_tolerance_m`` (metres),
    ``anchor_candidate_deduplication_m`` (course chainage metres) and
    ``ambiguity_score_margin_m`` (metres). Context requires 15 NORMAL transitions;
    endpoint gaps additionally require 30 observations. The per-side 300-record /
    300-second caps bound that search. At most 128 full path allocations are tried
    per gap across all context windows; truncation is never called unique.

    Local observed progression uses qualified recorded-distance deltas or preserved
    GPS geometry. The context error budget is observed_length * ratio + the two
    measured projection errors, with every intermediate point within anchor tolerance.
    Gap signal consistency allows the larger of the relative tolerance and
    ``signal_distance_absolute_tolerance_m``: a small absolute budget for short
    paths, not an expansion of the GPS edit scope or the anchor match radius.
    The ``missing_completion_*`` speed and record-count bounds now cover all gaps.

    Deprecated compatibility fields, not used by the local planner:
    ``one_sided_anchor_*``, ``one_sided_drift_*``,
    ``signal_course_length_ratio_*`` and ``anchor_stability_scan_max_records``.
    They remain readable for old callers/config dumps, not as write-scope controls.
    ``mixed_region_*`` still configure standalone diagnostic grouping in safety.py;
    diagnostic envelopes never grant permission to edit coordinates.
    """

    anchor_match_tolerance_m: float = 75.0
    high_confidence_anchor_distance_m: float = 50.0
    anchor_candidate_deduplication_m: float = 25.0
    one_sided_anchor_match_tolerance_m: float = 100.0
    one_sided_anchor_candidate_deduplication_m: float = 40.0
    one_sided_drift_corridor_tolerance_m: float = 15.0
    one_sided_drift_stable_record_count: int = 15
    one_sided_drift_search_max_records: int = 256
    ambiguity_score_margin_m: float = 10.0
    minimum_course_span_m: float = 10.0
    signal_course_length_ratio_min: float = 0.5
    signal_course_length_ratio_max: float = 2.0
    maximum_anchor_candidates: int = 32
    maximum_reconstruction_intervals: int = 100
    anchor_stability_min_normal_transitions: int = 15
    anchor_stability_scan_max_records: int = 60
    mixed_region_search_max_records: int = 1_500
    mixed_region_max_clean_gap_records: int = 15
    missing_alignment_min_position_records: int = 30
    missing_alignment_max_distance_ratio_error: float = 0.15
    # Metres: absolute floor for path/signal length comparison on short gaps.
    # This is a reconstruction tolerance, never evidence of coordinate corruption.
    signal_distance_absolute_tolerance_m: float = 3.0
    missing_completion_max_course_speed_mps: float = 10.0
    missing_completion_max_connector_speed_mps: float = 10.0
    missing_completion_max_run_records: int = 50_000
    # Hard per-side alignment context caps, in records and elapsed seconds respectively.
    # These are maxima, not required window sizes; the nearest sufficient context wins.
    local_alignment_max_context_records: int = 300
    local_alignment_max_context_seconds: float = 300.0
    # Maximum complete path allocations per gap, summed across context windows.
    local_alignment_max_path_evaluations: int = 128

    def __post_init__(self) -> None:
        """Reject unsafe or contradictory reconstruction bounds."""
        positive_values = {
            "signal_distance_absolute_tolerance_m": self.signal_distance_absolute_tolerance_m,
            "local_alignment_max_context_seconds": self.local_alignment_max_context_seconds,
            "anchor_match_tolerance_m": self.anchor_match_tolerance_m,
            "high_confidence_anchor_distance_m": self.high_confidence_anchor_distance_m,
            "anchor_candidate_deduplication_m": self.anchor_candidate_deduplication_m,
            "one_sided_anchor_match_tolerance_m": self.one_sided_anchor_match_tolerance_m,
            "one_sided_anchor_candidate_deduplication_m": (
                self.one_sided_anchor_candidate_deduplication_m
            ),
            "one_sided_drift_corridor_tolerance_m": (self.one_sided_drift_corridor_tolerance_m),
            "ambiguity_score_margin_m": self.ambiguity_score_margin_m,
            "minimum_course_span_m": self.minimum_course_span_m,
            "signal_course_length_ratio_min": self.signal_course_length_ratio_min,
            "signal_course_length_ratio_max": self.signal_course_length_ratio_max,
            "missing_alignment_max_distance_ratio_error": (
                self.missing_alignment_max_distance_ratio_error
            ),
            "missing_completion_max_course_speed_mps": (
                self.missing_completion_max_course_speed_mps
            ),
            "missing_completion_max_connector_speed_mps": (
                self.missing_completion_max_connector_speed_mps
            ),
        }
        for name, value in positive_values.items():
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.high_confidence_anchor_distance_m > self.anchor_match_tolerance_m:
            raise ValueError(
                "high_confidence_anchor_distance_m must not exceed anchor_match_tolerance_m"
            )
        if self.high_confidence_anchor_distance_m > self.one_sided_anchor_match_tolerance_m:
            raise ValueError(
                "high_confidence_anchor_distance_m must not exceed "
                "one_sided_anchor_match_tolerance_m"
            )
        if self.signal_course_length_ratio_min > self.signal_course_length_ratio_max:
            raise ValueError(
                "signal_course_length_ratio_min must not exceed signal_course_length_ratio_max"
            )
        if self.maximum_anchor_candidates < 1:
            raise ValueError("maximum_anchor_candidates must be at least one")
        if self.maximum_reconstruction_intervals < 1:
            raise ValueError("maximum_reconstruction_intervals must be at least one")
        if self.one_sided_drift_stable_record_count < 2:
            raise ValueError("one_sided_drift_stable_record_count must be at least two")
        if self.one_sided_drift_search_max_records < self.one_sided_drift_stable_record_count:
            raise ValueError(
                "one_sided_drift_search_max_records must not be less than "
                "one_sided_drift_stable_record_count"
            )
        if self.anchor_stability_min_normal_transitions < 1:
            raise ValueError("anchor_stability_min_normal_transitions must be at least one")
        if self.anchor_stability_scan_max_records < self.anchor_stability_min_normal_transitions:
            raise ValueError(
                "anchor_stability_scan_max_records must not be less than "
                "anchor_stability_min_normal_transitions"
            )
        if self.mixed_region_search_max_records < 1:
            raise ValueError("mixed_region_search_max_records must be at least one")
        if self.mixed_region_max_clean_gap_records < 0:
            raise ValueError("mixed_region_max_clean_gap_records must not be negative")
        if self.missing_alignment_max_distance_ratio_error >= 1:
            raise ValueError("missing_alignment_max_distance_ratio_error must be less than one")
        if self.missing_alignment_min_position_records < 2:
            raise ValueError("missing_alignment_min_position_records must be at least two")
        if self.missing_completion_max_run_records < 1:
            raise ValueError("missing_completion_max_run_records must be at least one")
        if self.local_alignment_max_context_records < 2:
            raise ValueError("local_alignment_max_context_records must be at least two")
        if self.local_alignment_max_path_evaluations < 1:
            raise ValueError("local_alignment_max_path_evaluations must be at least one")
