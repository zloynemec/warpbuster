"""012E automatic hypothesis selection, application and the single-command flow."""

import json
from dataclasses import replace

import pytest

from tests.test_osm_application import GRAPH, Routes, fixture
from warpbuster.cli import main
from warpbuster.config import AutomaticOSMApplicationConfig, GapCandidateRankingConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence as Confidence
from warpbuster.models.reconstruction import ReconstructionReason as Reason
from warpbuster.reconstruction import apply_automatic_osm_routes, build_repair_plan
from warpbuster.reconstruction import automatic_osm as automatic
from warpbuster.reconstruction import osm as osm_module
from warpbuster.reconstruction.osm import OSMReconstructionError, OSMReconstructionProvider


class AuditedRoutes(Routes):
    def alternatives(self, *args, **kwargs):
        result = super().alternatives(*args, **kwargs)
        return replace(
            result,
            candidates=tuple(
                replace(c, document={**c.document, "audit": {"status": "PASS"}})
                for c in result.candidates
            ),
        )


def discover(activity, base, **kwargs):
    return OSMReconstructionProvider(AuditedRoutes(**kwargs)).discover(activity, base, GRAPH)


def audit(plan):
    return json.loads(plan.automatic_osm_json)


def test_automatic_writes_medium_hypothesis_losslessly(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=3)
    plan = apply_automatic_osm_routes(activity, integrity, base, discovery)
    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    assert candidate.confidence is Confidence.MEDIUM
    assert candidate.osm_provenance.confirmation is None
    assert candidate.osm_provenance.identity_basis == "automatic_osm_selection"
    assert audit(plan)["decisions"][0]["selected_route_id"] == "route-0"
    result = write_repaired_fit(activity, plan, minimum_confidence=Confidence.MEDIUM)
    assert result.validation.valid and result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    fixed = read_fit(result.output_path)
    changed = {u.record_index for u in candidate.coordinate_updates}
    for a, b in zip(activity.records, fixed.records, strict=True):
        assert (a.timestamp, a.distance, a.speed, a.altitude) == (
            b.timestamp,
            b.distance,
            b.speed,
            b.altitude,
        )
        if a.index not in changed:
            assert (a.latitude, a.longitude) == (b.latitude, b.longitude)
    evaluation = discovery.evaluations[0]
    reversed_discovery = replace(
        discovery,
        evaluations=(replace(evaluation, candidates=tuple(reversed(evaluation.candidates))),),
    )
    again = apply_automatic_osm_routes(activity, integrity, base, reversed_discovery)
    assert again.interval_plans[0].coordinate_updates == candidate.coordinate_updates
    assert audit(again) == audit(plan)
    other = write_repaired_fit(
        activity, again, tmp_path / "second.fit", minimum_confidence=Confidence.MEDIUM
    )
    assert other.output_path.read_bytes() == result.output_path.read_bytes()
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes


@pytest.mark.parametrize("gpx_state", ["accepted", "low", "invalid"])
def test_gpx_first_only_for_accepted_applicable_candidate(tmp_path, gpx_state):
    activity, course, integrity, _, _ = fixture(tmp_path)
    base = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    candidate = base.interval_plans[0]
    if gpx_state == "low":
        candidate = replace(candidate, confidence=Confidence.LOW)
    if gpx_state == "invalid":
        updates = candidate.coordinate_updates
        candidate = replace(
            candidate, coordinate_updates=(replace(updates[0], candidate_latitude=80), *updates[1:])
        )
    base = replace(base, interval_plans=(candidate,))
    plan = apply_automatic_osm_routes(activity, integrity, base, discover(activity, base))
    if gpx_state == "accepted":
        assert plan.interval_plans == base.interval_plans
        assert audit(plan)["decisions"][0]["status"] == "gpx_selected"
    else:
        assert plan.interval_plans[0].osm_provenance is not None
        assert not plan.unresolved_gaps


def test_failed_first_allocation_tries_next_and_respects_budget(tmp_path, monkeypatch):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    allocate = automatic._allocate

    def fail_first(*args):
        return (
            Reason.LOCAL_DISTANCE_INCONSISTENT if args[3].route_id == "route-0" else allocate(*args)
        )

    monkeypatch.setattr(automatic, "_allocate", fail_first)
    plan = apply_automatic_osm_routes(activity, integrity, base, discovery)
    decision = audit(plan)["decisions"][0]
    assert decision["selected_route_id"] == "route-1"
    assert decision["attempts"][0]["reason"] == "local_distance_inconsistent"
    limited = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        config=AutomaticOSMApplicationConfig(maximum_candidate_attempts=1),
    )
    assert not limited.interval_plans
    assert audit(limited)["decisions"][0]["reason"] == "candidate_attempt_limit"


@pytest.mark.parametrize(
    "threshold,expected", [(Confidence.HIGH, 0), (Confidence.MEDIUM, 1), (Confidence.LOW, 1)]
)
def test_explicit_threshold_is_respected(tmp_path, threshold, expected):
    activity, _, integrity, base, _ = fixture(tmp_path)
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discover(activity, base), minimum_confidence=threshold
    )
    assert len(plan.interval_plans) == expected


