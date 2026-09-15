"""Shared policy, file-to-result semantics and complete companion integration."""

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from tests.processing_factory import processing_fixture
from tests.test_repair_cli import _repairable_fixture
from warpbuster.cli import main
from warpbuster.fit.writer import FitWriteError
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.pipeline import (
    DEFAULT_REPAIR_POLICY,
    LEGACY_REPAIR_POLICY,
    OSMMode,
    PipelineConfig,
    PipelineError,
    RepairPolicy,
    run_repair,
)
from warpbuster.pipeline import repair as pipeline


def test_shared_policy_is_immutable_and_library_acquisition_is_opt_in():
    assert DEFAULT_REPAIR_POLICY.as_dict() == {
        "fill_missing_from_course": True,
        "minimum_invalidation_confidence": "medium",
        "minimum_confidence": "medium",
    }
    assert LEGACY_REPAIR_POLICY.as_dict() == {
        "fill_missing_from_course": False,
        "minimum_invalidation_confidence": "high",
        "minimum_confidence": "high",
    }
    assert PipelineConfig().osm_mode is OSMMode.DISABLED
    with pytest.raises(FrozenInstanceError):
        DEFAULT_REPAIR_POLICY.minimum_confidence = IntegrityConfidence.LOW
    with pytest.raises(ValueError):
        RepairPolicy(minimum_invalidation_confidence=IntegrityConfidence.LOW)


@pytest.mark.parametrize(
    "name",
    [
        "record_limit",
        "base_plan_timeout_seconds",
        "osm_maximum_cells",
        "osm_maximum_area_km2",
        "osm_coverage_buffer_m",
        "osm_ipc_maximum_bytes",
    ],
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "10", None])
def test_shared_resource_limits_reject_unbounded_values(name, value):
    with pytest.raises(ValueError, match=name):
        replace(PipelineConfig(), **{name: value})


def test_one_sequence_preserves_dry_run_and_writes_only_once(tmp_path, monkeypatch):
    fit, course = _repairable_fixture(tmp_path)
    sources = fit.read_bytes(), course.read_bytes()
    calls = []
    detector, writer = pipeline.analyze_integrity, pipeline.write_repaired_fit

    def detect(activity):
        # Detection receives only the original activity, never GPX or OSM.
        calls.append("detect")
        return detector(activity)

    def write(*args, **kwargs):
        calls.append("write")
        return writer(*args, **kwargs)

    monkeypatch.setattr(pipeline, "analyze_integrity", detect)
    monkeypatch.setattr(pipeline, "write_repaired_fit", write)
    preview = run_repair(fit, course, dry_run=True)
    assert preview.write_result is None and preview.fixed_activity is None
    assert not (tmp_path / "original.fixed.fit").exists()
    assert calls == ["detect"]
    written = run_repair(fit, course)
    assert calls == ["detect", "detect", "write"]
    assert written.plan == preview.plan
    assert written.selection == preview.selection
    assert written.write_result.post_write_verified
    assert written.write_result.diff.unexpected_changed_field_count == 0
    assert (fit.read_bytes(), course.read_bytes()) == sources
    unchanged = run_repair(written.write_result.output_path, course)
    assert unchanged.write_result is None
    assert calls == ["detect", "detect", "write", "detect"]


def test_writer_refusal_and_existing_destination_are_preserved(tmp_path, monkeypatch):
    fit, course = _repairable_fixture(tmp_path)
    output = tmp_path / "existing.fit"
    output.write_bytes(b"existing result")
    with pytest.raises(PipelineError, match="already exists") as refused:
        run_repair(fit, course, output)
    assert refused.value.code == "repair_refused"
    assert output.read_bytes() == b"existing result"

    def refuse(*args, **kwargs):
        raise FitWriteError("unsafe geometry")

    monkeypatch.setattr(pipeline, "write_repaired_fit", refuse)
    with pytest.raises(PipelineError, match="unsafe geometry"):
        run_repair(fit, course, tmp_path / "refused.fit")
    assert not (tmp_path / "refused.fit").exists()


def test_input_errors_are_stable_and_record_limit_precedes_detection(tmp_path, monkeypatch):
    fit, course = _repairable_fixture(tmp_path)
    with pytest.raises(PipelineError) as bad_fit:
        run_repair(tmp_path / "absent.fit", course)
    assert bad_fit.value.code == "invalid_fit"
    with pytest.raises(PipelineError) as bad_course:
        run_repair(fit, tmp_path / "absent.gpx")
    assert bad_course.value.code == "invalid_gpx"

    def unexpected(*args):
        pytest.fail("oversized activity must not reach detection")

    monkeypatch.setattr(pipeline, "analyze_integrity", unexpected)
    with pytest.raises(PipelineError) as oversized:
        run_repair(fit, course, config=PipelineConfig(record_limit=1))
    assert oversized.value.code == "too_many_records"


