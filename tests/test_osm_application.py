"""Confirmed OSM fallback: route identity is never inferred from engine ranking."""

from dataclasses import replace

import pytest

from tests.local_reconstruction_factory import local_fixture
from warpbuster.config import OSMApplicationConfig
from warpbuster.fit.reader import read_fit
from warpbuster.fit.writer import write_repaired_fit
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import (
    AllocationMethod,
    CoordinateState,
    OSMRouteConfirmation,
)
from warpbuster.reconstruction import (
    apply_confirmed_osm_routes,
    build_repair_plan,
    osm_route_fingerprint,
    select_repair_intervals,
)
from warpbuster.reconstruction.osm import (
    OSMReconstructionProvider,
    RoutingAlternativesData,
    RoutingCandidateData,
)
from warpbuster.report.fit import write_result_report

GRAPH = "sha256:" + "a" * 64


class Routes:
    def __init__(self, transform=None, alternatives=1):
        self.transform = transform or (lambda start, end: (start, end))
        self.count = alternatives
        self.calls = 0

    def alternatives(self, graph_id, start, end, alternates, *, start_context=None):
        self.calls += 1
        return RoutingAlternativesData(
            "READY",
            tuple(
                RoutingCandidateData(
                    f"route-{i}",
                    "primary" if i == 0 else "alternative",
                    tuple(self.transform(start, end)),
                    {"route_id": f"route-{i}", "audit": {"edges": [1, 2]}, "warnings": []},
                )
                for i in range(self.count)
            ),
            {
                "graph": {"graph_id": graph_id, "snapshot_id": "snapshot-1"},
                "profile": {"sha256": "profile-1"},
                "search": {"exhaustive": False},
            },
        )


def fixture(tmp_path, **kwargs):
    activity, course = local_fixture(tmp_path, missing=((150, 179),), **kwargs)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    return activity, course, integrity, base, discovery


def confirm(activity, base, discovery, config=None, integrity=None, evaluation_index=0):
    evaluation = discovery.evaluations[evaluation_index]
    route = evaluation.candidates[0]
    return OSMRouteConfirmation(
        evaluation.interval.gap_id,
        route.route_id,
        osm_route_fingerprint(
            activity,
            base,
            discovery,
            evaluation.interval.gap_id,
            route.route_id,
            config=config,
            integrity_config=(integrity or analyze_integrity(activity)).config,
        ),
        "Synthetic fixture ground truth: exact travelled route reviewed",
    )


def test_confirmed_route_writes_losslessly_and_reproducibly(tmp_path):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    confirmation = confirm(activity, base, discovery)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (confirmation,))
    assert len(plan.interval_plans) == 1 and not plan.unresolved_gaps
    candidate = plan.interval_plans[0]
    assert candidate.provenance is None
    assert candidate.osm_provenance.allocation_method is AllocationMethod.RECORDED_DISTANCE
    assert candidate.osm_provenance.search_exhaustive is False
    assert candidate.preserve_recorded_distance
    assert base.coordinate_mask == plan.coordinate_mask and base.gaps == plan.gaps
    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)
    assert result.validation.valid and result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.distance_field_change_count == result.summary_field_change_count == 0
    for a, b, mask in zip(activity.records, fixed.records, base.coordinate_mask, strict=True):
        assert (a.timestamp, a.distance, a.speed, a.altitude) == (
            b.timestamp,
            b.distance,
            b.speed,
            b.altitude,
        )
        if mask.state is CoordinateState.PRESERVED:
            assert (a.latitude, a.longitude) == (b.latitude, b.longitude)
        else:
            assert b.latitude is not None and b.longitude is not None
    report = write_result_report(result)
    assert report["gap_inventory"][0]["provider"] == "osm"
    provenance = report["gap_inventory"][0]["provenance"]
    assert provenance["confirmation"]["fingerprint"] == confirmation.fingerprint
    assert "diff" in report
    assert activity.preservation.source_path.read_bytes() == activity.preservation.raw_bytes
    second = apply_confirmed_osm_routes(activity, integrity, base, discovery, (confirmation,))
    assert second == plan
    other = write_repaired_fit(activity, second, tmp_path / "second.fit")
    assert other.output_path.read_bytes() == result.output_path.read_bytes()


