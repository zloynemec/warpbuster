"""Parameterized synthetic inputs for local reconstruction, not a private-track copy."""

from itertools import pairwise
from pathlib import Path

from tests.activity_factory import eastward_observations
from tests.fit_factory import write_trajectory_activity
from tests.gpx_factory import write_gpx_activity
from warpbuster.fit.reader import read_fit
from warpbuster.geo import geodesic_distance_m
from warpbuster.gpx.course import read_gpx_course
from warpbuster.models.activity import ActivityData
from warpbuster.models.reconstruction import CourseData


def local_fixture(
    tmp_path: Path,
    *,
    count: int = 600,
    missing: tuple[tuple[int, int], ...] = ((0, 19), (150, 179), (560, 599)),
    spikes: tuple[int, ...] = (),
    name: str = "activity",
    position_fields: bool = True,
    detour: tuple[int, int, float] | None = None,
) -> tuple[ActivityData, CourseData]:
    full = eastward_observations(
        [float(i) for i in range(count)], [float(i * 2) for i in range(count)]
    )
    movement = list(full)
    distances = [float(i * 2) for i in range(count)]
    speeds = [2.0] * count
    if detour is not None:
        start, end, height_m = detour
        midpoint = (start + end) / 2
        half_span = (end - start) / 2
        movement = [
            (elapsed, lat + height_m * max(0, 1 - abs(i - midpoint) / half_span) / 111195, lon)
            for i, (elapsed, lat, lon) in enumerate(full)
        ]
        distances = [0.0]
        for previous, current in pairwise(movement):
            increment = geodesic_distance_m(previous[1], previous[2], current[1], current[2])
            distances.append(distances[-1] + increment)
        speeds = [2.0, *(b - a for a, b in pairwise(distances))]
    observations = [
        (
            int(elapsed or 0),
            None if any(a <= i <= b for a, b in missing) else 56.0 if i in spikes else lat,
            None if any(a <= i <= b for a, b in missing) else lon,
        )
        for i, (elapsed, lat, lon) in enumerate(movement)
    ]
    path = tmp_path / f"{name}.fit"
    course_path = tmp_path / f"{name}.gpx"
    write_trajectory_activity(
        path,
        observations,
        retain_invalid_position_fields=position_fields,
        distances_m=distances,
        speeds_mps=speeds,
        altitudes_m=[100.0] * count,
    )
    write_gpx_activity(
        course_path,
        [
            [
                (lat, lon, None, None)
                for _elapsed, lat, lon in full
                if lat is not None and lon is not None
            ]
        ],
    )
    return read_fit(path), read_gpx_course(course_path)
