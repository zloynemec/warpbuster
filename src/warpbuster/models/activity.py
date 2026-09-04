"""Vendor-neutral activity and source-preservation models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

type SourceFieldName = str | int


class ActivityFileFormat(StrEnum):
    """Supported source activity container formats."""

    FIT = "fit"
    GPX = "gpx"


@dataclass(frozen=True, slots=True)
class SourceRecordRef:
    """Stable link from a normalized record to its source data message."""

    message_index: int
    occurrence_index: int


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """Decoded source message retained for inspection and future patching."""

    index: int
    frame_index: int
    byte_offset: int
    global_message_number: int
    message_type: str
    occurrence_index: int
    fields: Mapping[SourceFieldName, object]
    raw_chunk: bytes


@dataclass(frozen=True, slots=True)
class DeveloperFieldDefinition:
    """Human-readable metadata for one FIT developer field."""

    key: int
    developer_data_index: int | None
    field_definition_number: int | None
    name: str | None
    units: str | None
    native_message_number: int | None
    native_field_number: int | None
    occurrences: int


@dataclass(frozen=True, slots=True)
class UnknownFieldSummary:
    """Occurrence count for an unrecognized native FIT field."""

    message_type: str
    field_number: int
    occurrences: int


@dataclass(frozen=True, slots=True)
class CoordinateBounds:
    """Geographic bounds in decimal degrees."""

    min_latitude: float
    max_latitude: float
    min_longitude: float
    max_longitude: float


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """One vendor-neutral activity observation."""

    index: int
    timestamp: datetime | None
    latitude: float | None
    longitude: float | None
    altitude: float | None
    distance: float | None
    speed: float | None
    heart_rate: int | None
    cadence: int | None
    power: int | None
    temperature: float | None
    source: SourceRecordRef
    continuity_id: int = 0


@dataclass(frozen=True, slots=True)
class FitPreservationData:
    """FIT-specific source data kept outside normalized activity semantics."""

    source_path: Path
    raw_bytes: bytes
    messages: tuple[SourceMessage, ...]
    definitions: tuple[Mapping[str, object], ...]
    profile_version: str
    crc_valid: bool
    compatibility_warnings: tuple[str, ...] = ()
    source_format: ActivityFileFormat = field(default=ActivityFileFormat.FIT, init=False)


@dataclass(frozen=True, slots=True)
class GpxPreservationData:
    """GPX source metadata retained for inspection without implying FIT output."""

    source_path: Path
    raw_bytes: bytes
    version: str | None
    creator: str | None
    track_count: int
    segment_count: int
    source_format: ActivityFileFormat = field(default=ActivityFileFormat.GPX, init=False)


type ActivityPreservationData = FitPreservationData | GpxPreservationData


@dataclass(frozen=True, slots=True)
class ActivityData:
    """Normalized activity plus immutable source-preservation metadata."""

    records: tuple[ActivityRecord, ...]
    laps: tuple[SourceMessage, ...]
    sessions: tuple[SourceMessage, ...]
    events: tuple[SourceMessage, ...]
    manufacturer: str | int | None
    product: str | int | None
    sport: str | int | None
    sub_sport: str | int | None
    start_time: datetime | None
    duration_seconds: float | None
    recorded_distance_m: float | None
    coordinate_bounds: CoordinateBounds | None
    available_fields: frozenset[str]
    message_counts: Mapping[str, int]
    developer_fields: tuple[DeveloperFieldDefinition, ...]
    unknown_fields: tuple[UnknownFieldSummary, ...]
    preservation: ActivityPreservationData
