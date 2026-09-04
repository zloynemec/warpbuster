"""Task 006B one-sided GNSS failure detection and reconstruction tests."""

from dataclasses import replace
from math import cos, radians
from pathlib import Path
from time import perf_counter

from tests.activity_factory import Observation, eastward_observations, make_activity
from tests.gpx_factory import GpxPoint, write_gpx_activity
from warpbuster.config import CourseReconstructionConfig, IntegrityConfig
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import (
    IntegrityConfidence,
    IntervalDetectionKind,
    IntervalReason,
    OneSidedClusterReason,
)
from warpbuster.models.reconstruction import (
    CoordinateState,
    GapRepairPlan,
    RepairIntervalAction,
)
from warpbuster.reconstruction import build_repair_plan, select_repair_intervals
from warpbuster.report.analyze import analyze_report
from warpbuster.report.repair import repair_report


def test_missing_exit_cluster_has_medium_course_independent_interval() -> None:
    """Impossible entry plus two tainted components and dropout prove one bounded cluster."""
    activity = make_activity(_one_sided_observations())

    report = analyze_integrity(activity, _detector_config())

    assert len(report.corrupted_intervals) == 1
    interval = report.corrupted_intervals[0]
    assert (interval.start_record_index, interval.end_record_index) == (6, 17)
    assert (interval.trusted_before_record_index, interval.trusted_after_record_index) == (5, 18)
    assert interval.exit_transition is None
    assert interval.detection_kind is IntervalDetectionKind.ONE_SIDED_CLUSTER
    assert interval.confidence is IntegrityConfidence.MEDIUM
    assert interval.reasons == (
        IntervalReason.IMPOSSIBLE_TRANSITION_IN,
        IntervalReason.MISSING_EXIT_BOUNDARY,
        IntervalReason.STABLE_OUTER_ANCHORS,
        IntervalReason.TAINTED_POSITION_COMPONENTS,
        IntervalReason.PLAUSIBLE_BRIDGE,
    )
    cluster = report.one_sided_search_diagnostics.retained_clusters[0]
    assert cluster.reconstructable is True
    assert cluster.missing_position_record_count == 7
    assert cluster.positioned_component_count == 2
    assert cluster.tainted_positioned_component_count == 2
    assert cluster.anchor_before_normal_transition_count >= 3
    assert cluster.anchor_after_normal_transition_count >= 3
    assert cluster.bridge is not None
    assert cluster.bridge.apparent_speed_mps < cluster.bridge.maximum_plausible_speed_mps


def test_untainted_plausible_component_stays_unresolved() -> None:
    """A clean component inside the bounds blocks interval creation and coordinate repair."""
    observations = _one_sided_observations()
    observations[10:13] = eastward_observations([10.0, 11.0, 12.0], [90.0, 93.0, 96.0])

    report = analyze_integrity(make_activity(observations), _detector_config())

    assert report.corrupted_intervals == ()
    cluster = report.one_sided_search_diagnostics.retained_clusters[0]
    assert cluster.reconstructable is False
    assert cluster.positioned_component_count == 2
    assert cluster.tainted_positioned_component_count == 1
    assert OneSidedClusterReason.UNTAINTED_POSITION_COMPONENT in cluster.reasons


def test_abnormal_transition_across_dropout_does_not_taint_clean_component() -> None:
    """A non-adjacent jump across missing records is not proof about the next component."""
    observations = _one_sided_observations()
    observations[10:13] = eastward_observations([10.0, 11.0, 12.0], [50.0, 53.0, 56.0])

    report = analyze_integrity(make_activity(observations), _detector_config())

    assert report.corrupted_intervals == ()
    cluster = report.one_sided_search_diagnostics.retained_clusters[0]
    assert cluster.suspicious_transition_count >= 1
    assert cluster.tainted_positioned_component_count == 1
    assert OneSidedClusterReason.UNTAINTED_POSITION_COMPONENT in cluster.reasons


def test_one_sided_cluster_never_crosses_continuity_boundary() -> None:
    """A segment boundary invalidates the after anchor even when local context is normal."""
    activity = make_activity(_one_sided_observations())
    activity = replace(
        activity,
        records=tuple(
            replace(record, continuity_id=1) if record.index >= 18 else record
            for record in activity.records
        ),
    )

    report = analyze_integrity(activity, _detector_config())

    assert report.corrupted_intervals == ()
    cluster = report.one_sided_search_diagnostics.retained_clusters[0]
    assert cluster.reconstructable is False
    assert OneSidedClusterReason.CONTINUITY_BOUNDARY in cluster.reasons


