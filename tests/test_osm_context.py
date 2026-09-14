"""Eligible original suffix extraction independent of GPX and routing."""

from dataclasses import replace

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import OSMReconstructionConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import SourceMessage
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.osm_context import collect_start_context


def fixture(tmp_path):
    activity, _ = local_fixture(tmp_path, missing=((100, 109), (150, 179)))
    plan = build_repair_plan(activity, analyze_integrity(activity))
    return activity, plan


def test_suffix_stops_at_previous_gap(tmp_path):
    activity, plan = fixture(tmp_path)
    result = collect_start_context(activity, plan, 149, OSMReconstructionConfig())
    assert [p[0] for p in result.points] == list(range(110, 150))
    assert result.stop_reason == "ineligible_record"
    assert result.points[-1][2:] == (
        activity.records[149].latitude,
        activity.records[149].longitude,
    )


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("mask", "ineligible_record"),
        ("continuity", "continuity_boundary"),
        ("missing_time", "invalid_timestamp"),
        ("time", "time_step"),
        ("event", "event_boundary"),
    ],
)
def test_never_skips_a_boundary(tmp_path, fault, reason):
    activity, plan = fixture(tmp_path)
    records = list(activity.records)
    mask = list(plan.coordinate_mask)
    if fault == "mask":
        mask[140] = replace(mask[140], anchor_eligible=False)
    if fault == "continuity":
        records[140] = replace(records[140], continuity_id=999)
    if fault == "missing_time":
        records[140] = replace(records[140], timestamp=None)
    if fault == "time":
        records[140] = replace(records[140], timestamp=records[141].timestamp)
    if fault == "event":
        ev = SourceMessage(
            records[141].source.message_index,
            0,
            0,
            21,
            "event",
            0,
            {"timestamp": records[141].timestamp, "event": "timer", "event_type": "stop"},
            b"",
        )
        activity = replace(activity, events=(ev,))
    activity = replace(activity, records=tuple(records))
    plan = replace(plan, coordinate_mask=tuple(mask))
    result = collect_start_context(activity, plan, 149, OSMReconstructionConfig())
    assert result.points[0][0] == 141
    assert result.stop_reason == reason


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("context_maximum_points", 8, "point_limit"),
        ("context_maximum_age_s", 7.0, "age_limit"),
        ("context_maximum_length_m", 10.0, "length_limit"),
        ("context_maximum_step_s", 0.5, "time_step"),
    ],
)
def test_collection_bounds(tmp_path, field, value, reason):
    activity, plan = fixture(tmp_path)
    result = collect_start_context(
        activity, plan, 149, replace(OSMReconstructionConfig(), **{field: value})
    )
    assert result.stop_reason == reason
    assert result.points[-1][0] == 149


@pytest.mark.parametrize(
    "field",
    [
        "context_maximum_points",
        "context_maximum_age_s",
        "context_maximum_length_m",
        "context_maximum_step_s",
    ],
)
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_config_rejects_invalid_collection_bounds(field, value):
    with pytest.raises(ValueError):
        replace(OSMReconstructionConfig(), **{field: value})
