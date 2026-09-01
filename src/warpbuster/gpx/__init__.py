"""Separate GPX activity and reference-course adapters."""

from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course
from warpbuster.gpx.reader import GpxReadError, read_gpx

__all__ = ["GpxCourseReadError", "GpxReadError", "read_gpx", "read_gpx_course"]
