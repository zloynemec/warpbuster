"""Package bootstrap tests."""

import warpbuster
import warpbuster.config
import warpbuster.models.activity


def test_package_imports() -> None:
    """The package and bootstrap namespaces are importable."""
    assert warpbuster.__version__ == "0.1.0.dev0"
