"""Semantic FIT preservation diff with explicit expected-change accounting."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from warpbuster.fit.reader import read_fit
from warpbuster.models.activity import FitPreservationData, SourceFieldName, SourceMessage
from warpbuster.models.fit import FieldChange, FitDiffReport, PreservationMetric

_MAX_RETAINED_CHANGES = 200
_EXPECTED_REPAIR_FIELDS = {
    ("record", "position_lat"),
    ("record", "position_long"),
    ("record", "distance"),
    ("lap", "total_distance"),
    ("lap", "avg_speed"),
    ("lap", "enhanced_avg_speed"),
    ("session", "total_distance"),
    ("session", "avg_speed"),
    ("session", "enhanced_avg_speed"),
}
_SENSOR_FIELDS = {
    "altitude",
    "enhanced_altitude",
    "speed",
    "enhanced_speed",
    "heart_rate",
    "cadence",
    "power",
    "temperature",
}
_MISSING = "<missing>"


def diff_fit(
    original_path: str | Path,
    fixed_path: str | Path,
) -> FitDiffReport:
    """Decode and compare two FIT files at message-field occurrence granularity."""
    original = read_fit(original_path)
    fixed = read_fit(fixed_path)
    if not isinstance(original.preservation, FitPreservationData) or not isinstance(
        fixed.preservation, FitPreservationData
    ):
        raise TypeError("FIT diff requires FIT preservation data")
    return diff_preservation(original.preservation, fixed.preservation)


def diff_preservation(
    original: FitPreservationData,
    fixed: FitPreservationData,
) -> FitDiffReport:
    """Compare decoded preservation snapshots without assuming writer provenance."""
    original_messages = original.messages
    fixed_messages = fixed.messages
    structure_compatible = len(original_messages) == len(fixed_messages) and all(
        _message_identity(left) == _message_identity(right)
        for left, right in zip(original_messages, fixed_messages, strict=True)
    )
    definitions_unchanged = _definition_chunks(original) == _definition_chunks(fixed)

    retained: list[FieldChange] = []
    changed_count = 0
    expected_count = 0
    unexpected_count = 0
    changed_records: set[int] = set()
    metric_counts = {
        "all": [0, 0],
        "timestamps": [0, 0],
        "sensors": [0, 0],
        "developer": [0, 0],
        "unknown": [0, 0],
    }

    for left, right in zip(original_messages, fixed_messages, strict=False):
        if _message_identity(left) != _message_identity(right):
            continue
        left_fields = _flatten_fields(left.fields)
        right_fields = _flatten_fields(right.fields)
        for field_key in sorted(set(left_fields) | set(right_fields), key=_field_sort_key):
            left_value = left_fields.get(field_key, _MISSING)
            right_value = right_fields.get(field_key, _MISSING)
            unchanged = left_value == right_value
            categories = _metric_categories(field_key)
            for category in categories:
                metric_counts[category][0] += 1
                if unchanged:
                    metric_counts[category][1] += 1
            if unchanged:
                continue
            changed_count += 1
            display_name = _display_field_name(field_key)
            expected = (left.message_type, display_name) in _EXPECTED_REPAIR_FIELDS
            if expected:
                expected_count += 1
            else:
                unexpected_count += 1
            if left.message_type == "record":
                changed_records.add(left.occurrence_index)
            if len(retained) < _MAX_RETAINED_CHANGES:
                retained.append(
                    FieldChange(
                        message_type=left.message_type,
                        occurrence_index=left.occurrence_index,
                        field_name=display_name,
                        original_value=left_value,
                        fixed_value=right_value,
                        expected=expected,
                    )
                )

    return FitDiffReport(
        original_path=original.source_path,
        fixed_path=fixed.source_path,
        structure_compatible=structure_compatible,
        definitions_unchanged=definitions_unchanged,
        original_message_count=len(original_messages),
        fixed_message_count=len(fixed_messages),
        changed_record_count=len(changed_records),
        changed_field_count=changed_count,
        expected_changed_field_count=expected_count,
        unexpected_changed_field_count=unexpected_count,
        retained_changes=tuple(retained),
        truncated_change_count=changed_count - len(retained),
        all_fields=_metric(metric_counts["all"]),
        timestamps=_metric(metric_counts["timestamps"]),
        sensors=_metric(metric_counts["sensors"]),
        developer_fields=_metric(metric_counts["developer"]),
        unknown_fields=_metric(metric_counts["unknown"]),
    )


def _message_identity(message: SourceMessage) -> tuple[int, str, int]:
    return (
        message.global_message_number,
        message.message_type,
        message.occurrence_index,
    )


def _definition_chunks(preservation: FitPreservationData) -> tuple[object, ...]:
    return tuple(definition.get("raw_chunk") for definition in preservation.definitions)


def _flatten_fields(
    fields: Mapping[SourceFieldName, object],
) -> dict[tuple[str, str | int], object]:
    flattened: dict[tuple[str, str | int], object] = {}
    for name, value in fields.items():
        if name == "developer_fields" and isinstance(value, Mapping):
            for developer_name, developer_value in value.items():
                flattened[("developer", str(developer_name))] = developer_value
        elif isinstance(name, int):
            flattened[("unknown", name)] = value
        else:
            flattened[("native", str(name))] = value
    return flattened


def _field_sort_key(field_key: tuple[str, str | int]) -> tuple[str, str]:
    return field_key[0], str(field_key[1])


def _display_field_name(field_key: tuple[str, str | int]) -> str:
    category, name = field_key
    if category == "developer":
        return f"developer_fields.{name}"
    if category == "unknown":
        return f"unknown_{name}"
    return str(name)


def _metric_categories(field_key: tuple[str, str | int]) -> tuple[str, ...]:
    category, name = field_key
    categories = ["all"]
    if category == "developer":
        categories.append("developer")
    elif category == "unknown":
        categories.append("unknown")
    elif name == "timestamp":
        categories.append("timestamps")
    elif name in _SENSOR_FIELDS:
        categories.append("sensors")
    return tuple(categories)


def _metric(counts: list[int]) -> PreservationMetric:
    compared, unchanged = counts
    percentage = 100.0 if compared == 0 else unchanged * 100.0 / compared
    return PreservationMetric(
        compared_count=compared,
        unchanged_count=unchanged,
        percentage=percentage,
    )
