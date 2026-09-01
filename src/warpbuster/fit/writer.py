"""Byte-preserving application of READY repair plans to original FIT files."""

from __future__ import annotations

import os
import struct
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path

import fitdecode
from fitdecode.utils import compute_crc  # type: ignore[import-untyped]

from warpbuster.fit.diff import diff_fit
from warpbuster.fit.validate import validate_fit
from warpbuster.geo import geodesic_distance_m
from warpbuster.models.activity import ActivityData, ActivityRecord, FitPreservationData
from warpbuster.models.fit import FitWriteResult
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    CandidateCoordinate,
    IntervalRepairPlan,
    RepairPlan,
    RepairSelection,
)
from warpbuster.reconstruction.selection import select_repair_intervals

_SEMICIRCLES_PER_DEGREE = (1 << 31) / 180.0
_DISTANCE_QUANTIZATION_M = 0.01
_DISTANCE_REGRESSION_TOLERANCE_M = 0.01
# FIT summary timestamps have one-second resolution while total_elapsed_time may carry
# milliseconds. Values selecting an end more than one second apart are inconsistent.
_SUMMARY_END_ALIGNMENT_TOLERANCE_SECONDS = 1.0


class FitWriteError(ValueError):
    """Raised when a FIT repair cannot be applied without violating safety rules."""


@dataclass(frozen=True, slots=True)
class _PatchRequest:
    message_index: int
    message_type: str
    occurrence_index: int
    field_name: str
    value: int | float
    raw_value: bool
    category: str


def default_output_path(source_path: str | Path) -> Path:
    """Return the non-overwriting default output name for one original FIT."""
    source = Path(source_path)
    return source.with_name(f"{source.stem}.fixed{source.suffix}")


def write_repaired_fit(
    activity: ActivityData,
    plan: RepairPlan,
    output_path: str | Path | None = None,
    *,
    minimum_confidence: IntegrityConfidence = IntegrityConfidence.HIGH,
    overwrite: bool = False,
) -> FitWriteResult:
    """Atomically apply candidates, replacing an existing output only when requested."""
    preservation = activity.preservation
    if not isinstance(preservation, FitPreservationData):
        raise FitWriteError("FIT writer requires original FIT preservation data")
    selection = select_repair_intervals(plan, minimum_confidence)
    _require_writeable_selection(selection)
    source_path = preservation.source_path
    if source_path.read_bytes() != preservation.raw_bytes:
        raise FitWriteError("original FIT changed after it was read")
    destination = Path(output_path) if output_path is not None else default_output_path(source_path)
    if destination.resolve() == source_path.resolve():
        raise FitWriteError("output path must differ from the original FIT path")
    if destination.exists() and not overwrite:
        raise FitWriteError(f"output already exists: {destination}")
    if not destination.parent.exists():
        raise FitWriteError(f"output directory does not exist: {destination.parent}")

    requests = _patch_requests(activity, selection.selected_interval_plans)
    patched_bytes, category_counts = _patch_fit_bytes(preservation.raw_bytes, requests)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary.write(patched_bytes)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)

        validation = validate_fit(temporary_path)
        if not validation.valid:
            raise FitWriteError("patched FIT failed validation")
        diff = diff_fit(source_path, temporary_path)
        if not diff.structure_compatible or not diff.definitions_unchanged:
            raise FitWriteError("patched FIT changed message or definition structure")
        if diff.unexpected_changed_field_count:
            raise FitWriteError(
                "patched FIT contains "
                f"{diff.unexpected_changed_field_count} unexpected field changes"
            )
        if overwrite:
            os.replace(temporary_path, destination)
        else:
            try:
                os.link(temporary_path, destination)
            except FileExistsError as error:
                raise FitWriteError(f"output already exists: {destination}") from error
            temporary_path.unlink()
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    return FitWriteResult(
        source_path=source_path,
        output_path=destination,
        bytes_written=len(patched_bytes),
        coordinate_field_change_count=category_counts.get("coordinate", 0),
        distance_field_change_count=category_counts.get("distance", 0),
        summary_field_change_count=category_counts.get("summary", 0),
        selection=selection,
        validation=replace(validation, path=destination),
        diff=replace(diff, fixed_path=destination),
    )


