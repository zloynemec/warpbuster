"""Select an activity input adapter without leaking formats into the detector."""

from __future__ import annotations

from pathlib import Path

from warpbuster.fit.reader import FitReadError, read_fit
from warpbuster.gpx.reader import GpxReadError, read_gpx
from warpbuster.models.activity import ActivityData


class ActivityReadError(ValueError):
    """Raised when an activity format is unsupported or its reader rejects it."""


def read_activity(path: str | Path) -> ActivityData:
    """Read a FIT or GPX activity selected by its case-insensitive suffix."""
    source_path = Path(path)
    suffix = source_path.suffix.casefold()
    try:
        if suffix == ".fit":
            return read_fit(source_path)
        if suffix == ".gpx":
            return read_gpx(source_path)
    except (FitReadError, GpxReadError) as error:
        raise ActivityReadError(str(error)) from error
    raise ActivityReadError(f"unsupported activity format for {source_path}; expected .fit or .gpx")
