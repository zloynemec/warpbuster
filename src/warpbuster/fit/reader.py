"""Read FIT activities into the vendor-neutral activity model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast

import fitdecode

from warpbuster.models.activity import (
    ActivityData,
    ActivityRecord,
    CoordinateBounds,
    DeveloperFieldDefinition,
    FitPreservationData,
    SourceFieldName,
    SourceMessage,
    SourceRecordRef,
    UnknownFieldSummary,
)

_SEMICIRCLES_PER_DEGREE = (1 << 31) / 180.0
# FIT profiles historically used two minor-version digits. Current profiles use three.
_EXTENDED_PROFILE_VERSION_THRESHOLD = 10_000
_LEGACY_PROFILE_VERSION_SCALE = 100
_EXTENDED_PROFILE_VERSION_SCALE = 1_000

type _DeveloperObservation = tuple[
    int,
    int,
    str | None,
    str | None,
    int | None,
    int | None,
]


class FitReadError(ValueError):
    """Raised when an input cannot be decoded as a valid FIT activity."""


def read_fit(path: str | Path) -> ActivityData:
    """Decode a FIT file and normalize its activity records."""
    source_path = Path(path)
    raw_bytes = source_path.read_bytes()
    source_messages: list[SourceMessage] = []
    definitions: list[Mapping[str, object]] = []
    developer_observations: list[_DeveloperObservation] = []
    occurrences: Counter[str] = Counter()
    profile_versions: list[str] = []

    try:
        with fitdecode.FitReader(
            raw_bytes,
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
            keep_raw_chunks=True,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitHeader):
                    profile_versions.append(_header_profile_version(frame))
                elif isinstance(frame, fitdecode.FitDefinitionMessage):
                    definitions.append(_definition_snapshot(frame))
                elif isinstance(frame, fitdecode.FitDataMessage):
                    message_type = str(frame.name)
                    source_messages.append(
                        _message_snapshot(
                            len(source_messages),
                            occurrences[message_type],
                            frame,
                        )
                    )
                    occurrences[message_type] += 1
                    developer_observations.extend(_developer_fields(frame))
    except Exception as error:
        raise FitReadError(f"cannot decode FIT file {source_path}: {error}") from error

    messages = tuple(source_messages)
    record_sources = _messages_of_type(messages, "record")
    records = tuple(
        _normalize_record(index, source.fields, source)
        for index, source in enumerate(record_sources)
    )
    sessions = _messages_of_type(messages, "session")
    laps = _messages_of_type(messages, "lap")
    events = _messages_of_type(messages, "event")
    file_ids = _messages_of_type(messages, "file_id")
    file_id_fields = file_ids[0].fields if file_ids else {}
    message_counts = Counter(message.message_type for message in messages)

    return ActivityData(
        records=records,
        laps=laps,
        sessions=sessions,
        events=events,
        manufacturer=_identity_value(file_id_fields.get("manufacturer")),
        product=_product_value(file_id_fields),
        sport=_session_identity(sessions, "sport"),
        sub_sport=_session_identity(sessions, "sub_sport"),
        start_time=_activity_start_time(records, sessions),
        duration_seconds=_activity_duration(records, sessions),
        recorded_distance_m=_recorded_distance(records, sessions),
        coordinate_bounds=_coordinate_bounds(records),
        available_fields=_available_fields(records),
        message_counts=MappingProxyType(dict(sorted(message_counts.items()))),
        developer_fields=_developer_field_summaries(developer_observations),
        unknown_fields=_unknown_field_summaries(messages),
        preservation=FitPreservationData(
            source_path=source_path,
            raw_bytes=raw_bytes,
            messages=messages,
            definitions=tuple(definitions),
            profile_version=", ".join(dict.fromkeys(profile_versions)),
            crc_valid=True,
        ),
    )


def _header_profile_version(frame: object) -> str:
    chunk = frame.chunk  # type: ignore[attr-defined]
    encoded_version = int.from_bytes(bytes(chunk.bytes[2:4]), byteorder="little")
    scale = (
        _EXTENDED_PROFILE_VERSION_SCALE
        if encoded_version >= _EXTENDED_PROFILE_VERSION_THRESHOLD
        else _LEGACY_PROFILE_VERSION_SCALE
    )
    major, minor = divmod(encoded_version, scale)
    return f"{major}.{minor}"


def _definition_snapshot(frame: object) -> Mapping[str, object]:
    chunk = frame.chunk  # type: ignore[attr-defined]
    return MappingProxyType(
        {
            "frame_index": int(chunk.index),
            "byte_offset": int(chunk.offset),
            "raw_chunk": bytes(chunk.bytes),
            "local_message_number": int(frame.local_mesg_num),  # type: ignore[attr-defined]
            "global_message_number": int(frame.global_mesg_num),  # type: ignore[attr-defined]
            "message_type": str(frame.name),  # type: ignore[attr-defined]
        }
    )


def _message_snapshot(
    index: int,
    occurrence_index: int,
    frame: object,
) -> SourceMessage:
    native_fields: dict[SourceFieldName, object] = {}
    developer_fields: dict[SourceFieldName, object] = {}
    for field in frame.fields:  # type: ignore[attr-defined]
        key = cast(SourceFieldName, field.name_or_num)
        if bool(getattr(field.field_def, "is_dev", False)):
            developer_fields[key] = cast(object, field.value)
        else:
            native_fields[key] = cast(object, field.value)
    if developer_fields:
        native_fields["developer_fields"] = MappingProxyType(developer_fields)

    chunk = frame.chunk  # type: ignore[attr-defined]
    return SourceMessage(
        index=index,
        frame_index=int(chunk.index),
        byte_offset=int(chunk.offset),
        global_message_number=int(frame.global_mesg_num),  # type: ignore[attr-defined]
        message_type=str(frame.name),  # type: ignore[attr-defined]
        occurrence_index=occurrence_index,
        fields=MappingProxyType(native_fields),
        raw_chunk=bytes(chunk.bytes),
    )


def _developer_fields(frame: object) -> list[_DeveloperObservation]:
    observations: list[_DeveloperObservation] = []
    for field in frame.fields:  # type: ignore[attr-defined]
        field_definition = field.field_def
        if not bool(getattr(field_definition, "is_dev", False)):
            continue
        field_profile = field.field
        native_field_number = _as_int(getattr(field_profile, "native_field_num", None))
        observations.append(
            (
                int(field_definition.dev_data_index),
                int(field_definition.def_num),
                _as_string(field.name),
                _as_string(field.units),
                int(frame.global_mesg_num) if native_field_number is not None else None,  # type: ignore[attr-defined]
                native_field_number,
            )
        )
    return observations


def _normalize_record(
    index: int,
    message: Mapping[SourceFieldName, object],
    source: SourceMessage,
) -> ActivityRecord:
    return ActivityRecord(
        index=index,
        timestamp=_as_datetime(message.get("timestamp")),
        latitude=_position_degrees(message.get("position_lat")),
        longitude=_position_degrees(message.get("position_long")),
        altitude=_preferred_float(message, "enhanced_altitude", "altitude"),
        distance=_as_float(message.get("distance")),
        speed=_preferred_float(message, "enhanced_speed", "speed"),
        heart_rate=_as_int(message.get("heart_rate")),
        cadence=_as_int(message.get("cadence")),
        power=_as_int(message.get("power")),
        temperature=_as_float(message.get("temperature")),
        source=SourceRecordRef(
            message_index=source.index,
            occurrence_index=source.occurrence_index,
        ),
    )


def _messages_of_type(
    messages: tuple[SourceMessage, ...],
    message_type: str,
) -> tuple[SourceMessage, ...]:
    return tuple(message for message in messages if message.message_type == message_type)


def _position_degrees(value: object) -> float | None:
    numeric = _as_float(value)
    return None if numeric is None else numeric / _SEMICIRCLES_PER_DEGREE


def _preferred_float(
    message: Mapping[SourceFieldName, object],
    preferred_name: str,
    fallback_name: str,
) -> float | None:
    preferred = _as_float(message.get(preferred_name))
    return preferred if preferred is not None else _as_float(message.get(fallback_name))


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _identity_value(value: object) -> str | int | None:
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return value


def _product_value(fields: Mapping[SourceFieldName, object]) -> str | int | None:
    for name, value in fields.items():
        if isinstance(name, str) and name != "product" and name.endswith("_product"):
            identity = _identity_value(value)
            if identity is not None:
                return identity
    return _identity_value(fields.get("product"))


def _session_identity(
    sessions: tuple[SourceMessage, ...],
    field_name: str,
) -> str | int | None:
    return next(
        (
            identity
            for session in sessions
            if (identity := _identity_value(session.fields.get(field_name))) is not None
        ),
        None,
    )


def _activity_start_time(
    records: tuple[ActivityRecord, ...],
    sessions: tuple[SourceMessage, ...],
) -> datetime | None:
    session_starts = [
        start
        for session in sessions
        if (start := _as_datetime(session.fields.get("start_time"))) is not None
    ]
    if session_starts:
        return min(session_starts)
    return next((record.timestamp for record in records if record.timestamp is not None), None)


def _activity_duration(
    records: tuple[ActivityRecord, ...],
    sessions: tuple[SourceMessage, ...],
) -> float | None:
    session_durations = [
        duration
        for session in sessions
        if (duration := _as_float(session.fields.get("total_elapsed_time"))) is not None
    ]
    if session_durations:
        return sum(session_durations)
    timestamps = [record.timestamp for record in records if record.timestamp is not None]
    if len(timestamps) < 2:
        return 0.0 if timestamps else None
    return (timestamps[-1] - timestamps[0]).total_seconds()


def _recorded_distance(
    records: tuple[ActivityRecord, ...],
    sessions: tuple[SourceMessage, ...],
) -> float | None:
    session_distances = [
        distance
        for session in sessions
        if (distance := _as_float(session.fields.get("total_distance"))) is not None
    ]
    if session_distances:
        return sum(session_distances)
    return next(
        (record.distance for record in reversed(records) if record.distance is not None),
        None,
    )


def _coordinate_bounds(records: tuple[ActivityRecord, ...]) -> CoordinateBounds | None:
    positions = [
        (record.latitude, record.longitude)
        for record in records
        if record.latitude is not None and record.longitude is not None
    ]
    if not positions:
        return None
    latitudes = [latitude for latitude, _longitude in positions]
    longitudes = [longitude for _latitude, longitude in positions]
    return CoordinateBounds(
        min_latitude=min(latitudes),
        max_latitude=max(latitudes),
        min_longitude=min(longitudes),
        max_longitude=max(longitudes),
    )


def _available_fields(records: tuple[ActivityRecord, ...]) -> frozenset[str]:
    presence = {
        "timestamp": any(record.timestamp is not None for record in records),
        "position": any(
            record.latitude is not None and record.longitude is not None for record in records
        ),
        "altitude": any(record.altitude is not None for record in records),
        "distance": any(record.distance is not None for record in records),
        "speed": any(record.speed is not None for record in records),
        "heart_rate": any(record.heart_rate is not None for record in records),
        "cadence": any(record.cadence is not None for record in records),
        "power": any(record.power is not None for record in records),
        "temperature": any(record.temperature is not None for record in records),
    }
    return frozenset(name for name, is_present in presence.items() if is_present)


def _developer_field_summaries(
    observations: list[_DeveloperObservation],
) -> tuple[DeveloperFieldDefinition, ...]:
    counts = Counter((observation[0], observation[1]) for observation in observations)
    metadata = {(observation[0], observation[1]): observation[2:] for observation in observations}
    return tuple(
        DeveloperFieldDefinition(
            key=key,
            developer_data_index=developer_data_index,
            field_definition_number=field_number,
            name=metadata[(developer_data_index, field_number)][0],
            units=metadata[(developer_data_index, field_number)][1],
            native_message_number=metadata[(developer_data_index, field_number)][2],
            native_field_number=metadata[(developer_data_index, field_number)][3],
            occurrences=counts[(developer_data_index, field_number)],
        )
        for key, (developer_data_index, field_number) in enumerate(sorted(counts))
    )


def _as_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _unknown_field_summaries(
    messages: tuple[SourceMessage, ...],
) -> tuple[UnknownFieldSummary, ...]:
    counts: Counter[tuple[str, int]] = Counter()
    for message in messages:
        counts.update(
            (message.message_type, field_name)
            for field_name in message.fields
            if isinstance(field_name, int)
        )
    return tuple(
        UnknownFieldSummary(message_type, field_number, occurrences)
        for (message_type, field_number), occurrences in sorted(counts.items())
    )