def _require_writeable_selection(selection: RepairSelection) -> None:
    if len(selection.decisions) != selection.detected_interval_count:
        raise FitWriteError("repair selection must describe every detected interval")
    if not selection.selected_interval_plans:
        raise FitWriteError(
            "no reconstruction candidate meets minimum confidence "
            f"{selection.minimum_confidence.value.upper()}"
        )


def _patch_requests(
    activity: ActivityData,
    interval_plans: tuple[IntervalRepairPlan, ...],
) -> tuple[_PatchRequest, ...]:
    requests: list[_PatchRequest] = []
    coordinate_updates = {
        update.record_index: update
        for interval in interval_plans
        for update in interval.coordinate_updates
    }
    expected_update_count = sum(len(interval.coordinate_updates) for interval in interval_plans)
    if len(coordinate_updates) != expected_update_count:
        raise FitWriteError("repair plan contains duplicate coordinate updates")

    for interval in interval_plans:
        allowed_indices = set(
            range(
                interval.interval.start_record_index,
                interval.interval.end_record_index + 1,
            )
        )
        if {update.record_index for update in interval.coordinate_updates} != allowed_indices:
            raise FitWriteError("coordinate updates must cover exactly one corrupted interval")

    for record_index, update in coordinate_updates.items():
        if not 0 <= record_index < len(activity.records):
            raise FitWriteError(f"coordinate update record index is invalid: {record_index}")
        record = activity.records[record_index]
        if update.timestamp != record.timestamp:
            raise FitWriteError(f"coordinate update timestamp mismatch at record {record_index}")
        if not -90.0 <= update.candidate_latitude <= 90.0:
            raise FitWriteError(f"candidate latitude is invalid at record {record_index}")
        if not -180.0 <= update.candidate_longitude <= 180.0:
            raise FitWriteError(f"candidate longitude is invalid at record {record_index}")
        requests.extend(
            (
                _record_request(
                    record,
                    "position_lat",
                    round(update.candidate_latitude * _SEMICIRCLES_PER_DEGREE),
                    raw_value=True,
                    category="coordinate",
                ),
                _record_request(
                    record,
                    "position_long",
                    round(update.candidate_longitude * _SEMICIRCLES_PER_DEGREE),
                    raw_value=True,
                    category="coordinate",
                ),
            )
        )

    corrections, desired_distances = _distance_corrections(activity, coordinate_updates)
    for record_index, distance_m in desired_distances.items():
        requests.append(
            _record_request(
                activity.records[record_index],
                "distance",
                distance_m,
                raw_value=False,
                category="distance",
            )
        )
    if desired_distances:
        requests.extend(_summary_requests(activity, corrections))
    return tuple(requests)


def _record_request(
    record: ActivityRecord,
    field_name: str,
    value: int | float,
    *,
    raw_value: bool,
    category: str,
) -> _PatchRequest:
    return _PatchRequest(
        message_index=record.source.message_index,
        message_type="record",
        occurrence_index=record.source.occurrence_index,
        field_name=field_name,
        value=value,
        raw_value=raw_value,
        category=category,
    )


def _distance_corrections(
    activity: ActivityData,
    coordinate_updates: Mapping[int, CandidateCoordinate],
) -> tuple[tuple[float, ...], dict[int, float]]:
    corrections = [0.0] * len(activity.records)
    desired: dict[int, float] = {}
    if not any(record.distance is not None for record in activity.records):
        return tuple(corrections), desired

    changed_indices = set(coordinate_updates)
    for previous, current in pairwise(activity.records):
        corrections[current.index] = corrections[previous.index]
        if previous.index not in changed_indices and current.index not in changed_indices:
            continue
        if previous.distance is None or current.distance is None:
            raise FitWriteError(
                "cannot safely correct a partial distance stream around repaired coordinates"
            )
        original_increment = current.distance - previous.distance
        if original_increment < -_DISTANCE_REGRESSION_TOLERANCE_M:
            raise FitWriteError("cannot correct a decreasing source distance stream")
        previous_latitude, previous_longitude = _repaired_position(
            previous,
            coordinate_updates,
        )
        current_latitude, current_longitude = _repaired_position(
            current,
            coordinate_updates,
        )
        if (
            previous_latitude is None
            or previous_longitude is None
            or current_latitude is None
            or current_longitude is None
        ):
            raise FitWriteError("repaired distance edge has missing coordinates")
        replacement_increment = geodesic_distance_m(
            previous_latitude,
            previous_longitude,
            current_latitude,
            current_longitude,
        )
        corrections[current.index] += replacement_increment - original_increment

    for record in activity.records:
        if record.distance is None:
            continue
        corrected = record.distance + corrections[record.index]
        if corrected < 0:
            raise FitWriteError("distance correction would produce a negative value")
        if abs(corrected - record.distance) >= _DISTANCE_QUANTIZATION_M / 2.0:
            desired[record.index] = corrected
    return tuple(corrections), desired


