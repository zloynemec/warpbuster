"""Package bootstrap tests."""

import subprocess
import sys
from importlib.resources import files

import warpbuster
import warpbuster.config
import warpbuster.models.activity
import warpbuster.report.html


def test_package_imports() -> None:
    """The package and bootstrap namespaces are importable."""
    assert warpbuster.__version__ == "0.1.0"


def test_html_template_is_packaged() -> None:
    """The interactive report template is available through package resources."""
    template = (
        files("warpbuster.report")
        .joinpath("assets")
        .joinpath("report.html")
        .read_text(encoding="utf-8")
    )
    assert "__WARPBUSTER_REPORT_DATA__" in template
    assert "connect-src https://unpkg.com" in template


def test_core_reconstruction_import_does_not_load_osm_companion() -> None:
    """The optional routing runtime stays lazy until OSM opt-in constructs its adapter."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import warpbuster.reconstruction; "
                "assert 'warpbuster_osm_routing' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
