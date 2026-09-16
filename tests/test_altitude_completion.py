"""Task 021E: independent height scope and lossless FIT writer integration."""

import json
from dataclasses import replace
from types import SimpleNamespace

from tests.test_automatic_osm import discover, fixture
from tests.test_osm_application import GRAPH
from warpbuster.cli import main
from warpbuster.fit import writer as fit_writer
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.pipeline import DEMMode, OSMMode, PipelineConfig, run_repair
from warpbuster.pipeline.config import DEFAULT_REPAIR_POLICY
from warpbuster.pipeline.dem import DEMResult
from warpbuster.pipeline.osm import OSMResult
from warpbuster.reconstruction import build_repair_plan
from warpbuster.reconstruction.altitude_completion import (
    DEM_VERTICAL_DATUM,
    AltitudeCompletionReason,
    AltitudeCompletionStatus,
    plan_altitude_completion,
)
from warpbuster.reconstruction.approximate_contract import ApproximateSelectionPolicy
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes

SNAPSHOT = "sha256:" + "a" * 64


class FakeSampler:
    def __init__(self, *, missing: bool = False):
        self.missing = missing

    def sample(self, snapshot_id, points):
        return SimpleNamespace(
            dem_snapshot_id=snapshot_id,
            elevation_profile_id="sha256:" + "b" * 64,
            samples=tuple(
                SimpleNamespace(
                    original_vertex_index=index,
                    raw_elevation_m=None if self.missing else -20.0 + index / 10,
                )
                for index, _point in enumerate(points)
            ),
        )


def _inputs(tmp_path):
    activity, _, integrity, base, _ = fixture(tmp_path, include_altitude=False)
    discovery = discover(activity, base)
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    return activity, integrity, base, discovery, plan


def test_missing_height_is_added_only_to_selected_osm_gap_and_diff_is_scoped(tmp_path):
    activity, _, _, _, plan = _inputs(tmp_path)
    completion = plan_altitude_completion(
        activity,
        plan,
        FakeSampler(),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=None,
    )
    assert completion.status is AltitudeCompletionStatus.PLANNED
    assert completion.eligible_records == len(completion.updates) == 30
    assert completion.public_summary()["policy_id"] == "dem-missing-fit-altitude-v1"
    assert completion.public_summary()["source_datum_basis"] == "no_fit_altitude_stream"
    assert completion.updates[0].altitude_m < 0
    source_bytes = activity.preservation.raw_bytes
    result = write_repaired_fit(
        activity,
        plan,
        tmp_path / "fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
        altitude_completion=completion,
    )
    fixed = read_fit(result.output_path)
    assert all(fixed.records[item.record_index].altitude is not None for item in completion.updates)
    assert fixed.records[149].altitude is None and fixed.records[180].altitude is None
    assert result.altitude_field_change_count == len(completion.updates)
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.compared_count == result.diff.timestamps.unchanged_count
    assert result.diff.sensors.compared_count - result.diff.sensors.unchanged_count == len(
        completion.updates
    )
    assert (
        result.diff.developer_fields.compared_count == result.diff.developer_fields.unchanged_count
    )
    assert activity.preservation.source_path.read_bytes() == source_bytes


def test_partial_fit_height_requires_explicit_matching_datum(tmp_path):
    activity, _, _, _, plan = _inputs(tmp_path)
    modified = replace(
        activity, records=(replace(activity.records[0], altitude=100.0), *activity.records[1:])
    )
    unknown = plan_altitude_completion(
        modified,
        plan,
        FakeSampler(),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=None,
    )
    assert unknown.status is AltitudeCompletionStatus.UNAVAILABLE
    assert unknown.reason is AltitudeCompletionReason.DATUM_UNKNOWN
    declared = plan_altitude_completion(
        modified,
        plan,
        FakeSampler(),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=DEM_VERTICAL_DATUM,
    )
    assert declared.status is AltitudeCompletionStatus.PLANNED


