"""Task 022A: FIT-only coverage gate and compatible pipeline entry point."""

from dataclasses import replace
from math import cos, radians

import pytest

from tests.fit_factory import write_trajectory_activity
from tests.processing_factory import processing_fixture
from tests.test_repair_cli import _repairable_fixture
from warpbuster.fit.reader import read_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.pipeline import CoverageStatus, PipelineConfig, run_repair
from warpbuster.pipeline import repair as pipeline
from warpbuster.pipeline.coverage import measure_observed_gps_coverage
from warpbuster.reconstruction.gaps import coordinate_mask


def _fit(tmp_path, missing=(), *, times=None, timer_events=None):
    path = tmp_path / "coverage.fit"
    stamps = times if times is not None else list(range(101))
    write_trajectory_activity(
        path,
        [
            (stamp, None, None)
            if index in missing
            else (stamp, 44.0, 33.0 + index * 0.00001)
            for index, stamp in enumerate(stamps)
        ],
        timer_events=timer_events,
    )
    return path


@pytest.mark.parametrize(
    ("missing", "observed", "status"),
    [
        (range(26, 75), 50.0, CoverageStatus.BELOW_THRESHOLD),
        (range(26, 74), 51.0, CoverageStatus.PASSED),
        (range(26, 73), 52.0, CoverageStatus.PASSED),
    ],
)
def test_coverage_threshold_uses_active_time_without_missing_edges(
    tmp_path, missing, observed, status
):
    run = run_repair(_fit(tmp_path, missing), dry_run=True)
    assert run.course is None
    assert run.policy.fill_missing_from_course is False
    assert run.coverage.status is status
    assert run.coverage.observed_percent == pytest.approx(observed)
    assert run.coverage.active_duration_seconds == 100
    assert run.coverage.observed_duration_seconds == observed
    if status is CoverageStatus.BELOW_THRESHOLD:
        assert run.write_result is None


def test_coverage_uses_active_time_and_excludes_sparse_observation(tmp_path):
    paused = run_repair(
        _fit(tmp_path, range(21, 40), timer_events=[(20, "stop"), (40, "start")]),
        dry_run=True,
    ).coverage
    assert paused.active_duration_seconds == 80
    assert paused.observed_duration_seconds == 80
    assert paused.status is CoverageStatus.PASSED

    sparse = run_repair(_fit(tmp_path, times=[0, 3600, 3601]), dry_run=True).coverage
    assert sparse.active_duration_seconds == 3601
    assert sparse.observed_duration_seconds == 1
    assert sparse.status is CoverageStatus.BELOW_THRESHOLD


@pytest.mark.parametrize("value", [0, -1, 101, float("inf"), float("nan"), True, "51"])
def test_minimum_coverage_config_validation(value):
    with pytest.raises(ValueError, match="minimum_observed_gps_coverage_percent"):
        replace(PipelineConfig(), minimum_observed_gps_coverage_percent=value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "30"])
def test_maximum_interval_config_validation(value):
    with pytest.raises(ValueError, match="maximum_observed_gps_interval_seconds"):
        replace(PipelineConfig(), maximum_observed_gps_interval_seconds=value)


def test_unavailable_clock_and_coverage_refusal_precede_optional_stages(tmp_path, monkeypatch):
    path = _fit(tmp_path, range(26, 75))

    def unexpected(*args, **kwargs):
        pytest.fail("optional reconstruction must not run below the coverage threshold")

    monkeypatch.setattr(pipeline, "run_osm_pipeline", unexpected)
    monkeypatch.setattr(pipeline, "run_dem_stage", unexpected)
    monkeypatch.setattr(pipeline, "write_repaired_fit", unexpected)
    result = run_repair(path, output_path=tmp_path / "unused.fit")
    assert result.coverage.status is CoverageStatus.BELOW_THRESHOLD
    assert result.osm_eligible_gaps == 0
    assert result.write_result is None
    assert not (tmp_path / "unused.fit").exists()

    activity = read_fit(path)
    integrity = analyze_integrity(activity)
    mask = coordinate_mask(
        activity, integrity, pipeline.DEFAULT_REPAIR_POLICY.minimum_invalidation_confidence
    )
    invalid = replace(
        activity,
        records=(
            *activity.records[:1],
            replace(activity.records[1], timestamp=None),
            *activity.records[2:],
        ),
    )
    coverage = measure_observed_gps_coverage(
        invalid, integrity, mask, minimum_percent=51, maximum_interval_seconds=30
    )
    assert coverage.status is CoverageStatus.UNAVAILABLE
    assert coverage.observed_percent is None


def test_gpx_call_ignores_fit_only_coverage_and_keeps_old_signature(tmp_path):
    fit, course = _repairable_fixture(tmp_path)
    preview = run_repair(
        fit,
        course,
        None,
        config=PipelineConfig(minimum_observed_gps_coverage_percent=100),
        dry_run=True,
    )
    assert preview.coverage.status is CoverageStatus.NOT_APPLICABLE
    assert preview.plan.interval_plans


def test_fit_only_passes_internal_gap_to_osm_stage(tmp_path, monkeypatch):
    path = _fit(tmp_path, range(40, 50))
    called = []

    def collect(activity, integrity, plan, config, *, policy):
        called.append((plan, policy))
        return pipeline.OSMResult(plan, "not_needed")

    monkeypatch.setattr(pipeline, "run_osm_pipeline", collect)
    result = run_repair(path, dry_run=True)
    assert result.coverage.status is CoverageStatus.PASSED
    assert result.course is None
    assert result.policy.fill_missing_from_course is False
    assert result.osm_eligible_gaps == 1
    assert called and called[0][0].gaps


@pytest.mark.integration
def test_fit_only_internal_gap_uses_cached_osm_and_writes_fit(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    scale = 111_195.0 * cos(radians(44.0))
    fit = tmp_path / "original.fit"
    write_trajectory_activity(
        fit,
        [
            (index, None, None)
            if 100 <= index <= 150
            else (index, 44.0, 33.0 + index * 2 / scale)
            for index in range(401)
        ],
        distances_m=[float(index * 2) for index in range(401)],
        speeds_mps=[2.0] * 401,
    )
    output = tmp_path / "fit-only.fixed.fit"
    result = run_repair(fit, output_path=output, config=replace(config, approximate_osm=True))
    assert result.coverage.status is CoverageStatus.PASSED
    assert result.osm_eligible_gaps == 1
    assert result.osm.discovery is not None
    assert result.write_result is not None
    assert result.write_result.validation.valid
    assert result.write_result.diff.unexpected_changed_field_count == 0
    assert output.is_file()
