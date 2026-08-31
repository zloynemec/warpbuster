"""Tests for the bootstrap command-line interface."""

import pytest

from warpbuster import __version__
from warpbuster.cli import main


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """The CLI exposes the package version."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])

    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"warpbuster {__version__}"


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """The empty bootstrap CLI remains discoverable."""
    assert main([]) == 0
    assert "usage: warpbuster" in capsys.readouterr().out
