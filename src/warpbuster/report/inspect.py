"""Console and JSON renderers for FIT inspection."""

from __future__ import annotations

import json

from warpbuster.models.activity import ActivityData

_FIELD_NAMES = (
    "timestamp",
    "position",
    "altitude",
    "distance",
    "speed",
    "heart_rate",
    "cadence",
    "power",
    "temperature",
)


def inspect_report(activity: ActivityData) -> dict[str, object]:
    """Build the stable v0.1 machine-readable inspect report."""
    bounds = activity.coordinate_bounds
    return {
        "schema_version": "0.1",
        "source": {
            "path": str(activity.preservation.source_path),
            "size_bytes": len(activity.preservation.raw_bytes),
            "crc_valid": activity.preservation.crc_valid,
            "fit_profile_version": activity.preservation.profile_version,
        },
        "device": {
            "manufacturer": activity.manufacturer,
            "product": activity.product,
        },
        "start_time": (
            activity.start_time.isoformat() if activity.start_time is not None else None
        ),
        "duration_seconds": activity.duration_seconds,
        "record_count": len(activity.records),
        "recorded_distance_m": activity.recorded_distance_m,
        "coordinate_bounds": (
            {
                "min_latitude": bounds.min_latitude,
                "max_latitude": bounds.max_latitude,
                "min_longitude": bounds.min_longitude,
                "max_longitude": bounds.max_longitude,
            }
            if bounds is not None
            else None
        ),
        "fields": {name: name in activity.available_fields for name in _FIELD_NAMES},
        "message_types": dict(activity.message_counts),
        "laps": len(activity.laps),
        "sessions": len(activity.sessions),
        "events": len(activity.events),
        "developer_fields": [
            {
                "key": field.key,
                "developer_data_index": field.developer_data_index,
                "field_definition_number": field.field_definition_number,
                "name": field.name,
                "units": field.units,
                "native_message_number": field.native_message_number,
                "native_field_number": field.native_field_number,
                "occurrences": field.occurrences,
            }
            for field in activity.developer_fields
        ],
        "unknown_fields": [
            {
                "message_type": field.message_type,
                "field_number": field.field_number,
                "occurrences": field.occurrences,
            }
            for field in activity.unknown_fields
        ],
    }


def inspect_json(activity: ActivityData) -> str:
    """Render an inspect report as deterministic JSON."""
    return json.dumps(inspect_report(activity), ensure_ascii=False, indent=2, sort_keys=True)


def inspect_console(activity: ActivityData) -> str:
    """Render a compact human-readable inspect report."""
    report = inspect_report(activity)
    source = report["source"]
    device = report["device"]
    fields = report["fields"]
    if not isinstance(source, dict) or not isinstance(device, dict) or not isinstance(fields, dict):
        raise TypeError("invalid inspect report shape")

    lines = [
        "WarpBuster FIT inspect",
        f"File: {source['path']}",
        f"FIT profile: {source['fit_profile_version']} (CRC valid: yes)",
        f"Manufacturer: {_display(device['manufacturer'])}",
        f"Product: {_display(device['product'])}",
        f"Start: {_display(report['start_time'])}",
        f"Duration: {_number(report['duration_seconds'], 's')}",
        f"Records: {report['record_count']}",
        f"Recorded distance: {_number(report['recorded_distance_m'], 'm')}",
        f"Bounds: {_bounds(activity)}",
        "Fields: "
        + ", ".join(f"{name}={'yes' if fields[name] else 'no'}" for name in _FIELD_NAMES),
        (
            f"Messages: {', '.join(f'{name}={count}' for name, count in activity.message_counts.items())}"
        ),
        f"Laps: {len(activity.laps)}; sessions: {len(activity.sessions)}; events: {len(activity.events)}",
        f"Developer fields: {len(activity.developer_fields)}",
    ]
    lines.extend(
        f"  - {_display(field.name)} [{_display(field.units)}], occurrences={field.occurrences}"
        for field in activity.developer_fields
    )
    lines.append(f"Unknown fields: {len(activity.unknown_fields)}")
    return "\n".join(lines)


def _display(value: object) -> str:
    return "n/a" if value is None or value == "" else str(value)


def _number(value: object, unit: str) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "n/a"
    return f"{value:.2f} {unit}"


def _bounds(activity: ActivityData) -> str:
    bounds = activity.coordinate_bounds
    if bounds is None:
        return "n/a"
    return (
        f"lat {bounds.min_latitude:.6f}..{bounds.max_latitude:.6f}, "
        f"lon {bounds.min_longitude:.6f}..{bounds.max_longitude:.6f}"
    )
