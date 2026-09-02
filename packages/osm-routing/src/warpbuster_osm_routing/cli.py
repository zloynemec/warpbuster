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
from warpbuster_osm_routing.models import GeoPoint
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

    remove = subparsers.add_parser("remove", help="remove one exact verified graph")
    remove.add_argument("graph_id")
    _add_cache_options(remove)

    prune = subparsers.add_parser("prune", help="list or remove old derived graphs")
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list candidates (default)")
    mode.add_argument("--apply", action="store_true", help="remove listed candidates")
    _add_cache_options(prune)

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
    return 0


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
            "status": "READY",
            "graph_id": result.graph_id,
            "manifest_path": str(result.manifest_path),
            "graph": result.document,
        }
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
        print(f"Graph {document['graph_id']}: READY")
        print(f"Manifest: {document['manifest_path']}")
    elif operation == "remove":
        print(f"Removed graph: {document['graph_id']}")
    elif operation == "prune":
        print(f"Graph prune: {document['status']}")
        print(f"Candidates: {len(document['candidates'])}; removed: {len(document['removed'])}")


if __name__ == "__main__":
    raise SystemExit(main())
