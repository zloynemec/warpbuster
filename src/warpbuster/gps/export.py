"""Pure JSON and GPX renderers; only original trusted observations are exported."""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal
from xml.etree.ElementTree import Element, SubElement, tostring

from warpbuster.gps.analysis import GpsAnalysisResult
from warpbuster.report.analyze import analyze_report


def gps_report(result: GpsAnalysisResult) -> dict[str, object]:
    """Return the same point decisions and detector evidence used by the CLI."""
    observations = result.source.observations
    points = []
    for observation, decision in zip(observations, result.points, strict=True):
        transitions = []
        for transition in decision.transitions:
            transitions.append(
                {
                    "from_seq": observations[transition.from_record_index].seq,
                    "to_seq": observations[transition.to_record_index].seq,
                    "distance_m": transition.distance_m,
                    "elapsed_seconds": transition.elapsed_seconds,
                    "apparent_speed_mps": transition.apparent_speed_mps,
                    "classification": transition.classification.value,
                    "reasons": list(transition.reasons),
                }
            )
        points.append(
            {
                **asdict(observation),
                "status": decision.status,
                "confidence": decision.confidence.value,
                "reasons": list(decision.reasons),
                "transitions": transitions,
                "interval_indices": list(decision.interval_indices),
            }
        )
    summary = {
        status: sum(point.status == status for point in result.points)
        for status in ("trusted", "rejected", "uncertain")
    }
    return {
        "schema_version": "0.1",
        "scope": "gps_point_acceptance",
        "source": str(result.source.activity.preservation.source_path),
        "ordering": "seq",
        "policy": asdict(result.config),
        "profile_assumption": (
            "running/trail; recorder CSV does not declare a sport"
            if result.config.detector.profile == "running"
            else "generic; sport unspecified"
        ),
        "summary": summary,
        "track_status": "available" if summary["trusted"] else "no_trusted_points",
        "points": points,
        "detector": analyze_report(result.source.activity, result.integrity),
    }


def gps_json(result: GpsAnalysisResult) -> str:
    """Render deterministic JSON without NaN/Infinity extensions."""
    return json.dumps(gps_report(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def trusted_gpx(result: GpsAnalysisResult) -> str:
    """Export accepted observations, splitting every omitted run or long gap."""
    root = Element(
        "gpx",
        {
            "xmlns": "http://www.topografix.com/GPX/1/1",
            "version": "1.1",
            "creator": "WarpBuster",
        },
    )
    track = SubElement(root, "trk")
    SubElement(track, "name").text = "Trusted original GPS observations"
    if not any(point.status == "trusted" for point in result.points):
        SubElement(track, "desc").text = "No trusted points under the selected WB policy."
    segment: Element | None = None
    previous_timestamp = None
    for observation, record, decision in zip(
        result.source.observations,
        result.source.activity.records,
        result.points,
        strict=True,
    ):
        if decision.status != "trusted":
            segment = None
            previous_timestamp = None
            continue
        timestamp = record.timestamp
        if timestamp is None:
            raise AssertionError("recorder timestamps must be present")
        if segment is None or (
            previous_timestamp is not None
            and (
                not 0
                < (timestamp - previous_timestamp).total_seconds()
                <= result.config.max_gap_seconds
            )
        ):
            segment = SubElement(track, "trkseg")
        # GPX decimals cannot use the scientific notation accepted by CSV numeric fields.
        point = SubElement(
            segment,
            "trkpt",
            {
                "lat": format(Decimal(observation.lat), "f"),
                "lon": format(Decimal(observation.lon), "f"),
            },
        )
        SubElement(point, "time").text = observation.fix_timestamp_utc
        previous_timestamp = timestamp
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode") + "\n"
