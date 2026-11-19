"""Export the repository's algorithm without importing or executing application code."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("docs/algorithm/implementation.json")
SOURCE_ROOTS = (
    "src/warpbuster",
    "packages/osm-manager/src/warpbuster_osm_manager",
    "packages/osm-routing/src/warpbuster_osm_routing",
    "web/backend/warpbuster_web",
)
RESOURCE_PATHS = (
    "pyproject.toml",
    "web/pyproject.toml",
    "packages/osm-manager/pyproject.toml",
    "packages/osm-manager/osm-manager.example.toml",
    "packages/osm-routing/pyproject.toml",
    "packages/osm-routing/osm-routing.example.toml",
    "Dockerfile",
    "src/warpbuster/report/assets/report.html",
    "web/dist/res/index.html",
    "web/dist/assets/api.mjs",
    "web/dist/assets/fit-only.mjs",
    "web/dist/assets/fix-upload.mjs",
    "web/dist/assets/metrika.js",
    "web/dist/assets/polling.mjs",
    "web/dist/assets/result-map.mjs",
    "web/dist/assets/result.mjs",
    "web/dist/assets/run-summary.mjs",
)
DECISIONS = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.TryStar,
    ast.ExceptHandler,
    ast.Match,
    ast.match_case,
    ast.Assert,
    ast.BoolOp,
    ast.Compare,
    ast.comprehension,
    ast.Raise,
    ast.Break,
    ast.Continue,
)


def encode(node):
    """Keep suites structured and expressions as exact Python semantics via unparse."""
    if isinstance(node, list):
        return [encode(item) for item in node]
    if isinstance(node, ast.expr | ast.arguments | ast.alias | ast.keyword | ast.withitem):
        return {"python": ast.unparse(node)}
    if isinstance(node, ast.AST):
        return {
            "kind": type(node).__name__,
            **({"line": node.lineno} if hasattr(node, "lineno") else {}),
            **{
                name: encode(value)
                for name, value in ast.iter_fields(node)
                if name not in {"type_ignores", "type_comment"}
            },
        }
    return node


def decision_points(tree):
    points = []
    for ordinal, node in enumerate(ast.walk(tree)):
        if not isinstance(node, DECISIONS):
            continue
        # match_case and comprehension carry no source locations in CPython.
        line = getattr(node, "lineno", None)
        descendants = [n.lineno for n in ast.walk(node) if hasattr(n, "lineno")]
        line = line or min(descendants, default=1)
        if isinstance(node, ast.If | ast.IfExp | ast.While | ast.Assert):
            expression = ast.unparse(node.test)
        elif isinstance(node, ast.For | ast.AsyncFor):
            expression = f"{ast.unparse(node.target)} in {ast.unparse(node.iter)}"
        elif isinstance(node, ast.Try | ast.TryStar):
            expression = "handlers, orelse and finalbody are recorded in program"
        elif isinstance(node, ast.ExceptHandler):
            expression = ast.unparse(node.type) if node.type is not None else "BaseException"
        elif isinstance(node, ast.Match):
            expression = ast.unparse(node.subject)
        elif isinstance(node, ast.match_case):
            expression = ast.unparse(node.pattern)
            if node.guard is not None:
                expression += " if " + ast.unparse(node.guard)
        else:
            expression = ast.unparse(node)
        points.append(
            {"node": ordinal, "kind": type(node).__name__, "line": line, "python": expression}
        )
    return sorted(points, key=lambda item: (item["line"], item["node"]))


def symbols(tree, path, lines):
    result = []

    def visit(node, scope):
        definition = isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        if definition:
            scope = (*scope, node.name)
            item = {
                "id": path + "::" + ".".join(scope),
                "kind": type(node).__name__,
                "line": node.lineno,
                "end_line": node.end_lineno,
                "description": ast.get_docstring(node),
                "decorators": [ast.unparse(d) for d in node.decorator_list],
            }
            if isinstance(node, ast.ClassDef):
                item["bases"] = [ast.unparse(base) for base in node.bases]
                item["declared_fields"] = [
                    {
                        "name": ast.unparse(field.target),
                        "type": ast.unparse(field.annotation),
                        "required": field.value is None,
                        "default": encode(field.value),
                        "source_line": lines[field.lineno - 1].strip(),
                    }
                    for field in node.body
                    if isinstance(field, ast.AnnAssign)
                ]
                item["assigned_members"] = [
                    {"names": [ast.unparse(t) for t in field.targets], "value": encode(field.value)}
                    for field in node.body
                    if isinstance(field, ast.Assign)
                ]
            else:
                item["arguments"] = ast.unparse(node.args)
                item["returns"] = encode(node.returns)
            result.append(item)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, ())
    return result


def build_document(root=ROOT):
    modules = []
    for path in sorted(p for directory in SOURCE_ROOTS for p in (root / directory).rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        source = content.decode("utf-8")
        tree = ast.parse(source, filename=relative)
        modules.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "description": ast.get_docstring(tree),
                "symbols": symbols(tree, relative, source.splitlines()),
                "decision_points": decision_points(tree),
                "program": encode(tree),
            }
        )
    resources = []
    for name in RESOURCE_PATHS:
        path = root / name
        if not path.is_file():
            raise ValueError(f"missing resource: {name}")
        content = path.read_bytes()
        resources.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source": content.decode("utf-8"),
            }
        )
    fingerprints = {item["path"]: item["sha256"] for item in [*modules, *resources]}
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return {
        "schema_version": 1,
        "kind": "warpbuster_algorithm_implementation",
        "generator": {
            "path": "scripts/export_algorithm.py",
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
        },
        "source_digest_sha256": digest,
        "scope": {
            "python_roots": list(SOURCE_ROOTS),
            "resources": [item["path"] for item in resources],
            "external_implementation_included": False,
            "live_environment_included": False,
        },
        "encoding": {
            "program": "Python 3.14 AST statements; expressions use {python: ast.unparse(node)}",
            "suite_order": "body, orelse, handlers and finalbody retain source order",
            "decision_index": "Includes inline conditions, short-circuit Boolean operators, comparisons, comprehensions, loop controls and exceptions; node is the ast.walk ordinal",
            "required_field": "No class-body default; dataclass inheritance/factories may change the effective constructor default",
            "defaults": "Declarations and factories, not live environment values; see validation and profile methods in program",
            "authority": "program is exact implementation semantics; descriptions are source docstrings and may contain historical wording",
            "exceptions": "Explicit handlers/raises are indexed; implicit Python and dependency exceptions remain governed by Python and dependency semantics",
            "resources": "Verbatim local JS, configuration and template sources; no JS control-flow analysis is claimed",
        },
        "counts": {
            "python_modules": len(modules),
            "symbols": sum(len(m["symbols"]) for m in modules),
            "decision_points": sum(len(m["decision_points"]) for m in modules),
            "resources": len(resources),
        },
        "modules": modules,
        "resources": resources,
    }


def validate_flow(flow, catalogue):
    """Validate the documented graph and source references without running it."""
    if (
        not isinstance(flow, dict)
        or type(flow.get("schema_version")) is not int
        or flow["schema_version"] != 2
    ):
        raise ValueError("flow requires schema_version 2")
    if flow.get("implementation") != OUTPUT.name:
        raise ValueError("flow implementation must name the generated catalogue")
    for name in ("kind", "language", "documented_on"):
        if not isinstance(flow.get(name), str) or not flow[name]:
            raise ValueError(f"{name} must be a nonempty string")
    for name in ("semantics", "navigation"):
        if not isinstance(flow.get(name), dict) or not flow[name]:
            raise ValueError(f"{name} must be a nonempty object")
    scenarios = flow.get("scenario_matrix")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenario_matrix must be a nonempty array")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or any(
            not isinstance(scenario.get(key), str) or not scenario[key]
            for key in ("case", "expected")
        ):
            raise ValueError("scenario_matrix requires case and expected strings")
    groups = ("invariants", "entrypoints", "pipeline", "secondary_paths")
    ids = []
    for name in groups:
        items = flow.get(name)
        if not isinstance(items, list) or not items:
            raise ValueError(f"{name} must be a nonempty array")
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                raise ValueError(f"{name} entries require a nonempty string id")
            ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate flow ids")
    symbols = {s["id"] for module in catalogue["modules"] for s in module["symbols"]}
    resources = {item["path"] for item in catalogue["resources"]}
    stages = {item["id"] for item in flow["pipeline"]}

    def check(value):
        if isinstance(value, list):
            for item in value:
                check(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"implementation", "resource_paths"}:
                    if (
                        not isinstance(item, list)
                        or not item
                        or any(not isinstance(ref, str) for ref in item)
                    ):
                        raise ValueError(f"{key} must be a nonempty string array")
                    if len(item) != len(set(item)):
                        raise ValueError(f"duplicate {key} references")
                    if not set(item) <= (symbols if key == "implementation" else resources):
                        raise ValueError(f"unknown {key} reference")
                elif key == "next":
                    raise ValueError("use transitions, not next")
                elif key in {
                    "ordered_rules",
                    "transition_rules",
                    "completion_checks",
                    "exit_codes",
                }:
                    if not isinstance(item, list) or not item:
                        raise ValueError(f"{key} must be a nonempty array")
                    for rule in item:
                        required = ("when",) if key == "exit_codes" else ("when", "then")
                        if not isinstance(rule, dict) or any(
                            not isinstance(rule.get(field), str) or not rule[field]
                            for field in required
                        ):
                            raise ValueError(f"{key} requires nonempty rule strings")
                        if key == "exit_codes" and (
                            type(rule.get("code")) is not int or rule["code"] not in {0, 1, 2, 3}
                        ):
                            raise ValueError("invalid CLI exit code")
                        check(rule)
                else:
                    check(item)

    for name in groups:
        for item in flow[name]:
            if "implementation" not in item:
                raise ValueError(f"{name} entries require implementation references")
        check(flow[name])
    edges = flow.get("transitions")
    if not isinstance(edges, list) or not edges:
        raise ValueError("transitions must be a nonempty array")
    neighbours = {stage: set() for stage in stages}
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict) or any(
            not isinstance(edge.get(key), str) or not edge[key]
            for key in ("from", "to", "kind", "when")
        ):
            raise ValueError("transitions require from, to, kind and when strings")
        if edge["from"] not in stages or edge["to"] not in stages:
            raise ValueError("transition refers to an unknown stage")
        if edge["kind"] not in {"next", "substage"}:
            raise ValueError("invalid transition kind")
        identity = tuple(edge[key] for key in ("from", "to", "kind", "when"))
        if identity in seen_edges:
            raise ValueError("duplicate transition")
        seen_edges.add(identity)
        neighbours[edge["from"]].add(edge["to"])
    pending = []
    for entry in flow["entrypoints"]:
        if "start" in entry:
            if not isinstance(entry["start"], str) or entry["start"] not in stages:
                raise ValueError("entrypoint start must name a stage")
            pending.append(entry["start"])
    reached = set()
    while pending:
        stage = pending.pop()
        if stage not in reached:
            reached.add(stage)
            pending.extend(neighbours[stage] - reached)
    if reached != stages:
        raise ValueError(f"unreachable stages: {sorted(stages - reached)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the snapshot is stale")
    args = parser.parse_args()
    try:
        catalogue = build_document()
        validate_flow(
            json.loads((ROOT / "docs/algorithm/flow.json").read_text(encoding="utf-8")), catalogue
        )
    except (OSError, ValueError) as error:
        parser.exit(2, f"Algorithm export failed: {error}\n")
    document = json.dumps(catalogue, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path = ROOT / OUTPUT
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != document:
            parser.exit(
                1, "Algorithm snapshot is stale; run .venv/bin/python scripts/export_algorithm.py\n"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
    print(f"{'Checked' if args.check else 'Wrote'} {OUTPUT}")


if __name__ == "__main__":
    main()
