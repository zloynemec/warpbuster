"""Human- and machine-readable reports."""

from warpbuster.report.analyze import analyze_console, analyze_json, analyze_report
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
from warpbuster.report.inspect import inspect_console, inspect_json, inspect_report
from warpbuster.report.repair import repair_console, repair_json, repair_report

__all__ = [
    "HtmlReportError",
    "analyze_console",
    "analyze_json",
    "analyze_report",
    "diff_console",
    "diff_json",
    "ensure_html_output_available",
    "inspect_console",
    "inspect_json",
    "inspect_report",
    "repair_console",
    "repair_json",
    "repair_report",
    "validation_console",
    "validation_json",
    "write_analyze_html",
    "write_repair_html",
    "write_result_console",
    "write_result_json",
]
