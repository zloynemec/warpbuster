"""Command-line entry point for offline production maintenance."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .cleanup import AdminError, purge_osm_cache, purge_tracks


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="warpbuster-admin")
    command.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("WARPBUSTER_WEB_DATA", ".warpbuster-web")),
    )
    actions = command.add_subparsers(dest="action", required=True)
    tracks = actions.add_parser("purge-tracks", help="remove track pairs created on a UTC date")
    tracks.add_argument("--date", required=True, help="UTC date in YYYY-MM-DD format")
    tracks.add_argument("--confirm", choices=("DELETE",))
    cache = actions.add_parser("purge-osm-cache", help="remove all OSM snapshots and graphs")
    cache.add_argument("--confirm", choices=("DELETE",))
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.action == "purge-tracks":
            result = purge_tracks(args.data_dir, args.date, confirm=args.confirm == "DELETE")
        else:
            result = purge_osm_cache(args.data_dir, confirm=args.confirm == "DELETE")
    except AdminError as error:
        print(json.dumps({"status": "refused", "error": str(error)}))
        return 2
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