@pytest.mark.parametrize("route_count", [1, 2, 3])
def test_non_exhaustive_routes_are_never_implicitly_selected(tmp_path, route_count):
    activity, _, integrity, base, _ = fixture(tmp_path)
    discovery = OSMReconstructionProvider(Routes(alternatives=route_count)).discover(
        activity, base, GRAPH
    )
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery)
    for confidence in IntegrityConfidence:
        assert not select_repair_intervals(plan, confidence).selected_interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "osm_route_unconfirmed"


@pytest.mark.parametrize(
    "fault", ["duplicate", "fingerprint", "route", "evidence", "config", "audit"]
)
def test_conflicting_or_stale_confirmation_cannot_bypass_gates(tmp_path, fault):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    c = confirm(activity, base, discovery)
    config = OSMApplicationConfig()
    confirmations = (c,)
    if fault == "duplicate":
        confirmations = (c, c)
    elif fault == "fingerprint":
        confirmations = (replace(c, fingerprint="old"),)
    elif fault == "route":
        confirmations = (replace(c, route_id="other"),)
    elif fault == "evidence":
        confirmations = (replace(c, evidence=" "),)
    elif fault == "config":
        config = replace(config, maximum_connector_m=1.0)
    elif fault == "audit":
        e = replace(discovery.evaluations[0], _routing_document_json='{"changed":true}')
        discovery = replace(discovery, evaluations=(e,))
    plan = apply_confirmed_osm_routes(
        activity, integrity, base, discovery, confirmations, config=config
    )
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == (
        "osm_confirmation_conflict" if fault == "duplicate" else "osm_confirmation_stale"
    )


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("connector", "osm_connector_too_long"),
        ("invalid", "osm_geometry_invalid"),
        ("distance", "local_distance_inconsistent"),
        ("limit", "search_limit_reached"),
        ("speed", "active_time_traversal_implausible"),
    ],
)
def test_confirmed_identity_still_requires_physical_checks(tmp_path, fault, reason):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    config = OSMApplicationConfig()
    if fault in {"connector", "invalid", "distance"}:
        transform = {
            "connector": lambda s, e: ((s[0] + 0.01, s[1]), e),
            "invalid": lambda s, e: ((91.0, s[1]), e),
            "distance": lambda s, e: (s, (s[0] + 0.0005, s[1]), e),
        }[fault]
        discovery = OSMReconstructionProvider(Routes(transform)).discover(activity, base, GRAPH)
    if fault == "limit":
        config = replace(config, maximum_gap_records=29)
    if fault == "speed":
        config = replace(config, maximum_speed_mps=1.0)
    c = confirm(activity, base, discovery, config)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,), config=config)
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == reason


def test_collect_both_sources_and_apply_confirmed_osm_to_other_gap(tmp_path):
    activity, course = local_fixture(tmp_path, missing=((150, 179), (400, 429)))
    # Supply GPX only for the first local region; the second remains unresolved.
    segment = course.segments[0]
    course = replace(course, segments=(replace(segment, points=segment.points[:300]),))
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity, course, fill_missing_from_course=True)
    assert len(base.interval_plans) == 1
    client = Routes()
    discovery = OSMReconstructionProvider(client).discover(activity, base, GRAPH)
    assert client.calls == 2
    assert all(item.candidates for item in discovery.evaluations)
    c = confirm(activity, base, discovery, evaluation_index=1)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    assert plan.interval_plans[0] is base.interval_plans[0]
    assert plan.interval_plans[1].osm_provenance is not None
    result = write_repaired_fit(activity, plan, minimum_confidence=IntegrityConfidence.MEDIUM)
    assert all(r.latitude is not None for r in read_fit(result.output_path).records)
    assert result.diff.unexpected_changed_field_count == 0


@pytest.mark.parametrize(
    "field",
    [
        "maximum_connector_m",
        "maximum_speed_mps",
        "distance_absolute_tolerance_m",
        "distance_relative_tolerance",
        "maximum_gap_records",
    ],
)
@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_application_config_rejects_invalid_bounds(field, value):
    with pytest.raises(ValueError):
        OSMApplicationConfig(**{field: value})


