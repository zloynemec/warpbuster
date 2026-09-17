"""Task 022B endpoint hints must never edit preserved FIT movement."""

import json
from dataclasses import replace
from math import cos, radians

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.processing_factory import processing_fixture
from tests.test_dem_choice import FakeDemSampler
from warpbuster.fit.reader import read_fit
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import GapOrigin
from warpbuster.pipeline import CoverageStatus, DEMMode, EndpointHints, UserEndpoint, run_repair
from warpbuster.reconstruction.approximate_contract import DemComparisonConfig
from warpbuster.reconstruction.endpoints import apply_endpoint_routes, choose_dem_endpoint_routes
from warpbuster.reconstruction.osm import RoutingAlternativesData, RoutingCandidateData


@pytest.mark.parametrize(
    "hint",
    [
        EndpointHints(start=UserEndpoint(44.0, 33.0)),
        EndpointHints(finish=UserEndpoint(44.0, 33.0 + 800 / (111_195 * cos(radians(44.0))))),
    ],
)
@pytest.mark.integration
def test_fit_only_endpoint_uses_cached_osm_and_preserves_observed_records(tmp_path, hint):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    missing = range(0, 51) if hint.start is not None else range(350, 401)
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None) if index in missing else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )
    original = read_fit(fit)
    output = tmp_path / "endpoint.fixed.fit"
    run = run_repair(
        fit,
        output_path=output,
        config=replace(config, approximate_osm=True),
        endpoint_hints=hint,
    )
    assert run.coverage.status is CoverageStatus.PASSED
    assert run.osm_eligible_gaps == 1
    assert run.write_result is not None, (
        run.osm.status,
        run.osm.error_code,
        run.osm.warning,
        run.plan.endpoint_audit_json,
        run.plan.automatic_osm_json,
    )
    assert run.write_result.validation.valid
    assert run.write_result.diff.unexpected_changed_field_count == 0
    fixed = read_fit(output)
    for record in original.records:
        changed = fixed.records[record.index]
        assert record.timestamp == changed.timestamp
        if record.index in missing:
            assert changed.latitude is not None and changed.longitude is not None
        else:
            assert (record.latitude, record.longitude) == (changed.latitude, changed.longitude)


def test_endpoint_contract_rejects_invalid_and_conflicting_inputs(tmp_path):
    with pytest.raises(ValueError):
        UserEndpoint(float("nan"), 33.0)
    with pytest.raises(ValueError):
        UserEndpoint(44.0, 181.0)
    with pytest.raises(ValueError):
        EndpointHints(start=UserEndpoint(44.0, 33.0), loop_point=UserEndpoint(44.0, 33.0))


def test_supplied_point_cannot_move_preserved_fit_edge(tmp_path):
    fit = tmp_path / "preserved.fit"
    write_trajectory_activity(
        fit,
        [(index, 44.0, 33.0 + index * 2 / 80_000) for index in range(100)],
    )
    run = run_repair(
        fit,
        dry_run=True,
        endpoint_hints=EndpointHints(start=UserEndpoint(45.0, 34.0)),
    )
    assert run.osm_eligible_gaps == 0
    assert not run.selection.has_changes
    assert json.loads(run.plan.endpoint_audit_json) == [
        {
            "kind": "prefix",
            "reason": "fit_edge_preserved",
            "source": "user_supplied",
            "endpoint": [45.0, 34.0],
            "time_binding": "activity_boundary_assumption",
            "status": "unused",
        }
    ]


def test_coverage_gate_blocks_endpoint_reconstruction(tmp_path):
    fit = tmp_path / "mostly-missing.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None) if index < 60 else (index, 44.0, 33.0 + index * 2 / 80_000)
            for index in range(100)
        ],
    )
    output = tmp_path / "blocked.fit"
    run = run_repair(
        fit,
        output_path=output,
        endpoint_hints=EndpointHints(start=UserEndpoint(44.0, 33.0)),
    )
    assert run.coverage.status is CoverageStatus.BELOW_THRESHOLD
    assert run.osm_eligible_gaps == 0
    assert run.write_result is None
    assert not output.exists()


@pytest.mark.integration
def test_one_loop_point_reconstructs_two_edges_independently(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None)
            if index <= 50 or index >= 600
            else (index, 44.0, 33.0 + 2 * min(index, 1000 - index) / scale)
            for index in range(1001)
        ],
        distances_m=[float(index * 2) for index in range(1001)],
        speeds_mps=[2.0] * 1001,
    )
    run = run_repair(
        fit,
        output_path=tmp_path / "loop.fixed.fit",
        config=replace(config, approximate_osm=True),
        endpoint_hints=EndpointHints(loop_point=UserEndpoint(44.0, 33.0)),
    )
    assert run.coverage.status is CoverageStatus.PASSED
    assert run.write_result is not None, run.plan.endpoint_audit_json
    assert len(run.selection.selected_interval_plans) == 2, (
        {
            key: value
            for key, value in json.loads(run.plan.automatic_osm_json)["decisions"][1].items()
            if key in {"status", "reason", "attempts", "gap_id"}
        },
        run.plan.endpoint_audit_json,
    )
    assert {
        candidate.interval.kind.value for candidate in run.selection.selected_interval_plans
    } == {"prefix", "suffix"}
    fixed = read_fit(run.write_result.output_path)
    for index in (0, 1000):
        assert fixed.records[index].latitude == pytest.approx(44.0, abs=1e-7)
        assert fixed.records[index].longitude == pytest.approx(33.0, abs=1e-7)
    audit = json.loads(run.plan.endpoint_audit_json)
    assert [item["status"] for item in audit] == ["osm_selected", "osm_selected"]


