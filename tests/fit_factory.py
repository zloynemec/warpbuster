"""Synthetic FIT fixture generation for public tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import cos, radians
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from garmin_fit_sdk import BASE_TYPE, BASE_TYPE_DEFINITIONS, Encoder, Profile

if TYPE_CHECKING:
    from garmin_fit_sdk.mesgs import DeveloperDataIdMesg, FieldDescriptionMesg


def write_synthetic_activity(path: Path) -> bytes:
    """Write a small valid activity with sensors, missing GNSS, and a developer field."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    developer_id = cast(
        "DeveloperDataIdMesg",
        {
            "developer_data_index": 0,
            "application_id": list(range(16)),
            "application_version": 1,
        },
    )
    field_description = cast(
        "FieldDescriptionMesg",
        {
            "developer_data_index": 0,
            "field_definition_number": 0,
            "fit_base_type_id": (
                BASE_TYPE["UINT16"] | BASE_TYPE_DEFINITIONS[BASE_TYPE["UINT16"]]["endian_flag"]
            ),
            "field_name": "synthetic_metric",
            "units": "points",
            "native_mesg_num": Profile["mesg_num"]["RECORD"],
        },
    )
    encoder = Encoder()
    encoder.add_developer_field(0, developer_id, field_description)
    encoder.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {
            "type": "activity",
            "manufacturer": "garmin",
            "product": 123,
            "time_created": start,
        },
    )
    encoder.on_mesg(Profile["mesg_num"]["DEVELOPER_DATA_ID"], dict(developer_id))
    encoder.on_mesg(Profile["mesg_num"]["FIELD_DESCRIPTION"], dict(field_description))
    encoder.on_mesg(
        Profile["mesg_num"]["EVENT"],
        {"timestamp": start, "event": "timer", "event_type": "start"},
    )

    for index in range(4):
        record: dict[str, Any] = {
            "timestamp": start + timedelta(seconds=index),
            "enhanced_altitude": 100.0 + index,
            "distance": 10.0 * index,
            "enhanced_speed": 3.5,
            "heart_rate": 140 + index,
            "cadence": 80,
            "power": 250,
            "temperature": 10,
            "developer_fields": {0: 100 + index},
        }
        if index < 3:
            record["position_lat"] = _semicircles(55.0 + index * 0.001)
            record["position_long"] = _semicircles(37.0 + index * 0.001)
        encoder.on_mesg(Profile["mesg_num"]["RECORD"], record)

    encoder.on_mesg(
        Profile["mesg_num"]["LAP"],
        {
            "timestamp": start + timedelta(seconds=3),
            "start_time": start,
            "total_elapsed_time": 3.0,
            "total_distance": 30.0,
        },
    )
    encoder.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": start + timedelta(seconds=3),
            "start_time": start,
            "total_elapsed_time": 3.0,
            "total_timer_time": 3.0,
            "total_distance": 30.0,
            "sport": "running",
        },
    )
    encoder.on_mesg(
        Profile["mesg_num"]["ACTIVITY"],
        {"timestamp": start + timedelta(seconds=3), "total_timer_time": 3.0},
    )
    raw_bytes = encoder.close()
    path.write_bytes(raw_bytes)
    return raw_bytes


def write_trajectory_activity(
    path: Path,
    observations: list[tuple[int, float | None, float | None]],
    *,
    retain_invalid_position_fields: bool = False,
    distances_m: list[float] | None = None,
    speeds_mps: list[float] | None = None,
    altitudes_m: list[float] | None = None,
) -> bytes:
    """Write a valid FIT containing a caller-supplied synthetic trajectory."""
    if distances_m is not None and len(distances_m) != len(observations):
        raise ValueError("distances_m and observations must have equal length")
    if speeds_mps is not None and len(speeds_mps) != len(observations):
        raise ValueError("speeds_mps and observations must have equal length")
    if altitudes_m is not None and len(altitudes_m) != len(observations):
        raise ValueError("altitudes_m and observations must have equal length")
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    encoder = Encoder()
    encoder.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {
            "type": "activity",
            "manufacturer": "garmin",
            "product": 123,
            "time_created": start,
        },
    )
    encoder.on_mesg(
        Profile["mesg_num"]["EVENT"],
        {"timestamp": start, "event": "timer", "event_type": "start"},
    )
    for index, (elapsed_seconds, latitude, longitude) in enumerate(observations):
        record: dict[str, Any] = {
            "timestamp": start + timedelta(seconds=elapsed_seconds),
        }
        if distances_m is not None:
            record["distance"] = distances_m[index]
        if speeds_mps is not None:
            record["enhanced_speed"] = speeds_mps[index]
        if altitudes_m is not None:
            record["enhanced_altitude"] = altitudes_m[index]
        if latitude is not None and longitude is not None:
            record["position_lat"] = _semicircles(latitude)
            record["position_long"] = _semicircles(longitude)
        elif retain_invalid_position_fields:
            record["position_lat"] = 0x7FFFFFFF
            record["position_long"] = 0x7FFFFFFF
        encoder.on_mesg(Profile["mesg_num"]["RECORD"], record)

    duration = float(observations[-1][0]) if observations else 0.0
    encoder.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": start + timedelta(seconds=duration),
            "start_time": start,
            "total_elapsed_time": duration,
            "total_timer_time": duration,
            **(
                {"total_distance": distances_m[-1]}
                if distances_m is not None and distances_m
                else {}
            ),
            "sport": "running",
        },
    )
    encoder.on_mesg(
        Profile["mesg_num"]["ACTIVITY"],
        {"timestamp": start + timedelta(seconds=duration), "total_timer_time": duration},
    )
    raw_bytes = encoder.close()
    path.write_bytes(raw_bytes)
    return raw_bytes


