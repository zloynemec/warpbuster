"""FIT write, validation, and semantic diff domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from warpbuster.models.reconstruction import RepairPlan, RepairSelection


class ValidationSeverity(StrEnum):
    """Severity of one FIT validation finding."""

    ERROR = "error"
    WARNING = "warning"


class ValidationIssueCode(StrEnum):
    """Stable machine-readable FIT validation issue codes."""

    DECODE_FAILED = "decode_failed"
    OPAQUE_FIELD_COMPATIBILITY = "opaque_field_compatibility"
    EMPTY_ACTIVITY = "empty_activity"
    TIMESTAMP_REGRESSION = "timestamp_regression"
    LATITUDE_OUT_OF_RANGE = "latitude_out_of_range"
    LONGITUDE_OUT_OF_RANGE = "longitude_out_of_range"
    DISTANCE_REGRESSION = "distance_regression"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One validation finding attached to an optional record."""

    code: ValidationIssueCode
    severity: ValidationSeverity
    message: str
    record_index: int | None = None


@dataclass(frozen=True, slots=True)
class FitValidationReport:
    """Structural and normalized consistency result for one FIT file."""

    path: Path
    valid: bool
    decode_valid: bool
    crc_valid: bool
    record_count: int
    message_count: int
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class FieldChange:
    """One decoded FIT field difference between aligned messages."""

    message_type: str
    occurrence_index: int
    field_name: str
    original_value: object
    fixed_value: object
    expected: bool


@dataclass(frozen=True, slots=True)
class PreservationMetric:
    """Unchanged occurrence count and percentage for one field category."""

    compared_count: int
    unchanged_count: int
    percentage: float


@dataclass(frozen=True, slots=True)
class FitDiffReport:
    """Semantic preservation diff for two FIT files."""

    original_path: Path
    fixed_path: Path
    structure_compatible: bool
    definitions_unchanged: bool
    original_message_count: int
    fixed_message_count: int
    changed_record_count: int
    changed_field_count: int
    expected_changed_field_count: int
    unexpected_changed_field_count: int
    retained_changes: tuple[FieldChange, ...]
    truncated_change_count: int
    all_fields: PreservationMetric
    timestamps: PreservationMetric
    sensors: PreservationMetric
    developer_fields: PreservationMetric
    unknown_fields: PreservationMetric
    added_coordinate_field_count: int = 0
    definition_count_delta: int = 0


@dataclass(frozen=True, slots=True)
class FitWriteResult:
    """Successful atomic application of selected reconstruction candidates."""

    source_path: Path
    output_path: Path
    bytes_written: int
    coordinate_field_change_count: int
    distance_field_change_count: int
    summary_field_change_count: int
    selection: RepairSelection
    validation: FitValidationReport
    diff: FitDiffReport
    plan: RepairPlan | None = None
    post_write_verified: bool = False
