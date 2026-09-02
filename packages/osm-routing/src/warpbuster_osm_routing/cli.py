"""Command-line interface for Valhalla graph preparation and the Task 010A spike."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import GeoPoint, RouteRequest
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
        document = {
            "operation": str(args.command),
            "status": "error" if args.command == "spike" else "ERROR",
            "error": error.as_dict(),
        }
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
        return RouteService(config).route(
            RouteRequest(args.graph_id, args.start, args.end)
        ).as_dict()
    if args.command == "remove":
        return cache.remove(args.graph_id)
    if args.command == "prune":
        return cache.prune(apply=args.apply)
    raise AssertionError(f"unhandled command: {args.command}")


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
    elif operation == "route":
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
        if document["route"] is not None:
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


if __name__ == "__main__":
    raise SystemExit(main())
