"""Candidate-only OSM reconstruction provider, strictly after integrity detection."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from warpbuster.config import OSMReconstructionConfig
from warpbuster.models.activity import ActivityData
from warpbuster.models.reconstruction import (
    CoordinateState,
    GapRepairPlan,
    MissingCourseRunKind,
    OSMAnchor,
    OSMDryRunResult,
    OSMDryRunStatus,
    OSMGapEvaluation,
    OSMGapOutcome,
    OSMPathPoint,
    OSMReconstructionReason,
    OSMRouteCandidate,
    ReconstructionGap,
    RepairPlan,
)


@dataclass(frozen=True, slots=True)
class RoutingCandidateData:
    """Detached view of a typed companion-package route candidate."""

    route_id: str
    role: str
    coordinates: tuple[tuple[float, float], ...]
    document: dict[str, object]


@dataclass(frozen=True, slots=True)
class RoutingAlternativesData:
    """Narrow companion result consumed by the provider."""

    status: str
    candidates: tuple[RoutingCandidateData, ...]
    document: dict[str, object]


class RoutingClient(Protocol):
    """Testable boundary around the direct typed routing package API."""

    def alternatives(
        self,
        graph_id: str,
        start: tuple[float, float],
        end: tuple[float, float],
        alternates: int,
    ) -> RoutingAlternativesData: ...


@dataclass(slots=True)
class OSMReconstructionError(Exception):
    """Controlled operation-level failure; per-gap route outcomes are not exceptions."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class ValhallaRoutingClient:
    """Lazy adapter to warpbuster-osm-routing's public typed API."""

    def __init__(
        self, config_path: Path | None = None, cache_directory: Path | None = None
    ) -> None:
        try:
            from warpbuster_osm_routing import RouteService, RoutingCacheConfig
            from warpbuster_osm_routing.errors import RoutingError
            from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest
        except ImportError as error:
            raise OSMReconstructionError(
                "OSM_ROUTING_UNAVAILABLE",
                "warpbuster-osm-routing or its required typed API is unavailable",
                {"import_name": error.name, "import_error": str(error)},
            ) from error
        try:
            config = RoutingCacheConfig.load(config_path).with_cache_directory(cache_directory)
        except ValueError as error:
            raise OSMReconstructionError("CONFIG_INVALID", str(error)) from error
        self._service = RouteService(config)
        self._geo_point = GeoPoint
        self._request = RouteAlternativesRequest
        self._routing_error = RoutingError

    def alternatives(
        self,
        graph_id: str,
        start: tuple[float, float],
        end: tuple[float, float],
        alternates: int,
    ) -> RoutingAlternativesData:
        try:
            result = self._service.alternatives(
                self._request(
                    graph_id,
                    self._geo_point(*start),
                    self._geo_point(*end),
                    alternates,
                )
            )
        except self._routing_error as error:
            raise OSMReconstructionError(error.code, error.message, dict(error.details)) from error
        return RoutingAlternativesData(
            status=result.status.value,
            candidates=tuple(
                RoutingCandidateData(
                    candidate.route_id,
                    candidate.role,
                    tuple((point.latitude, point.longitude) for point in candidate.coordinates),
                    candidate.as_dict(),
                )
                for candidate in result.candidates
            ),
            document=result.as_dict(),
        )


class OSMReconstructionProvider:
    """Discover audited OSM paths without allocating or selecting them."""

    def __init__(
        self, client: RoutingClient, config: OSMReconstructionConfig | None = None
    ) -> None:
        self.client = client
        self.config = config or OSMReconstructionConfig()

    def discover(self, activity: ActivityData, plan: RepairPlan, graph_id: str) -> OSMDryRunResult:
        if not isinstance(graph_id, str) or not graph_id:
            raise OSMReconstructionError("INVALID_GRAPH_ID", "OSM graph ID must not be empty")
        gpx_candidates = {
            item.interval.gap_id for item in plan.interval_plans if isinstance(item, GapRepairPlan)
        }
        evaluations: list[OSMGapEvaluation] = []
        query_count = 0
        retained_points = 0
        for gap in plan.gaps:
            if gap.gap_id in gpx_candidates:
                evaluations.append(
                    OSMGapEvaluation(
                        gap,
                        OSMGapOutcome.NOT_QUERIED,
                        False,
                        reasons=(OSMReconstructionReason.GPX_CANDIDATE_ALREADY_AVAILABLE,),
                    )
                )
                continue
            anchors, reason = _anchors(activity, plan, gap)
            if reason is not None:
                evaluations.append(
                    OSMGapEvaluation(
                        gap,
                        OSMGapOutcome.NOT_QUERIED,
                        False,
                        reasons=(reason,),
                    )
                )
                continue
            assert anchors is not None
            before, after = anchors
            if query_count >= self.config.maximum_gap_queries:
                evaluations.append(
                    OSMGapEvaluation(
                        gap,
                        OSMGapOutcome.NOT_QUERIED,
                        False,
                        before,
                        after,
                        reasons=(OSMReconstructionReason.QUERY_LIMIT_REACHED,),
                    )
                )
                continue
            query_count += 1
            result = self.client.alternatives(
                graph_id,
                (before.latitude, before.longitude),
                (after.latitude, after.longitude),
                self.config.requested_alternatives,
            )
            if result.status != "READY":
                evaluations.append(
                    OSMGapEvaluation(
                        gap,
                        OSMGapOutcome.UNRESOLVED,
                        True,
                        before,
                        after,
                        reasons=(_route_reason(result.status),),
                        route_status=result.status,
                        _routing_document_json=_json(_diagnostics(result.document)),
                    )
                )
                continue
            point_count = sum(len(item.coordinates) for item in result.candidates)
            if retained_points + point_count > self.config.maximum_total_candidate_points:
                evaluations.append(
                    OSMGapEvaluation(
                        gap,
                        OSMGapOutcome.UNRESOLVED,
                        True,
                        before,
                        after,
                        reasons=(OSMReconstructionReason.RESULT_LIMIT_REACHED,),
                        route_status=result.status,
                        _routing_document_json=_json(_diagnostics(result.document)),
                    )
                )
                continue
            candidates = tuple(_candidate(item) for item in result.candidates)
            retained_points += point_count
            evaluations.append(
                OSMGapEvaluation(
                    gap,
                    OSMGapOutcome.CANDIDATES_AVAILABLE if candidates else OSMGapOutcome.UNRESOLVED,
                    True,
                    before,
                    after,
                    candidates=candidates,
                    reasons=() if candidates else (OSMReconstructionReason.NO_ROUTE,),
                    route_status=result.status,
                    _routing_document_json=_json(_diagnostics(result.document)),
                )
            )
        status = _status(evaluations, bool(plan.gaps))
        return OSMDryRunResult(
            graph_id,
            status,
            tuple(evaluations),
            self.config.requested_alternatives,
            self.config.maximum_gap_queries,
            self.config.maximum_total_candidate_points,
        )