@pytest.mark.integration
def test_endpoint_and_internal_gap_share_one_fit_write(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None)
            if index <= 50 or 100 <= index <= 150
            else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )
    output = tmp_path / "combined.fixed.fit"
    run = run_repair(
        fit,
        output_path=output,
        config=replace(config, approximate_osm=True),
        endpoint_hints=EndpointHints(start=UserEndpoint(44.0, 33.0)),
    )
    assert run.write_result is not None, (run.plan.endpoint_audit_json, run.osm.status)
    assert len(run.selection.selected_interval_plans) == 2, (
        {
            key: value
            for key, value in json.loads(run.plan.automatic_osm_json)["decisions"][1].items()
            if key in {"status", "reason", "attempts", "gap_id"}
        },
        run.plan.endpoint_audit_json,
    )
    assert run.write_result.diff.unexpected_changed_field_count == 0
    assert run.write_result.validation.valid


@pytest.mark.integration
def test_dem_can_prefer_another_preflight_safe_endpoint_route(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None) if index <= 50 else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
        altitudes_m=[
            230 + 40 * max(0, 1 - abs(index - 25) / 25) if index <= 50 else 230.0
            for index in range(401)
        ],
    )
    run = run_repair(
        fit,
        config=replace(config, approximate_osm=True),
        endpoint_hints=EndpointHints(start=UserEndpoint(44.0, 33.0)),
        dry_run=True,
    )
    selected = run.plan.interval_plans[0]
    assert selected.osm_provenance is not None
    updates = []
    for item in selected.coordinate_updates:
        bump = 20 / 111_195 * max(0, 1 - abs(item.record_index - 25) / 25)
        updates.append(replace(item, candidate_latitude=item.candidate_latitude + bump))
    hill = replace(
        selected,
        coordinate_updates=tuple(updates),
        osm_provenance=replace(selected.osm_provenance, route_id="hill-route"),
    )
    fallback = replace(run.plan, endpoint_alternatives=(selected, hill))
    internal = replace(run.plan, interval_plans=(), endpoint_alternatives=())
    chosen = choose_dem_endpoint_routes(
        run.activity,
        internal,
        fallback,
        FakeDemSampler(),
        "snapshot-a",
        DemComparisonConfig(),
    )
    assert chosen.interval_plans[0].osm_provenance.route_id == "hill-route"
    assert json.loads(chosen.endpoint_audit_json)[0]["selected_by_dem"] is True


@pytest.mark.integration
def test_missing_dem_snapshot_keeps_safe_endpoint_2d_plan(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None) if index <= 50 else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )
    run = run_repair(
        fit,
        output_path=tmp_path / "2d.fixed.fit",
        config=replace(
            config,
            approximate_osm=True,
            dem_mode=DEMMode.OFFLINE,
            dem_snapshot_id="sha256:" + "a" * 64,
        ),
        endpoint_hints=EndpointHints(start=UserEndpoint(44.0, 33.0)),
    )
    assert run.dem.status == "unavailable"
    assert run.write_result is not None
    assert run.plan.endpoint_audit_json is not None


@pytest.mark.integration
def test_invalidated_edge_never_receives_missing_only_approximation(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    fit = tmp_path / "original.fit"
    scale = 111_195.0 * cos(radians(44.0))
    write_trajectory_activity(
        fit,
        [
            (index, None, None) if index <= 50 else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )
    point = UserEndpoint(44.0, 33.0)
    run = run_repair(
        fit,
        config=replace(config, approximate_osm=True),
        endpoint_hints=EndpointHints(start=point),
        dry_run=True,
    )
    candidate = run.plan.interval_plans[0]
    changed_gap = replace(
        candidate.interval,
        origin=GapOrigin.MIXED,
        invalidated_count=1,
        invalidation_confidence=IntegrityConfidence.HIGH,
    )
    base = replace(
        run.plan,
        gaps=(changed_gap,),
        interval_plans=(),
        endpoint_alternatives=(),
        endpoint_audit_json=None,
    )

    class Client:
        def alternatives(self, graph_id, start, end, alternates):
            return RoutingAlternativesData(
                "READY",
                (
                    RoutingCandidateData(
                        "bounded-route",
                        "primary",
                        tuple(
                            (item.candidate_latitude, item.candidate_longitude)
                            for item in candidate.coordinate_updates
                        ),
                        {"audit": {"status": "PASS"}},
                    ),
                ),
                {},
            )

    result = apply_endpoint_routes(
        run.activity,
        run.integrity,
        base,
        "prepared-graph",
        Client(),
        EndpointHints(start=point),
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert not result.interval_plans
    assert json.loads(result.endpoint_audit_json)[0]["reason"] == "strict_route_not_established"
