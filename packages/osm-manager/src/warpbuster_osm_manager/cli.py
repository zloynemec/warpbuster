"""Standalone command-line and JSON protocol for OSM Manager."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NoReturn

from warpbuster_osm_manager._version import __version__
from warpbuster_osm_manager.config import (
    COVERAGE_SCHEME_VERSION,
    DATASET_PROFILE,
    DEFAULT_CONFIG_FILENAME,
    MANIFEST_VERSION,
    PROTOCOL_VERSION,
    OsmManagerConfig,
)
from warpbuster_osm_manager.coverage import (
    ParsedGeometry,
    parse_bbox,
    plan_from_bbox,
    plan_from_geojson,
    plan_from_geometry,
    plan_from_gpx,
    validated_point,
)
from warpbuster_osm_manager.errors import ErrorCode, InvalidInputError, OsmManagerError
from warpbuster_osm_manager.models import CoveragePlan, GeoPoint
from warpbuster_osm_manager.overpass import OVERPASS_QUERY_TEMPLATE_VERSION
from warpbuster_osm_manager.service import OsmManager, read_protocol_request


class ArgumentParser(argparse.ArgumentParser):
    """Convert usage failures to the stable error protocol."""

    def error(self, message: str) -> NoReturn:
        raise InvalidInputError(message)


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone CLI parser."""
    parser = ArgumentParser(prog="warpbuster-osm", description="Manage local raw OSM snapshots")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--config",
        type=Path,
        help="override the default ./osm-manager.toml configuration",
    )
    parser.add_argument("--cache-dir", type=Path, help="override the platform cache directory")
    parser.add_argument("--overpass-url", help="override the HTTPS Overpass endpoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure = subparsers.add_parser("ensure", help="ensure cached OSM coverage")
    _add_coverage_arguments(ensure)
    _add_ensure_policy_arguments(ensure)

    refresh = subparsers.add_parser("refresh", help="force a new snapshot for coverage")
    _add_coverage_arguments(refresh)
    refresh.add_argument("--max-age", type=_duration, help=argparse.SUPPRESS)
    refresh.add_argument("--require-fresh", action="store_true", help=argparse.SUPPRESS)
    refresh.add_argument("--json", action="store_true", help="emit protocol JSON")

    list_parser = subparsers.add_parser("list", help="list cached snapshots")
    list_parser.add_argument("--json", action="store_true", help="emit protocol JSON")

    inspect = subparsers.add_parser("inspect", help="verify one snapshot")
    inspect.add_argument("snapshot_id")
    inspect.add_argument("--json", action="store_true", help="emit protocol JSON")

    import_parser = subparsers.add_parser("import", help="import a local OSM extract")
    import_parser.add_argument("osm_file", type=Path)
    import_parser.add_argument("--json", action="store_true", help="emit protocol JSON")

    remove = subparsers.add_parser("remove", help="remove one exact snapshot manifest")
    remove.add_argument("snapshot_id")
    remove.add_argument("--json", action="store_true", help="emit protocol JSON")

    prune = subparsers.add_parser("prune", help="find or delete old unreferenced blobs")
    mode = prune.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="show candidates without deletion")
    mode.add_argument("--apply", action="store_true", help="delete listed candidates")
    prune.add_argument("--json", action="store_true", help="emit protocol JSON")

    doctor = subparsers.add_parser("doctor", help="check cache and configuration")
    doctor.add_argument("--json", action="store_true", help="emit protocol JSON")

    capabilities = subparsers.add_parser("capabilities", help="show supported protocol and formats")
    capabilities.add_argument("--json", action="store_true", help="emit protocol JSON")
    return parser


def _add_coverage_arguments(parser: argparse.ArgumentParser) -> None:
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--gpx", type=Path, help="derive coverage from GPX trk/rte geometry")
    sources.add_argument("--geojson", type=Path, help="derive coverage from WGS84 GeoJSON")
    sources.add_argument("--bbox", help="coverage as WEST,SOUTH,EAST,NORTH")
    sources.add_argument("--request", type=Path, help="protocol v1 JSON request")
    parser.add_argument("--buffer-m", type=float, help="geodesic line/point buffer in metres")


