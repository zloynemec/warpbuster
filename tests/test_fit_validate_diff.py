"""FIT validation and semantic preservation diff tests."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from tests.activity_factory import eastward_observations, make_activity
from tests.fit_factory import write_repairable_activity
from warpbuster.fit.diff import diff_fit
from warpbuster.fit.validate import validate_activity, validate_fit
from warpbuster.models.fit import ValidationIssueCode


def test_validate_accepts_crc_valid_consistent_fit(tmp_path: Path) -> None:
    """A decodable monotonic FIT passes structural and normalized checks."""
    path = tmp_path / "valid.fit"
    write_repairable_activity(path)

    report = validate_fit(path)

    assert report.valid is True
    assert report.decode_valid is True
    assert report.crc_valid is True
    assert report.record_count == 33
    assert report.issues == ()


def test_validate_reports_decode_or_crc_failure(tmp_path: Path) -> None:
    """Malformed input returns a report rather than escaping a parser exception."""
    path = tmp_path / "broken.fit"
    path.write_bytes(b"not a FIT")

    report = validate_fit(path)

    assert report.valid is False
    assert report.decode_valid is False
    assert report.crc_valid is False
    assert report.issues[0].code is ValidationIssueCode.DECODE_FAILED

    crc_path = tmp_path / "bad-crc.fit"
    corrupted = bytearray(write_repairable_activity(crc_path))
    corrupted[-1] ^= 0xFF
    crc_path.write_bytes(corrupted)
    crc_report = validate_fit(crc_path)
    assert crc_report.valid is False
    assert crc_report.crc_valid is False


def test_validate_reports_normalized_consistency_failures() -> None:
    """Timestamp, coordinate, and distance regressions are independently visible."""
    activity = make_activity(eastward_observations([0.0, 1.0], [0.0, 3.0]))
    first, second = activity.records
    assert first.timestamp is not None
    broken = replace(
        activity,
        records=(
            replace(first, distance=10.0),
            replace(
                second,
                timestamp=first.timestamp - timedelta(seconds=1),
                latitude=100.0,
                distance=5.0,
            ),
        ),
    )

    report = validate_activity(broken)

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {
        ValidationIssueCode.LATITUDE_OUT_OF_RANGE,
        ValidationIssueCode.TIMESTAMP_REGRESSION,
        ValidationIssueCode.DISTANCE_REGRESSION,
    }


def test_diff_counts_unexpected_sensor_changes_and_preservation(tmp_path: Path) -> None:
    """A non-repair sensor mutation is visible and lowers sensor preservation."""
    original_path = tmp_path / "original.fit"
    changed_path = tmp_path / "changed.fit"
    write_repairable_activity(original_path)
    write_repairable_activity(changed_path, heart_rate_offset=1)

    report = diff_fit(original_path, changed_path)

    assert report.structure_compatible is True
    assert report.definitions_unchanged is True
    assert report.changed_record_count == 33
    assert report.changed_field_count == 33
    assert report.expected_changed_field_count == 0
    assert report.unexpected_changed_field_count == 33
    assert report.timestamps.percentage == 100.0
    assert report.sensors.percentage < 100.0
    assert all(change.field_name == "heart_rate" for change in report.retained_changes)