def _anchors(
    activity: ActivityData, plan: RepairPlan, gap: ReconstructionGap
) -> tuple[tuple[OSMAnchor, OSMAnchor] | None, OSMReconstructionReason | None]:
    if (
        gap.kind is not MissingCourseRunKind.INTERNAL
        or gap.anchor_before_record_index is None
        or gap.anchor_after_record_index is None
    ):
        return None, OSMReconstructionReason.TWO_ANCHORS_REQUIRED
    before_index, after_index = gap.anchor_before_record_index, gap.anchor_after_record_index
    if not (0 <= before_index < len(activity.records) and 0 <= after_index < len(activity.records)):
        return None, OSMReconstructionReason.ANCHOR_NOT_ELIGIBLE
    if len(plan.coordinate_mask) != len(activity.records):
        raise OSMReconstructionError(
            "OSM_RECONSTRUCTION_PROTOCOL_ERROR",
            "repair plan coordinate mask does not match the activity record count",
        )
    before_mask, after_mask = plan.coordinate_mask[before_index], plan.coordinate_mask[after_index]
    before_record, after_record = activity.records[before_index], activity.records[after_index]
    if (
        before_mask.state is not CoordinateState.PRESERVED
        or after_mask.state is not CoordinateState.PRESERVED
        or not before_mask.anchor_eligible
        or not after_mask.anchor_eligible
    ):
        return None, OSMReconstructionReason.ANCHOR_NOT_ELIGIBLE
    if (
        before_record.continuity_id != gap.continuity_id
        or after_record.continuity_id != gap.continuity_id
        or before_index >= gap.start_record_index
        or after_index <= gap.end_record_index
    ):
        return None, OSMReconstructionReason.CONTINUITY_MISMATCH
    values = (
        before_record.latitude,
        before_record.longitude,
        after_record.latitude,
        after_record.longitude,
    )
    if any(value is None or not math.isfinite(value) for value in values):
        return None, OSMReconstructionReason.INVALID_ANCHOR_POSITION
    assert before_record.latitude is not None and before_record.longitude is not None
    assert after_record.latitude is not None and after_record.longitude is not None
    before_latitude = before_record.latitude
    before_longitude = before_record.longitude
    after_latitude = after_record.latitude
    after_longitude = after_record.longitude
    if not (
        -90 <= before_latitude <= 90
        and -180 <= before_longitude <= 180
        and -90 <= after_latitude <= 90
        and -180 <= after_longitude <= 180
    ):
        return None, OSMReconstructionReason.INVALID_ANCHOR_POSITION
    return (
        OSMAnchor(before_index, before_latitude, before_longitude),
        OSMAnchor(after_index, after_latitude, after_longitude),
    ), None


def _candidate(value: RoutingCandidateData) -> OSMRouteCandidate:
    return OSMRouteCandidate(
        value.route_id,
        value.role,
        tuple(OSMPathPoint(latitude, longitude) for latitude, longitude in value.coordinates),
        _json(value.document),
    )


def _diagnostics(document: dict[str, object]) -> dict[str, object]:
    """Detach route-set diagnostics without retaining duplicate route geometry."""
    keys = (
        "operation",
        "status",
        "request",
        "graph",
        "profile",
        "query_policy",
        "snapping",
        "search",
        "route_choice",
        "comparisons",
        "alternatives_policy",
    )
    return {key: document[key] for key in keys if key in document}


def _route_reason(status: str) -> OSMReconstructionReason:
    try:
        return OSMReconstructionReason(status.casefold())
    except ValueError as error:
        raise OSMReconstructionError(
            "OSM_ROUTING_PROTOCOL_ERROR", f"unsupported routing status: {status}"
        ) from error


def _status(evaluations: list[OSMGapEvaluation], had_gaps: bool) -> OSMDryRunStatus:
    available = sum(item.outcome is OSMGapOutcome.CANDIDATES_AVAILABLE for item in evaluations)
    return (
        OSMDryRunStatus.NOT_NEEDED
        if not had_gaps
        else OSMDryRunStatus.PARTIAL
        if available and available < len(evaluations)
        else OSMDryRunStatus.CANDIDATES_AVAILABLE
        if available
        else OSMDryRunStatus.NO_CANDIDATES
    )


def _json(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as error:
        raise OSMReconstructionError(
            "OSM_ROUTING_PROTOCOL_ERROR", "routing result is not valid JSON"
        ) from error
