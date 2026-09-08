"""Stable presentation of candidate-only OSM reconstruction results."""

from __future__ import annotations

from dataclasses import asdict
from typing import cast

from warpbuster.models.reconstruction import OSMDryRunResult
from warpbuster.report.gaps import json_value


def osm_reconstruction_report(result: OSMDryRunResult) -> dict[str, object]:
    """Serialize one immutable provider result without adding selection semantics."""
    evaluations = []
    graph: object = {"graph_id": result.graph_id}
    profile: object = None
    for number, evaluation in enumerate(result.evaluations, 1):
        routing = evaluation.routing_document()
        if routing is not None:
            graph = routing.get("graph", graph)
            profile = routing.get("profile", profile)
        evaluations.append(
            {
                "number": number,
                "gap_id": evaluation.interval.gap_id,
                "records": [
                    evaluation.interval.start_record_index,
                    evaluation.interval.end_record_index,
                ],
                "kind": evaluation.interval.kind.value,
                "origin": evaluation.interval.origin.value,
                "outcome": evaluation.outcome.value,
                "queried": evaluation.queried,
                "route_status": evaluation.route_status,
                "anchor_before": json_value(asdict(evaluation.anchor_before))
                if evaluation.anchor_before
                else None,
                "anchor_after": json_value(asdict(evaluation.anchor_after))
                if evaluation.anchor_after
                else None,
                "reasons": [reason.value for reason in evaluation.reasons],
                "candidate_count": len(evaluation.candidates),
                "search": routing.get("search") if routing else None,
                "snapping": routing.get("snapping") if routing else None,
                "comparisons": routing.get("comparisons", []) if routing else [],
                "candidates": [
                    {
                        **candidate.as_dict(),
                        "coordinates": [
                            [point.latitude, point.longitude] for point in candidate.coordinates
                        ],
                        "candidate_only": True,
                        "allocated_to_records": False,
                        "application_allowed": False,
                    }
                    for candidate in evaluation.candidates
                ],
            }
        )
    return {
        "protocol_version": 1,
        "status": result.status.value,
        "dry_run": True,
        "application_allowed": False,
        "graph_id": result.graph_id,
        "graph": graph,
        "profile": profile,
        "query_count": result.query_count,
        "candidate_gap_count": result.candidate_gap_count,
        "candidate_count": result.candidate_count,
        "gap_count": len(result.evaluations),
        "config": {
            "requested_alternatives": result.requested_alternatives,
            "maximum_gap_queries": result.maximum_gap_queries,
            "maximum_total_candidate_points": result.maximum_total_candidate_points,
        },
        "gap_evaluations": evaluations,
    }


def osm_reconstruction_console(result: OSMDryRunResult) -> list[str]:
    """Render concise candidate diagnostics without calling them repairable."""
    report = osm_reconstruction_report(result)
    lines = [
        "",
        "OSM reconstruction dry-run",
        f"Graph: {result.graph_id}",
        "Application: DISABLED (candidate discovery only)",
        (
            f"Status: {result.status.value.upper()}; gaps={len(result.evaluations)}, "
            f"queries={result.query_count}, candidate_gaps={result.candidate_gap_count}, "
            f"routes={result.candidate_count}"
        ),
    ]
    evaluations = cast(list[dict[str, object]], report["gap_evaluations"])
    for item in evaluations:
        reasons = ",".join(cast(list[str], item["reasons"])) or "none"
        search = item.get("search")
        exhaustive = search.get("exhaustive") if isinstance(search, dict) else None
        lines.append(
            f"  G{item['number']} ({item['gap_id']}): {str(item['outcome']).upper()}; "
            f"queried={'yes' if item['queried'] else 'no'}; "
            f"routes={item['candidate_count']}; exhaustive={exhaustive}; reasons={reasons}"
        )
        for candidate in cast(list[dict[str, object]], item["candidates"]):
            summary = candidate.get("summary")
            length = summary.get("length_m") if isinstance(summary, dict) else None
            endpoint_delta = _endpoint_delta(candidate)
            warnings = candidate.get("warnings")
            warning_codes = (
                ",".join(
                    str(warning.get("code"))
                    for warning in warnings
                    if isinstance(warning, dict) and warning.get("code")
                )
                if isinstance(warnings, list)
                else ""
            )
            lines.append(
                f"    {candidate.get('role')} {candidate.get('route_id')}: "
                f"length={length} m; snap={_snap_distance(item, 'start')}/"
                f"{_snap_distance(item, 'end')} m; endpoint_delta={endpoint_delta}; "
                f"warnings={warning_codes or 'none'}; candidate_only=yes; allocated=no"
            )
    return lines


def _snap_distance(item: dict[str, object], side: str) -> object:
    snapping = item.get("snapping")
    side_document = snapping.get(side) if isinstance(snapping, dict) else None
    selected = side_document.get("selected") if isinstance(side_document, dict) else None
    return selected.get("distance_m") if isinstance(selected, dict) else None


def _endpoint_delta(candidate: dict[str, object]) -> str:
    audit = candidate.get("audit")
    checks = audit.get("checks") if isinstance(audit, dict) else None
    if not isinstance(checks, list):
        return "None/None m"
    endpoint = next(
        (check for check in checks if isinstance(check, dict) and check.get("name") == "endpoints"),
        None,
    )
    if endpoint is None:
        return "None/None m"
    return f"{endpoint.get('start_delta_m')}/{endpoint.get('end_delta_m')} m"
