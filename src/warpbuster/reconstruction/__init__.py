"""Optional reconstruction providers operating after integrity detection."""

from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes
from warpbuster.reconstruction.candidate_ranking import rank_gap_candidates
from warpbuster.reconstruction.course import build_course_repair_plan
from warpbuster.reconstruction.local import build_repair_plan
from warpbuster.reconstruction.missing import build_missing_course_plan
from warpbuster.reconstruction.orchestration import merge_repair_plans
from warpbuster.reconstruction.osm import OSMReconstructionProvider, ValhallaRoutingClient
from warpbuster.reconstruction.osm_application import (
    apply_confirmed_osm_routes,
    osm_route_fingerprint,
)
from warpbuster.reconstruction.selection import select_repair_intervals

__all__ = [
    "OSMReconstructionProvider",
    "ValhallaRoutingClient",
    "apply_automatic_osm_routes",
    "apply_confirmed_osm_routes",
    "build_course_repair_plan",
    "build_missing_course_plan",
    "build_repair_plan",
    "merge_repair_plans",
    "osm_route_fingerprint",
    "rank_gap_candidates",
    "select_repair_intervals",
]
