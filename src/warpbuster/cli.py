"""Command-line entry point for WarpBuster Core."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from warpbuster import __version__
from warpbuster.activity_reader import ActivityReadError, read_activity
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityStatus
from warpbuster.report.analyze import analyze_console, analyze_json
from warpbuster.report.inspect import inspect_console, inspect_json


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="warpbuster",
        description="Detect physically impossible GNSS data in FIT and GPX activities.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="inspect the contents of a FIT or GPX activity",
    )
    inspect_parser.add_argument("activity_file", type=Path, help="path to a FIT or GPX file")
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON report",
    )
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="analyze local physical transitions in a FIT or GPX activity",
    )
    analyze_parser.add_argument("activity_file", type=Path, help="path to a FIT or GPX file")
    analyze_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON report",
    )
    analyze_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show pipeline details; repeat for detector diagnostics",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the WarpBuster command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        try:
            activity = read_activity(args.activity_file)
        except (ActivityReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(inspect_json(activity) if args.json else inspect_console(activity))
        return 0
    if args.command == "analyze":
        try:
            activity = read_activity(args.activity_file)
        except (ActivityReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        integrity = analyze_integrity(activity)
        print(
            analyze_json(activity, integrity)
            if args.json
            else analyze_console(activity, integrity, verbosity=args.verbose)
        )
        if integrity.status in {IntegrityStatus.CORRUPTED, IntegrityStatus.SUSPICIOUS}:
            return 1
        return 0
    parser.print_help()
    return 0