def test_absent_fit_coordinate_fields_are_supported(tmp_path):
    activity, _, integrity, base, discovery = fixture(tmp_path, position_fields=False)
    c = confirm(activity, base, discovery)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    result = write_repaired_fit(activity, plan)
    assert result.diff.added_coordinate_field_count == 60
    assert result.diff.unexpected_changed_field_count == 0


@pytest.mark.parametrize("method", ["distance", "speed", "time"])
def test_pauses_allocate_by_active_time_and_preserve_events(tmp_path, method):
    from tests.test_pause_reconstruction import _fixture

    activity, _ = _fixture(tmp_path, method=method, records_in_pause=True)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    plan = apply_confirmed_osm_routes(
        activity, integrity, base, discovery, (confirm(activity, base, discovery),)
    )
    candidate = plan.interval_plans[0]
    assert (
        candidate.osm_provenance.allocation_method
        is {
            "distance": AllocationMethod.RECORDED_DISTANCE,
            "speed": AllocationMethod.RECORDED_SPEED,
            "time": AllocationMethod.TIMESTAMPS,
        }[method]
    )
    assert candidate.osm_provenance.timing.paused_seconds == 300
    assert (
        len(
            {
                (u.candidate_latitude, u.candidate_longitude)
                for u in candidate.coordinate_updates
                if 160 <= u.record_index <= 460
            }
        )
        == 1
    )
    result = write_repaired_fit(activity, plan)
    assert read_fit(result.output_path).events == activity.events
    assert result.diff.timestamps.percentage == 100
    if method != "distance":
        assert write_result_report(result)["distance"]["unresolved_distance_signal"] is True


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("open", "timer_state_unresolved"),
        ("whole", "no_active_time"),
        ("conflict", "pause_distance_conflict"),
        ("timestamp", "timing_unusable"),
        ("speed_signal", "local_distance_inconsistent"),
    ],
)
def test_unusable_timing_and_conflicting_signals_refuse(tmp_path, fault, reason):
    from tests.test_pause_reconstruction import _events

    activity, _, _, _, _ = fixture(tmp_path)
    if fault in {"open", "whole", "conflict"}:
        events = {
            "open": [(160, "stop_all", 0)],
            "whole": [(149, "stop_all", 0), (180, "start", 0)],
            "conflict": [(160, "stop_all", 0), (170, "start", 0)],
        }[fault]
        activity = _events(activity, [(0, "start", 0), *events])
    else:
        records = list(activity.records)
        if fault == "timestamp":
            records[160] = replace(records[160], timestamp=records[159].timestamp)
        else:
            records = [replace(r, speed=5.0) for r in records]
        activity = replace(activity, records=tuple(records))
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    confirmations = tuple(
        confirm(activity, base, discovery, evaluation_index=i)
        for i, e in enumerate(discovery.evaluations)
        if e.candidates
    )
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, confirmations)
    assert not plan.interval_plans
    assert reason in {r.value for f in plan.unresolved_gaps for r in f.reasons}


@pytest.mark.parametrize(
    "missing,spikes,origin",
    [
        ((), (150,), "invalidated"),
        (((151, 160),), (150,), "mixed"),
    ],
)
def test_only_independently_invalidated_or_missing_records_are_filled(
    tmp_path,
    missing,
    spikes,
    origin,
):
    activity, _ = local_fixture(tmp_path, missing=missing, spikes=spikes)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    plan = apply_confirmed_osm_routes(
        activity, integrity, base, discovery, (confirm(activity, base, discovery),)
    )
    assert plan.interval_plans[0].interval.origin.value == origin
    result = write_repaired_fit(activity, plan)
    fixed = read_fit(result.output_path)
    for mask, before, after in zip(
        plan.coordinate_mask, activity.records, fixed.records, strict=True
    ):
        if mask.state is CoordinateState.PRESERVED:
            assert before.latitude == after.latitude and before.longitude == after.longitude
    assert result.diff.unexpected_changed_field_count == 0
    assert analyze_integrity(activity) == integrity


