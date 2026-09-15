"""Keep repair orchestration behind the shared Core boundary."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
        elif isinstance(node, ast.Import):
            yield from (item.name for item in node.names)


def test_web_does_not_assemble_detector_reconstruction_or_writer():
    forbidden = ("warpbuster.integrity", "warpbuster.reconstruction", "warpbuster.fit.writer")
    violations = [
        (str(path.relative_to(ROOT)), name)
        for path in (ROOT / "web/backend/warpbuster_web").rglob("*.py")
        for name in imports(path)
        if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden)
    ]
    assert not violations, f"Call warpbuster.pipeline.run_repair instead: {violations}"


def test_core_has_no_web_dependency():
    assert not [
        (str(path.relative_to(ROOT)), name)
        for path in (ROOT / "src/warpbuster").rglob("*.py")
        for name in imports(path)
        if name == "warpbuster_web" or name.startswith("warpbuster_web.")
    ]
