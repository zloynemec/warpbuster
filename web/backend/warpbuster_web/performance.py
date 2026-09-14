"""Allowlisted public projection of the service HTML report's running summary."""

import math

from warpbuster.report.html import _activity_performance

SUMMARY_FIELDS = (
    "distance_m",
    "timer_duration_seconds",
    "average_pace_seconds_per_km",
    "total_ascent_m",
    "total_descent_m",
)
SPLIT_FIELDS = (
    "index",
    "start_distance_m",
    "end_distance_m",
    "distance_m",
    "elapsed_seconds",
    "pace_seconds_per_km",
    "ascent_m",
    "descent_m",
    "average_heart_rate_bpm",
    "average_cadence",
    "coordinate_coverage_ratio",
)


def number(value):
    return value if isinstance(value, int | float) and math.isfinite(value) else None


def public_performance(activity, *, source: str, distance_quality: str):
    # Reuse the exact calculations of the local HTML; never serialize its full payload.
    values = _activity_performance(activity)
    return {
        "source": source,
        "distance_quality": distance_quality,
        **{key: number(values[key]) for key in SUMMARY_FIELDS},
        "cadence_unit": "steps_per_minute"
        if values["cadence_label"] == "Cadence (steps/min)"
        else "fit_per_minute",
        "splits": [
            {
                **{key: number(split[key]) for key in SPLIT_FIELDS},
                "complete_kilometre": bool(split["complete_kilometre"]),
            }
            for split in values["splits"]
        ],
    }
