"""Command-line entry point for WarpBuster Core."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from warpbuster import __version__
from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.report.inspect import inspect_console, inspect_json


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="warpbuster",
        description="Detect physically impossible GNSS data in FIT activities.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="inspect the contents of a FIT activity",
    )
    inspect_parser.add_argument("fit_file", type=Path, help="path to the FIT file")
    inspect_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the WarpBuster command-line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "inspect":
        try:
            activity = read_fit(args.fit_file)
        except (FitReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(inspect_json(activity) if args.json else inspect_console(activity))
        return 0
    parser.print_help()
    return 0
