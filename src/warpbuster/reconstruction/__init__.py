"""Optional reconstruction providers operating after integrity detection."""

from warpbuster.reconstruction.approximate_contract import (
    ApproximateSelectionPolicy,
    DemComparisonConfig,
    assess_gap_scope,
    assess_ranked_osm_candidate,
)
from warpbuster.reconstruction.automatic_osm import apply_automatic_osm_routes
from warpbuster.reconstruction.candidate_ranking import rank_gap_candidates
from warpbuster.reconstruction.course import build_course_repair_plan
from warpbuster.reconstruction.local import build_repair_plan
from warpbuster.reconstruction.missing import build_missing_course_plan
from warpbuster.reconstruction.orchestration import merge_repair_plans
from warpbuster.reconstruction.osm import (
    OSMReconstructionProvider,
    ValhallaRoutingClient,
    osm_gap_anchors,
)
from warpbuster.reconstruction.osm_application import (
    apply_confirmed_osm_routes,
    osm_route_fingerprint,
)
from warpbuster.reconstruction.selection import select_repair_intervals

__all__ = [
    "ApproximateSelectionPolicy",
    "DemComparisonConfig",
    "OSMReconstructionProvider",
    "ValhallaRoutingClient",
    "apply_automatic_osm_routes",
    "apply_confirmed_osm_routes",
    "assess_gap_scope",
    "assess_ranked_osm_candidate",
    "build_course_repair_plan",
    "build_missing_course_plan",
    "build_repair_plan",
    "merge_repair_plans",
    "osm_gap_anchors",
    "osm_route_fingerprint",
    "rank_gap_candidates",
    "select_repair_intervals",
]