def test_optional_osm_failure_keeps_exact_gpx_output(tmp_path, monkeypatch):
    fit, course = _repairable_fixture(tmp_path)
    expected = run_repair(fit, course, tmp_path / "gpx.fit")

    def fail(*args, **kwargs):
        raise RuntimeError("companion failure")

    monkeypatch.setattr(pipeline, "run_osm_pipeline", fail)
    fallback = run_repair(fit, course, tmp_path / "fallback.fit")
    assert fallback.osm.error_code == "routing_failed"
    assert fallback.plan == expected.plan
    assert (
        fallback.write_result.output_path.read_bytes()
        == expected.write_result.output_path.read_bytes()
    )


def test_full_cli_defaults_share_policy_and_create_fit_diff(tmp_path, capsys):
    fit, course = _repairable_fixture(tmp_path)
    output = tmp_path / "cli.fit"
    args = [
        "process",
        str(fit),
        str(course),
        "--osm-mode",
        "disabled",
        "--json",
        "--output",
        str(output),
    ]
    assert main([*args, "--dry-run"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not output.exists()
    assert preview["pipeline"]["policy"] == DEFAULT_REPAIR_POLICY.as_dict()
    assert main(args) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["pipeline"] == preview["pipeline"]
    assert written["diff"]["unexpected_changed_field_count"] == 0
    assert written["repair_plan"]["selection"] == preview["selection"]


@pytest.mark.integration
def test_auto_acquisition_delegates_to_manager_and_reuses_graph_offline(
    tmp_path, monkeypatch, capsys
):
    _, _, _, config = processing_fixture(tmp_path, seed_cache=False)
    from warpbuster_osm_manager.overpass import HttpDownload, UrlLibTransport

    downloads = []

    def download(self, **kwargs):
        # Replace only HTTP transport. Real Manager coverage, ensure, validation,
        # cache publication and real Routing graph preparation remain exercised.
        downloads.append(kwargs["body"])
        data = (tmp_path / "source.osm").read_bytes()
        kwargs["destination"].write_bytes(data)
        return HttpDownload(kwargs["url"], len(data), 200)

    monkeypatch.setattr(UrlLibTransport, "download", download)
    fit, course = tmp_path / "original.fit", tmp_path / "course.gpx"
    output = tmp_path / "automatic.fit"
    args = [
        "process",
        str(fit),
        str(course),
        "--work-dir",
        str(config.data_dir),
        "--output",
        str(output),
        "--json",
    ]
    assert main(args) == 0
    automatic = json.loads(capsys.readouterr().out)
    audit = automatic["pipeline"]["osm"]["audit"]
    assert downloads
    assert audit["snapshot"]["downloaded"] is True
    assert audit["graph"]["status"] != "CACHED"
    assert any(d["status"] == "osm_selected" for d in audit["application"]["decisions"])
    assert Path(audit["snapshot"]["manifest_path"]).is_file()
    downloads.clear()
    assert main([*args, "--overwrite", "--osm-mode", "offline"]) == 0
    cached = json.loads(capsys.readouterr().out)
    assert not downloads
    assert cached["pipeline"]["osm"]["audit"]["graph"]["status"] == "CACHED"
    assert cached["pipeline"]["osm"]["audit"]["snapshot"]["downloaded"] is False
    exact = run_repair(
        fit,
        course,
        tmp_path / "exact.fit",
        config=replace(
            config,
            osm_graph_id=audit["graph"]["graph_id"],
            osm_cache_dir=config.data_dir / "osm" / "routing",
        ),
    )
    assert exact.write_result.output_path.read_bytes() == output.read_bytes()


@pytest.mark.integration
def test_osm_uses_the_same_explicit_confidence_as_gpx_and_writer(tmp_path):
    _, _, _, config = processing_fixture(tmp_path)
    fit, course = tmp_path / "original.fit", tmp_path / "course.gpx"
    high = run_repair(
        fit,
        course,
        config=config,
        policy=replace(
            DEFAULT_REPAIR_POLICY,
            minimum_confidence=IntegrityConfidence.HIGH,
        ),
    )
    assert high.write_result is None
    assert not high.selection.has_changes
    medium = run_repair(fit, course, config=config)
    assert medium.write_result is not None
    assert medium.plan.interval_plans[0].osm_provenance is not None
    assert medium.selection.minimum_confidence is DEFAULT_REPAIR_POLICY.minimum_confidence