def write_repairable_activity(
    path: Path,
    *,
    heart_rate_offset: int = 0,
    summary_timestamp_at_start: bool = False,
) -> bytes:
    """Write a READY single-spike FIT with corrupted coordinate-derived distance."""
    start = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    developer_id = cast(
        "DeveloperDataIdMesg",
        {
            "developer_data_index": 0,
            "application_id": list(range(16)),
            "application_version": 1,
        },
    )
    field_description = cast(
        "FieldDescriptionMesg",
        {
            "developer_data_index": 0,
            "field_definition_number": 0,
            "fit_base_type_id": (
                BASE_TYPE["UINT16"] | BASE_TYPE_DEFINITIONS[BASE_TYPE["UINT16"]]["endian_flag"]
            ),
            "field_name": "preserved_metric",
            "units": "points",
            "native_mesg_num": Profile["mesg_num"]["RECORD"],
        },
    )
    encoder = Encoder()
    encoder.add_developer_field(0, developer_id, field_description)
    encoder.on_mesg(
        Profile["mesg_num"]["FILE_ID"],
        {
            "type": "activity",
            "manufacturer": "garmin",
            "product": 123,
            "time_created": start,
        },
    )
    encoder.on_mesg(Profile["mesg_num"]["DEVELOPER_DATA_ID"], dict(developer_id))
    encoder.on_mesg(Profile["mesg_num"]["FIELD_DESCRIPTION"], dict(field_description))
    encoder.on_mesg(
        Profile["mesg_num"]["EVENT"],
        {"timestamp": start, "event": "timer", "event_type": "start"},
    )

    metres_per_longitude_degree = 111_195.0 * cos(radians(55.0))
    recorded_distance = 0.0
    for index in range(33):
        if index:
            recorded_distance += 10_000.0 if index in {16, 17} else 6.0
        latitude = 56.0 if index == 16 else 55.0
        longitude = 37.0 if index == 16 else 37.0 + index * 6.0 / metres_per_longitude_degree
        encoder.on_mesg(
            Profile["mesg_num"]["RECORD"],
            {
                "timestamp": start + timedelta(seconds=index),
                "position_lat": _semicircles(latitude),
                "position_long": _semicircles(longitude),
                "distance": recorded_distance,
                "enhanced_speed": 6.0,
                "enhanced_altitude": 100.0 + index / 10.0,
                "heart_rate": 140 + index % 5 + heart_rate_offset,
                "cadence": 85,
                "power": 250 + index,
                "temperature": 10,
                "developer_fields": {0: 1_000 + index},
            },
        )

    duration = 32.0
    summary = {
        "timestamp": start if summary_timestamp_at_start else start + timedelta(seconds=duration),
        "start_time": start,
        "total_elapsed_time": duration,
        "total_timer_time": duration,
        "total_distance": recorded_distance,
        "enhanced_avg_speed": recorded_distance / duration,
    }
    encoder.on_mesg(Profile["mesg_num"]["LAP"], dict(summary))
    encoder.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {**summary, "sport": "running"},
    )
    encoder.on_mesg(
        Profile["mesg_num"]["ACTIVITY"],
        {"timestamp": start + timedelta(seconds=duration), "total_timer_time": duration},
    )
    raw_bytes = encoder.close()
    path.write_bytes(raw_bytes)
    return raw_bytes


def _semicircles(degrees: float) -> int:
    return round(degrees * (1 << 31) / 180.0)
