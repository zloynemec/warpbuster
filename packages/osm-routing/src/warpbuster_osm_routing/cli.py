"""Command-line interface for Valhalla graph preparation and the Task 010A spike."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig
from warpbuster_osm_routing.dem_coverage import plan_coverage
from warpbuster_osm_routing.dem_profile import MAPZEN_SKADI_EGM96_V1
from warpbuster_osm_routing.elevation_filter import ElevationFilteringPolicy
from warpbuster_osm_routing.elevation_gpx import ElevationGpxWriter
from warpbuster_osm_routing.elevation_service import ElevationService
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import GeoPoint, RouteAlternativesRequest, RouteRequest
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1
from warpbuster_osm_routing.route_service import RouteService
from warpbuster_osm_routing.spike import run_spike


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="warpbuster-osm-route",
        description="Prepare and inspect local Valhalla graphs from OSM Manager snapshots",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="materialize and cache a READY graph")
    prepare.add_argument("manifest", type=Path)
    prepare.add_argument("--rebuild", action="store_true")
    _add_cache_options(prepare)

    listing = subparsers.add_parser("list", help="list locally cached graphs")
    _add_cache_options(listing)

    inspect = subparsers.add_parser("inspect", help="verify one exact graph")
    inspect.add_argument("graph_id")
    _add_cache_options(inspect)

    route = subparsers.add_parser("route", help="build one audited route on an exact graph")
    route.add_argument("graph_id")
    route.add_argument("--from", dest="start", type=_parse_point, required=True)
    route.add_argument("--to", dest="end", type=_parse_point, required=True)
    route.add_argument(
        "--alternates",
        default="0",
        metavar="N",
        help="additional routes: 0 (default), 1 or 2; never guarantees exhaustive search",
    )
    _add_cache_options(route)

    remove = subparsers.add_parser("remove", help="remove one exact verified graph")
    remove.add_argument("graph_id")
    _add_cache_options(remove)

    prune = subparsers.add_parser("prune", help="list or remove old derived graphs")
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list candidates (default)")
    mode.add_argument("--apply", action="store_true", help="remove listed candidates")
    _add_cache_options(prune)

    profile = subparsers.add_parser("profile", help="inspect versioned routing profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_commands.add_parser("show", help="show trail-running profile v1")
    profile_show.add_argument("--json", action="store_true")

    dem = subparsers.add_parser("dem", help="inspect the Task 020 DEM dataset contract")
    dem_commands = dem.add_subparsers(dest="dem_command", required=True)
    dem_profile = dem_commands.add_parser("profile", help="inspect the Skadi dataset profile")
    dem_profile.add_argument("--json", action="store_true")
    dem_prepare = dem_commands.add_parser("prepare", help="cache Skadi tiles for a route JSON")
    dem_prepare.add_argument("route", type=Path)
    dem_prepare.add_argument("--mode", choices=("auto", "offline", "disabled"), default="offline")
    dem_prepare.add_argument("--cache-dir", type=Path)
    dem_prepare.add_argument("--json", action="store_true")
    dem_inspect = dem_commands.add_parser("inspect", help="verify one DEM snapshot")
    dem_inspect.add_argument("snapshot_id")
    dem_inspect.add_argument("--cache-dir", type=Path)
    dem_inspect.add_argument("--json", action="store_true")
    dem_prune = dem_commands.add_parser("prune", help="list or remove old DEM cache entries")
    dem_prune.add_argument("--apply", action="store_true")
    dem_prune.add_argument("--cache-dir", type=Path)
    dem_prune.add_argument("--json", action="store_true")
    dem_sample = dem_commands.add_parser(
        "sample", help="sample raw height from a verified snapshot"
    )
    dem_sample.add_argument("snapshot_id")
    dem_sample.add_argument("route", type=Path)
    dem_sample.add_argument("--cache-dir", type=Path)
    _add_dem_filter_options(dem_sample)
    dem_sample.add_argument("--json", action="store_true")
    dem_export = dem_commands.add_parser("export", help="write DEM GPX and audit sidecar")
    dem_export.add_argument("snapshot_id")
    dem_export.add_argument("route", type=Path)
    dem_export.add_argument("--output", type=Path, required=True)
    dem_export.add_argument("--graph-id")
    dem_export.add_argument("--cache-dir", type=Path)
    _add_dem_filter_options(dem_export)
    dem_export.add_argument("--json", action="store_true")

    spike = subparsers.add_parser("spike", help="run the Task 010A temporary route probe")
    spike.add_argument("manifest", type=Path)
    spike.add_argument("--work-dir", type=Path, required=True)
    spike.add_argument("--from", dest="start", type=_parse_point, required=True)
    spike.add_argument("--to", dest="end", type=_parse_point, required=True)
    spike.add_argument("--alternates", type=int, default=0)
    spike.add_argument("--overwrite", action="store_true")
    spike.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = _execute(args)
    except RoutingError as error:
        operation = str(args.command)
        if operation == "route" and args.alternates not in (0, "0"):
            operation = "route_alternatives"
        document = {
            "operation": operation,
            "status": "error" if args.command == "spike" else "ERROR",
            "error": error.as_dict(),
        }
        if operation == "route_alternatives":
            document["protocol_version"] = 1
        if getattr(args, "json", False):
            print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        else:
            print(f"WarpBuster OSM routing: ERROR [{error.code}]", file=sys.stderr)
            print(f"  {error.message}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
    else:
        _print_console(document)
    return 1 if args.command == "route" and document.get("status") != "READY" else 0


def _execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "route":
        try:
            args.alternates = int(args.alternates)
        except ValueError as error:
            raise RoutingError(
                "INVALID_REQUEST", "alternates must be an integer: 0, 1 or 2"
            ) from error
        if not 0 <= args.alternates <= 2:
            raise RoutingError("INVALID_REQUEST", "alternates must be 0, 1 or 2")
    if args.command == "spike":
        return run_spike(
            args.manifest,
            args.work_dir,
            args.start,
            args.end,
            alternates=args.alternates,
            overwrite=args.overwrite,
        ).as_dict()
    if args.command == "profile":
        assert args.profile_command == "show"
        return {
            "operation": "profile_show",
            "status": "OK",
            "profile": TRAIL_RUNNING_V1.inspection_document(),
        }
    if args.command == "dem":
        if args.dem_command == "profile":
            return {
                "operation": "dem_profile_show",
                "status": "OK",
                "profile": MAPZEN_SKADI_EGM96_V1.inspection_document(),
            }
        dem_config = DemCacheConfig.defaults()
        if args.cache_dir is not None:
            from dataclasses import replace

            dem_config = replace(dem_config, cache_directory=args.cache_dir)
        dem_cache = DemCache(dem_config)
        if args.dem_command == "inspect":
            return {"operation": "dem_inspect", **dem_cache.inspect(args.snapshot_id).as_dict()}
        if args.dem_command == "prune":
            return dem_cache.prune(apply=args.apply)
        if args.dem_command in {"sample", "export"}:
            points = _read_dem_route(
                args.route, dem_config.maximum_manifest_bytes, dem_config.maximum_points
            )
            filtering_policy = ElevationFilteringPolicy(
                window_radius_m=args.smoothing_radius_m,
                minimum_excursion_m=args.minimum_excursion_m,
            )
            profile = ElevationService(dem_cache, filtering_policy=filtering_policy).sample(
                args.snapshot_id, points
            )
            if args.dem_command == "sample":
                return {"operation": "dem_sample", **profile.as_dict()}
            return {
                "operation": "dem_export",
                **ElevationGpxWriter(dem_cache)
                .write(profile, args.output, graph_id=args.graph_id)
                .as_dict(),
            }
        assert args.dem_command == "prepare"
        if args.mode == "disabled":
            return {"operation": "dem_prepare", **dem_cache.ensure(None, "disabled").as_dict()}
        points = _read_dem_route(
            args.route, dem_config.maximum_manifest_bytes, dem_config.maximum_points
        )
        plan = plan_coverage(
            points,
            buffer_m=dem_config.buffer_m,
            maximum_points=dem_config.maximum_points,
            maximum_tiles=dem_config.maximum_tiles,
        )
        return {
            "operation": "dem_prepare",
            "coverage": plan.as_dict(),
            **dem_cache.ensure(plan, args.mode).as_dict(),
        }
    try:
        config = RoutingCacheConfig.load(args.config).with_cache_directory(args.cache_dir)
    except ValueError as error:
        raise RoutingError("CONFIG_INVALID", str(error)) from error
    cache = GraphCache(config)
    if args.command == "prepare":
        return cache.prepare(args.manifest, rebuild=args.rebuild).as_dict()
    if args.command == "list":
        graphs = cache.list_graphs()
        return {"operation": "list", "status": "OK", "count": len(graphs), "graphs": graphs}
    if args.command == "inspect":
        result = cache.inspect(args.graph_id)
        return {
            "operation": "inspect",
            "status": result.status,
            "graph_id": result.graph_id,
            "manifest_path": str(result.manifest_path),
            "graph": result.document,
        }
    if args.command == "route":
        if args.alternates:
            return (
                RouteService(config)
                .alternatives(
                    RouteAlternativesRequest(
                        args.graph_id,
                        args.start,
                        args.end,
                        args.alternates,
                    )
                )
                .as_dict()
            )
        return (
            RouteService(config).route(RouteRequest(args.graph_id, args.start, args.end)).as_dict()
        )
    if args.command == "remove":
        return cache.remove(args.graph_id)
    if args.command == "prune":
        return cache.prune(apply=args.apply)
    raise AssertionError(f"unhandled command: {args.command}")


def _read_dem_route(path: Path, maximum_bytes: int, maximum_points: int) -> tuple[GeoPoint, ...]:
    try:
        if path.stat().st_size > maximum_bytes:
            raise RoutingError("RESOURCE_LIMIT_EXCEEDED", "DEM route JSON exceeds byte limit")
        route = json.loads(path.read_text(encoding="utf-8"))
        raw_points = route["points"]
        if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= maximum_points:
            raise ValueError("DEM route points are invalid")
        return tuple(GeoPoint(item["latitude"], item["longitude"]) for item in raw_points)
    except (OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        raise RoutingError("INVALID_REQUEST", "invalid DEM route JSON") from error


def _add_dem_filter_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--smoothing-radius-m", type=float, default=60.0)
    parser.add_argument("--minimum-excursion-m", type=float, default=3.0)


def _add_cache_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="explicit osm-routing.toml")
    parser.add_argument("--cache-dir", type=Path, help="override derived graph cache")
    parser.add_argument("--json", action="store_true")


def _parse_point(value: str) -> GeoPoint:
    try:
        latitude_text, longitude_text = value.split(",", maxsplit=1)
        return GeoPoint(latitude=float(latitude_text), longitude=float(longitude_text))
    except ValueError as error:
        raise argparse.ArgumentTypeError("point must be LATITUDE,LONGITUDE") from error


def _print_console(document: dict[str, Any]) -> None:
    operation = document.get("operation")
    if operation == "valhalla_feasibility_spike":
        snapshot = document["snapshot"]
        engine = document["engine"]
        artifacts = document["artifacts"]
        timings = document["timings_seconds"]
        probe = document["probe"]
        print("WarpBuster Valhalla feasibility spike")
        print(f"Verdict: {str(document['verdict']).upper()}")
        print(f"Snapshot: {snapshot['snapshot_id']}")
        print(f"Engine: Valhalla {engine['version']}")
        print(f"PBF: {artifacts['pbf_bytes']} bytes; tiles: {artifacts['tile_bytes']} bytes")
        print(f"Routes: {probe['returned_routes']}; total time: {timings['total']} s")
    elif operation == "prepare":
        graph = document["graph"]
        stats = graph["materialization"]["statistics"]
        print("WarpBuster OSM graph cache")
        print(f"Status: {document['status']}")
        print(f"Graph: {document['graph_id']}")
        print(f"Manifest: {document['manifest_path']}")
        print(
            "Objects: "
            f"nodes={stats['selected_nodes']}, ways={stats['selected_ways']}, "
            f"relations={stats['selected_relations']}"
        )
    elif operation == "list":
        print(f"WarpBuster OSM graph cache: {document['count']} graph(s)")
        for graph in document["graphs"]:
            print(f"- {graph['graph_id']}: {graph['status']}")
    elif operation == "inspect":
        print(f"Graph {document['graph_id']}: {document['status']}")
        print(f"Manifest: {document['manifest_path']}")
    elif operation in {"route", "route_alternatives"}:
        print("WarpBuster audited OSM route")
        print(f"Status: {document['status']}")
        print(f"Graph: {document['graph']['graph_id']}")
        for anchor in ("start", "end"):
            snap = document["snapping"][anchor]
            distance = (
                f"; distance={snap['selected']['distance_m']} m"
                if snap["selected"] is not None
                else ""
            )
            print(f"{anchor.title()} snap: {snap['status']}{distance}")
        if operation == "route_alternatives":
            _print_alternatives(document)
        elif document["route"] is not None:
            print(f"Distance: {document['route']['summary']['length_m']} m")
            print(f"Audit: {document['route']['audit']['status']}")
    elif operation == "remove":
        print(f"Removed graph: {document['graph_id']}")
    elif operation == "prune":
        print(f"Graph prune: {document['status']}")
        print(f"Candidates: {len(document['candidates'])}; removed: {len(document['removed'])}")
    elif operation == "profile_show":
        profile = document["profile"]
        print("WarpBuster trail routing profile")
        print(f"Profile: {profile['profile_id']}")
        print(f"SHA-256: {profile['profile_sha256']}")
        print(
            f"Engine: {profile['engine']['name']} {profile['installed_engine_version']} "
            f"(compatible={'yes' if profile['engine_compatible'] else 'no'})"
        )
        print(json.dumps(profile["costing_options"], indent=2, sort_keys=True))
    elif operation == "dem_profile_show":
        profile = document["profile"]
        print("WarpBuster DEM dataset profile")
        print(f"Profile: {profile['profile_id']}")
        print(f"SHA-256: {profile['profile_sha256']}")
        print(f"Vertical datum: {profile['vertical_datum']}")
        print(f"Attribution sources: {len(profile['attributions'])}")
    elif operation in {"dem_prepare", "dem_inspect"}:
        print(f"WarpBuster DEM: {document['status']}")
        if document["dem_snapshot_id"] is not None:
            print(f"Snapshot: {document['dem_snapshot_id']}")
    elif operation == "dem_prune":
        print(f"WarpBuster DEM prune: {document['status']}")
        for key in ("snapshots", "index", "objects"):
            print(f"{key}: {len(document[key])}")
    elif operation == "dem_sample":
        print(f"WarpBuster DEM height: {document['status']}")
        print(f"Snapshot: {document['dem_snapshot_id']}")
        print(f"Profile: {document['elevation_profile_id']}")
        print(f"Samples: {len(document['samples'])}")
    elif operation == "dem_export":
        print(f"WarpBuster DEM export: {document['status']}")
        print(f"GPX: {document['gpx_path']}")
        print(f"Audit: {document['audit_path']}")


def _print_alternatives(document: dict[str, Any]) -> None:
    search = document["search"]
    print(
        f"Alternatives: requested={search['requested_alternates']}; "
        f"engine={search['engine_returned_alternates']}; unique={search['unique_alternates']}; "
        f"duplicates={search['duplicates_removed']}"
    )
    print(f"Route choice: {document['route_choice']['status']}")
    print("Search is NOT exhaustive; one candidate does not prove a unique path.")
    print("Overlap/diversity: directed edge-weight similarity, not exact spatial overlap.")
    if search["reasons"]:
        print("Search diagnostics: " + ", ".join(search["reasons"]))
    if not document["routes"]:
        return
    print(
        "Route ID | Role | Distance m | Delta m | Ratio | Overlap* | Diversity* | Audit | Warnings"
    )
    for route in document["routes"]:
        metrics = route["vs_primary"]
        comparative = (
            f"{metrics['length_delta_m']:+.3f} | {metrics['distance_ratio']:.3f} | "
            f"{metrics['overlap_b']:.1%} | {metrics['diversity_ratio']:.1%}"
            if metrics
            else "— | — | — | —"
        )
        warnings = ", ".join(item["code"] for item in route["warnings"]) or "—"
        print(
            f"{route['route_id']} | {route['role']} | "
            f"{route['geometry']['calculated_length_m']:.3f} | {comparative} | "
            f"{route['audit']['status']} | {warnings}"
        )
    print(
        "* Compared with the engine primary; overlap is the candidate's shared edge-weight fraction."
    )


if __name__ == "__main__":
    raise SystemExit(main())
