"""Synthetic FIT fixture generation for public tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
) -> bytes:
    """Write a valid FIT containing a caller-supplied synthetic trajectory."""
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
    for elapsed_seconds, latitude, longitude in observations:
        record: dict[str, Any] = {
            "timestamp": start + timedelta(seconds=elapsed_seconds),
        }
        if latitude is not None and longitude is not None:
            record["position_lat"] = _semicircles(latitude)
            record["position_long"] = _semicircles(longitude)
        encoder.on_mesg(Profile["mesg_num"]["RECORD"], record)

    duration = float(observations[-1][0]) if observations else 0.0
    encoder.on_mesg(
        Profile["mesg_num"]["SESSION"],
        {
            "timestamp": start + timedelta(seconds=duration),
            "start_time": start,
            "total_elapsed_time": duration,
            "total_timer_time": duration,
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


def _semicircles(degrees: float) -> int:
    return round(degrees * (1 << 31) / 180.0)
