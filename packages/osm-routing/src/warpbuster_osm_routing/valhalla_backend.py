"""Narrow, replaceable boundary around the pyvalhalla runtime."""

from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import valhalla

from warpbuster_osm_routing.errors import RoutingError, RoutingSpikeError
from warpbuster_osm_routing.models import GeoPoint

VALHALLA_PROFILE_ID = "valhalla-pedestrian-spike-v1"
VALHALLA_BUILD_PROFILE_ID = "valhalla-pedestrian-graph-v1"


def build_config(tiles_directory: Path) -> tuple[dict[str, Any], str]:
    """Create the pinned offline graph configuration and its semantic hash."""
    # pyvalhalla 3.8.3 returns a shallow copy whose nested dictionaries still share
    # module defaults. Detach immediately so a later build cannot mutate this config.
    config: dict[str, Any] = deepcopy(
        valhalla.get_config(tile_extract="", tile_dir=tiles_directory)
    )
    mjolnir = config["mjolnir"]
    mjolnir["concurrency"] = 1
    mjolnir["keep_osm_node_ids"] = True
    mjolnir["keep_all_osm_node_ids"] = True
    mjolnir["tile_url"] = ""
    config["loki"]["service_defaults"]["minimum_reachability"] = 0
    semantic_config = deepcopy(config)
    semantic_config["mjolnir"]["tile_dir"] = "<DERIVED_TILE_DIRECTORY>"
    canonical = json.dumps(semantic_config, sort_keys=True, separators=(",", ":")).encode()
    return config, hashlib.sha256(canonical).hexdigest()


def build_tiles(config_path: Path, pbf_path: Path, timeout_seconds: float | None = None) -> str:
    """Build graph tiles with the executable bundled in the pyvalhalla wheel."""
    executable = Path(valhalla.PYVALHALLA_DIR) / "bin" / "valhalla_build_tiles"
    if not executable.is_file():
        raise RoutingSpikeError(
            "valhalla_unavailable", "pyvalhalla does not contain valhalla_build_tiles"
        )
    try:
        completed = subprocess.run(
            [str(executable), "-c", str(config_path), str(pbf_path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise RoutingError(
            "BUILD_TIMEOUT",
            "Valhalla tile build exceeded its configured timeout",
            {"timeout_seconds": timeout_seconds},
        ) from error
    except (OSError, subprocess.CalledProcessError) as error:
        stderr = getattr(error, "stderr", "")
        raise RoutingSpikeError(
            "tile_build_failed",
            "Valhalla tile build failed",
            {"stderr": str(stderr)[-4000:]},
        ) from error
    return completed.stderr[-4000:]


def probe_route(
    config: dict[str, Any],
    start: GeoPoint,
    end: GeoPoint,
    alternates: int,
) -> dict[str, Any]:
    """Run locate, route and trace audit calls against the local graph."""
    actor = valhalla.Actor(config)
    start_snap = _locate(actor, start)
    end_snap = _locate(actor, end)
    request: dict[str, Any] = {
        "locations": [start.as_valhalla(), end.as_valhalla()],
        "costing": "pedestrian",
        "units": "kilometers",
        "directions_type": "none",
    }
    if alternates:
        request["alternates"] = alternates
    try:
        route = json.loads(actor.route(json.dumps(request, separators=(",", ":"))))
    except Exception as error:
        raise RoutingSpikeError("no_route", f"Valhalla route failed: {error}") from error
    trips = [route.get("trip")]
    trips.extend(item.get("trip") for item in route.get("alternates", []))
    routes = [_audit_trip(actor, trip) for trip in trips if isinstance(trip, dict)]
    if not routes:
        raise RoutingSpikeError("no_route", "Valhalla returned no route geometry")
    return {
        "requested_alternates": alternates,
        "returned_routes": len(routes),
        "start_snap": start_snap,
        "end_snap": end_snap,
        "routes": routes,
    }


def _locate(actor: Any, point: GeoPoint) -> dict[str, Any]:
    request = {"locations": [point.as_valhalla()], "costing": "pedestrian", "verbose": True}
    try:
        locations = json.loads(actor.locate(json.dumps(request, separators=(",", ":"))))
    except Exception as error:
        raise RoutingSpikeError("snap_failed", f"Valhalla locate failed: {error}") from error
    edges = locations[0].get("edges", []) if locations else []
    candidates = []
    for edge in sorted(edges, key=lambda item: float(item.get("distance", float("inf"))))[:8]:
        info = edge.get("edge_info", {})
        candidates.append(
            {
                "correlated_latitude": edge.get("correlated_lat"),
                "correlated_longitude": edge.get("correlated_lon"),
                "distance_m": edge.get("distance"),
                "percent_along": edge.get("percent_along"),
                "edge_id": edge.get("edge_id", {}).get("value"),
                "way_id": info.get("way_id"),
                "osm_node_ids": info.get("osm_node_ids", []),
                "use": edge.get("edge", {}).get("classification", {}).get("use"),
                "surface": edge.get("edge", {}).get("classification", {}).get("surface"),
                "pedestrian_access": edge.get("edge", {}).get("access", {}).get("pedestrian"),
            }
        )
    if not candidates:
        raise RoutingSpikeError("snap_failed", "Valhalla found no pedestrian edge for anchor")
    return {"input": point.as_valhalla(), "candidate_count": len(edges), "candidates": candidates}


def _audit_trip(actor: Any, trip: dict[str, Any]) -> dict[str, Any]:
    legs = trip.get("legs", [])
    shapes = [leg.get("shape") for leg in legs if isinstance(leg.get("shape"), str)]
    encoded_shape = "".join(shapes)
    if not encoded_shape:
        raise RoutingSpikeError("route_audit_failed", "route has no encoded geometry")
    attributes_request = {
        "encoded_polyline": encoded_shape,
        "shape_match": "map_snap",
        "costing": "pedestrian",
        "filters": {
            "action": "include",
            "attributes": [
                "edge.id",
                "edge.length",
                "edge.sac_scale",
                "edge.surface",
                "edge.use",
                "edge.way_id",
            ],
        },
    }
    try:
        attributes = json.loads(
            actor.trace_attributes(json.dumps(attributes_request, separators=(",", ":")))
        )
    except Exception as error:
        raise RoutingSpikeError(
            "route_audit_failed", f"Valhalla trace audit failed: {error}"
        ) from error
    edges = attributes.get("edges", [])
    return {
        "summary": trip.get("summary", {}),
        "encoded_polyline": encoded_shape,
        "geometry_sha256": hashlib.sha256(encoded_shape.encode()).hexdigest(),
        "edge_count": len(edges),
        "way_ids": sorted({edge["way_id"] for edge in edges if edge.get("way_id")}),
        "edges": edges,
    }
