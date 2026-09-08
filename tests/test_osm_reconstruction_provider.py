"""Candidate-only OSM reconstruction over the immutable Task 011 gap inventory."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import CourseReconstructionConfig, OSMReconstructionConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.reconstruction import (
    OSMDryRunStatus,
    OSMGapOutcome,
    OSMReconstructionReason,
)
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.osm import (
    OSMReconstructionError,
    OSMReconstructionProvider,
    RoutingAlternativesData,
    RoutingCandidateData,
)
from warpbuster.report.repair import repair_console, repair_report

GRAPH_ID = "sha256:" + "a" * 64


class FakeClient:
    def __init__(self, status: str = "READY", point_count: int = 2) -> None:
        self.status = status
        self.point_count = point_count
        self.calls: list[tuple[object, ...]] = []

    def alternatives(self, graph_id, start, end, alternates):
        self.calls.append((graph_id, start, end, alternates))
        candidates = (
            (
                RoutingCandidateData(
                    "route-1",
                    "primary",
                    tuple((44.0, 33.0 + index * 0.00001) for index in range(self.point_count)),
                    {"route_id": "route-1", "role": "primary", "summary": {"length_m": 50.0}},
                ),
            )
            if self.status == "READY"
            else ()
        )
        return RoutingAlternativesData(
            self.status,
            candidates,
            {
                "protocol_version": 1,
                "operation": "route_alternatives",
                "status": self.status,
                "graph": {"graph_id": graph_id, "snapshot_id": "sha256:snapshot"},
                "snapping": {"start": {}, "end": {}},
                "search": {"exhaustive": False},
            },
        )


def test_only_internal_unresolved_gap_is_queried(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path)
    plan = build_repair_plan(activity, analyze_integrity(activity))
    original_plan, original_activity = plan, activity
    client = FakeClient()

    result = OSMReconstructionProvider(client).discover(activity, plan, GRAPH_ID)

    assert result.status is OSMDryRunStatus.PARTIAL
    assert result.query_count == result.candidate_gap_count == result.candidate_count == 1
    assert [item.outcome for item in result.evaluations] == [
        OSMGapOutcome.NOT_QUERIED,
        OSMGapOutcome.CANDIDATES_AVAILABLE,
        OSMGapOutcome.NOT_QUERIED,
    ]
    assert result.evaluations[0].reasons == (OSMReconstructionReason.TWO_ANCHORS_REQUIRED,)
    assert result.evaluations[2].reasons == (OSMReconstructionReason.TWO_ANCHORS_REQUIRED,)
    internal = result.evaluations[1]
    assert internal.anchor_before.record_index == 149
    assert internal.anchor_after.record_index == 180
    assert client.calls == [
        (
            GRAPH_ID,
            (activity.records[149].latitude, activity.records[149].longitude),
            (activity.records[180].latitude, activity.records[180].longitude),
            2,
        )
    ]
    assert internal.candidates[0].as_dict()["route_id"] == "route-1"
    mutable = internal.candidates[0].as_dict()
    mutable["route_id"] = "changed"
    assert internal.candidates[0].as_dict()["route_id"] == "route-1"
    assert plan == original_plan and activity == original_activity


def test_existing_gpx_candidate_is_not_queried(tmp_path: Path) -> None:
    activity, course = local_fixture(tmp_path, missing=((150, 179),))
    plan = build_repair_plan(
        activity, analyze_integrity(activity), course, fill_missing_from_course=True
    )
    client = FakeClient()

    result = OSMReconstructionProvider(client).discover(activity, plan, GRAPH_ID)

    assert not client.calls
    assert result.evaluations[0].reasons == (
        OSMReconstructionReason.GPX_CANDIDATE_ALREADY_AVAILABLE,
    )


@pytest.mark.parametrize(
    "status,reason",
    [
        ("OUTSIDE_COVERAGE", OSMReconstructionReason.OUTSIDE_COVERAGE),
        ("NO_SNAP", OSMReconstructionReason.NO_SNAP),
        ("AMBIGUOUS_SNAP", OSMReconstructionReason.AMBIGUOUS_SNAP),
        ("NO_ROUTE", OSMReconstructionReason.NO_ROUTE),
    ],
)
def test_route_domain_outcome_is_local(
    tmp_path: Path, status: str, reason: OSMReconstructionReason
) -> None:
    activity, _ = local_fixture(tmp_path, missing=((150, 179),))
    plan = build_repair_plan(activity, analyze_integrity(activity))
    result = OSMReconstructionProvider(FakeClient(status)).discover(activity, plan, GRAPH_ID)
    assert result.status is OSMDryRunStatus.NO_CANDIDATES
    assert result.evaluations[0].outcome is OSMGapOutcome.UNRESOLVED
    assert result.evaluations[0].reasons == (reason,)


def test_query_and_result_limits_never_retain_partial_candidate(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=((150, 159), (300, 309)))
    plan = build_repair_plan(activity, analyze_integrity(activity))
    client = FakeClient(point_count=3)
    config = OSMReconstructionConfig(maximum_gap_queries=1, maximum_total_candidate_points=2)
    result = OSMReconstructionProvider(client, config).discover(activity, plan, GRAPH_ID)
    assert len(client.calls) == 1
    assert result.evaluations[0].reasons == (OSMReconstructionReason.RESULT_LIMIT_REACHED,)
    assert result.evaluations[0].candidates == ()
    assert result.evaluations[1].reasons == (OSMReconstructionReason.QUERY_LIMIT_REACHED,)


def test_invalid_anchor_and_protocol_are_controlled(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=((150, 179),))
    plan = build_repair_plan(activity, analyze_integrity(activity))
    mask = list(plan.coordinate_mask)
    mask[149] = replace(mask[149], anchor_eligible=False)
    client = FakeClient()
    result = OSMReconstructionProvider(client).discover(
        activity, replace(plan, coordinate_mask=tuple(mask)), GRAPH_ID
    )
    assert result.evaluations[0].reasons == (OSMReconstructionReason.ANCHOR_NOT_ELIGIBLE,)
    assert not client.calls

    with pytest.raises(OSMReconstructionError, match="unsupported routing status"):
        OSMReconstructionProvider(FakeClient("BROKEN")).discover(activity, plan, GRAPH_ID)


def test_report_is_additive_and_explicitly_non_applicable(tmp_path: Path) -> None:
    activity, _ = local_fixture(tmp_path, missing=((150, 179),))
    plan = build_repair_plan(activity, analyze_integrity(activity))
    result = OSMReconstructionProvider(FakeClient()).discover(activity, plan, GRAPH_ID)
    config = CourseReconstructionConfig()
    assert "osm_reconstruction" not in repair_report(plan, None, config)
    report = repair_report(plan, None, config, osm_result=result)
    osm = report["osm_reconstruction"]
    assert osm["application_allowed"] is False
    assert osm["candidate_count"] == 1
    candidate = osm["gap_evaluations"][0]["candidates"][0]
    assert candidate["candidate_only"] is True
    assert candidate["allocated_to_records"] is False
    assert "Candidate coordinate updates: 0" in repair_console(
        plan,
        None,
        config,
        osm_result=result,
    )
    assert "Application: DISABLED" in repair_console(
        plan,
        None,
        config,
        osm_result=result,
    )


def test_20k_records_keep_queries_bounded(tmp_path: Path) -> None:
    missing = tuple((200 + index * 450, 209 + index * 450) for index in range(40))
    activity, _ = local_fixture(tmp_path, count=20_000, missing=missing)
    plan = build_repair_plan(activity, analyze_integrity(activity))
    client = FakeClient()

    result = OSMReconstructionProvider(client).discover(activity, plan, GRAPH_ID)

    assert len(client.calls) == result.query_count == 32
    assert (
        sum(
            item.reasons == (OSMReconstructionReason.QUERY_LIMIT_REACHED,)
            for item in result.evaluations
        )
        == 8
    )
