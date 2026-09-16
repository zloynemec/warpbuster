"""Task 021C: DEM can reorder safe OSM alternatives, never grant write permission."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_automatic_osm import discover
from tests.test_osm_application import GRAPH, Routes, fixture
from warpbuster.config import AutomaticOSMApplicationConfig, GapCandidateRankingConfig
from warpbuster.models.reconstruction import OSMPathPoint
from warpbuster.reconstruction import apply_automatic_osm_routes, build_repair_plan
from warpbuster.reconstruction.approximate_contract import (
    ApproximateSelectionPolicy,
    DemComparisonConfig,
)
from warpbuster.reconstruction.osm import OSMReconstructionProvider


class FakeDemSampler:
    def __init__(self, *, void=False, failure=False, snapshot_mismatch=False):
        self.calls = []
        self.void = void
        self.failure = failure
        self.snapshot_mismatch = snapshot_mismatch

    def sample(self, snapshot_id, points):
        self.calls.append((snapshot_id, tuple(points)))
        if self.failure:
            raise RuntimeError("synthetic DEM backend failure")
        is_hill = max(point.latitude for point in points) - points[0].latitude > 10 / 111195
        count = len(points) - 2
        samples = []
        for index in range(len(points)):
            hill = 40 * max(0, 1 - abs((index - 1) - (count - 1) / 2) / ((count - 1) / 2))
            raw = 100 + hill if is_hill else 100.0
            if self.void and is_hill and index % 2 == 0:
                raw = None
            samples.append(
                SimpleNamespace(
                    original_vertex_index=index,
                    raw_elevation_m=raw,
                    chainage_m=float(index * 2),
                )
            )
        return SimpleNamespace(
            dem_snapshot_id="wrong-snapshot" if self.snapshot_mismatch else snapshot_id,
            elevation_profile_id="profile-hill" if is_hill else "profile-flat",
            samples=tuple(samples),
        )


def scenario(tmp_path, *, altitude_offset=230.0):
    activity, _, integrity, base, _ = fixture(tmp_path)
    gap = base.gaps[0]
    count = gap.record_count
    records = list(activity.records)
    for offset, index in enumerate(range(gap.start_record_index, gap.end_record_index + 1)):
        hill = 40 * max(0, 1 - abs(offset - (count - 1) / 2) / ((count - 1) / 2))
        records[index] = replace(records[index], altitude=altitude_offset + hill)
    activity = replace(activity, records=tuple(records))
    discovery = discover(activity, base, alternatives=2)
    evaluation = discovery.evaluations[0]
    flat, hill_route = evaluation.candidates
    before, after = hill_route.coordinates
    middle = OSMPathPoint(
        (before.latitude + after.latitude) / 2 + 20 / 111195,
        (before.longitude + after.longitude) / 2,
    )
    hill_route = replace(hill_route, coordinates=(before, middle, after))
    discovery = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=(flat, hill_route)),),
    )
    policy = ApproximateSelectionPolicy(ranking=GapCandidateRankingConfig(near_best_score_delta=10))
    return activity, integrity, base, discovery, policy


def decision(plan):
    return json.loads(plan.automatic_osm_json)["decisions"][0]


def test_dem_shape_changes_2d_choice_and_ignores_uniform_offset(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    baseline = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=policy
    )
    assert decision(baseline)["selected_route_id"] == "route-0"
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-1"
    assert decision(chosen)["dem_evidence"] == {"status": "usable", "selected_by_dem": True}
    assert [call[0] for call in sampler.calls] == ["snapshot-a", "snapshot-a"]
    attempts = decision(chosen)["approximate_audit"]["attempts"]
    assert [item["dem_profile_id"] for item in attempts] == ["profile-flat", "profile-hill"]
    assert attempts[1]["dem_shape_error_m"] == pytest.approx(0)
    assert attempts[0]["dem_shape_error_m"] > 5

    shifted, integrity, base, discovery, policy = scenario(tmp_path, altitude_offset=330.0)
    again = apply_automatic_osm_routes(
        shifted,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=FakeDemSampler(),
        dem_snapshot_id="snapshot-a",
    )
    assert decision(again)["selected_route_id"] == "route-1"
    evaluation = discovery.evaluations[0]
    reversed_discovery = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=tuple(reversed(evaluation.candidates))),),
    )
    reversed_choice = apply_automatic_osm_routes(
        shifted,
        integrity,
        base,
        reversed_discovery,
        approximate_policy=policy,
        dem_sampler=FakeDemSampler(),
        dem_snapshot_id="snapshot-a",
    )
    assert decision(reversed_choice)["selected_route_id"] == "route-1"


def test_no_fit_altitude_skips_dem_and_preserves_2d_choice(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    records = tuple(
        replace(record, altitude=None)
        if base.gaps[0].start_record_index <= record.index <= base.gaps[0].end_record_index
        else record
        for record in activity.records
    )
    activity = replace(activity, records=records)
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert decision(chosen)["approximate_audit"]["dem_status"] == "uninformative"
    assert decision(chosen)["approximate_audit"]["reasons"] == ["dem_uninformative"]
    assert decision(chosen)["dem_evidence"] == {"status": "uninformative", "selected_by_dem": False}
    assert sampler.calls == []


@pytest.mark.parametrize(
    ("sampler", "status"),
    [
        (FakeDemSampler(void=True), "uninformative"),
        (FakeDemSampler(failure=True), "unavailable"),
        (FakeDemSampler(snapshot_mismatch=True), "unavailable"),
    ],
)
def test_partial_or_failed_dem_never_blocks_2d_repair(tmp_path, sampler, status):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert decision(chosen)["approximate_audit"]["dem_status"] == status
    assert decision(chosen)["approximate_audit"]["reasons"] == [f"dem_{status}"]
    if status == "uninformative":
        assert decision(chosen)["approximate_audit"]["dem_profile_ids"] == [
            "profile-flat",
            "profile-hill",
        ]


def test_small_dem_advantage_and_candidate_order_fall_back_deterministically(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    policy = replace(policy, dem=DemComparisonConfig(minimum_dem_advantage_m=100))
    first = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=FakeDemSampler(),
        dem_snapshot_id="snapshot-a",
    )
    evaluation = discovery.evaluations[0]
    reversed_discovery = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=tuple(reversed(evaluation.candidates))),),
    )
    second = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        reversed_discovery,
        approximate_policy=policy,
        dem_sampler=FakeDemSampler(),
        dem_snapshot_id="snapshot-a",
    )
    assert (
        decision(first)["selected_route_id"] == decision(second)["selected_route_id"] == "route-0"
    )
    assert decision(first)["approximate_audit"]["dem_status"] == "uninformative"


def test_dem_policy_validation_and_hash():
    original = ApproximateSelectionPolicy()
    assert original.dem == DemComparisonConfig(5, 30.0, 0.8, 5.0, 8)
    changed = replace(original, dem=DemComparisonConfig(minimum_dem_advantage_m=10))
    assert original.policy_hash != changed.policy_hash
    for kwargs in (
        {"minimum_aligned_samples": 0},
        {"minimum_span_m": 0},
        {"minimum_coverage_fraction": 1.1},
        {"maximum_profiles_per_gap": True},
    ):
        with pytest.raises(ValueError):
            DemComparisonConfig(**kwargs)


def test_profile_budget_falls_back_before_sampling(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    policy = replace(policy, dem=DemComparisonConfig(maximum_profiles_per_gap=1))
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert decision(chosen)["approximate_audit"]["dem_status"] == "unavailable"
    assert sampler.calls == []


def test_truncated_candidate_attempts_do_not_create_dem_advantage(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    policy = replace(
        policy,
        application=AutomaticOSMApplicationConfig(maximum_candidate_attempts=1),
    )
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert decision(chosen)["approximate_audit"]["dem_status"] == "unavailable"
    assert sampler.calls == []


def test_dem_only_profiles_2d_near_best_plans(tmp_path):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    evaluation = discovery.evaluations[0]
    distant = replace(
        evaluation.candidates[1],
        coordinates=tuple(
            OSMPathPoint(point.latitude + 40 / 111195, point.longitude)
            for point in evaluation.candidates[1].coordinates
        ),
    )
    discovery = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=(evaluation.candidates[0], distant)),),
    )
    policy = replace(policy, ranking=GapCandidateRankingConfig(near_best_score_delta=1))
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=policy,
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert sampler.calls == []


@pytest.mark.parametrize(
    ("dem", "expected_calls"),
    [
        (DemComparisonConfig(minimum_aligned_samples=100), 0),
        (DemComparisonConfig(minimum_span_m=1000), 2),
    ],
)
def test_minimum_samples_and_span_are_policy_bounded(tmp_path, dem, expected_calls):
    activity, integrity, base, discovery, policy = scenario(tmp_path)
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        approximate_policy=replace(policy, dem=dem),
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selected_route_id"] == "route-0"
    assert decision(chosen)["approximate_audit"]["dem_status"] == "uninformative"
    assert len(sampler.calls) == expected_calls


def test_gpx_first_and_unverified_osm_do_not_call_dem(tmp_path):
    activity, course, integrity, _, _ = fixture(tmp_path)
    base = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    sampler = FakeDemSampler()
    chosen = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discover(activity, base),
        approximate_policy=ApproximateSelectionPolicy(),
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert decision(chosen)["selection_mode"] == "gpx_first"
    assert sampler.calls == []

    base = build_repair_plan(activity, integrity)
    unverified = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    refused = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        unverified,
        approximate_policy=ApproximateSelectionPolicy(),
        dem_sampler=sampler,
        dem_snapshot_id="snapshot-a",
    )
    assert not refused.interval_plans
    assert sampler.calls == []
