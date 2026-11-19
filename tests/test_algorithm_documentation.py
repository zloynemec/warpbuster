"""The algorithm catalogue must cover source branches without executing the core."""

import ast
import copy
import json
import platform

import pytest

from scripts.export_algorithm import (
    OUTPUT,
    RESOURCE_PATHS,
    ROOT,
    SOURCE_ROOTS,
    build_document,
    decision_points,
    encode,
    validate_flow,
)


@pytest.fixture(scope="module")
def catalogue():
    return build_document()


@pytest.fixture
def flow():
    return json.loads((ROOT / "docs/algorithm/flow.json").read_text(encoding="utf-8"))


def test_algorithm_snapshot_matches_current_source(catalogue):
    assert json.loads((ROOT / OUTPUT).read_text(encoding="utf-8")) == catalogue


def test_flow_references_real_symbols_stages_and_resources(flow, catalogue):
    validate_flow(flow, catalogue)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("pipeline", 0, "implementation"), "module.py::function", "string array"),
        (("pipeline", 0, "implementation"), [], "string array"),
        (("pipeline", 0, "implementation"), ["missing.py::function"], "unknown implementation"),
        (("pipeline", 0, "ordered_rules"), "rule", "nonempty array"),
        (("pipeline", 0, "ordered_rules"), [{"when": "input accepted"}], "rule strings"),
        (("entrypoints", 0, "start"), [], "start must name"),
        (("transitions", 0, "from"), "missing_stage", "unknown stage"),
        (("transitions", 0, "when"), "", "when strings"),
        (("transitions", 0, "kind"), "invented", "transition kind"),
        (("transitions",), [], "nonempty array"),
        (("schema_version",), 1, "schema_version 2"),
        (("schema_version",), 2.0, "schema_version 2"),
        (("semantics",), [], "nonempty object"),
        (("scenario_matrix",), [], "nonempty array"),
        (("entrypoints", 1, "exit_codes"), [{"code": False, "when": "done"}], "CLI exit code"),
    ],
)
def test_flow_rejects_malformed_structure(flow, catalogue, path, value, message):
    target = flow
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=message):
        validate_flow(flow, catalogue)


def test_flow_rejects_duplicate_ids_references_and_edges(flow, catalogue):
    mutations = [
        (lambda f: f["pipeline"].append(copy.deepcopy(f["pipeline"][0])), "duplicate flow ids"),
        (
            lambda f: f["pipeline"][0]["implementation"].append(
                f["pipeline"][0]["implementation"][0]
            ),
            "duplicate implementation",
        ),
        (
            lambda f: f["transitions"].append(copy.deepcopy(f["transitions"][0])),
            "duplicate transition",
        ),
    ]
    for mutate, message in mutations:
        invalid = copy.deepcopy(flow)
        mutate(invalid)
        with pytest.raises(ValueError, match=message):
            validate_flow(invalid, catalogue)


def test_flow_rejects_unreachable_substage(flow, catalogue):
    flow["transitions"] = [
        edge for edge in flow["transitions"] if edge["to"] != "coordinate_free_course"
    ]
    with pytest.raises(ValueError, match=r"unreachable stages.*coordinate_free_course"):
        validate_flow(flow, catalogue)


def test_flow_rejects_missing_required_group(flow, catalogue):
    del flow["entrypoints"]
    with pytest.raises(ValueError, match="entrypoints must be a nonempty array"):
        validate_flow(flow, catalogue)


def test_flow_rejects_legacy_or_nested_navigation(flow, catalogue):
    flow["pipeline"][0]["ordered_rules"][0]["next"] = "detect_integrity"
    with pytest.raises(ValueError, match="use transitions"):
        validate_flow(flow, catalogue)


@pytest.fixture
def export_root(tmp_path):
    for name in SOURCE_ROOTS:
        directory = tmp_path / name
        directory.mkdir(parents=True)
        (directory / "example.py").write_text("def choose(): return 1\n", encoding="utf-8")
    for name in RESOURCE_PATHS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public resource\n", encoding="utf-8")
    return tmp_path


def test_export_ignores_unlisted_javascript(export_root):
    before = build_document(export_root)
    (export_root / "web/dist/assets/debug.js").write_text("PRIVATE_MARKER", encoding="utf-8")
    assert build_document(export_root) == before


def test_export_names_missing_resource(export_root):
    (export_root / "web/dist/assets/run-summary.mjs").unlink()
    with pytest.raises(ValueError, match=r"missing resource: web/dist/assets/run-summary\.mjs"):
        build_document(export_root)


def test_export_records_interpreter_and_is_repeatable(export_root, monkeypatch):
    before = build_document(export_root)
    assert before["generator"]["python_version"] == platform.python_version()
    assert before["generator"]["python_implementation"] == platform.python_implementation()
    assert build_document(export_root) == before
    monkeypatch.setattr("scripts.export_algorithm.platform.python_version", lambda: "3.15.0")
    after = build_document(export_root)
    assert after["generator"]["python_version"] == "3.15.0"
    assert before["source_digest_sha256"] == after["source_digest_sha256"]


def test_catalogue_retains_nested_branches_exceptions_and_inline_conditions():
    tree = ast.parse("""
def choose(items, enabled=True):
    try:
        for item in items:
            if enabled and item > 0:
                return item if item != 2 else 0
        else:
            raise ValueError("empty")
    except ValueError:
        return [x for x in items if x > 1]
    finally:
        enabled = False
""")
    kinds = {point["kind"] for point in decision_points(tree)}
    assert {
        "Try",
        "For",
        "If",
        "BoolOp",
        "Compare",
        "IfExp",
        "Raise",
        "ExceptHandler",
        "comprehension",
    } <= kinds
    function = encode(tree)["body"][0]
    attempt = function["body"][0]
    assert attempt["body"][0]["orelse"][0]["kind"] == "Raise"
    assert attempt["handlers"][0]["body"][0]["kind"] == "Return"
    assert attempt["finalbody"][0]["kind"] == "Assign"
    assert function["args"] == {"python": "items, enabled=True"}
