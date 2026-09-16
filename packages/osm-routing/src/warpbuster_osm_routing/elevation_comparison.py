"""Read-only DEM/FIT/course altitude diagnostics for caller-aligned observations."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Literal

from warpbuster_osm_routing.elevation_service import ElevationProfile
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number

SourceKind = Literal["FIT", "GPX_COURSE"]
DEM_VERTICAL_DATUM = "WGS84/EGM96 geoid"


@dataclass(frozen=True, slots=True)
class AltitudeObservation:
    """Caller already matched this value to one exact canonical route sample."""

    source_kind: SourceKind
    sample_index: int
    chainage_m: float
    altitude_m: float | None
    altitude_field: str | None
    vertical_datum: str | None
    unit: str = "metre"


@dataclass(frozen=True, slots=True)
class AltitudeComparison:
    sources: tuple[dict[str, Any], ...]
    dem: dict[str, Any]
    purpose: str = "diagnostic_only"

    def as_dict(self) -> dict[str, Any]:
        return {"purpose": self.purpose, "dem": self.dem, "sources": list(self.sources)}


def compare_altitudes(
    profile: ElevationProfile, observations: tuple[AltitudeObservation, ...]
) -> AltitudeComparison:
    """No map matching, time alignment, confidence, selection or FIT mutation."""
    grouped: dict[SourceKind, list[AltitudeObservation]] = {}
    used: set[tuple[SourceKind, int]] = set()
    for item in observations:
        if (
            item.source_kind not in {"FIT", "GPX_COURSE"}
            or isinstance(item.sample_index, bool)
            or not isinstance(item.sample_index, int)
            or not 0 <= item.sample_index < len(profile.samples)
            or not finite_number(item.chainage_m)
            or abs(item.chainage_m - profile.samples[item.sample_index].chainage_m) > 1e-6
            or item.unit != "metre"
            or (item.altitude_m is not None and not finite_number(item.altitude_m))
            or (item.altitude_field is not None and not item.altitude_field.strip())
        ):
            raise RoutingError("INVALID_REQUEST", "invalid aligned altitude observation")
        key = (item.source_kind, item.sample_index)
        if key in used:
            raise RoutingError("INVALID_REQUEST", "duplicate aligned altitude observation")
        used.add(key)
        grouped.setdefault(item.source_kind, []).append(item)
    rows: list[dict[str, Any]] = []
    for kind in ("FIT", "GPX_COURSE"):
        source = grouped.get(kind, [])
        if not source:
            continue
        provenance = {(item.altitude_field, item.vertical_datum) for item in source}
        if len(provenance) != 1:
            raise RoutingError("INVALID_REQUEST", "mixed altitude provenance within source")
        field, datum = next(iter(provenance))
        paired: list[tuple[float, float]] = []
        for item in source:
            dem_value = profile.samples[item.sample_index].raw_elevation_m
            if item.altitude_m is not None and dem_value is not None:
                paired.append((item.altitude_m, dem_value))
        if datum is None or datum == "UNKNOWN":
            status = "DATUM_UNKNOWN"
        elif datum != DEM_VERTICAL_DATUM:
            status = "DATUM_MISMATCH"
        elif not paired:
            status = "NO_OVERLAP"
        else:
            status = "READY"
        residuals = [float(value - dem) for value, dem in paired] if status == "READY" else []
        rows.append(
            {
                "source_kind": kind,
                "altitude_field": field,
                "unit": "metre",
                "vertical_datum": datum or "UNKNOWN",
                "status": status,
                "observation_count": len(source),
                "source_missing_count": sum(item.altitude_m is None for item in source),
                "dem_missing_count": sum(
                    profile.samples[item.sample_index].raw_elevation_m is None for item in source
                ),
                "paired_count": len(paired),
                "residual_m": (
                    {
                        "mean_signed": statistics.fmean(residuals),
                        "median_signed": statistics.median(residuals),
                        "mean_absolute": statistics.fmean(abs(value) for value in residuals),
                        "maximum_absolute": max(abs(value) for value in residuals),
                    }
                    if residuals
                    else None
                ),
            }
        )
    dem = {
        "source_kind": "DEM",
        "altitude_field": "raw_elevation_m",
        "unit": "metre",
        "vertical_datum": DEM_VERTICAL_DATUM,
        "status": profile.status,
        "sample_count": len(profile.samples),
        "missing_count": sum(item.raw_elevation_m is None for item in profile.samples),
        "dem_snapshot_id": profile.dem_snapshot_id,
        "elevation_profile_id": profile.elevation_profile_id,
    }
    return AltitudeComparison(tuple(rows), dem)
