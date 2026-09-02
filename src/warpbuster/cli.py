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
from warpbuster.fit.writer import (
    FitWriteError,
    default_output_path,
    write_repaired_fit,
)
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, IntegrityStatus
from warpbuster.models.reconstruction import RepairPlanStatus
from warpbuster.reconstruction import (
    build_course_repair_plan,
    build_missing_course_plan,
    merge_repair_plans,
    select_repair_intervals,
)
from warpbuster.report.analyze import analyze_console, analyze_json
from warpbuster.report.fit import (
    diff_console,
    diff_json,
    validation_console,
    validation_json,
    write_result_console,
    write_result_json,
)
from warpbuster.report.html import (
    HtmlReportError,
    ensure_html_output_available,
    write_analyze_html,
    write_repair_html,
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
        "--html",
        type=Path,
        metavar="REPORT",
        help="write an interactive HTML report with an online map",
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
        "--overwrite",
        action="store_true",
        help="atomically replace existing FIT and HTML outputs",
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
        "--fill-missing-from-course",
        action="store_true",
        help=("opt in to MEDIUM course-backed completion of missing prefix/suffix coordinates"),
    )
    repair_parser.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable RepairPlan",
    )
    repair_parser.add_argument(
        "--html",
        type=Path,
        metavar="REPORT",
        help="write an interactive repair HTML report with an online map",
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
        if args.html is not None:
            try:
                write_analyze_html(activity, integrity, args.html)
            except (HtmlReportError, OSError) as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
        rendered = (
            analyze_json(activity, integrity)
            if args.json
            else analyze_console(activity, integrity, verbosity=args.verbose)
        )
        print(_html_notice(rendered, args.html) if not args.json else rendered)
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
        if args.html is not None:
            try:
                ensure_html_output_available(args.html, overwrite=args.overwrite)
            except HtmlReportError as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
            fit_destination = (
                args.output if args.output is not None else default_output_path(args.activity_file)
            )
            if not args.dry_run and args.html.resolve() == fit_destination.resolve():
                print(
                    "error: HTML report path must differ from FIT output path",
                    file=sys.stderr,
                )
                return 2
        integrity = analyze_integrity(activity)
        config = CourseReconstructionConfig()
        plan = build_course_repair_plan(activity, integrity, course, config)
        if args.fill_missing_from_course:
            plan = merge_repair_plans(
                plan,
                build_missing_course_plan(activity, integrity, course, config),
            )
        selection = select_repair_intervals(plan, args.min_confidence)
        if args.dry_run:
            if args.html is not None:
                try:
                    write_repair_html(
                        activity,
                        integrity,
                        course,
                        plan,
                        config,
                        args.html,
                        minimum_confidence=args.min_confidence,
                        overwrite=args.overwrite,
                    )
                except (HtmlReportError, OSError) as error:
                    print(f"error: {error}", file=sys.stderr)
                    return 2
            rendered = (
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
            print(_html_notice(rendered, args.html) if not args.json else rendered)
            if selection.selected_interval_plans or plan.status is RepairPlanStatus.NOT_NEEDED:
                return 0
            return 3
        if not selection.selected_interval_plans:
            if args.html is not None:
                try:
                    write_repair_html(
                        activity,
                        integrity,
                        course,
                        plan,
                        config,
                        args.html,
                        minimum_confidence=args.min_confidence,
                        overwrite=args.overwrite,
                    )
                except (HtmlReportError, OSError) as error:
                    print(f"error: {error}", file=sys.stderr)
                    return 2
            rendered = (
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
            print(_html_notice(rendered, args.html) if not args.json else rendered)
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
                overwrite=args.overwrite,
            )
        except (FitWriteError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 3
        if args.html is not None:
            try:
                fixed_activity = read_fit(result.output_path)
                write_repair_html(
                    activity,
                    integrity,
                    course,
                    plan,
                    config,
                    args.html,
                    minimum_confidence=args.min_confidence,
                    fixed_activity=fixed_activity,
                    write_result=result,
                    overwrite=args.overwrite,
                )
            except (FitReadError, HtmlReportError, OSError) as error:
                print(
                    f"error: FIT was written to {result.output_path}, "
                    f"but HTML report failed: {error}",
                    file=sys.stderr,
                )
                return 3
        rendered = write_result_json(result) if args.json else write_result_console(result)
        print(_html_notice(rendered, args.html) if not args.json else rendered)
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


def _html_notice(rendered: str, output_path: Path | None) -> str:
    if output_path is None:
        return rendered
    return f"{rendered}\nHTML report: {output_path}"
