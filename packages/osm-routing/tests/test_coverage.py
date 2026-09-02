from __future__ import annotations

import pytest

from warpbuster_osm_routing.coverage import cell_id_for_point, contains, parse_coverage
from warpbuster_osm_routing.errors import RoutingError
from warpbuster_osm_routing.models import GeoPoint


def test_zoom_12_membership_is_explicit() -> None:
    coverage = parse_coverage(
        {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489"],
            "buffer_m": 1000,
            "area_km2": 1,
        }
    )

    assert cell_id_for_point(GeoPoint(44.0, 33.0)) == "12/2423/1489"
    assert contains(coverage, GeoPoint(44.0, 33.0))
    assert not contains(coverage, GeoPoint(45.0, 34.0))


@pytest.mark.parametrize(
    "coverage",
    [
        None,
        {"scheme": "future", "cell_ids": ["12/2423/1489"], "buffer_m": 0, "area_km2": 1},
        {"scheme": "web-mercator-v1", "cell_ids": [], "buffer_m": 0, "area_km2": 1},
        {
            "scheme": "web-mercator-v1",
            "cell_ids": ["12/2423/1489", "12/2423/1489"],
            "buffer_m": 0,
            "area_km2": 1,
        },
    ],
)
def test_invalid_coverage_is_rejected(coverage: object) -> None:
    with pytest.raises(RoutingError):
        parse_coverage(coverage)