def test_ordinary_dropout_does_not_create_one_sided_candidate() -> None:
    """Missing GNSS without an impossible entry remains UNKNOWN, not corrupted."""
    observations = eastward_observations([0.0, 1.0, 2.0], [0.0, 3.0, 6.0])
    observations.extend((float(index), None, None) for index in range(3, 12))
    observations.extend(eastward_observations([12.0, 13.0, 14.0], [36.0, 39.0, 42.0]))

    report = analyze_integrity(make_activity(observations), _detector_config())

    assert report.corrupted_intervals == ()
    assert report.one_sided_search_diagnostics.impossible_entries_considered == 0
    assert report.one_sided_search_diagnostics.retained_clusters == ()


def test_medium_one_sided_candidate_requires_explicit_medium_selection(tmp_path: Path) -> None:
    """Course reconstruction is available, but the default HIGH policy skips it."""
    activity = make_activity(_one_sided_observations())
    integrity = analyze_integrity(activity, _detector_config())
    course_path = tmp_path / "course.gpx"
    points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in eastward_observations(
            [float(index) for index in range(25)],
            [float(index * 3) for index in range(25)],
            latitude=55.00009,
        )
        if latitude is not None and longitude is not None
    ]
    write_gpx_activity(course_path, [points])
    plan = build_repair_plan(
        activity,
        integrity,
        read_gpx_course(course_path),
        CourseReconstructionConfig(
            anchor_match_tolerance_m=75.0,
            high_confidence_anchor_distance_m=50.0,
            minimum_course_span_m=1.0,
            anchor_stability_min_normal_transitions=3,
            anchor_stability_scan_max_records=5,
            one_sided_drift_stable_record_count=3,
        ),
        fill_missing_from_course=True,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )

    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    assert isinstance(candidate, GapRepairPlan)
    assert candidate.confidence is IntegrityConfidence.MEDIUM
    assert candidate.repair_eligible is False
    assert candidate.provenance is not None
    assert candidate.provenance.connector_distance_m > 15.0
    assert candidate.reconstruction_path_distance_m > candidate.provenance.course_span_distance_m
    assert (candidate.interval.start_record_index, candidate.interval.end_record_index) == (6, 17)
    default_selection = select_repair_intervals(plan)
    medium_selection = select_repair_intervals(plan, IntegrityConfidence.MEDIUM)
    assert default_selection.decisions[0].action is RepairIntervalAction.SKIPPED
    assert medium_selection.decisions[0].action is RepairIntervalAction.APPLIED
    assert len(medium_selection.selected_interval_plans) == 1


def test_abnormal_distance_allocation_falls_back_to_smooth_timestamps(tmp_path: Path) -> None:
    """A corrupted distance step cannot reintroduce an impossible candidate transition."""
    activity = make_activity(_one_sided_observations())
    embedded = [float(index) for index in range(len(activity.records))]
    embedded[5:19] = [
        0.0,
        1.0,
        2.0,
        3.0,
        4.0,
        5.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
    ]
    activity = replace(
        activity,
        records=tuple(
            replace(record, distance=embedded[record.index]) for record in activity.records
        ),
    )
    integrity = analyze_integrity(activity, _detector_config())
    course_path = tmp_path / "parallel.gpx"
    points: list[GpxPoint] = [
        (latitude, longitude, None, None)
        for _elapsed, latitude, longitude in eastward_observations(
            [float(index) for index in range(25)],
            [float(index * 3) for index in range(25)],
            latitude=55.00009,
        )
        if latitude is not None and longitude is not None
    ]
    write_gpx_activity(course_path, [points])

    plan = build_repair_plan(
        activity,
        integrity,
        read_gpx_course(course_path),
        CourseReconstructionConfig(
            minimum_course_span_m=1.0,
            anchor_stability_min_normal_transitions=3,
            anchor_stability_scan_max_records=5,
            one_sided_drift_stable_record_count=3,
        ),
        fill_missing_from_course=True,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )

    assert len(plan.interval_plans) == 1
    candidate = plan.interval_plans[0]
    assert isinstance(candidate, GapRepairPlan)
    assert candidate.provenance is not None
    assert candidate.provenance.allocation_method.value == "timestamps"
    assert "distance_implausible" in candidate.provenance.signal_diagnostics


