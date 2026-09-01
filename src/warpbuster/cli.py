"""Command-line entry point for WarpBuster Core."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from warpbuster import __version__
from warpbuster.activity_reader import ActivityReadError, read_activity
from warpbuster.config import CourseReconstructionConfig
from warpbuster.fit.diff import diff_fit
from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.fit.validate import validate_fit
from warpbuster.fit.writer import FitWriteError, write_repaired_fit
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, IntegrityStatus
from warpbuster.models.reconstruction import RepairPlanStatus
from warpbuster.reconstruction import build_course_repair_plan, select_repair_intervals
from warpbuster.report.analyze import analyze_console, analyze_json
from warpbuster.report.fit import (
    diff_console,
    diff_json,
    validation_console,
    validation_json,
    write_result_console,
    write_result_json,
)
from warpbuster.report.inspect import inspect_console, inspect_json
from warpbuster.report.repair import repair_console, repair_json


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
    repair_parser = subparsers.add_parser(
        "repair",
        help="repair a FIT from a safe course-based plan",
    )
    repair_parser.add_argument("activity_file", type=Path, help="path to the original FIT file")
    repair_parser.add_argument(
        "--course",
        type=Path,
        required=True,
        help="path to a reference GPX course",
    )
    repair_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and report RepairPlan without writing FIT",
    )
    repair_parser.add_argument(
        "--output",
        type=Path,
        help="output FIT path (default: <stem>.fixed.fit)",
    )
    repair_parser.add_argument(
        "--min-confidence",
        type=_confidence_argument,
        choices=tuple(IntegrityConfidence),
        default=IntegrityConfidence.HIGH,
        metavar="{low,medium,high}",
        help="repair candidates at this confidence or higher (default: high)",
    )
    repair_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable RepairPlan",
    )
    repair_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show anchor matching thresholds and safety details",
    )
    validate_parser = subparsers.add_parser(
        "validate",
        help="validate FIT decoding, CRC, timestamps, coordinates, and distance",
    )
    validate_parser.add_argument("fit_file", type=Path, help="path to a FIT file")
    validate_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable validation report",
    )
    diff_parser = subparsers.add_parser(
        "diff",
        help="compare original and repaired FIT preservation",
    )
    diff_parser.add_argument("original_fit", type=Path, help="path to the original FIT")
    diff_parser.add_argument("fixed_fit", type=Path, help="path to the repaired FIT")
    diff_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable FIT diff",
    )
    diff_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show a bounded sample of changed fields",
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
    if args.command == "repair":
        if args.activity_file.suffix.casefold() != ".fit":
            print("error: repair input must be the original FIT file", file=sys.stderr)
            return 2
        try:
            activity = read_fit(args.activity_file)
            course = read_gpx_course(args.course)
        except (FitReadError, GpxCourseReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        integrity = analyze_integrity(activity)
        config = CourseReconstructionConfig()
        plan = build_course_repair_plan(activity, integrity, course, config)
        selection = select_repair_intervals(plan, args.min_confidence)
        if args.dry_run:
            print(
                repair_json(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                )
                if args.json
                else repair_console(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                    verbosity=args.verbose,
                )
            )
            if selection.selected_interval_plans or plan.status is RepairPlanStatus.NOT_NEEDED:
                return 0
            return 3
        if not selection.selected_interval_plans:
            print(
                repair_json(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                )
                if args.json
                else repair_console(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                    verbosity=args.verbose,
                )
            )
            print(
                "error: no reconstruction candidate meets minimum confidence "
                f"{args.min_confidence.value.upper()}",
                file=sys.stderr,
            )
            return 3
        try:
            result = write_repaired_fit(
                activity,
                plan,
                args.output,
                minimum_confidence=args.min_confidence,
            )
        except (FitWriteError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 3
        print(write_result_json(result) if args.json else write_result_console(result))
        return 0
    if args.command == "validate":
        validation_result = validate_fit(args.fit_file)
        print(
            validation_json(validation_result)
            if args.json
            else validation_console(validation_result)
        )
        return 0 if validation_result.valid else 4
    if args.command == "diff":
        try:
            diff_result = diff_fit(args.original_fit, args.fixed_fit)
        except (FitReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print(
            diff_json(diff_result)
            if args.json
            else diff_console(diff_result, verbosity=args.verbose)
        )
        if (
            not diff_result.structure_compatible
            or not diff_result.definitions_unchanged
            or diff_result.unexpected_changed_field_count
        ):
            return 4
        return 0
    parser.print_help()
    return 0


def _confidence_argument(value: str) -> IntegrityConfidence:
    try:
        return IntegrityConfidence(value.casefold())
    except ValueError as error:
        raise argparse.ArgumentTypeError("confidence must be one of: low, medium, high") from error