def _repaired_position(
    record: ActivityRecord,
    coordinate_updates: Mapping[int, CandidateCoordinate],
) -> tuple[float | None, float | None]:
    update = coordinate_updates.get(record.index)
    if update is None:
        return record.latitude, record.longitude
    return update.candidate_latitude, update.candidate_longitude


def _summary_requests(
    activity: ActivityData,
    corrections: tuple[float, ...],
) -> tuple[_PatchRequest, ...]:
    requests: list[_PatchRequest] = []
    for message in (*activity.laps, *activity.sessions):
        original_total = _number(message.fields.get("total_distance"))
        if original_total is None:
            continue
        correction = _summary_correction(activity, message.fields, corrections)
        if abs(correction) < _DISTANCE_QUANTIZATION_M / 2.0:
            continue
        corrected_total = original_total + correction
        if corrected_total < 0:
            raise FitWriteError(
                f"distance correction would make {message.message_type} total negative"
            )
        requests.append(
            _PatchRequest(
                message_index=message.index,
                message_type=message.message_type,
                occurrence_index=message.occurrence_index,
                field_name="total_distance",
                value=corrected_total,
                raw_value=False,
                category="summary",
            )
        )
        timer_time = _number(message.fields.get("total_timer_time"))
        if timer_time is None or timer_time <= 0:
            continue
        corrected_average_speed = corrected_total / timer_time
        for field_name in ("avg_speed", "enhanced_avg_speed"):
            if field_name not in message.fields:
                continue
            requests.append(
                _PatchRequest(
                    message_index=message.index,
                    message_type=message.message_type,
                    occurrence_index=message.occurrence_index,
                    field_name=field_name,
                    value=corrected_average_speed,
                    raw_value=False,
                    category="summary",
                )
            )
    return tuple(requests)


def _summary_correction(
    activity: ActivityData,
    fields: Mapping[str | int, object],
    corrections: tuple[float, ...],
) -> float:
    start_time = fields.get("start_time")
    if not isinstance(start_time, datetime):
        raise FitWriteError("cannot align distance summary without a start timestamp")
    end_time = _summary_end_time(fields, start_time)
    end_indices = [
        record.index
        for record in activity.records
        if record.timestamp is not None and record.timestamp <= end_time
    ]
    if not end_indices:
        raise FitWriteError("distance summary ends before the first activity record")
    before_indices = [
        record.index
        for record in activity.records
        if record.timestamp is not None and record.timestamp < start_time
    ]
    correction_before = corrections[before_indices[-1]] if before_indices else 0.0
    return corrections[end_indices[-1]] - correction_before


