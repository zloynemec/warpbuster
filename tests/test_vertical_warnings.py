"""Course-independent altitude diagnostics."""

from dataclasses import replace
from typing import cast

from tests.activity_factory import eastward_observations, make_activity
from warpbuster.config import IntegrityConfig
from warpbuster.integrity import analyze_integrity
from warpbuster.models.activity import ActivityData
from warpbuster.models.integrity import IntegrityStatus, VerticalWarningReason
from warpbuster.report.analyze import analyze_report


def _activity_with_altitudes(altitudes: list[float]) -> ActivityData:
    activity = make_activity(
        eastward_observations(
            [float(index) for index in range(len(altitudes))],
            [float(index * 2) for index in range(len(altitudes))],
        )
    )
    return replace(
        activity,
        records=tuple(
            replace(record, altitude=altitudes[record.index]) for record in activity.records
        ),
    )


def test_sustained_vertical_rate_is_warning_not_coordinate_corruption() -> None:
    """An altitude sensor anomaly is reported without creating a GNSS interval."""
    activity = _activity_with_altitudes([100.0, 100.0, 105.0, 110.0, 115.0, 115.0])

    report = analyze_integrity(activity, IntegrityConfig.running())

    assert report.status is IntegrityStatus.CLEAN
    assert report.corrupted_intervals == ()
    assert len(report.vertical_warnings) == 1
    warning = report.vertical_warnings[0]
    assert (warning.start_record_index, warning.end_record_index) == (1, 4)
    assert warning.altitude_delta_m == 15.0
    assert warning.maximum_absolute_vertical_speed_mps == 5.0
    assert warning.reasons == (VerticalWarningReason.SUSTAINED_VERTICAL_RATE,)
    summary = cast(dict[str, object], analyze_report(activity, report)["summary"])
    assert summary["vertical_warning_count"] == 1


def test_single_extreme_vertical_transition_is_reported() -> None:
    """One sufficiently extreme one-second altitude step remains visible for review."""
    activity = _activity_with_altitudes([100.0, 100.0, 88.0, 88.0])

    report = analyze_integrity(activity, IntegrityConfig.running())

    assert len(report.vertical_warnings) == 1
    warning = report.vertical_warnings[0]
    assert (warning.start_record_index, warning.end_record_index) == (1, 2)
    assert warning.reasons == (VerticalWarningReason.SINGLE_EXTREME_VERTICAL_RATE,)


def test_generic_profile_leaves_vertical_scan_disabled() -> None:
    """No universal vertical ceiling is guessed for an unknown activity type."""
    activity = _activity_with_altitudes([100.0, 150.0])

    report = analyze_integrity(activity, IntegrityConfig())

    assert report.vertical_warnings == ()
    assert report.vertical_scan_diagnostics.enabled is False
