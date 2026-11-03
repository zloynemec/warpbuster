"""Strict Trail Companion locations.csv adapter; no timestamp repair."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from types import MappingProxyType

from warpbuster.models.activity import (
    ActivityData,
    ActivityRecord,
    CoordinateBounds,
    CsvPreservationData,
    SourceRecordRef,
)

_MEASUREMENTS = (
    "horizontal_accuracy_m",
    "speed_mps",
    "speed_accuracy_mps",
    "course_deg",
    "course_accuracy_deg",
)
_REQUIRED = ("seq", "fix_timestamp_utc", "lat", "lon", *_MEASUREMENTS)
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]00:00)")


class GpsReadError(ValueError):
    """Invalid recorder CSV, with source line and field context."""


@dataclass(frozen=True, slots=True)
class GpsObservation:
    """Source values in seq order; lexical coordinates/time allow exact GPX export."""

    seq: int
    fix_timestamp_utc: str
    lat: str
    lon: str
    horizontal_accuracy_m: float | None
    speed_mps: float | None
    speed_accuracy_mps: float | None
    course_deg: float | None
    course_accuracy_deg: float | None
    quality_flags: str | None


@dataclass(frozen=True, slots=True)
class GpsInput:
    """Normalized detector input and matching immutable source observations."""

    activity: ActivityData
    observations: tuple[GpsObservation, ...]


def read_locations(path: str | Path) -> GpsInput:
    """Validate CSV and order by unique seq, never by measurement timestamp."""
    source = Path(path)
    raw_bytes = source.read_bytes()
    try:
        reader = csv.DictReader(io.StringIO(raw_bytes.decode("utf-8-sig")), strict=True)
        fields = reader.fieldnames or []
        missing = sorted(set(_REQUIRED) - set(fields))
        if missing:
            raise GpsReadError(f"missing columns: {', '.join(missing)}")
        if len(fields) != len(set(fields)):
            raise GpsReadError("duplicate column names")
        observations: dict[int, tuple[GpsObservation, datetime, float, float]] = {}
        for values in reader:
            context = f"{source}: line {reader.line_num}"
            if None in values or any(value is None for value in values.values()):
                raise GpsReadError(f"{context}: column count does not match header")
            try:
                seq_text = values["seq"].strip()
                if not seq_text.isascii() or not seq_text.isdecimal():
                    raise ValueError("seq must be a nonnegative integer")
                seq = int(seq_text)
                if seq in observations:
                    raise ValueError(f"duplicate seq {seq}")
                time_text = values["fix_timestamp_utc"].strip()
                if _UTC_TIMESTAMP.fullmatch(time_text) is None:
                    raise ValueError("fix_timestamp_utc must be a full ISO 8601 UTC timestamp")
                try:
                    timestamp = datetime.fromisoformat(time_text)
                except ValueError as error:
                    raise ValueError("invalid fix_timestamp_utc") from error
                if timestamp.utcoffset() != timedelta(0):
                    raise ValueError("fix_timestamp_utc must include UTC timezone")
                lat = _number(values["lat"], "lat")
                lon = _number(values["lon"], "lon")
                if not -90 <= lat <= 90:
                    raise ValueError("lat outside [-90, 90]")
                if not -180 <= lon <= 180:
                    raise ValueError("lon outside [-180, 180]")
                measurements = {name: _measurement(values[name], name) for name in _MEASUREMENTS}
                course = measurements["course_deg"]
                if course is not None and course >= 360:
                    raise ValueError("course_deg outside [0, 360)")
                point = GpsObservation(
                    seq=seq,
                    fix_timestamp_utc=time_text,
                    lat=values["lat"].strip(),
                    lon=values["lon"].strip(),
                    quality_flags=values.get("quality_flags"),
                    **measurements,
                )
                observations[seq] = (point, timestamp, lat, lon)
            except ValueError as error:
                raise GpsReadError(f"{context}: {error}") from error
    except (UnicodeError, csv.Error) as error:
        raise GpsReadError(f"cannot decode CSV {source}: {error}") from error
    if not observations:
        raise GpsReadError(f"{source}: no observations")
    ordered = [observations[seq] for seq in sorted(observations)]
    records = tuple(
        ActivityRecord(
            index=index,
            timestamp=timestamp,
            latitude=lat,
            longitude=lon,
            speed=point.speed_mps,
            altitude=None,
            distance=None,
            heart_rate=None,
            cadence=None,
            power=None,
            temperature=None,
            source=SourceRecordRef(message_index=point.seq, occurrence_index=index),
        )
        for index, (point, timestamp, lat, lon) in enumerate(ordered)
    )
    timestamps = [item[1] for item in ordered]
    return GpsInput(
        observations=tuple(item[0] for item in ordered),
        activity=ActivityData(
            records=records,
            laps=(),
            sessions=(),
            events=(),
            manufacturer=None,
            product=None,
            sport=None,
            sub_sport=None,
            start_time=timestamps[0],
            duration_seconds=(timestamps[-1] - timestamps[0]).total_seconds(),
            recorded_distance_m=None,
            coordinate_bounds=CoordinateBounds(
                min_latitude=min(item[2] for item in ordered),
                max_latitude=max(item[2] for item in ordered),
                min_longitude=min(item[3] for item in ordered),
                max_longitude=max(item[3] for item in ordered),
            ),
            available_fields=frozenset(
                {"position", "timestamp"}
                | ({"speed"} if any(point[0].speed_mps is not None for point in ordered) else set())
            ),
            message_counts=MappingProxyType({"location": len(records)}),
            developer_fields=(),
            unknown_fields=(),
            preservation=CsvPreservationData(source_path=source, raw_bytes=raw_bytes),
        ),
    )


def _number(text: str, name: str) -> float:
    try:
        value = float(text)
        if value == 0 and Decimal(text) != 0:
            raise ValueError("number underflows normalized precision")
    except (ValueError, InvalidOperation) as error:
        raise ValueError(f"invalid {name}: {text!r}") from error
    if not isfinite(value):
        raise ValueError(f"non-finite {name}")
    return value


def _measurement(text: str, name: str) -> float | None:
    if not text.strip():
        return None
    value = _number(text, name)
    return value if value >= 0 else None
