"""GPX activity reader and normalization tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.gpx_factory import write_gpx_activity
from warpbuster.gpx.reader import GpxReadError, read_gpx
from warpbuster.models.activity import ActivityFileFormat, GpxPreservationData


def test_reader_normalizes_standard_track_and_preserves_source(tmp_path: Path) -> None:
    """Standard track points map to the same vendor-neutral activity model as FIT."""
    gpx_path = tmp_path / "activity.gpx"
    raw_bytes = write_gpx_activity(
        gpx_path,
        [
            [
                (55.0, 37.0, "2026-01-01T08:00:00Z", 100.0),
                (55.0001, 37.0001, "2026-01-01T08:00:05Z", 101.5),
                (55.0002, 37.0002, "2026-01-01T08:00:10Z", None),
            ]
        ],
        activity_type="Trail Running",
    )

    activity = read_gpx(gpx_path)

    assert len(activity.records) == 3
    assert activity.records[0].timestamp == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert activity.records[1].altitude == 101.5
    assert activity.records[2].altitude is None
    assert activity.sport == "running"
    assert activity.sub_sport == "trail"
    assert activity.start_time == datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
    assert activity.duration_seconds == 10.0
    assert activity.recorded_distance_m is None
    assert activity.available_fields == {"position", "timestamp", "altitude"}
    assert activity.coordinate_bounds is not None
    assert activity.coordinate_bounds.max_latitude == 55.0002
    assert isinstance(activity.preservation, GpxPreservationData)
    assert activity.preservation.source_format is ActivityFileFormat.GPX
    assert activity.preservation.raw_bytes == raw_bytes
    assert activity.preservation.version == "1.1"
    assert activity.preservation.creator == "WarpBuster tests"
    assert activity.preservation.track_count == 1
    assert activity.preservation.segment_count == 1


def test_reader_preserves_track_segment_continuity(tmp_path: Path) -> None:
    """Every GPX track segment receives a distinct continuity identifier."""
    gpx_path = tmp_path / "segments.gpx"
    write_gpx_activity(
        gpx_path,
        [
            [(55.0, 37.0, "2026-01-01T08:00:00Z", None)],
            [(56.0, 38.0, "2026-01-01T08:00:01Z", None)],
        ],
    )

    activity = read_gpx(gpx_path)

    assert [record.continuity_id for record in activity.records] == [0, 1]
    assert activity.message_counts == {"track": 1, "track_point": 2, "track_segment": 2}


def test_reader_keeps_missing_time_and_unknown_type_conservative(tmp_path: Path) -> None:
    """Absent time and a free-text type do not invent timestamps or sport semantics."""
    gpx_path = tmp_path / "unknown.gpx"
    write_gpx_activity(
        gpx_path,
        [[(55.0, 37.0, None, None), (55.0001, 37.0001, None, None)]],
        activity_type="Expedition",
    )

    activity = read_gpx(gpx_path)

    assert activity.sport is None
    assert activity.sub_sport is None
    assert activity.start_time is None
    assert activity.duration_seconds is None
    assert "timestamp" not in activity.available_fields


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (b"<not-gpx/>", "root element is not gpx"),
        (b"<gpx><trk>", "cannot decode GPX file"),
        (b'<!DOCTYPE gpx><gpx version="1.1"/>', "DTD and entity"),
        (b'<gpx version="1.1"><trk><trkseg/></trk></gpx>', "no track points"),
        (
            b'<gpx><trk><trkseg><trkpt lat="91" lon="37"/></trkseg></trk></gpx>',
            "lat out of range",
        ),
    ],
)
def test_reader_rejects_invalid_or_unsafe_gpx(
    tmp_path: Path,
    contents: bytes,
    message: str,
) -> None:
    """Malformed structure, unsafe XML, and impossible coordinates fail explicitly."""
    gpx_path = tmp_path / "invalid.gpx"
    gpx_path.write_bytes(contents)

    with pytest.raises(GpxReadError, match=message):
        read_gpx(gpx_path)
