"""Strict FIT decoding and conservative normalized consistency validation."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.models.activity import ActivityData, FitPreservationData
from warpbuster.models.fit import (
    FitValidationReport,
    ValidationIssue,
    ValidationIssueCode,
    ValidationSeverity,
)

_DISTANCE_REGRESSION_TOLERANCE_M = 0.01


def validate_fit(path: str | Path) -> FitValidationReport:
    """Decode a FIT with CRC checking and validate normalized core invariants."""
    source_path = Path(path)
    try:
        activity = read_fit(source_path)
    except (FitReadError, OSError) as error:
        return FitValidationReport(
            path=source_path,
            valid=False,
            decode_valid=False,
            crc_valid=False,
            record_count=0,
            message_count=0,
            issues=(
                ValidationIssue(
                    code=ValidationIssueCode.DECODE_FAILED,
                    severity=ValidationSeverity.ERROR,
                    message=str(error),
                ),
            ),
        )
    return validate_activity(activity, path=source_path)


def validate_activity(
    activity: ActivityData,
    *,
    path: Path | None = None,
) -> FitValidationReport:
    """Validate an already decoded FIT activity without changing it."""
    if not isinstance(activity.preservation, FitPreservationData):
        raise TypeError("FIT validation requires FIT preservation data")
    issues: list[ValidationIssue] = [
        ValidationIssue(
            code=ValidationIssueCode.OPAQUE_FIELD_COMPATIBILITY,
            severity=ValidationSeverity.WARNING,
            message=message,
        )
        for message in activity.preservation.compatibility_warnings
    ]
    if not activity.records:
        issues.append(
            ValidationIssue(
                code=ValidationIssueCode.EMPTY_ACTIVITY,
                severity=ValidationSeverity.ERROR,
                message="FIT activity contains no record messages",
            )
        )

    for record in activity.records:
        if record.latitude is not None and not -90.0 <= record.latitude <= 90.0:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.LATITUDE_OUT_OF_RANGE,
                    severity=ValidationSeverity.ERROR,
                    message=f"latitude {record.latitude} is outside [-90, 90]",
                    record_index=record.index,
                )
            )
        if record.longitude is not None and not -180.0 <= record.longitude <= 180.0:
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.LONGITUDE_OUT_OF_RANGE,
                    severity=ValidationSeverity.ERROR,
                    message=f"longitude {record.longitude} is outside [-180, 180]",
                    record_index=record.index,
                )
            )

    for previous, current in pairwise(activity.records):
        if previous.continuity_id != current.continuity_id:
            continue
        if (
            previous.timestamp is not None
            and current.timestamp is not None
            and current.timestamp < previous.timestamp
        ):
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.TIMESTAMP_REGRESSION,
                    severity=ValidationSeverity.ERROR,
                    message="record timestamp decreases within one continuity segment",
                    record_index=current.index,
                )
            )
        if (
            previous.distance is not None
            and current.distance is not None
            and current.distance < previous.distance - _DISTANCE_REGRESSION_TOLERANCE_M
        ):
            issues.append(
                ValidationIssue(
                    code=ValidationIssueCode.DISTANCE_REGRESSION,
                    severity=ValidationSeverity.ERROR,
                    message="record distance decreases within one continuity segment",
                    record_index=current.index,
                )
            )

    return FitValidationReport(
        path=path or activity.preservation.source_path,
        valid=not any(issue.severity is ValidationSeverity.ERROR for issue in issues),
        decode_valid=True,
        crc_valid=activity.preservation.crc_valid,
        record_count=len(activity.records),
        message_count=len(activity.preservation.messages),
        issues=tuple(issues),
    )
