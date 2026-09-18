"""021D public report contains only approved approximate-selection evidence."""

import json

from tests.test_altitude_completion import SNAPSHOT, FakeSampler
from tests.test_automatic_osm import discover, fixture
from warpbuster.pipeline import DEMMode, OSMMode
from warpbuster.pipeline import repair as pipeline
from warpbuster.pipeline.dem import DEMResult
from warpbuster.pipeline.osm import OSMResult
from warpbuster.reconstruction.altitude_completion import plan_altitude_completion
from warpbuster.reconstruction.approximate_contract import ApproximateSelectionPolicy
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes
from warpbuster_web.config import WebConfig
from warpbuster_web.processing import process_job


def test_web_approximate_report_is_versioned_and_privacy_allowlisted(tmp_path, monkeypatch):
    activity, _, _, _, _ = fixture(tmp_path)
    source = tmp_path / "activity.fit"
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "original.fit").write_bytes(source.read_bytes())
    (directory / "course.gpx").write_text(
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg><trkpt lat="45" lon="34"/><trkpt lat="45.001" lon="34.001"/>'
        "</trkseg></trk></gpx>"
    )

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            minimum_confidence=policy.minimum_confidence,
            approximate_policy=ApproximateSelectionPolicy(),
        )
        return OSMResult(plan, "complete", discovery=discovery)

    monkeypatch.setattr(pipeline, "run_osm_pipeline", osm_stage)
    config = WebConfig(
        data_dir=directory,
        osm_mode=OSMMode.OFFLINE,
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        dem_snapshot_id="sha256:" + "a" * 64,
    )
    assert process_job(directory, 100_000, config=config)
    report = json.loads((directory / "result.json").read_text())
    assert report["schema_version"] == 5
    assert report["fit_diff"]["timestamps_unchanged"]
    assert report["fit_diff"]["sensors_unchanged"]
    assert report["approximate_osm"]["policy_id"] == "approximate-original-missing-osm-v1"
    assert report["approximate_osm"]["dem"]["status"] == "unavailable"
    assert report["approximate_osm"]["decisions"][0]["selected_route_id"] == "route-0"
    serialized = json.dumps(report)
    assert str(activity.preservation.source_path) not in serialized
    assert "altitude_observations" not in serialized
    assert "native_stderr" not in serialized


def test_web_altitude_completion_reports_counts_without_raw_heights(tmp_path, monkeypatch):
    fixture(tmp_path, include_altitude=False)
    directory = tmp_path / "web-altitude"
    directory.mkdir()
    (directory / "original.fit").write_bytes((tmp_path / "activity.fit").read_bytes())
    (directory / "course.gpx").write_text(
        '<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        '<trk><trkseg><trkpt lat="45" lon="34"/><trkpt lat="45.001" lon="34.001"/>'
        "</trkseg></trk></gpx>"
    )

    def osm_stage(activity, integrity, base, config, *, policy):
        discovery = discover(activity, base)
        plan = apply_automatic_osm_routes(
            activity,
            integrity,
            base,
            discovery,
            minimum_confidence=policy.minimum_confidence,
            approximate_policy=ApproximateSelectionPolicy(),
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
    config = WebConfig(
        data_dir=directory,
        osm_mode=OSMMode.OFFLINE,
        approximate_osm=True,
        dem_mode=DEMMode.OFFLINE,
        complete_missing_altitude=True,
    )
    assert process_job(directory, 100_000, config=config)
    report = json.loads((directory / "result.json").read_text())
    assert report["altitude_completion"]["status"] == "applied"
    assert report["altitude_completion"]["completed_records"] == 30
    assert report["fit_diff"]["altitude_fields"] == 30
    assert report["fit_diff"]["non_altitude_sensors_unchanged"]
    assert not report["fit_diff"]["sensors_unchanged"]
    assert all(
        change["field"] not in {"altitude", "enhanced_altitude"}
        for change in report["fit_diff"]["changes"]
    )
    assert "raw_elevation_m" not in json.dumps(report)
