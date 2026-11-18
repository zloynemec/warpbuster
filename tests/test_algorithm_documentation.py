"""The algorithm catalogue must cover source branches without executing the core."""

import ast
import json

from scripts.export_algorithm import OUTPUT, ROOT, build_document, decision_points, encode


def test_algorithm_snapshot_matches_current_source():
    assert json.loads((ROOT / OUTPUT).read_text()) == build_document()


def test_flow_references_real_symbols_stages_and_resources():
    document = build_document()
    flow = json.loads((ROOT / "docs/algorithm/flow.json").read_text())
    symbol_ids = {symbol["id"] for module in document["modules"] for symbol in module["symbols"]}
    stage_ids = {stage["id"] for stage in flow["pipeline"]}
    resources = {item["path"] for item in document["resources"]}

    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "implementation" and isinstance(item, list):
                    assert set(item) <= symbol_ids, set(item) - symbol_ids
                elif key == "next":
                    assert item in stage_ids
                elif key == "resource_paths":
                    assert set(item) <= resources
                else:
                    check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    check(flow)
    all_ids = [
        item["id"]
        for group in ("invariants", "entrypoints", "pipeline", "secondary_paths")
        for item in flow[group]
    ]
    assert len(all_ids) == len(set(all_ids))
    assert flow["implementation"] == OUTPUT.name


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
