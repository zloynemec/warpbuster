"""Package bootstrap tests."""

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