def test_prefix_suffix_and_stale_mask_never_become_osm_candidates(tmp_path):
    activity, _ = local_fixture(tmp_path)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    c = confirm(activity, base, discovery, evaluation_index=1)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    assert len(plan.interval_plans) == 1 and len(plan.unresolved_gaps) == 2
    assert all(f.reasons[0].value == "no_trusted_local_anchor" for f in plan.unresolved_gaps)
    with pytest.raises(ValueError, match="snapshot"):
        apply_confirmed_osm_routes(
            activity, integrity, replace(base, coordinate_mask=base.coordinate_mask[:-1]), discovery
        )


def test_real_sensor_developer_and_unknown_payload_preservation(tmp_path):
    import struct

    from tests.fit_factory import write_repairable_activity
    from tests.test_fit_coordinate_extension import _container

    path = tmp_path / "payload.fit"
    raw = write_repairable_activity(path)
    # Add a native field with unknown semantics in an extra record, and an unknown
    # message. Its coordinates/timestamp are absent: terminal gap stays unresolved.
    body = raw[raw[0] : -2]
    body += bytes((0x40, 0, 0, 20, 0, 1, 250, 2, 0x84)) + b"\x00" + struct.pack("<H", 43210)
    body += bytes((0x40, 0, 0)) + struct.pack("<H", 65000) + bytes((1, 1, 2, 0x84))
    body += b"\x00" + struct.pack("<H", 54321)
    path.write_bytes(_container(body))
    activity = read_fit(path)
    assert activity.unknown_fields and activity.developer_fields
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    discovery = OSMReconstructionProvider(Routes()).discover(activity, base, GRAPH)
    confirmations = tuple(
        confirm(activity, base, discovery, evaluation_index=i)
        for i, e in enumerate(discovery.evaluations)
        if e.candidates
    )
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, confirmations)
    assert plan.interval_plans
    assert plan.interval_plans[0].osm_provenance.distance_signal_status == "implausible"
    result = write_repaired_fit(activity, plan)
    assert result.diff.developer_fields.percentage == result.diff.unknown_fields.percentage == 100
    assert result.diff.sensors.percentage == result.diff.timestamps.percentage == 100
    assert result.diff.unexpected_changed_field_count == 0
    fixed = read_fit(result.output_path)
    for before, after in zip(
        activity.preservation.messages, fixed.preservation.messages, strict=True
    ):
        if (
            before.message_type == "record"
            and before.fields.get("timestamp") == activity.records[16].timestamp
        ):
            continue
        assert before.raw_chunk == after.raw_chunk


def test_html_has_confirmed_provider_provenance_and_fit_diff(tmp_path):
    from warpbuster.config import CourseReconstructionConfig
    from warpbuster.report.html import write_repair_html

    activity, _, integrity, base, discovery = fixture(tmp_path)
    c = confirm(activity, base, discovery)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    result = write_repaired_fit(activity, plan)
    output = tmp_path / "osm.html"
    write_repair_html(
        activity,
        integrity,
        None,
        result.plan,
        CourseReconstructionConfig(),
        output,
        write_result=result,
        fixed_activity=read_fit(result.output_path),
    )
    html = output.read_text()
    assert c.fingerprint in html
    assert "caller_confirmed_route" in html
    assert "source_unverified" in html
    assert "unexpected_changed_field_count" in html


def test_full_route_chainage_not_chord_drives_loop_allocation(tmp_path):
    activity, _, _, _, _ = fixture(tmp_path)
    activity = replace(
        activity, records=tuple(replace(r, distance=None, speed=None) for r in activity.records)
    )
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)

    def transform(s, e):
        return (s, (s[0] + 0.0001, s[1]), s, e)

    discovery = OSMReconstructionProvider(Routes(transform)).discover(activity, base, GRAPH)
    plan = apply_confirmed_osm_routes(
        activity, integrity, base, discovery, (confirm(activity, base, discovery),)
    )
    candidate = plan.interval_plans[0]
    assert candidate.osm_provenance.allocation_method is AllocationMethod.TIMESTAMPS
    assert candidate.reconstruction_path_distance_m > 80
    assert max(u.candidate_latitude for u in candidate.coordinate_updates) > (
        activity.records[149].latitude + 0.00005
    )


def test_confirmation_fingerprint_binds_normalized_source_and_fit_bytes(tmp_path):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    c = confirm(activity, base, discovery)
    records = list(activity.records)
    records[160] = replace(records[160], speed=3.0)
    activity = replace(activity, records=tuple(records))
    assert confirm(activity, base, discovery, integrity=integrity).fingerprint != c.fingerprint
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    assert plan.unresolved_gaps[0].reasons[0].value == "osm_confirmation_stale"