@pytest.mark.parametrize(
    "fault", ["audit", "duplicate", "anchors", "geometry", "points", "records", "quality"]
)
def test_unusable_osm_is_unresolved_without_mutating_base(tmp_path, fault):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    if fault != "audit":
        discovery = discover(activity, base)
    if fault == "quality":
        discovery = discover(
            activity,
            base,
            transform=lambda a, b: ((a[0] + 40 / 111195, a[1]), (b[0] + 40 / 111195, b[1])),
        )
    evaluation = discovery.evaluations[0]
    route = evaluation.candidates[0]
    if fault == "duplicate":
        evaluation = replace(evaluation, candidates=(route, route))
    elif fault == "anchors":
        evaluation = replace(
            evaluation, anchor_before=replace(evaluation.anchor_before, latitude=60)
        )
    elif fault == "geometry":
        evaluation = replace(
            evaluation,
            candidates=(
                replace(
                    route,
                    coordinates=(
                        replace(route.coordinates[0], latitude=float("nan")),
                        *route.coordinates[1:],
                    ),
                ),
            ),
        )
    discovery = replace(discovery, evaluations=(evaluation,))
    config = AutomaticOSMApplicationConfig(maximum_gap_records=1) if fault == "records" else None
    ranking = (
        GapCandidateRankingConfig(maximum_candidate_points=1)
        if fault == "points"
        else GapCandidateRankingConfig(maximum_recommended_score=0)
        if fault == "quality"
        else None
    )
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, config=config, ranking_config=ranking
    )
    assert not plan.interval_plans and plan.unresolved_gaps
    assert base.coordinate_mask == plan.coordinate_mask and base.gaps == plan.gaps


def test_approximate_connectors_and_distance_budget(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    # A road 10m to the north passes the new approximate gates (> old 5m limit).
    discovery = discover(
        activity,
        base,
        transform=lambda a, b: ((a[0] + 10 / 111195, a[1]), (b[0] + 10 / 111195, b[1])),
    )
    plan = apply_automatic_osm_routes(activity, integrity, base, discovery)
    assert plan.interval_plans
    assert plan.interval_plans[0].osm_provenance.connector_distance_m > 5
    strict = apply_automatic_osm_routes(
        activity,
        integrity,
        base,
        discovery,
        config=AutomaticOSMApplicationConfig(
            distance_absolute_tolerance_m=5, distance_relative_tolerance=0.01
        ),
    )
    provenance = strict.interval_plans[0].osm_provenance
    assert provenance.allocation_method.value == "timestamps"
    assert "distance_path_mismatch" in provenance.signal_diagnostics
    assert "speed_path_mismatch" in provenance.signal_diagnostics


def test_cli_preview_write_share_plan_and_backend_failure_preserves_gpx(
    tmp_path, capsys, monkeypatch
):
    activity, course, _, _, _ = fixture(tmp_path)
    monkeypatch.setattr(osm_module, "ValhallaRoutingClient", lambda *_args: AuditedRoutes())
    args = ["repair", str(activity.preservation.source_path), "--osm-graph-id", GRAPH, "--json"]
    assert main([*args, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert main(args) == 0
    written = json.loads(capsys.readouterr().out)
    assert preview["automatic_osm"] == written["automatic_osm"]
    assert preview["selection"]["minimum_confidence"] == "medium"
    assert written["output_written"] and written["diff"]["unexpected_changed_field_count"] == 0
    assert written["repair_plan"]["candidate_ranking"] == preview["candidate_ranking"]

    def unavailable(*args):
        raise OSMReconstructionError("BACKEND_FAILURE", "test failure", {})

    monkeypatch.setattr(osm_module, "ValhallaRoutingClient", unavailable)
    assert (
        main(
            [
                *args,
                "--course",
                str(course.source_path),
                "--fill-missing-from-course",
                "--overwrite",
            ]
        )
        == 0
    )
    partial = json.loads(capsys.readouterr().out)
    assert partial["automatic_osm"]["status"] == "unavailable"
    assert partial["gap_inventory"][0]["provider"] == "gpx"


def test_automatic_config_defaults_and_validation():
    config = AutomaticOSMApplicationConfig()
    assert (
        config.maximum_connector_m,
        config.distance_absolute_tolerance_m,
        config.distance_relative_tolerance,
    ) == (100, 100, 0.2)
    assert (config.maximum_candidate_attempts, config.maximum_gap_records) == (64, 10000)
    for value in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            AutomaticOSMApplicationConfig(maximum_candidate_attempts=value)


def test_score_tie_prefers_shorter_alternative_not_primary(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = discover(activity, base, alternatives=2)
    evaluation = discovery.evaluations[0]
    primary, alternative = evaluation.candidates
    a, b = primary.coordinates
    middle = replace(a, latitude=a.latitude + 3 / 111195, longitude=(a.longitude + b.longitude) / 2)
    primary = replace(primary, coordinates=(a, middle, b))
    discovery = replace(
        discovery, evaluations=(replace(evaluation, candidates=(primary, alternative)),)
    )
    plan = apply_automatic_osm_routes(activity, integrity, base, discovery)
    ranks = automatic.rank_gap_candidates(activity, base, discovery).rankings[0].candidates
    assert len({r.score for r in ranks}) == 1
    assert audit(plan)["decisions"][0]["selected_route_id"] == alternative.route_id


@pytest.mark.parametrize("method", ["distance", "speed", "time"])
def test_automatic_pause_allocation_preserves_clock_and_events(tmp_path, method):
    from tests.test_pause_reconstruction import _fixture

    activity, _ = _fixture(tmp_path, method=method, records_in_pause=True)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    plan = apply_automatic_osm_routes(activity, integrity, base, discover(activity, base))
    candidate = plan.interval_plans[0]
    assert candidate.osm_provenance.timing.paused_seconds == 300
    positions = {
        (u.candidate_latitude, u.candidate_longitude)
        for u in candidate.coordinate_updates
        if 160 <= u.record_index <= 460
    }
    assert len(positions) == 1
    result = write_repaired_fit(activity, plan, minimum_confidence=Confidence.MEDIUM)
    assert result.post_write_verified
    assert read_fit(result.output_path).events == activity.events
    assert result.diff.timestamps.percentage == 100
