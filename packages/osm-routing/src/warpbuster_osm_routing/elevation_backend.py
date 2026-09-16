"""Typed, bounded offline adapter for pinned pyvalhalla height()."""

from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Protocol

import valhalla

from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint

PINNED_VALHALLA_VERSION = "3.8.3"


class ElevationBackend(Protocol):
    def heights(self, points: tuple[GeoPoint, ...]) -> tuple[float | None, ...]: ...


class ValhallaElevationBackend:
    """One actor bound to a temporary, local elevation directory."""

    def __init__(self, elevation_directory: Path, maximum_response_bytes: int) -> None:
        if valhalla.__version__ != PINNED_VALHALLA_VERSION:
            raise RoutingError("DEM_ENGINE_MISMATCH", "unexpected pyvalhalla version")
        config = deepcopy(valhalla.get_config(tile_extract="", tile_dir=elevation_directory))
        config["additional_data"]["elevation"] = str(elevation_directory.resolve())
        config["mjolnir"]["tile_url"] = ""
        self._actor = valhalla.Actor(config)
        self.maximum_response_bytes = maximum_response_bytes

    def heights(self, points: tuple[GeoPoint, ...]) -> tuple[float | None, ...]:
        request = json.dumps(
            {"shape": [point.as_valhalla() for point in points], "range": False},
            separators=(",", ":"),
            allow_nan=False,
        )
        try:
            response = self._actor.height(request)
        except Exception as error:
            raise RoutingError("DEM_ENGINE_ERROR", "Valhalla height request failed") from error
        if (
            not isinstance(response, str)
            or len(response.encode("utf-8")) > self.maximum_response_bytes
        ):
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM height response exceeds byte limit")
        try:
            document = json.loads(response, parse_constant=lambda _: float("nan"))
        except (ValueError, TypeError) as error:
            raise RoutingError("DEM_RESPONSE_INVALID", "DEM height response is not JSON") from error
        if not isinstance(document, dict) or set(document) != {"shape", "height"}:
            raise RoutingError("DEM_RESPONSE_INVALID", "DEM height response fields are invalid")
        values = document["height"]
        shape = document["shape"]
        if not isinstance(values, list) or len(values) != len(points):
            raise RoutingError("DEM_RESPONSE_INVALID", "DEM height count mismatch")
        if not isinstance(shape, list) or len(shape) != len(points):
            raise RoutingError("DEM_RESPONSE_INVALID", "DEM shape count mismatch")
        for actual, requested in zip(shape, points, strict=True):
            if (
                not isinstance(actual, dict)
                or set(actual) != {"lat", "lon"}
                or isinstance(actual["lat"], bool)
                or isinstance(actual["lon"], bool)
                or not isinstance(actual["lat"], int | float)
                or not isinstance(actual["lon"], int | float)
                or not math.isfinite(actual["lat"])
                or not math.isfinite(actual["lon"])
                # Valhalla serializes its echo to six decimal places (occasionally
                # one last-digit lower). The caller's
                # original geometry, not this rounded echo, remains authoritative.
                or abs(actual["lat"] - requested.latitude) > 0.000002
                or abs(actual["lon"] - requested.longitude) > 0.000002
            ):
                raise RoutingError("DEM_RESPONSE_INVALID", "DEM response changed sample geometry")
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            )
            for value in values
        ):
            raise RoutingError("DEM_RESPONSE_INVALID", "DEM response contains invalid elevation")
        return tuple(float(value) if value is not None else None for value in values)
