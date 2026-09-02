"""Isolation boundary regression for the standalone routing distribution."""

from __future__ import annotations

import ast
from pathlib import Path


def test_routing_package_does_not_import_core_or_osm_manager() -> None:
    source_root = Path(__file__).parents[1] / "src" / "warpbuster_osm_routing"
    forbidden: list[str] = []
    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if module == "warpbuster" or module.startswith("warpbuster."):
                    forbidden.append(f"{path.name}: {module}")
                if module == "warpbuster_osm_manager" or module.startswith(
                    "warpbuster_osm_manager."
                ):
                    forbidden.append(f"{path.name}: {module}")
    assert forbidden == []