def _add_ensure_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-age", type=_duration, help="freshness duration such as 30d")
    parser.add_argument("--offline", action="store_true", help="forbid all network access")
    parser.add_argument("--refresh", action="store_true", help="force new OSM data")
    parser.add_argument("--require-fresh", action="store_true", help="reject stale-cache fallback")
    parser.add_argument("--json", action="store_true", help="emit protocol JSON")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one OSM Manager command and return its stable exit status."""
    parser = build_parser()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    json_requested = "--json" in arguments
    try:
        args = parser.parse_args(arguments)
        json_requested = bool(getattr(args, "json", False))
        config = _load_config(args)
        if args.command == "capabilities":
            result = _capabilities()
        else:
            manager = OsmManager(config)
            result = _dispatch(manager, args)
        _emit(result, json_output=json_requested)
        return 0
    except OsmManagerError as error:
        _emit_error(error, json_output=json_requested)
        return error.exit_code
    except ValueError as error:
        wrapped = InvalidInputError(str(error))
        _emit_error(wrapped, json_output=json_requested)
        return wrapped.exit_code


def _load_config(args: argparse.Namespace) -> OsmManagerConfig:
    config = OsmManagerConfig.defaults()
    config_path = args.config or Path.cwd() / DEFAULT_CONFIG_FILENAME
    if args.config is not None or config_path.is_file():
        config = OsmManagerConfig.from_toml(config_path, base=config)
    return config.with_overrides(
        cache_directory=args.cache_dir,
        overpass_url=args.overpass_url,
    )


def _dispatch(manager: OsmManager, args: argparse.Namespace) -> dict[str, Any]:
    if args.command in {"ensure", "refresh"}:
        plan, policy = _coverage_and_policy(args, manager.config)
        refresh = args.command == "refresh" or policy["refresh"]
        if not policy["offline"]:
            print(
                f"OSM coverage: cells={len(plan.cells)}, area={plan.area_km2:.2f} km²; "
                "checking local cache",
                file=sys.stderr,
            )
        result = manager.ensure(
            plan,
            max_age_seconds=policy["max_age_seconds"],
            offline=policy["offline"],
            refresh=refresh,
            require_fresh=policy["require_fresh"],
        )
        if result.downloaded:
            print(
                f"OSM coverage downloaded from {manager.config.overpass_url}",
                file=sys.stderr,
            )
        return result.as_dict()
    if args.command == "list":
        snapshots = manager.list_snapshots()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "list",
            "status": "ok",
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        }
    if args.command == "inspect":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "inspect",
            "status": "ok",
            **manager.inspect_snapshot(args.snapshot_id),
        }
    if args.command == "import":
        import_result = manager.import_file(args.osm_file).as_dict()
        import_result["operation"] = "import"
        return import_result
    if args.command == "remove":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "remove",
            "status": "ok",
            **manager.remove_snapshot(args.snapshot_id),
        }
    if args.command == "prune":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "prune",
            "status": "ok",
            **manager.prune(apply=bool(args.apply)),
        }
    if args.command == "doctor":
        return {
            "protocol_version": PROTOCOL_VERSION,
            "operation": "doctor",
            **manager.doctor(),
        }
    raise InvalidInputError(f"unsupported command: {args.command}")


def _coverage_and_policy(
    args: argparse.Namespace, config: OsmManagerConfig
) -> tuple[CoveragePlan, dict[str, Any]]:
    if args.request is not None:
        plan, policy = _from_protocol_request(
            args.request,
            config,
            buffer_override_m=args.buffer_m,
        )
        if args.max_age is not None:
            policy["max_age_seconds"] = args.max_age
        for name in ("offline", "refresh", "require_fresh"):
            if bool(getattr(args, name, False)):
                policy[name] = True
        return plan, policy
    if args.gpx is not None:
        plan = plan_from_gpx(args.gpx, config, buffer_m=args.buffer_m)
    elif args.geojson is not None:
        plan = plan_from_geojson(args.geojson, config, buffer_m=args.buffer_m)
    elif args.bbox is not None:
        if args.buffer_m is not None:
            raise InvalidInputError("--buffer-m is not supported with --bbox")
        plan = plan_from_bbox(parse_bbox(args.bbox), config)
    else:
        raise InvalidInputError("one coverage source is required")
    return plan, {
        "max_age_seconds": getattr(args, "max_age", None),
        "offline": bool(getattr(args, "offline", False)),
        "refresh": bool(getattr(args, "refresh", False)),
        "require_fresh": bool(getattr(args, "require_fresh", False)),
    }


def _from_protocol_request(
    path: Path,
    config: OsmManagerConfig,
    *,
    buffer_override_m: float | None = None,
) -> tuple[CoveragePlan, dict[str, Any]]:
    document = read_protocol_request(path)
    if document.get("protocol_version") != PROTOCOL_VERSION:
        raise OsmManagerError(
            ErrorCode.PROTOCOL_UNSUPPORTED,
            "request protocol_version is not supported",
            {"supported": [PROTOCOL_VERSION], "received": document.get("protocol_version")},
        )
    coverage = document.get("coverage")
    if not isinstance(coverage, dict):
        raise InvalidInputError("protocol request must contain a coverage object")
    buffer_m = coverage.get("buffer_m")
    if buffer_m is not None:
        try:
            buffer_m = float(buffer_m)
        except (TypeError, ValueError) as error:
            raise InvalidInputError("coverage.buffer_m must be numeric") from error
    if buffer_override_m is not None:
        buffer_m = buffer_override_m
    base = path.parent
    sources = [
        name for name in ("gpx_path", "geojson_path", "bbox", "geometry") if name in coverage
    ]
    if len(sources) != 1:
        raise InvalidInputError("protocol coverage must contain exactly one source")
    source = sources[0]
    if source == "gpx_path":
        plan = plan_from_gpx(base / str(coverage[source]), config, buffer_m=buffer_m)
    elif source == "geojson_path":
        plan = plan_from_geojson(base / str(coverage[source]), config, buffer_m=buffer_m)
    elif source == "bbox":
        if buffer_m is not None:
            raise InvalidInputError("coverage.buffer_m is not supported with bbox")
        value = coverage[source]
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        plan = plan_from_bbox(parse_bbox(str(value)), config)
    else:
        plan = _plan_from_inline_geometry(coverage[source], config, buffer_m)
    policy = document.get("policy", {})
    if not isinstance(policy, dict):
        raise InvalidInputError("protocol policy must be an object")
    return plan, {
        "max_age_seconds": _optional_positive_int(policy.get("max_age_seconds")),
        "offline": _optional_boolean(policy, "offline"),
        "refresh": _optional_boolean(policy, "refresh"),
        "require_fresh": _optional_boolean(policy, "require_fresh"),
    }


def _plan_from_inline_geometry(
    geometry: Any, config: OsmManagerConfig, buffer_m: float | None
) -> CoveragePlan:
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise InvalidInputError("protocol inline geometry currently requires a LineString")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise InvalidInputError("inline LineString must contain at least two coordinates")
    points: list[GeoPoint] = []
    for value in coordinates:
        if not isinstance(value, list) or len(value) < 2:
            raise InvalidInputError("inline coordinate must contain longitude and latitude")
        longitude, latitude = value[:2]
        points.append(validated_point(longitude, latitude))
    return plan_from_geometry(
        ParsedGeometry(lines=(tuple(points),)),
        config,
        source_kind="protocol_geometry",
        buffer_m=config.gpx_corridor_buffer_m if buffer_m is None else buffer_m,
    )


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidInputError("max_age_seconds must be a positive integer")
    return value


def _optional_boolean(policy: dict[str, Any], name: str) -> bool:
    value = policy.get(name, False)
    if not isinstance(value, bool):
        raise InvalidInputError(f"policy.{name} must be a boolean")
    return value


def _duration(value: str) -> int:
    units = {"s": 1, "m": 60, "h": 60 * 60, "d": 24 * 60 * 60}
    if len(value) < 2 or value[-1] not in units:
        raise argparse.ArgumentTypeError("duration must use s, m, h, or d")
    try:
        amount = int(value[:-1])
    except ValueError as error:
        raise argparse.ArgumentTypeError("duration amount must be an integer") from error
    if amount <= 0:
        raise argparse.ArgumentTypeError("duration must be positive")
    return amount * units[value[-1]]


def _capabilities() -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "operation": "capabilities",
        "status": "ok",
        "manager_version": __version__,
        "manifest_versions": [MANIFEST_VERSION],
        "dataset_profiles": [DATASET_PROFILE],
        "coverage_schemes": [COVERAGE_SCHEME_VERSION],
        "overpass_query_template": OVERPASS_QUERY_TEMPLATE_VERSION,
        "coverage_inputs": ["gpx", "geojson", "bbox", "protocol_geometry"],
        "osm_formats": [".osm", ".osm.gz", ".osm.pbf"],
        "commands": [
            "ensure",
            "refresh",
            "list",
            "inspect",
            "import",
            "remove",
            "prune",
            "doctor",
            "capabilities",
        ],
    }


def _emit(document: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(document, sort_keys=True, separators=(",", ":")))
        return
    operation = document.get("operation", "operation")
    status = document.get("status", "ok")
    print(f"WarpBuster OSM Manager: {operation} {status}")
    if snapshot_id := document.get("snapshot_id"):
        print(f"Snapshot: {snapshot_id}")
    if manifest_path := document.get("manifest_path"):
        print(f"Manifest: {manifest_path}")
    if "snapshot_count" in document:
        print(f"Snapshots: {document['snapshot_count']}")
    if operation == "doctor":
        print(f"Cache: {document['cache_directory']}")
    warnings = document.get("warnings", [])
    for warning in warnings:
        print(f"Warning: {warning}")


def _emit_error(error: OsmManagerError, *, json_output: bool) -> None:
    if json_output:
        print(
            json.dumps(
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "operation": "error",
                    "status": "error",
                    "error_code": error.code.value,
                    "message": error.message,
                    "details": error.details or {},
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    else:
        print(f"error [{error.code.value}]: {error.message}", file=sys.stderr)
