"""Course-independent trusted-anchor safety tests."""

from inspect import signature
from pathlib import Path

from tests.activity_factory import eastward_observations, make_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.gpx.course import read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityConfidence, TransitionClassification
from warpbuster.models.reconstruction import ReconstructionReason
from warpbuster.reconstruction import build_course_repair_plan
from warpbuster.reconstruction.safety import assess_interval_safety
from warpbuster.report.repair import repair_console, repair_report


def test_single_spike_has_stable_directional_anchor_context() -> None:
    """Normal movement before and after an isolated jump supports both anchors."""
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0, 2.0], [0.0, 3.0, 6.0]),
            (3.0, 56.0, 37.0),
            *eastward_observations([4.0, 5.0, 6.0], [12.0, 15.0, 18.0]),
        ]
    )
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[0],
        _safety_config(),
    )

    assert safety.anchors_stable is True
    assert safety.anchor_before.consecutive_normal_transition_count == 2
    assert safety.anchor_after.consecutive_normal_transition_count == 2
    assert safety.mixed_region is None


def test_nearby_impossible_transition_blocks_before_anchor() -> None:
    """An anchor inside another jump cluster is not trusted as normal context."""
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0], [0.0, 3.0]),
            (2.0, 56.0, 37.0),
            *eastward_observations([3.0, 4.0], [9.0, 12.0]),
            (5.0, 56.0, 37.0),
            *eastward_observations([6.0, 7.0, 8.0], [18.0, 21.0, 24.0]),
        ]
    )
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[1],
        _safety_config(),
    )

    assert safety.anchor_before.stable is False
    assert safety.anchor_before.consecutive_normal_transition_count == 1
    assert safety.anchor_before.blocking_classification is TransitionClassification.IMPOSSIBLE
    assert safety.anchor_after.stable is True


def test_missing_position_blocks_insufficient_after_context() -> None:
    """A nearby dropout cannot be crossed to manufacture a stable anchor window."""
    activity = make_activity(
        [
            *eastward_observations([0.0, 1.0, 2.0], [0.0, 3.0, 6.0]),
            (3.0, 56.0, 37.0),
            *eastward_observations([4.0, 5.0], [12.0, 15.0]),
            (6.0, None, None),
            *eastward_observations([7.0], [21.0]),
        ]
    )
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[0],
        _safety_config(),
    )

    assert safety.anchor_before.stable is True
    assert safety.anchor_after.stable is False
    assert safety.anchor_after.blocking_record_index == 6
    assert ReconstructionReason.MISSING_POSITION_CONTEXT in safety.anchor_after.reasons


def test_mixed_region_finds_stable_outer_anchors_but_is_not_repairable() -> None:
    """Clustered jumps and a dropout yield bounded MEDIUM diagnostics, never auto-repair."""
    activity = _mixed_region_activity(include_distant_spike=False)
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[1],
        _safety_config(),
    )
    region = safety.mixed_region

    assert region is not None
    assert (region.start_record_index, region.end_record_index) == (3, 9)
    assert (
        region.proposed_trusted_before_record_index,
        region.proposed_trusted_after_record_index,
    ) == (2, 10)
    assert region.missing_position_record_count == 1
    assert region.impossible_transition_count == 4
    assert region.outer_anchor_before is not None and region.outer_anchor_before.stable
    assert region.outer_anchor_after is not None and region.outer_anchor_after.stable
    assert region.bridge_plausible is True
    assert region.confidence is IntegrityConfidence.MEDIUM
    assert region.repair_eligible is False
    assert region.reconstructable is True
    assert region.all_positioned_components_tainted is True
    assert len(region.components) == 2
    assert ReconstructionReason.MIXED_REGION_REQUIRES_REVIEW in region.reasons


def test_distant_anomaly_is_not_joined_to_mixed_region() -> None:
    """Evidence beyond the configured clean gap cannot inflate one mixed region."""
    activity = _mixed_region_activity(include_distant_spike=True)
    integrity = analyze_integrity(activity)

    safety = assess_interval_safety(
        activity,
        integrity,
        integrity.corrupted_intervals[1],
        _safety_config(),
    )

    assert safety.mixed_region is not None
    assert safety.mixed_region.end_record_index == 9


def test_safety_boundary_has_no_course_parameter() -> None:
    """Trusted-anchor validation cannot accidentally depend on GPX geometry."""
    assert tuple(signature(assess_interval_safety).parameters) == (
        "activity",
        "integrity",
        "interval",
        "config",
    )


def test_local_gap_evidence_is_exposed_without_composite_write_envelopes(tmp_path: Path) -> None:
    """Diagnostic envelopes remain course-free; the report audits exact editable holes."""
    activity = _mixed_region_activity(include_distant_spike=False)
    integrity = analyze_integrity(activity)
    course_path = tmp_path / "irrelevant.gpx"
    write_gpx_activity(course_path, [[(55.0, 37.0, None, None), (55.0, 37.001, None, None)]])
    course = read_gpx_course(course_path)
    plan = build_course_repair_plan(activity, integrity, course, _safety_config())
    report = repair_report(plan, course, _safety_config())
    assert report["coordinate_coverage"]["invalidated"] == 2
    assert len(report["gap_inventory"]) == 3
    assert {item["record_index"] for item in report["coordinate_dispositions"]} == {4, 7}
    console = repair_console(plan, course, _safety_config())
    assert "Coordinate coverage:" in console
    assert "Invalidations: 2" in console


def _safety_config() -> CourseReconstructionConfig:
    return CourseReconstructionConfig(
        anchor_stability_min_normal_transitions=2,
        anchor_stability_scan_max_records=4,
        mixed_region_search_max_records=100,
        mixed_region_max_clean_gap_records=1,
    )


def _mixed_region_activity(*, include_distant_spike: bool) -> ActivityData:
    observations = [
        *eastward_observations([0.0, 1.0, 2.0, 3.0], [0.0, 3.0, 6.0, 9.0]),
        (4.0, 56.0, 37.0),
        *eastward_observations([5.0, 6.0], [15.0, 18.0]),
        (7.0, 56.0, 37.0),
        *eastward_observations([8.0], [24.0]),
        (9.0, None, None),
        *eastward_observations([10.0, 11.0, 12.0, 13.0], [30.0, 33.0, 36.0, 39.0]),
    ]
    if include_distant_spike:
        observations.extend(
            [
                *eastward_observations(
                    [float(index) for index in range(14, 31)],
                    [float(index * 3) for index in range(14, 31)],
                ),
                (31.0, 56.0, 37.0),
                *eastward_observations([32.0], [96.0]),
            ]
        )
    return make_activity(observations)
