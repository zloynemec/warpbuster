"""Configuration models for WarpBuster Core."""

from __future__ import annotations

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
