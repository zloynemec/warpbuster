"""Course reconstruction configuration tests."""

from dataclasses import asdict

import pytest

from warpbuster.config import CourseReconstructionConfig


def test_reconstruction_thresholds_are_named_and_serializable() -> None:
    """Every matching or allocation threshold has an explicit stable name."""
    assert asdict(CourseReconstructionConfig()) == {
        "anchor_match_tolerance_m": 75.0,
        "high_confidence_anchor_distance_m": 50.0,
        "anchor_candidate_deduplication_m": 25.0,
        "one_sided_anchor_match_tolerance_m": 100.0,
        "one_sided_anchor_candidate_deduplication_m": 40.0,
        "one_sided_drift_corridor_tolerance_m": 15.0,
        "one_sided_drift_stable_record_count": 15,
        "one_sided_drift_search_max_records": 256,
        "ambiguity_score_margin_m": 10.0,
        "minimum_course_span_m": 10.0,
        "signal_course_length_ratio_min": 0.5,
        "signal_course_length_ratio_max": 2.0,
        "maximum_anchor_candidates": 32,
        "maximum_reconstruction_intervals": 100,
        "anchor_stability_min_normal_transitions": 15,
        "anchor_stability_scan_max_records": 60,
        "mixed_region_search_max_records": 1_500,
        "mixed_region_max_clean_gap_records": 15,
    }


@pytest.mark.parametrize(
    "name, value",
    [
        ("anchor_match_tolerance_m", 0.0),
        ("high_confidence_anchor_distance_m", 0.0),
        ("anchor_candidate_deduplication_m", 0.0),
        ("one_sided_anchor_match_tolerance_m", 0.0),
        ("one_sided_anchor_candidate_deduplication_m", 0.0),
        ("one_sided_drift_corridor_tolerance_m", 0.0),
        ("one_sided_drift_stable_record_count", 0),
        ("one_sided_drift_search_max_records", 0),
        ("ambiguity_score_margin_m", 0.0),
        ("minimum_course_span_m", 0.0),
        ("signal_course_length_ratio_min", 0.0),
        ("signal_course_length_ratio_max", 0.0),
        ("maximum_anchor_candidates", 0),
        ("maximum_reconstruction_intervals", 0),
        ("anchor_stability_min_normal_transitions", 0),
        ("anchor_stability_scan_max_records", 0),
        ("mixed_region_search_max_records", 0),
    ],
)
def test_reconstruction_thresholds_reject_non_positive_values(
    name: str,
    value: float,
) -> None:
    """Unsafe zero or negative bounds fail during config construction."""
    with pytest.raises(ValueError, match=name):
        CourseReconstructionConfig(**{name: value})  # type: ignore[arg-type]


def test_reconstruction_thresholds_reject_contradictory_ranges() -> None:
    """HIGH tolerance and signal ratios must be internally consistent."""
    with pytest.raises(ValueError, match="high_confidence_anchor_distance_m"):
        CourseReconstructionConfig(
            anchor_match_tolerance_m=10.0,
            high_confidence_anchor_distance_m=11.0,
        )
    with pytest.raises(ValueError, match="one_sided_anchor_match_tolerance_m"):
        CourseReconstructionConfig(
            high_confidence_anchor_distance_m=50.0,
            one_sided_anchor_match_tolerance_m=49.0,
        )
    with pytest.raises(ValueError, match="signal_course_length_ratio_min"):
        CourseReconstructionConfig(
            signal_course_length_ratio_min=2.0,
            signal_course_length_ratio_max=1.0,
        )
    with pytest.raises(ValueError, match="anchor_stability_scan_max_records"):
        CourseReconstructionConfig(
            anchor_stability_min_normal_transitions=20,
            anchor_stability_scan_max_records=19,
        )
    with pytest.raises(ValueError, match="one_sided_drift_search_max_records"):
        CourseReconstructionConfig(
            one_sided_drift_stable_record_count=20,
            one_sided_drift_search_max_records=19,
        )


def test_mixed_region_clean_gap_may_be_zero() -> None:
    """Adjacent evidence grouping may be configured without any clean records."""
    assert CourseReconstructionConfig(mixed_region_max_clean_gap_records=0)
