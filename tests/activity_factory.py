"""Factories for vendor-neutral detector tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import cos, radians
from pathlib import Path
from types import MappingProxyType

from warpbuster.models.activity import (
    ActivityData,
    ActivityRecord,
    FitPreservationData,
    SourceRecordRef,
)

type Observation = tuple[float | None, float | None, float | None]


def make_activity(
    observations: list[Observation],
    *,
    sport: str | int | None = "running",
    sub_sport: str | int | None = None,
) -> ActivityData:
    """Build a minimal normalized activity from elapsed seconds and coordinates."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    records = tuple(
        ActivityRecord(
            index=index,
            timestamp=start + timedelta(seconds=elapsed) if elapsed is not None else None,
            latitude=latitude,
            longitude=longitude,
            altitude=None,
            distance=None,
            speed=None,
            heart_rate=None,
            cadence=None,
            power=None,
            temperature=None,
            source=SourceRecordRef(message_index=index, occurrence_index=index),
        )
        for index, (elapsed, latitude, longitude) in enumerate(observations)
    )
    return ActivityData(
        records=records,
        laps=(),
        sessions=(),
        events=(),
        manufacturer=None,
        product=None,
        sport=sport,
        sub_sport=sub_sport,
        start_time=records[0].timestamp if records else None,
        duration_seconds=None,
        recorded_distance_m=None,
        coordinate_bounds=None,
        available_fields=frozenset(),
        message_counts=MappingProxyType({}),
        developer_fields=(),
        unknown_fields=(),
        preservation=FitPreservationData(
            source_path=Path("synthetic.fit"),
            raw_bytes=b"",
            messages=(),
            definitions=(),
            profile_version="test",
            crc_valid=True,
        ),
    )


def eastward_observations(
    elapsed_seconds: list[float],
    distances_m: list[float],
    *,
    latitude: float = 55.0,
) -> list[Observation]:
    """Create observations at approximate eastward offsets from one origin."""
    if len(elapsed_seconds) != len(distances_m):
        raise ValueError("elapsed_seconds and distances_m must have equal length")
    metres_per_longitude_degree = 111_195.0 * cos(radians(latitude))
    return [
        (elapsed, latitude, 37.0 + distance / metres_per_longitude_degree)
        for elapsed, distance in zip(elapsed_seconds, distances_m, strict=True)
    ]
