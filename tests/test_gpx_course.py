"""GPX reference-course reader tests."""

from pathlib import Path
from typing import cast
from xml.etree import ElementTree

import pytest

from tests.gpx_factory import write_gpx_activity
from warpbuster.gpx.course import GpxCourseReadError, read_gpx_course

_GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def test_course_reader_preserves_continuous_segments_and_distance(tmp_path: Path) -> None:
    """Track segments become separate course polylines with cumulative distances."""
    path = tmp_path / "course.gpx"
    write_gpx_activity(
        path,
        [
            [(55.0, 37.0, None, 100.0), (55.0, 37.001, None, 110.0)],
            [(56.0, 38.0, None, None), (56.001, 38.0, None, None)],
        ],
    )

    course = read_gpx_course(path)

    assert course.version == "1.1"
    assert course.creator == "WarpBuster tests"
    assert course.point_count == 4
    assert len(course.segments) == 2
    assert course.segments[0].points[0].elevation_m == 100.0
    assert course.segments[0].points[0].cumulative_distance_m == 0.0
    assert course.segments[0].length_m > 60.0
    assert course.total_distance_m == pytest.approx(
        sum(segment.length_m for segment in course.segments)
    )


def test_course_reader_accepts_route_points_without_activity_semantics(tmp_path: Path) -> None:
    """A GPX route is valid reference geometry even though it is not an activity track."""
    path = tmp_path / "route.gpx"
    ElementTree.register_namespace("", _GPX_NAMESPACE)
    root = ElementTree.Element(
        f"{{{_GPX_NAMESPACE}}}gpx",
        {"version": "1.1", "creator": "route-test"},
    )
    route = ElementTree.SubElement(root, f"{{{_GPX_NAMESPACE}}}rte")
    for latitude, longitude in ((55.0, 37.0), (55.001, 37.001)):
        ElementTree.SubElement(
            route,
            f"{{{_GPX_NAMESPACE}}}rtept",
            {"lat": str(latitude), "lon": str(longitude)},
        )
    path.write_bytes(
        cast(bytes, ElementTree.tostring(root, encoding="utf-8", xml_declaration=True))
    )

    course = read_gpx_course(path)

    assert len(course.segments) == 1
    assert course.point_count == 2


@pytest.mark.parametrize(
    "contents, message",
    [
        (b"<gpx><trk><trkseg><trkpt lat='55' lon='37'/></trkseg></trk></gpx>", "two points"),
        (b"<!DOCTYPE gpx><gpx/>", "DTD"),
        (
            b"<gpx><rte><rtept lat='nan' lon='37'/><rtept lat='55' lon='37'/></rte></gpx>",
            "out of range",
        ),
    ],
)
def test_course_reader_rejects_unusable_or_unsafe_input(
    tmp_path: Path,
    contents: bytes,
    message: str,
) -> None:
    """Malformed reference data is rejected before reconstruction."""
    path = tmp_path / "invalid.gpx"
    path.write_bytes(contents)

    with pytest.raises(GpxCourseReadError, match=message):
        read_gpx_course(path)
