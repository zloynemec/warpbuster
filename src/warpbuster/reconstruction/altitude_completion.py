"""Task 021E: separate, conservative DEM-to-FIT altitude completion decision."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum

from warpbuster.models.activity import ActivityData, FitPreservationData
from warpbuster.models.integrity import IntegrityConfidence
from warpbuster.models.reconstruction import GapOrigin, GapRepairPlan, RepairPlan
from warpbuster.reconstruction.dem_choice import DemSampler
from warpbuster.reconstruction.selection import select_repair_intervals

DEM_VERTICAL_DATUM = "WGS84/EGM96 geoid"
ALTITUDE_COMPLETION_POLICY_ID = "dem-missing-fit-altitude-v1"
ALTITUDE_COMPLETION_POLICY_HASH = (
    "sha256:"
    + hashlib.sha256(
        json.dumps(
            {
                "id": ALTITUDE_COMPLETION_POLICY_ID,
                "origin": "original_missing",
                "provider": "osm",
                "existing_altitude": "immutable",
                "dem_values": "raw_only",
                "vertical_datum": DEM_VERTICAL_DATUM,
                "partial_fit_stream": "explicit_matching_datum_required",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
)


class AltitudeCompletionStatus(StrEnum):
    DISABLED = "disabled"
    NOT_NEEDED = "not_needed"
    PLANNED = "planned"
    UNAVAILABLE = "unavailable"
    APPLIED = "applied"


class AltitudeCompletionReason(StrEnum):
    DEM_UNAVAILABLE = "dem_unavailable"
    DATUM_UNKNOWN = "datum_unknown"
    SOURCE_UNSUPPORTED = "source_unsupported"
    NO_DEM_SAMPLES = "no_dem_samples"
    SAMPLING_FAILED = "sampling_failed"
    WRITER_REFUSED = "writer_refused"


@dataclass(frozen=True, slots=True)
class AltitudeUpdate:
    record_index: int
    altitude_m: float
    field_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.record_index < 0 or not math.isfinite(self.altitude_m):
            raise ValueError("invalid altitude update")
        if (
            not self.field_names
            or len(set(self.field_names)) != len(self.field_names)
            or not set(self.field_names) <= {"altitude", "enhanced_altitude"}
        ):
            raise ValueError("invalid FIT altitude fields")


@dataclass(frozen=True, slots=True)
class AltitudeCompletionPlan:
    status: AltitudeCompletionStatus
    updates: tuple[AltitudeUpdate, ...] = ()
    reason: AltitudeCompletionReason | None = None
    snapshot_id: str | None = None
    profile_ids: tuple[str, ...] = ()
    eligible_records: int = 0
    source_datum_basis: str | None = None

    def __post_init__(self) -> None:
        if (
            self.status in {AltitudeCompletionStatus.PLANNED, AltitudeCompletionStatus.APPLIED}
        ) != bool(self.updates):
            raise ValueError("planned altitude completion requires updates")
        if len({item.record_index for item in self.updates}) != len(self.updates):
            raise ValueError("duplicate altitude updates")
        if self.eligible_records < len(self.updates):
            raise ValueError("altitude update count exceeds eligibility")
        if self.source_datum_basis not in {
            None,
            "no_fit_altitude_stream",
            "explicit_egm96_declaration",
        }:
            raise ValueError("invalid altitude datum provenance")

    def public_summary(self) -> dict[str, object]:
        return {
            "policy_id": ALTITUDE_COMPLETION_POLICY_ID,
            "policy_hash": ALTITUDE_COMPLETION_POLICY_HASH,
            "status": self.status.value,
            "reason": self.reason.value if self.reason else None,
            "eligible_records": self.eligible_records,
            "planned_records": len(self.updates),
            "completed_records": len(self.updates)
            if self.status is AltitudeCompletionStatus.APPLIED
            else 0,
            "snapshot_id": self.snapshot_id,
            "profile_ids": list(self.profile_ids),
            "vertical_datum": DEM_VERTICAL_DATUM if self.updates else None,
            "source_datum_basis": self.source_datum_basis,
        }


DISABLED_ALTITUDE_COMPLETION = AltitudeCompletionPlan(AltitudeCompletionStatus.DISABLED)


def plan_altitude_completion(
    activity: ActivityData,
    repair_plan: RepairPlan,
    sampler: DemSampler,
    snapshot_id: str,
    *,
    minimum_confidence: IntegrityConfidence,
    fit_altitude_datum: str | None,
) -> AltitudeCompletionPlan:
    """Only original-missing OSM gaps and truly missing altitude can receive DEM values.

    Existing plausible height is never overwritten. A partial FIT height stream has
    unknown datum unless explicitly declared compatible for this individual file.
    """
    selected = select_repair_intervals(repair_plan, minimum_confidence)
    candidates = tuple(
        item
        for item in selected.selected_interval_plans
        if isinstance(item, GapRepairPlan)
        and item.osm_provenance is not None
        and item.interval.origin is GapOrigin.ORIGINAL_MISSING
    )
    if not candidates:
        return AltitudeCompletionPlan(AltitudeCompletionStatus.NOT_NEEDED)
    missing_indices = {
        update.record_index
        for item in candidates
        for update in item.coordinate_updates
        if activity.records[update.record_index].altitude is None
    }
    if not missing_indices:
        return AltitudeCompletionPlan(AltitudeCompletionStatus.NOT_NEEDED)
    eligible = len(missing_indices)
    has_existing_altitude = any(record.altitude is not None for record in activity.records)
    if has_existing_altitude and fit_altitude_datum != DEM_VERTICAL_DATUM:
        return AltitudeCompletionPlan(
            AltitudeCompletionStatus.UNAVAILABLE,
            reason=AltitudeCompletionReason.DATUM_UNKNOWN,
            eligible_records=eligible,
        )
    preservation = activity.preservation
    if not isinstance(preservation, FitPreservationData):
        return AltitudeCompletionPlan(
            AltitudeCompletionStatus.UNAVAILABLE,
            reason=AltitudeCompletionReason.SOURCE_UNSUPPORTED,
            eligible_records=eligible,
        )
    if any(
        field.name is not None
        and any(token in field.name.casefold() for token in ("altitude", "elevation", "height"))
        for field in activity.developer_fields
    ):
        return AltitudeCompletionPlan(
            AltitudeCompletionStatus.UNAVAILABLE,
            reason=AltitudeCompletionReason.SOURCE_UNSUPPORTED,
            eligible_records=eligible,
        )
    try:
        from warpbuster_osm_routing.models import GeoPoint

        updates: list[AltitudeUpdate] = []
        profile_ids: list[str] = []
        for item in candidates:
            gap = item.interval
            if gap.anchor_before_record_index is None or gap.anchor_after_record_index is None:
                continue
            before = activity.records[gap.anchor_before_record_index]
            after = activity.records[gap.anchor_after_record_index]
            if (
                before.latitude is None
                or before.longitude is None
                or after.latitude is None
                or after.longitude is None
            ):
                continue
            coordinates = sorted(item.coordinate_updates, key=lambda update: update.record_index)
            vertices = (
                GeoPoint(before.latitude, before.longitude),
                *(
                    GeoPoint(update.candidate_latitude, update.candidate_longitude)
                    for update in coordinates
                ),
                GeoPoint(after.latitude, after.longitude),
            )
            profile = sampler.sample(snapshot_id, vertices)
            if profile.dem_snapshot_id != snapshot_id:
                continue
            profile_ids.append(profile.elevation_profile_id)
            seen: set[int] = set()
            for sample in profile.samples:
                vertex = sample.original_vertex_index
                if vertex is None or not 1 <= vertex <= len(coordinates) or vertex in seen:
                    continue
                seen.add(vertex)
                index = coordinates[vertex - 1].record_index
                height = sample.raw_elevation_m
                if index not in missing_indices or height is None or not math.isfinite(height):
                    continue
                source = preservation.messages[activity.records[index].source.message_index]
                fields = tuple(
                    name
                    for name in ("altitude", "enhanced_altitude")
                    if name in source.fields and source.fields[name] is None
                )
                if any(
                    name in source.fields and source.fields[name] is not None
                    for name in ("altitude", "enhanced_altitude")
                ):
                    continue
                updates.append(AltitudeUpdate(index, height, fields or ("enhanced_altitude",)))
    except Exception:
        return AltitudeCompletionPlan(
            AltitudeCompletionStatus.UNAVAILABLE,
            reason=AltitudeCompletionReason.SAMPLING_FAILED,
            eligible_records=eligible,
        )
    if not updates:
        return AltitudeCompletionPlan(
            AltitudeCompletionStatus.UNAVAILABLE,
            reason=AltitudeCompletionReason.NO_DEM_SAMPLES,
            snapshot_id=snapshot_id,
            profile_ids=tuple(profile_ids),
            eligible_records=eligible,
        )
    return AltitudeCompletionPlan(
        AltitudeCompletionStatus.PLANNED,
        updates=tuple(sorted(updates, key=lambda item: item.record_index)),
        snapshot_id=snapshot_id,
        profile_ids=tuple(profile_ids),
        eligible_records=eligible,
        source_datum_basis=(
            "explicit_egm96_declaration" if has_existing_altitude else "no_fit_altitude_stream"
        ),
    )