def _summary_end_time(
    fields: Mapping[str | int, object],
    start_time: datetime,
) -> datetime:
    declared_end = fields.get("timestamp")
    elapsed_seconds = _number(fields.get("total_elapsed_time"))
    elapsed_end = (
        start_time + timedelta(seconds=elapsed_seconds)
        if elapsed_seconds is not None and elapsed_seconds >= 0.0
        else None
    )

    if isinstance(declared_end, datetime):
        if elapsed_end is None:
            if declared_end < start_time:
                raise FitWriteError("distance summary ends before its start timestamp")
            return declared_end
        alignment_error = abs((declared_end - elapsed_end).total_seconds())
        if alignment_error <= _SUMMARY_END_ALIGNMENT_TOLERANCE_SECONDS:
            return declared_end

    if elapsed_end is not None:
        return elapsed_end
    raise FitWriteError(
        "cannot align distance summary without a consistent end timestamp "
        "or non-negative total_elapsed_time"
    )


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _patch_fit_bytes(
    raw_bytes: bytes,
    requests: tuple[_PatchRequest, ...],
) -> tuple[bytes, dict[str, int]]:
    patches_by_message: dict[int, list[_PatchRequest]] = {}
    for request in requests:
        patches_by_message.setdefault(request.message_index, []).append(request)
    patched = bytearray(raw_bytes)
    applied_requests: set[tuple[int, str]] = set()
    category_counts: dict[str, int] = {}
    data_message_index = 0
    header_count = 0
    crc_offsets: list[int] = []

    try:
        with fitdecode.FitReader(
            raw_bytes,
            check_crc=fitdecode.CrcCheck.RAISE,
            error_handling=fitdecode.ErrorHandling.RAISE,
            keep_raw_chunks=True,
        ) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitHeader):
                    header_count += 1
                elif isinstance(frame, fitdecode.FitCRC):
                    crc_offsets.append(int(frame.chunk.offset))
                elif isinstance(frame, fitdecode.FitDataMessage):
                    for request in patches_by_message.get(data_message_index, ()):
                        _apply_request(patched, frame, request, applied_requests, category_counts)
                    data_message_index += 1
    except Exception as error:
        raise FitWriteError(f"cannot patch original FIT structure: {error}") from error

    if header_count != 1 or crc_offsets != [len(raw_bytes) - 2]:
        raise FitWriteError("writer supports exactly one FIT container with one footer CRC")
    expected_requests = {(request.message_index, request.field_name) for request in requests}
    missing_requests = expected_requests - applied_requests
    if missing_requests:
        missing = ", ".join(f"message {index}.{name}" for index, name in sorted(missing_requests))
        raise FitWriteError(f"required FIT fields are absent: {missing}")
    patched[-2:] = compute_crc(patched[:-2]).to_bytes(2, byteorder="little")
    return bytes(patched), category_counts


def _apply_request(
    patched: bytearray,
    frame: object,
    request: _PatchRequest,
    applied_requests: set[tuple[int, str]],
    category_counts: dict[str, int],
) -> None:
    if str(frame.name) != request.message_type:  # type: ignore[attr-defined]
        raise FitWriteError(
            f"message {request.message_index} type changed from "
            f"{request.message_type} to {frame.name}"  # type: ignore[attr-defined]
        )
    if int(frame.chunk.index) < 0:  # type: ignore[attr-defined]
        raise FitWriteError("FIT frame has no stable raw chunk")
    field_data = next(
        (
            field
            for field in frame.fields  # type: ignore[attr-defined]
            if field.field_def is not None
            and not field.field_def.is_dev
            and field.name == request.field_name
        ),
        None,
    )
    if field_data is None:
        return
    field_definition = field_data.field_def
    if field_definition.size != field_definition.base_type.size:
        raise FitWriteError(
            f"cannot patch non-scalar field {request.message_type}.{request.field_name}"
        )
    payload_offset = int(frame.chunk.offset) + 1  # type: ignore[attr-defined]
    for definition in frame.def_mesg.all_field_defs:  # type: ignore[attr-defined]
        if definition is field_definition:
            break
        payload_offset += int(definition.size)
    encoded = _encode_field(frame, field_data, request)
    original = bytes(patched[payload_offset : payload_offset + len(encoded)])
    applied_requests.add((request.message_index, request.field_name))
    if original == encoded:
        return
    patched[payload_offset : payload_offset + len(encoded)] = encoded
    category_counts[request.category] = category_counts.get(request.category, 0) + 1


def _encode_field(frame: object, field_data: object, request: _PatchRequest) -> bytes:
    field_definition = field_data.field_def  # type: ignore[attr-defined]
    value: int | float = request.value
    if not request.raw_value:
        field = field_data.field  # type: ignore[attr-defined]
        if field is None:
            raise FitWriteError(
                f"cannot encode unknown field {request.message_type}.{request.field_name}"
            )
        offset = getattr(field, "offset", None)
        scale = getattr(field, "scale", None)
        if offset:
            value += offset
        if scale:
            value *= scale
    base_type = field_definition.base_type
    if not base_type.name.startswith("float"):
        value = round(value)
    try:
        return struct.pack(f"{frame.def_mesg.endian}{base_type.fmt}", value)  # type: ignore[attr-defined]
    except (OverflowError, struct.error) as error:
        raise FitWriteError(
            f"value for {request.message_type}.{request.field_name} is not encodable"
        ) from error
