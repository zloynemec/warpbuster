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
    """

    profile: IntegrityProfile = IntegrityProfile.GENERIC
    absolute_impossible_speed_mps: float | None = None
    absolute_impossible_distance_m: float = 50.0
    relative_suspicious_speed_floor_mps: float = 20.0
    relative_speed_multiplier: float = 6.0
    relative_mad_multiplier: float = 10.0
    relative_suspicious_distance_m: float = 20.0
    minimum_baseline_samples: int = 5

    @classmethod
    def running(cls) -> IntegrityConfig:
        """Return the conservative profile for running and trail running."""
        return cls(
            profile=IntegrityProfile.RUNNING,
            absolute_impossible_speed_mps=25.0,
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
        }
        for name, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if (
            self.absolute_impossible_speed_mps is not None
            and self.absolute_impossible_speed_mps <= 0
        ):
            raise ValueError("absolute_impossible_speed_mps must be greater than zero")
        if self.minimum_baseline_samples < 1:
            raise ValueError("minimum_baseline_samples must be at least one")
