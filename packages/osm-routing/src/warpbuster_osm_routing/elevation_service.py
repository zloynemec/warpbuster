"""Deterministic distance-domain sampling of an exact, verified DEM snapshot."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from warpbuster_osm_routing.dem_cache import DemCache
from warpbuster_osm_routing.elevation_backend import (
    PINNED_VALHALLA_VERSION,
    ElevationBackend,
    ValhallaElevationBackend,
)
from warpbuster_osm_routing.elevation_filter import (
    ElevationFilteringPolicy,
    ElevationTotals,
    filter_elevations,
)
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.geometry import finite_number, haversine_m, valid_wgs84
from warpbuster_osm_routing.models import GeoPoint

SAMPLING_SEMANTICS_VERSION = "valhalla-bilinear-geodesic-v1"
BackendFactory = Callable[[Path, int], ElevationBackend]


@dataclass(frozen=True, slots=True)
class ElevationSamplingConfig:
    maximum_spacing_m: float = 30.0
    maximum_input_points: int = 20_000
    maximum_samples: int = 20_000
    maximum_batch_samples: int = 512
    maximum_request_bytes: int = 128 * 1024
    maximum_response_bytes: int = 256 * 1024

    def validated(self) -> ElevationSamplingConfig:
        for name in (
            "maximum_input_points",
            "maximum_samples",
            "maximum_batch_samples",
            "maximum_request_bytes",
            "maximum_response_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RoutingError("INVALID_REQUEST", f"{name} must be a positive integer")
        if not finite_number(self.maximum_spacing_m) or self.maximum_spacing_m <= 0:
            raise RoutingError("INVALID_REQUEST", "maximum_spacing_m must be positive metres")
        if self.maximum_batch_samples > self.maximum_samples:
            raise RoutingError("INVALID_REQUEST", "batch limit exceeds sample limit")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "sampling_semantics_version": SAMPLING_SEMANTICS_VERSION,
            "maximum_spacing_m": self.maximum_spacing_m,
            "maximum_input_points": self.maximum_input_points,
            "maximum_samples": self.maximum_samples,
            "maximum_batch_samples": self.maximum_batch_samples,
            "maximum_request_bytes": self.maximum_request_bytes,
            "maximum_response_bytes": self.maximum_response_bytes,
        }


@dataclass(frozen=True, slots=True)
class ElevationSample:
    point: GeoPoint
    chainage_m: float
    raw_elevation_m: float | None
    filtered_elevation_m: float | None
    missing_reason: str | None
    original_vertex_index: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "latitude": self.point.latitude,
            "longitude": self.point.longitude,
            "chainage_m": self.chainage_m,
            "raw_elevation_m": self.raw_elevation_m,
            "filtered_elevation_m": self.filtered_elevation_m,
            "missing_reason": self.missing_reason,
            "original_vertex_index": self.original_vertex_index,
        }


@dataclass(frozen=True, slots=True)
class ElevationProfile:
    status: str
    dem_snapshot_id: str
    elevation_profile_id: str
    geometry_sha256: str
    sampling_policy_sha256: str
    sampling_policy: ElevationSamplingConfig
    filtering_policy_sha256: str
    filtering_policy: ElevationFilteringPolicy
    raw_totals: ElevationTotals
    filtered_totals: ElevationTotals
    samples: tuple[ElevationSample, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dem_snapshot_id": self.dem_snapshot_id,
            "elevation_profile_id": self.elevation_profile_id,
            "geometry_sha256": self.geometry_sha256,
            "sampling_policy_sha256": self.sampling_policy_sha256,
            "sampling_policy": self.sampling_policy.as_dict(),
            "filtering_policy_sha256": self.filtering_policy_sha256,
            "filtering_policy": self.filtering_policy.inspection_document(),
            "raw_totals": self.raw_totals.as_dict(),
            "filtered_totals": self.filtered_totals.as_dict(),
            "sampling_semantics_version": SAMPLING_SEMANTICS_VERSION,
            "engine_version": PINNED_VALHALLA_VERSION,
            "samples": [sample.as_dict() for sample in self.samples],
        }


class ElevationService:
    def __init__(
        self,
        cache: DemCache,
        config: ElevationSamplingConfig | None = None,
        *,
        filtering_policy: ElevationFilteringPolicy | None = None,
        backend_factory: BackendFactory = ValhallaElevationBackend,
    ) -> None:
        self.cache = cache
        self.config = (config or ElevationSamplingConfig()).validated()
        self.filtering_policy = (filtering_policy or ElevationFilteringPolicy()).validated()
        self.backend_factory = backend_factory

    def sample(self, snapshot_id: str, points: Sequence[GeoPoint]) -> ElevationProfile:
        vertices = tuple(points)
        sampled = densify(vertices, self.config)
        geometry_hash = _hash([[p.latitude, p.longitude] for p in vertices])
        sampling_hash = _hash(self.config.as_dict())
        filtering_hash = self.filtering_policy.sha256()
        profile_id = _hash(
            [
                snapshot_id,
                geometry_hash,
                PINNED_VALHALLA_VERSION,
                sampling_hash,
                filtering_hash,
            ]
        )
        with self.cache.lease(snapshot_id) as snapshot:
            inventory = {entry["name"]: entry["sha256"] for entry in snapshot.document["tiles"]}
            present = [
                (index, item)
                for index, item in enumerate(sampled)
                if _tile_name(item[0]) in inventory
            ]
            elevations: list[float | None] = [None] * len(sampled)
            view = Path(tempfile.mkdtemp(prefix="height-", dir=self.cache.staging))
            try:
                for name, digest in inventory.items():
                    target = view / name[:3] / f"{name}.hgt.gz"
                    target.parent.mkdir(exist_ok=True)
                    os.link(self.cache._object_path(digest), target)
                if present:
                    backend = self.backend_factory(view, self.config.maximum_response_bytes)
                    for offset in range(0, len(present), self.config.maximum_batch_samples):
                        batch = present[offset : offset + self.config.maximum_batch_samples]
                        batch_points = tuple(item[0] for _, item in batch)
                        request_size = len(
                            json.dumps(
                                {
                                    "shape": [point.as_valhalla() for point in batch_points],
                                    "range": False,
                                },
                                separators=(",", ":"),
                                allow_nan=False,
                            ).encode()
                        )
                        if request_size > self.config.maximum_request_bytes:
                            raise RoutingError(
                                "RESOURCE_LIMIT_EXCEEDED", "DEM request exceeds byte limit"
                            )
                        values = backend.heights(batch_points)
                        if len(values) != len(batch) or any(
                            value is not None and not finite_number(value) for value in values
                        ):
                            raise RoutingError(
                                "DEM_RESPONSE_INVALID", "DEM backend result is invalid"
                            )
                        for (index, _), value in zip(batch, values, strict=True):
                            elevations[index] = value
            except OSError as error:
                raise RoutingError(
                    "DEM_CACHE_CORRUPT", "cannot create DEM sampling view"
                ) from error
            finally:
                shutil.rmtree(view)
            filtered = filter_elevations(
                tuple(item[1] for item in sampled), tuple(elevations), self.filtering_policy
            )
            result = tuple(
                ElevationSample(
                    point,
                    chainage,
                    elevations[index],
                    filtered.values[index],
                    ("TILE_NOT_IN_SNAPSHOT" if _tile_name(point) not in inventory else "VOID")
                    if elevations[index] is None
                    else None,
                    original_index,
                )
                for index, (point, chainage, original_index) in enumerate(sampled)
            )
        return ElevationProfile(
            "PARTIAL" if any(item.raw_elevation_m is None for item in result) else "READY",
            snapshot_id,
            profile_id,
            geometry_hash,
            sampling_hash,
            self.config,
            filtering_hash,
            self.filtering_policy,
            filtered.raw_totals,
            filtered.filtered_totals,
            result,
        )


def densify(
    points: Sequence[GeoPoint], config: ElevationSamplingConfig
) -> tuple[tuple[GeoPoint, float, int | None], ...]:
    config.validated()
    if not 1 <= len(points) <= config.maximum_input_points or any(
        not valid_wgs84(point) for point in points
    ):
        raise RoutingError("INVALID_REQUEST", "invalid elevation polyline")
    segments: list[tuple[GeoPoint, GeoPoint, float, int]] = []
    count = 1
    for first, second in pairwise(points):
        if abs(second.longitude - first.longitude) > 180:
            raise RoutingError("INVALID_REQUEST", "antimeridian crossing is unsupported")
        length = haversine_m(first, second)
        steps = max(1, math.ceil(length / config.maximum_spacing_m))
        count += steps
        if count > config.maximum_samples:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM sample count exceeds limit")
        segments.append((first, second, length, steps))
    result: list[tuple[GeoPoint, float, int | None]] = [(points[0], 0.0, 0)]
    cumulative = 0.0
    for vertex_index, (first, second, length, steps) in enumerate(segments, start=1):
        for step in range(1, steps):
            result.append(
                (
                    _interpolate(first, second, step / steps),
                    cumulative + length * step / steps,
                    None,
                )
            )
        cumulative += length
        result.append((second, cumulative, vertex_index))
    return tuple(result)


def _interpolate(first: GeoPoint, second: GeoPoint, fraction: float) -> GeoPoint:
    lat1, lon1 = math.radians(first.latitude), math.radians(first.longitude)
    lat2, lon2 = math.radians(second.latitude), math.radians(second.longitude)
    angle = haversine_m(first, second) / 6_371_008.8
    if angle < 1e-12:
        return GeoPoint(
            first.latitude + (second.latitude - first.latitude) * fraction,
            first.longitude + (second.longitude - first.longitude) * fraction,
        )
    left = math.sin((1 - fraction) * angle) / math.sin(angle)
    right = math.sin(fraction * angle) / math.sin(angle)
    x = left * math.cos(lat1) * math.cos(lon1) + right * math.cos(lat2) * math.cos(lon2)
    y = left * math.cos(lat1) * math.sin(lon1) + right * math.cos(lat2) * math.sin(lon2)
    z = left * math.sin(lat1) + right * math.sin(lat2)
    return GeoPoint(math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def _tile_name(point: GeoPoint) -> str:
    latitude, longitude = math.floor(point.latitude), math.floor(point.longitude)
    return f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}"


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
