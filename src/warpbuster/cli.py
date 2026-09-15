"""Command-line entry point for WarpBuster Core."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from warpbuster import __version__
from warpbuster.activity_reader import ActivityReadError, read_activity
from warpbuster.fit.diff import diff_fit
from warpbuster.fit.reader import FitReadError
from warpbuster.fit.validate import validate_fit
from warpbuster.fit.writer import (
    default_output_path,
)
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.integrity import analyze_integrity
from warpbuster.models.integrity import IntegrityConfidence, IntegrityStatus
from warpbuster.models.reconstruction import RepairPlanStatus
from warpbuster.pipeline import (
    DEFAULT_REPAIR_POLICY,
    LEGACY_REPAIR_POLICY,
    OSMMode,
    PipelineConfig,
    PipelineError,
    RepairRun,
    run_repair,
)
from warpbuster.report.analyze import analyze_console, analyze_json
from warpbuster.report.fit import (
    diff_console,
    diff_json,
    validation_console,
    validation_json,
    write_result_console,
    write_result_json,
    write_result_report,
)
from warpbuster.report.html import (
    HtmlReportError,
    ensure_html_output_available,
    write_analyze_html,
    write_repair_html,
)
from warpbuster.report.inspect import inspect_console, inspect_json
from warpbuster.report.repair import repair_console, repair_json, repair_report

_AUTO_HTML_PATH = object()


def default_analyze_html_path(activity_file: str | Path) -> Path:
    """Return the default opt-in analysis HTML path next to the source activity."""
    source = Path(activity_file)
    return source.with_name(f"{source.stem}.analyze.html")


def default_repair_html_path(activity_file: str | Path) -> Path:
    """Return the default opt-in repair HTML path next to the source FIT."""
    source = Path(activity_file)
    return source.with_name(f"{source.stem}.repair.html")


def _resolve_html_argument(
    value: object,
    activity_file: Path,
    *,
    repair: bool,
) -> Path | None:
    if value is _AUTO_HTML_PATH:
        return (
            default_repair_html_path(activity_file)
            if repair
            else default_analyze_html_path(activity_file)
        )
    if value is None or isinstance(value, Path):
        return value
    raise TypeError("invalid --html argument")


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
        nargs="?",
        const=_AUTO_HTML_PATH,
        type=Path,
        metavar="REPORT",
        help=(
            "write an interactive HTML report with an online map "
            "(default: <activity-stem>.analyze.html)"
        ),
    )
    analyze_parser.add_argument(
        "--course",
        type=Path,
        help=("show a reference GPX course in the HTML report without using it for detection"),
    )
    analyze_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="atomically replace an existing HTML report",
    )
    analyze_parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show pipeline details; repeat for detector diagnostics",
    )
    repair_options = argparse.ArgumentParser(add_help=False)
    repair_options.add_argument("activity_file", type=Path, help="path to the original FIT file")
    repair_options.add_argument(
        "--dry-run",
        action="store_true",
        help="build and report RepairPlan without writing FIT",
    )
    repair_options.add_argument(
        "--output",
        type=Path,
        help="output FIT path (default: <stem>.fixed.fit)",
    )
    repair_options.add_argument(
        "--overwrite",
        action="store_true",
        help="atomically replace existing FIT and HTML outputs",
    )
    repair_options.add_argument(
        "--min-confidence",
        type=_confidence_argument,
        choices=tuple(IntegrityConfidence),
        default=None,
        metavar="{low,medium,high}",
        help="repair confidence threshold (process/OSM: medium; legacy repair: high)",
    )
    repair_options.add_argument(
        "--min-invalidation-confidence",
        type=_confidence_argument,
        choices=(IntegrityConfidence.HIGH, IntegrityConfidence.MEDIUM),
        default=None,
        help="coordinate invalidation threshold (process: medium; repair: high)",
    )
    repair_options.add_argument(
        "--fill-missing-from-course",
        action="store_true",
        default=None,
        help="fill original/invalidated gaps from GPX, including assumed course start/finish",
    )
    repair_options.add_argument(
        "--osm-graph-id",
        help="automatically apply OSM fallback from this exact prepared graph after GPX",
    )
    repair_options.add_argument(
        "--osm-routing-config",
        type=Path,
        help="optional osm-routing.toml used with --osm-graph-id",
    )
    repair_options.add_argument(
        "--osm-cache-dir",
        type=Path,
        help="optional prepared graph cache override used with --osm-graph-id",
    )
    repair_options.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable RepairPlan",
    )
    repair_options.add_argument(
        "--html",
        nargs="?",
        const=_AUTO_HTML_PATH,
        type=Path,
        metavar="REPORT",
        help=(
            "write an interactive repair HTML report with an online map "
            "(default: <activity-stem>.repair.html)"
        ),
    )
    repair_options.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="show anchor matching thresholds and safety details",
    )
    repair_parser = subparsers.add_parser(
        "repair",
        parents=[repair_options],
        help="repair a FIT from a safe course-based plan",
    )
    repair_parser.add_argument(
        "--course",
        type=Path,
        help="optional reference GPX course; without it only coordinate cleaning is available",
    )
    process_parser = subparsers.add_parser(
        "process",
        parents=[repair_options],
        help="process FIT + GPX with automatic OSM preparation and the shared repair policy",
        description="Complete FIT/GPX processing, including OSM snapshots and routing graphs.",
    )
    process_parser.add_argument("course_file", type=Path, help="reference GPX course")
    process_parser.add_argument(
        "--osm-mode",
        type=OSMMode,
        choices=tuple(OSMMode),
        default=OSMMode.AUTO,
        help="auto downloads missing coverage; offline uses cache; disabled uses GPX only",
    )
    process_parser.add_argument(
        "--work-dir",
        type=Path,
        default=PipelineConfig().data_dir,
        help="service files and reusable OSM caches (default: .warpbuster)",
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
        args.html = _resolve_html_argument(args.html, args.activity_file, repair=False)
        if args.course is not None and args.html is None:
            print("error: analyze --course requires --html", file=sys.stderr)
            return 2
        if args.overwrite and args.html is None:
            print("error: analyze --overwrite requires --html", file=sys.stderr)
            return 2
        if args.course is not None and args.activity_file.suffix.casefold() != ".fit":
            print("error: analyze --course requires a FIT activity input", file=sys.stderr)
            return 2
        try:
            activity = read_activity(args.activity_file)
        except (ActivityReadError, OSError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        integrity = analyze_integrity(activity)
        course = None
        if args.course is not None:
            try:
                course = read_gpx_course(args.course)
            except (GpxCourseReadError, OSError) as error:
                print(f"error: {error}", file=sys.stderr)
                return 2
        if args.html is not None:
            try:
                write_analyze_html(
                    activity,
                    integrity,
                    args.html,
                    course=course,
                    overwrite=args.overwrite,
                )
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
    if args.command in {"repair", "process"}:
        complete = args.command == "process"
        if complete:
            args.course = args.course_file
        args.html = _resolve_html_argument(args.html, args.activity_file, repair=True)
        if args.activity_file.suffix.casefold() != ".fit":
            print("error: repair input must be the original FIT file", file=sys.stderr)
            return 2
        policy = (
            DEFAULT_REPAIR_POLICY
            if complete
            else replace(
                LEGACY_REPAIR_POLICY,
                minimum_confidence=(
                    DEFAULT_REPAIR_POLICY.minimum_confidence
                    if args.osm_graph_id
                    else LEGACY_REPAIR_POLICY.minimum_confidence
                ),
            )
        )
        policy = replace(
            policy,
            fill_missing_from_course=(
                args.fill_missing_from_course
                if args.fill_missing_from_course is not None
                else policy.fill_missing_from_course
            ),
            minimum_confidence=args.min_confidence or policy.minimum_confidence,
            minimum_invalidation_confidence=(
                args.min_invalidation_confidence or policy.minimum_invalidation_confidence
            ),
        )
        args.min_confidence = policy.minimum_confidence
        if policy.fill_missing_from_course and args.course is None:
            print("error: --fill-missing-from-course requires --course", file=sys.stderr)
            return 2
        if args.osm_graph_id is None and (
            args.osm_routing_config is not None or args.osm_cache_dir is not None
        ):
            print(
                "error: --osm-routing-config/--osm-cache-dir require --osm-graph-id",
                file=sys.stderr,
            )
            return 2
        if args.html is not None:
            try:
                ensure_html_output_available(
                    args.html,
                    overwrite=args.overwrite,
                    protected_paths=(args.activity_file,)
                    + ((args.course,) if args.course is not None else ()),
                )
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
        execution = PipelineConfig(
            data_dir=args.work_dir if complete else PipelineConfig().data_dir,
            osm_mode=args.osm_mode if complete else OSMMode.DISABLED,
            osm_graph_id=args.osm_graph_id,
            osm_routing_config=args.osm_routing_config,
            osm_cache_dir=args.osm_cache_dir,
        )
        try:
            run = run_repair(
                args.activity_file,
                args.course,
                args.output,
                policy=policy,
                config=execution,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            )
        except PipelineError as error:
            print(f"error: {error}", file=sys.stderr)
            return 3 if error.code == "repair_refused" else 2
        activity, course, integrity = run.activity, run.course, run.integrity
        config, plan, selection = run.reconstruction_config, run.plan, run.selection
        osm_result, ranking_result = run.osm.discovery, run.ranking
        if run.osm.warning:
            print(f"warning: {run.osm.warning}", file=sys.stderr)
        elif complete and run.osm.status == "unavailable":
            print(
                f"warning: OSM [{run.osm.error_code}]; retaining GPX and cleaning", file=sys.stderr
            )
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
                        osm_result=osm_result,
                        ranking_result=ranking_result,
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
                    osm_result=osm_result,
                    ranking_result=ranking_result,
                )
                if args.json
                else repair_console(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                    verbosity=args.verbose,
                    osm_result=osm_result,
                    ranking_result=ranking_result,
                )
            )
            if complete and args.json:
                rendered = _process_json(rendered, run)
            print(_html_notice(rendered, args.html) if not args.json else rendered)
            if selection.has_changes or plan.status is RepairPlanStatus.NOT_NEEDED:
                return 0
            return 3
        if not selection.has_changes:
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
                        osm_result=osm_result,
                        ranking_result=ranking_result,
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
                    osm_result=osm_result,
                    ranking_result=ranking_result,
                )
                if args.json
                else repair_console(
                    plan,
                    course,
                    config,
                    minimum_confidence=args.min_confidence,
                    verbosity=args.verbose,
                    osm_result=osm_result,
                    ranking_result=ranking_result,
                )
            )
            if complete and args.json:
                rendered = _process_json(rendered, run)
            print(_html_notice(rendered, args.html) if not args.json else rendered)
            if plan.status is RepairPlanStatus.NOT_NEEDED:
                return 0
            print(
                "error: no coordinate invalidation or reconstruction candidate meets minimum confidence "
                f"{args.min_confidence.value.upper()}",
                file=sys.stderr,
            )
            return 3
        result = run.write_result
        assert result is not None
        if args.html is not None:
            try:
                fixed_activity = run.fixed_activity
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
                    osm_result=osm_result,
                    ranking_result=ranking_result,
                )
            except (FitReadError, HtmlReportError, OSError) as error:
                print(
                    f"error: FIT was written to {result.output_path}, "
                    f"but HTML report failed: {error}",
                    file=sys.stderr,
                )
                return 3
        if args.json and (complete or args.osm_graph_id is not None):
            document = write_result_report(result)
            document["repair_plan"] = repair_report(
                plan,
                course,
                config,
                minimum_confidence=args.min_confidence,
                osm_result=osm_result,
                ranking_result=ranking_result,
            )
            rendered = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        else:
            rendered = write_result_json(result) if args.json else write_result_console(result)
        if complete and args.json:
            rendered = _process_json(rendered, run)
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


def _process_json(rendered: str, run: RepairRun) -> str:
    """Include reproducible acquisition provenance in local full-process JSON."""
    document = json.loads(rendered)
    document["pipeline"] = {
        "policy": run.policy.as_dict(),
        "osm": {
            "status": run.osm.status,
            "stage": run.osm.stage,
            "error_code": run.osm.error_code,
            "audit": run.osm.private_audit,
        },
    }
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
