"""FIT reader and normalization tests."""

from pathlib import Path

import pytest

from tests.fit_factory import write_synthetic_activity
from warpbuster.fit.reader import FitReadError, read_fit


def test_reader_normalizes_activity_and_preserves_source(tmp_path: Path) -> None:
    """A valid FIT is normalized without losing its source representation."""
    fit_path = tmp_path / "activity.fit"
    raw_bytes = write_synthetic_activity(fit_path)

    activity = read_fit(fit_path)

    assert len(activity.records) == 4
    assert activity.records[0].latitude == pytest.approx(55.0, abs=1e-7)
    assert activity.records[0].longitude == pytest.approx(37.0, abs=1e-7)
    assert activity.records[0].altitude == 100.0
    assert activity.records[0].speed == 3.5
    assert activity.records[3].latitude is None
    assert activity.records[3].longitude is None
    assert activity.records[3].timestamp is not None
    assert activity.records[0].source.message_index < activity.records[1].source.message_index
    assert activity.manufacturer == "garmin"
    assert activity.product == 123
    assert activity.sport == "running"
    assert activity.sub_sport is None
    assert activity.duration_seconds == 3.0
    assert activity.recorded_distance_m == 30.0
    assert activity.coordinate_bounds is not None
    assert activity.available_fields >= {
        "timestamp",
        "position",
        "altitude",
        "distance",
        "speed",
        "heart_rate",
        "cadence",
        "power",
        "temperature",
    }
    assert len(activity.laps) == 1
    assert len(activity.sessions) == 1
    assert len(activity.events) == 1
    assert activity.message_counts["record"] == 4
    assert activity.developer_fields[0].name == "synthetic_metric"
    assert activity.developer_fields[0].units == "points"
    assert activity.developer_fields[0].occurrences == 4
    assert activity.preservation.raw_bytes == raw_bytes
    assert activity.preservation.crc_valid is True
    assert activity.preservation.profile_version == "21.214"
    assert activity.preservation.definitions
    assert activity.preservation.messages[0].raw_chunk
    assert activity.preservation.messages[0].byte_offset >= 0
    assert activity.unknown_fields == ()


def test_reader_rejects_invalid_fit(tmp_path: Path) -> None:
    """Invalid input produces an explicit domain error."""
    invalid_path = tmp_path / "invalid.fit"
    invalid_path.write_bytes(b"not a fit file")

    with pytest.raises(FitReadError, match="cannot decode FIT file"):
        read_fit(invalid_path)