def test_course_corridor_never_expands_independently_proven_scope(tmp_path: Path) -> None:
    """Physically plausible lateral movement outside the proof must remain untouched."""
    observations = _one_sided_observations()
    observations.extend(eastward_observations([23.0, 24.0], [69.0, 72.0]))
    metres_per_latitude_degree = 111_195.0
    lateral_offsets_m = {3: 5.0, 4: 10.0, 5: 20.0, 18: 20.0, 19: 10.0, 20: 5.0}
    observations = [
        (
            elapsed,
            latitude + lateral_offsets_m.get(index, 0.0) / metres_per_latitude_degree
            if latitude is not None
            else None,
            longitude,
        )
        for index, (elapsed, latitude, longitude) in enumerate(observations)
    ]
    activity = make_activity(observations)
    integrity = analyze_integrity(
        activity,
        replace(
            _detector_config(),
            relative_suspicious_speed_floor_mps=15.0,
            relative_speed_multiplier=2.0,
            relative_mad_multiplier=2.0,
        ),
    )
    course_path = tmp_path / "course.gpx"
    metres_per_longitude_degree = 111_195.0 * cos(radians(55.0))
    write_gpx_activity(
        course_path,
        [
            [
                (55.0, 37.0 + index * 3.0 / metres_per_longitude_degree, None, None)
                for index in range(25)
            ]
        ],
    )

    course = read_gpx_course(course_path)
    reconstruction_config = CourseReconstructionConfig(
        minimum_course_span_m=1.0,
        anchor_stability_min_normal_transitions=3,
        anchor_stability_scan_max_records=5,
        one_sided_drift_corridor_tolerance_m=6.0,
        one_sided_drift_stable_record_count=3,
        one_sided_drift_search_max_records=20,
    )
    plan = build_repair_plan(
        activity,
        integrity,
        course,
        reconstruction_config,
        fill_missing_from_course=True,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    without_course = build_repair_plan(
        activity,
        integrity,
        minimum_invalidation_confidence=IntegrityConfidence.MEDIUM,
    )
    assert plan.coordinate_mask == without_course.coordinate_mask
    assert {(gap.start_record_index, gap.end_record_index) for gap in plan.gaps} == {(6, 17)}
    assert all(
        item.state is CoordinateState.PRESERVED
        for item in plan.coordinate_mask
        if item.record_index < 6 or item.record_index > 17
    )
    assert all(
        6 <= update.record_index <= 17
        for candidate in plan.interval_plans
        for update in candidate.coordinate_updates
    )
    rendered = repair_report(
        plan,
        course,
        reconstruction_config,
        minimum_confidence=IntegrityConfidence.MEDIUM,
    )
    assert rendered["gap_inventory"][0]["start_record_index"] == 6
    assert rendered["gap_inventory"][0]["end_record_index"] == 17


def test_json_exposes_one_sided_proof_and_rejection_reasons() -> None:
    """Machine-readable diagnostics retain evidence even when a candidate is accepted."""
    activity = make_activity(_one_sided_observations())
    integrity = analyze_integrity(activity, _detector_config())

    report = analyze_report(activity, integrity)

    diagnostics = report["one_sided_search_diagnostics"]
    assert isinstance(diagnostics, dict)
    clusters = diagnostics["retained_clusters"]
    assert isinstance(clusters, list)
    assert clusters[0]["start_record_index"] == 6
    assert clusters[0]["end_record_index"] == 17
    assert clusters[0]["reconstructable"] is True
    assert clusters[0]["bridge"]["plausible"] is True
    assert "all_position_components_tainted" in clusters[0]["reasons"]


def test_one_sided_scan_remains_bounded_with_many_impossible_entries() -> None:
    """The added search stays under the existing 20k-record performance target."""
    count = 20_000
    base = eastward_observations(
        [float(index) for index in range(count)],
        [float(index * 3) for index in range(count)],
    )
    observations = [
        observation if index % 2 == 0 else (observation[0], 56.0, observation[2])
        for index, observation in enumerate(base)
    ]
    observations[-50] = (float(count - 50), None, None)
    config = replace(
        IntegrityConfig.running(),
        one_sided_search_max_records=64,
        one_sided_max_diagnostics=10,
    )

    started = perf_counter()
    report = analyze_integrity(make_activity(observations), config)
    elapsed = perf_counter() - started

    assert len(report.one_sided_search_diagnostics.retained_clusters) <= 10
    assert report.one_sided_search_diagnostics.records_scanned <= (
        report.one_sided_search_diagnostics.impossible_entries_considered * 64
    )
    assert elapsed < 5.0


def _detector_config() -> IntegrityConfig:
    return replace(
        IntegrityConfig.running(),
        one_sided_search_max_records=64,
        one_sided_max_clean_gap_records=5,
        one_sided_anchor_min_normal_transitions=3,
        one_sided_anchor_scan_max_records=5,
    )


def _one_sided_observations() -> list[Observation]:
    observations = eastward_observations(
        [float(index) for index in range(6)],
        [float(index * 3) for index in range(6)],
    )
    observations.extend(eastward_observations([6.0, 7.0], [85.0, 115.0]))
    observations.extend([(8.0, None, None), (9.0, None, None)])
    observations.extend(eastward_observations([10.0, 11.0, 12.0], [90.0, 120.0, 123.0]))
    observations.extend((float(index), None, None) for index in range(13, 18))
    observations.extend(
        eastward_observations(
            [float(index) for index in range(18, 23)],
            [float(index * 3) for index in range(18, 23)],
        )
    )
    return observations
