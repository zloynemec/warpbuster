"""Command-line entry point for WarpBuster Core."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from warpbuster import __version__


def build_parser() -> argparse.ArgumentParser:
    """Build the bootstrap command-line parser."""
    parser = argparse.ArgumentParser(
        prog="warpbuster",
        description="Detect physically impossible GNSS data in FIT activities.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the WarpBuster command-line interface."""
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0