def test_invalid_native_height_fields_are_filled_with_declared_datum(tmp_path):
    activity, _, _, _, _ = fixture(tmp_path)
    original_heights = tuple(record.altitude for record in activity.records)
    requests = tuple(
        fit_writer._record_request(
            activity.records[index],
            "enhanced_altitude",
            0xFFFFFFFF,
            raw_value=True,
            category="altitude",
        )
        for index in range(150, 180)
    )
    raw, _ = fit_writer._patch_fit_bytes(activity.preservation.raw_bytes, requests)
    activity.preservation.source_path.write_bytes(raw)
    activity = read_fit(activity.preservation.source_path)
    assert all(activity.records[index].altitude is None for index in range(150, 180))
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = discover(activity, base)
    plan = apply_automatic_osm_routes(
        activity, integrity, base, discovery, approximate_policy=ApproximateSelectionPolicy()
    )
    unknown = plan_altitude_completion(
        activity,
        plan,
        FakeSampler(),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=None,
    )
    assert unknown.reason is AltitudeCompletionReason.DATUM_UNKNOWN
    completion = plan_altitude_completion(
        activity,
        plan,
        FakeSampler(),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=DEM_VERTICAL_DATUM,
    )
    assert completion.status is AltitudeCompletionStatus.PLANNED
    assert all(item.field_names == ("enhanced_altitude",) for item in completion.updates)
    assert completion.public_summary()["source_datum_basis"] == "explicit_egm96_declaration"
    result = write_repaired_fit(
        activity,
        plan,
        tmp_path / "native.fixed.fit",
        minimum_confidence=IntegrityConfidence.MEDIUM,
        altitude_completion=completion,
    )
    fixed = read_fit(result.output_path)
    assert result.diff.definitions_unchanged
    assert result.altitude_field_change_count == 30
    assert fixed.records[149].altitude == original_heights[149]
    assert fixed.records[180].altitude == original_heights[180]


def test_void_dem_never_blocks_2d_selection(tmp_path):
    activity, _, _, _, plan = _inputs(tmp_path)
    completion = plan_altitude_completion(
        activity,
        plan,
        FakeSampler(missing=True),
        SNAPSHOT,
        minimum_confidence=IntegrityConfidence.MEDIUM,
        fit_altitude_datum=None,
    )
    assert completion.status is AltitudeCompletionStatus.UNAVAILABLE
    assert completion.reason is AltitudeCompletionReason.NO_DEM_SAMPLES
    assert plan.interval_plans[0].osm_provenance is not None


def test_writer_refusal_of_height_retries_safe_2d_fit(tmp_path, monkeypatch):
    from warpbuster.pipeline import repair as pipeline
    from warpbuster.reconstruction.altitude_completion import AltitudeUpdate

    activity, _, _, _, _ = _inputs(tmp_path)

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            approximate_policy=ApproximateSelectionPolicy(),
            minimum_confidence=policy.minimum_confidence,
        )
        return OSMResult(plan, "complete", discovery=discovery)

    def dem_stage(
        activity, integrity, base, fallback, discovery, config, policy, *, budget_seconds
    ):
        completion = plan_altitude_completion(
            activity,
            fallback,
            FakeSampler(),
            SNAPSHOT,
            minimum_confidence=policy.minimum_confidence,
            fit_altitude_datum=None,
        )
        bad = replace(completion, updates=(AltitudeUpdate(150, -1000.0, ("enhanced_altitude",)),))
        return DEMResult(fallback, "complete", snapshot_id=SNAPSHOT, altitude=bad)

    monkeypatch.setattr(pipeline, "run_osm_pipeline", osm_stage)
    monkeypatch.setattr(pipeline, "run_dem_stage", dem_stage)
    config = PipelineConfig(
        osm_mode=OSMMode.OFFLINE,
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        complete_missing_altitude=True,
    )
    policy = replace(DEFAULT_REPAIR_POLICY, fill_missing_from_course=False)
    run = run_repair(
        activity.preservation.source_path,
        output_path=tmp_path / "fallback.fit",
        config=config,
        policy=policy,
    )
    assert run.write_result is not None
    assert run.dem.altitude.reason is AltitudeCompletionReason.WRITER_REFUSED
    assert run.write_result.altitude_field_change_count == 0
    assert read_fit(run.write_result.output_path).records[150].altitude is None


def test_cli_dry_run_reports_height_plan_without_writing_fit(tmp_path, monkeypatch, capsys):
    from warpbuster.pipeline import repair as pipeline

    activity, _, _, _, _ = _inputs(tmp_path)

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            approximate_policy=ApproximateSelectionPolicy(),
            minimum_confidence=policy.minimum_confidence,
        )
        return OSMResult(plan, "complete", discovery=discovery)

    def dem_stage(
        activity, integrity, base, fallback, discovery, config, policy, *, budget_seconds
    ):
        completion = plan_altitude_completion(
            activity,
            fallback,
            FakeSampler(),
            SNAPSHOT,
            minimum_confidence=policy.minimum_confidence,
            fit_altitude_datum=None,
        )
        return DEMResult(fallback, "complete", snapshot_id=SNAPSHOT, altitude=completion)

    monkeypatch.setattr(pipeline, "run_osm_pipeline", osm_stage)
    monkeypatch.setattr(pipeline, "run_dem_stage", dem_stage)
    assert (
        main(
            [
                "repair",
                str(activity.preservation.source_path),
                "--osm-graph-id",
                GRAPH,
                "--approximate-missing-osm",
                "--dem-mode",
                "offline",
                "--complete-missing-altitude",
                "--min-confidence",
                "medium",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["pipeline"]["altitude_completion"]["status"] == "planned"
    assert report["pipeline"]["altitude_completion"]["planned_records"] == 30
    assert not (tmp_path / "activity.fixed.fit").exists()