def test_unrecognized_native_coordinate_semantics_refuse(tmp_path):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    messages = list(activity.preservation.messages)
    index = activity.records[160].source.message_index
    message = messages[index]
    messages[index] = replace(message, fields={**message.fields, 0: 123})
    activity = replace(
        activity, preservation=replace(activity.preservation, messages=tuple(messages))
    )
    c = confirm(activity, base, discovery)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    assert not plan.interval_plans
    assert plan.unresolved_gaps[0].reasons[0].value == "position_fields_unpatchable"


def test_default_config_is_explicit_and_stable():
    config = OSMApplicationConfig()
    assert (config.maximum_connector_m, config.maximum_speed_mps) == (5.0, 12.0)
    assert (config.distance_absolute_tolerance_m, config.distance_relative_tolerance) == (5.0, 0.05)
    assert config.maximum_gap_records == 10_000
    with pytest.raises(ValueError):
        replace(config, distance_relative_tolerance=1.01)


def test_route_geometry_and_source_bytes_change_review_fingerprint(tmp_path):
    activity, _, integrity, base, discovery = fixture(tmp_path)
    original = confirm(activity, base, discovery, integrity=integrity).fingerprint
    evaluation = discovery.evaluations[0]
    route = evaluation.candidates[0]
    point = replace(route.coordinates[0], latitude=route.coordinates[0].latitude + 0.000001)
    route = replace(route, coordinates=(point, *route.coordinates[1:]))
    changed = replace(discovery, evaluations=(replace(evaluation, candidates=(route,)),))
    assert confirm(activity, base, changed, integrity=integrity).fingerprint != original
    activity = replace(
        activity,
        preservation=replace(
            activity.preservation, raw_bytes=activity.preservation.raw_bytes + b"changed"
        ),
    )
    assert confirm(activity, base, discovery, integrity=integrity).fingerprint != original


@pytest.mark.integration
def test_native_valhalla_confirmed_route_reaches_atomic_fit_writer(tmp_path):
    from math import cos, radians

    from tests.fit_factory import write_trajectory_activity
    from tests.test_osm_reconstruction_native import _forked_osm, _manifest
    from warpbuster.reconstruction.osm import ValhallaRoutingClient

    routing = pytest.importorskip("warpbuster_osm_routing")
    scale = 111_195.0 * cos(radians(44.0))
    path = tmp_path / "native.fit"
    write_trajectory_activity(
        path,
        [
            (i, None, None) if 21 <= i <= 379 else (i, 44.0, 33.0 + i * 2 / scale)
            for i in range(401)
        ],
        altitudes_m=[100.0] * 401,
    )
    activity = read_fit(path)
    integrity = analyze_integrity(activity)
    base = build_repair_plan(activity, integrity)
    cache = tmp_path / "cache"
    graph = routing.GraphCache(
        routing.RoutingCacheConfig.defaults().with_cache_directory(cache)
    ).prepare(_manifest(tmp_path, _forked_osm()))
    discovery = OSMReconstructionProvider(ValhallaRoutingClient(cache_directory=cache)).discover(
        activity, base, graph.graph_id
    )
    assert discovery.candidate_count >= 2
    assert not apply_confirmed_osm_routes(activity, integrity, base, discovery).interval_plans
    c = confirm(activity, base, discovery)
    plan = apply_confirmed_osm_routes(activity, integrity, base, discovery, (c,))
    assert len(plan.interval_plans) == 1
    provenance = plan.interval_plans[0].osm_provenance
    assert provenance.graph_id == graph.graph_id
    assert "native-core-test" in provenance.routing_document_json
    assert "warpbuster-trail-running-v1" in provenance.routing_document_json
    result = write_repaired_fit(activity, plan)
    assert result.validation.valid and result.validation.crc_valid
    assert result.diff.unexpected_changed_field_count == 0
    assert result.diff.timestamps.percentage == result.diff.sensors.percentage == 100
    assert result.diff.added_coordinate_field_count == 718
    assert not analyze_integrity(read_fit(result.output_path)).corrupted_intervals
