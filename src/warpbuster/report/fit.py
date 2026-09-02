"""Console and JSON reports for FIT write, validation, and semantic diff."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path

from warpbuster.models.fit import (
    FieldChange,
    FitDiffReport,
    FitValidationReport,
    FitWriteResult,
    PreservationMetric,
)
from warpbuster.models.reconstruction import (
    MissingCourseRun,
    RepairIntervalDecision,
    RepairSelection,
)

_CONSOLE_CHANGE_LIMIT = 20


def validation_report(report: FitValidationReport) -> dict[str, object]:
    """Build a stable machine-readable validation report."""
    return {
        "schema_version": "0.1",
        "scope": "fit_validation",
        "path": str(report.path),
        "valid": report.valid,
        "decode_valid": report.decode_valid,
        "crc_valid": report.crc_valid,
        "record_count": report.record_count,
        "message_count": report.message_count,
        "issues": [
            {
                "code": issue.code.value,
                "severity": issue.severity.value,
                "message": issue.message,
                "record_index": issue.record_index,
            }
            for issue in report.issues
        ],
    }


def validation_json(report: FitValidationReport) -> str:
    """Render deterministic validation JSON."""
    return json.dumps(validation_report(report), ensure_ascii=False, indent=2, sort_keys=True)


def validation_console(report: FitValidationReport) -> str:
    """Render concise FIT validation output."""
    lines = [
        "WarpBuster FIT validate",
        f"File: {report.path}",
        f"Status: {'VALID' if report.valid else 'INVALID'}",
        f"Decode valid: {'yes' if report.decode_valid else 'no'}",
        f"CRC valid: {'yes' if report.crc_valid else 'no'}",
        f"Records: {report.record_count}; messages: {report.message_count}",
    ]
    lines.extend(
        f"  - {issue.severity.value.upper()} {issue.code.value}: {issue.message}"
        + (f" (record {issue.record_index})" if issue.record_index is not None else "")
        for issue in report.issues
    )
    return "\n".join(lines)


def diff_report(report: FitDiffReport) -> dict[str, object]:
    """Build a stable machine-readable FIT diff report."""
    return {
        "schema_version": "0.1",
        "scope": "fit_diff",
        "original_path": str(report.original_path),
        "fixed_path": str(report.fixed_path),
        "structure_compatible": report.structure_compatible,
        "definitions_unchanged": report.definitions_unchanged,
        "original_message_count": report.original_message_count,
        "fixed_message_count": report.fixed_message_count,
        "changed_record_count": report.changed_record_count,
        "changed_field_count": report.changed_field_count,
        "expected_changed_field_count": report.expected_changed_field_count,
        "unexpected_changed_field_count": report.unexpected_changed_field_count,
        "preservation": {
            "all_fields": _metric_report(report.all_fields),
            "timestamps": _metric_report(report.timestamps),
            "sensors": _metric_report(report.sensors),
            "developer_fields": _metric_report(report.developer_fields),
            "unknown_fields": _metric_report(report.unknown_fields),
        },
        "changes": [_change_report(change) for change in report.retained_changes],
        "truncated_change_count": report.truncated_change_count,
    }


def diff_json(report: FitDiffReport) -> str:
    """Render deterministic FIT diff JSON."""
    return json.dumps(diff_report(report), ensure_ascii=False, indent=2, sort_keys=True)


def diff_console(report: FitDiffReport, *, verbosity: int = 0) -> str:
    """Render FIT diff totals, preservation, and optionally bounded details."""
    lines = [
        "WarpBuster FIT diff",
        f"Original: {report.original_path}",
        f"Fixed: {report.fixed_path}",
        f"Structure compatible: {'yes' if report.structure_compatible else 'no'}",
        f"Definitions unchanged: {'yes' if report.definitions_unchanged else 'no'}",
        (
            f"Records changed: {report.changed_record_count}; fields changed: "
            f"{report.changed_field_count} (expected={report.expected_changed_field_count}, "
            f"unexpected={report.unexpected_changed_field_count})"
        ),
        (
            "Preservation: "
            f"timestamps={report.timestamps.percentage:.2f}%, "
            f"sensors={report.sensors.percentage:.2f}%, "
            f"developer={report.developer_fields.percentage:.2f}%, "
            f"unknown={report.unknown_fields.percentage:.2f}%"
        ),
    ]
    if verbosity:
        lines.extend(
            _change_console(change) for change in report.retained_changes[:_CONSOLE_CHANGE_LIMIT]
        )
        omitted = report.changed_field_count - min(
            len(report.retained_changes),
            _CONSOLE_CHANGE_LIMIT,
        )
        if omitted:
            lines.append(f"  ... {omitted} additional field changes omitted")
    return "\n".join(lines)


def write_result_report(result: FitWriteResult) -> dict[str, object]:
    """Build the machine-readable result of an atomic FIT write."""
    return {
        "schema_version": "0.1",
        "scope": "fit_repair_write",
        "source_path": str(result.source_path),
        "output_path": str(result.output_path),
        "output_written": True,
        "bytes_written": result.bytes_written,
        "coordinate_field_change_count": result.coordinate_field_change_count,
        "distance_field_change_count": result.distance_field_change_count,
        "summary_field_change_count": result.summary_field_change_count,
        "selection": _write_selection_report(result.selection),
        "validation": validation_report(result.validation),
        "diff": diff_report(result.diff),
    }


def write_result_json(result: FitWriteResult) -> str:
    """Render deterministic FIT write JSON."""
    return json.dumps(write_result_report(result), ensure_ascii=False, indent=2, sort_keys=True)


def write_result_console(result: FitWriteResult) -> str:
    """Render a concise successful FIT write summary."""
    lines = [
        "WarpBuster FIT repair",
        f"Source: {result.source_path}",
        f"Output: {result.output_path}",
        "Status: WRITTEN",
        (
            f"Application: {_write_application_status(result.selection)} "
            f"(minimum={result.selection.minimum_confidence.value.upper()}, "
            f"applied={result.selection.applied_interval_count}, "
            f"skipped={result.selection.skipped_interval_count})"
        ),
        f"Bytes: {result.bytes_written}",
        (
            "Changed fields: "
            f"coordinates={result.coordinate_field_change_count}, "
            f"distance={result.distance_field_change_count}, "
            f"summaries={result.summary_field_change_count}"
        ),
        (
            f"Validation: {'VALID' if result.validation.valid else 'INVALID'}; "
            f"unexpected changes={result.diff.unexpected_changed_field_count}"
        ),
        (
            "Preservation: "
            f"timestamps={result.diff.timestamps.percentage:.2f}%, "
            f"sensors={result.diff.sensors.percentage:.2f}%, "
            f"developer={result.diff.developer_fields.percentage:.2f}%, "
            f"unknown={result.diff.unknown_fields.percentage:.2f}%"
        ),
    ]
    lines.extend(_write_decision_console(decision) for decision in result.selection.decisions)
    return "\n".join(lines)


def _write_selection_report(selection: RepairSelection) -> dict[str, object]:
    return {
        "minimum_confidence": selection.minimum_confidence.value,
        "application_status": _write_application_status(selection).casefold(),
        "applied_interval_count": selection.applied_interval_count,
        "skipped_interval_count": selection.skipped_interval_count,
        "intervals": [
            {
                "start_record_index": decision.interval.start_record_index,
                "end_record_index": decision.interval.end_record_index,
                "confidence": decision.confidence.value,
                "action": decision.action.value,
                "candidate_available": decision.candidate_available,
                "coordinate_update_count": decision.coordinate_update_count,
                "target_kind": (
                    "missing_course_completion"
                    if isinstance(decision.interval, MissingCourseRun)
                    else "corrupted_interval"
                ),
                "selection_reasons": [reason.value for reason in decision.selection_reasons],
                "reconstruction_reasons": [
                    reason.value for reason in decision.reconstruction_reasons
                ],
            }
            for decision in selection.decisions
        ],
    }


def _write_application_status(selection: RepairSelection) -> str:
    return "PARTIAL" if selection.is_partial else "FULL"


def _write_decision_console(decision: RepairIntervalDecision) -> str:
    selection_reasons = ",".join(reason.value for reason in decision.selection_reasons)
    reconstruction_reasons = ",".join(reason.value for reason in decision.reconstruction_reasons)
    return (
        f"  - records {decision.interval.start_record_index}.."
        f"{decision.interval.end_record_index}: {decision.action.value.upper()}, "
        f"confidence={decision.confidence.value.upper()}, "
        f"candidate={'yes' if decision.candidate_available else 'no'}, "
        f"updates={decision.coordinate_update_count}, "
        f"selection_reasons={selection_reasons}, "
        f"reconstruction_reasons={reconstruction_reasons}"
    )


def _metric_report(metric: PreservationMetric) -> dict[str, object]:
    return {
        "compared_count": metric.compared_count,
        "unchanged_count": metric.unchanged_count,
        "percentage": metric.percentage,
    }


def _change_report(change: FieldChange) -> dict[str, object]:
    return {
        "message_type": change.message_type,
        "occurrence_index": change.occurrence_index,
        "field_name": change.field_name,
        "original_value": _json_value(change.original_value),
        "fixed_value": _json_value(change.fixed_value),
        "expected": change.expected,
    }


def _change_console(change: FieldChange) -> str:
    expectation = "expected" if change.expected else "UNEXPECTED"
    return (
        f"  - {change.message_type}[{change.occurrence_index}].{change.field_name}: "
        f"{change.original_value!r} -> {change.fixed_value!r} ({expectation})"
    )


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return repr(value)
