"""Optional DEM shape evidence for already preflight-safe OSM plans (Task 021C)."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from warpbuster.models.activity import ActivityData
from warpbuster.models.reconstruction import (
    GapRepairPlan,
    MissingCourseRunKind,
    ReconstructionGap,
)
from warpbuster.reconstruction.approximate_contract import DemComparisonConfig, DemEvidenceStatus

if TYPE_CHECKING:
    from warpbuster_osm_routing.elevation_service import ElevationProfile
    from warpbuster_osm_routing.models import GeoPoint


class DemSampler(Protocol):
    """Task 020 sampler; kept optional at the Core import boundary."""

    def sample(self, snapshot_id: str, points: Sequence[GeoPoint]) -> ElevationProfile: ...


@dataclass(frozen=True, slots=True)
class DemCandidateEvidence:
    route_id: str
    profile_id: str
    shape_error_m: float | None
    aligned_samples: int


@dataclass(frozen=True, slots=True)
class DemChoice:
    status: DemEvidenceStatus
    preferred_route_id: str | None = None
    snapshot_id: str | None = None
    candidates: tuple[DemCandidateEvidence, ...] = ()

    @property
    def profile_ids(self) -> tuple[str, ...]:
        return tuple(item.profile_id for item in self.candidates)


def compare_dem_candidates(
    activity: ActivityData,
    gap: ReconstructionGap,
    plans: Sequence[tuple[str, GapRepairPlan]],
    sampler: DemSampler,
    snapshot_id: str,
    config: DemComparisonConfig,
) -> DemChoice:
    """Prefer a DEM profile only when FIT altitude shape separates 2D-near-best plans.

    Every plan must already have passed allocation and the exact FIT writer preflight.
    Errors in the optional sampler never invalidate otherwise safe 2D plans.
    """
    if len(plans) < 2:
        return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
    if len(plans) > config.maximum_profiles_per_gap:
        return DemChoice(DemEvidenceStatus.UNAVAILABLE)
    observed = {
        record.index: float(record.altitude)
        for record in activity.records[gap.start_record_index : gap.end_record_index + 1]
        if record.altitude is not None and math.isfinite(record.altitude)
    }
    if len(observed) < config.minimum_aligned_samples:
        return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
    endpoint_gap = gap.kind in {MissingCourseRunKind.PREFIX, MissingCourseRunKind.SUFFIX}
    if not endpoint_gap and (
        gap.anchor_before_record_index is None or gap.anchor_after_record_index is None
    ):
        return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
    try:
        # Optional package is loaded only if DEM comparison is explicitly requested.
        from warpbuster_osm_routing.models import GeoPoint

        sampled: list[tuple[str, str, dict[int, tuple[float, float]]]] = []
        for route_id, plan in plans:
            updates = sorted(plan.coordinate_updates, key=lambda item: item.record_index)
            if [item.record_index for item in updates] != list(
                range(gap.start_record_index, gap.end_record_index + 1)
            ):
                return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
            if gap.kind is MissingCourseRunKind.PREFIX:
                assert gap.anchor_after_record_index is not None
                anchor = activity.records[gap.anchor_after_record_index]
                if anchor.latitude is None or anchor.longitude is None:
                    return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
                vertices = (
                    *(
                        GeoPoint(item.candidate_latitude, item.candidate_longitude)
                        for item in updates
                    ),
                    GeoPoint(anchor.latitude, anchor.longitude),
                )
                record_for_vertex = {i: item.record_index for i, item in enumerate(updates)}
            elif gap.kind is MissingCourseRunKind.SUFFIX:
                assert gap.anchor_before_record_index is not None
                anchor = activity.records[gap.anchor_before_record_index]
                if anchor.latitude is None or anchor.longitude is None:
                    return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
                vertices = (
                    GeoPoint(anchor.latitude, anchor.longitude),
                    *(
                        GeoPoint(item.candidate_latitude, item.candidate_longitude)
                        for item in updates
                    ),
                )
                record_for_vertex = {i + 1: item.record_index for i, item in enumerate(updates)}
            else:
                assert gap.anchor_before_record_index is not None
                assert gap.anchor_after_record_index is not None
                before = activity.records[gap.anchor_before_record_index]
                after = activity.records[gap.anchor_after_record_index]
                if (
                    before.latitude is None
                    or before.longitude is None
                    or after.latitude is None
                    or after.longitude is None
                ):
                    return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
                vertices = (
                    GeoPoint(before.latitude, before.longitude),
                    *(
                        GeoPoint(item.candidate_latitude, item.candidate_longitude)
                        for item in updates
                    ),
                    GeoPoint(after.latitude, after.longitude),
                )
                record_for_vertex = {i + 1: item.record_index for i, item in enumerate(updates)}
            profile = sampler.sample(snapshot_id, vertices)
            if profile.dem_snapshot_id != snapshot_id:
                return DemChoice(DemEvidenceStatus.UNAVAILABLE)
            aligned: dict[int, tuple[float, float]] = {}
            for sample in profile.samples:
                vertex = sample.original_vertex_index
                if vertex is None or vertex not in record_for_vertex:
                    continue
                record_index = record_for_vertex[vertex]
                height = sample.raw_elevation_m
                if record_index in observed and height is not None and math.isfinite(height):
                    if record_index in aligned or not math.isfinite(sample.chainage_m):
                        return DemChoice(DemEvidenceStatus.UNINFORMATIVE)
                    aligned[record_index] = (height, sample.chainage_m)
            sampled.append((route_id, profile.elevation_profile_id, aligned))
    except Exception:
        # Import, corrupt snapshot and native backend errors are optional evidence failures.
        return DemChoice(DemEvidenceStatus.UNAVAILABLE)

    unscored = tuple(
        DemCandidateEvidence(route_id, profile_id, None, len(aligned))
        for route_id, profile_id, aligned in sampled
    )
    common = set(observed).intersection(*(set(item[2]) for item in sampled))
    ordered = sorted(common)
    if (
        len(ordered) < config.minimum_aligned_samples
        or len(ordered) / len(observed) < config.minimum_coverage_fraction
    ):
        return DemChoice(
            DemEvidenceStatus.UNINFORMATIVE, snapshot_id=snapshot_id, candidates=unscored
        )
    evidence: list[DemCandidateEvidence] = []
    for route_id, profile_id, aligned in sampled:
        span = aligned[ordered[-1]][1] - aligned[ordered[0]][1]
        if not math.isfinite(span) or span < config.minimum_span_m:
            return DemChoice(
                DemEvidenceStatus.UNINFORMATIVE, snapshot_id=snapshot_id, candidates=unscored
            )
        differences = [observed[index] - aligned[index][0] for index in ordered]
        offset = statistics.median(differences)
        error = statistics.median(abs(value - offset) for value in differences)
        evidence.append(DemCandidateEvidence(route_id, profile_id, error, len(ordered)))
    ranked = sorted(evidence, key=lambda item: (item.shape_error_m, item.route_id))
    assert ranked[0].shape_error_m is not None and ranked[1].shape_error_m is not None
    advantage = ranked[1].shape_error_m - ranked[0].shape_error_m
    if advantage <= config.minimum_dem_advantage_m:
        return DemChoice(
            DemEvidenceStatus.UNINFORMATIVE, snapshot_id=snapshot_id, candidates=tuple(evidence)
        )
    return DemChoice(
        DemEvidenceStatus.USABLE,
        ranked[0].route_id,
        snapshot_id,
        tuple(evidence),
    )
