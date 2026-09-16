"""Offline Valhalla graph cache for WarpBuster OSM snapshots."""

__version__ = "0.1.0.dev0"

from warpbuster_osm_routing.config import RoutingCacheConfig
from warpbuster_osm_routing.dem_cache import DemCache, DemCacheConfig, DemSnapshot
from warpbuster_osm_routing.dem_coverage import DemCoveragePlan, plan_coverage
from warpbuster_osm_routing.dem_profile import MAPZEN_SKADI_EGM96_V1, SkadiDatasetProfile
from warpbuster_osm_routing.elevation_backend import ElevationBackend, ValhallaElevationBackend
from warpbuster_osm_routing.elevation_comparison import (
    AltitudeComparison,
    AltitudeObservation,
    compare_altitudes,
)
from warpbuster_osm_routing.elevation_filter import (
    ElevationFilteringPolicy,
    ElevationTotals,
    filter_elevations,
)
from warpbuster_osm_routing.elevation_gpx import (
    ElevationExportConfig,
    ElevationGpxArtifact,
    ElevationGpxWriter,
)
from warpbuster_osm_routing.elevation_service import (
    ElevationProfile,
    ElevationSample,
    ElevationSamplingConfig,
    ElevationService,
)
from warpbuster_osm_routing.graph_cache import GraphCache
from warpbuster_osm_routing.models import (
    GeoPoint,
    RouteAlternativesRequest,
    RouteAlternativesResult,
    RouteCandidate,
    RouteRequest,
    RouteResult,
    RouteStatus,
    Snapshot,
    SnapshotDataFile,
    SpikeResult,
    StartContext,
    StartContextPoint,
)
from warpbuster_osm_routing.profiles import TRAIL_RUNNING_V1, TrailRunningProfile
from warpbuster_osm_routing.route_service import RouteService
from warpbuster_osm_routing.spike import run_spike

__all__ = [
    "MAPZEN_SKADI_EGM96_V1",
    "TRAIL_RUNNING_V1",
    "AltitudeComparison",
    "AltitudeObservation",
    "DemCache",
    "DemCacheConfig",
    "DemCoveragePlan",
    "DemSnapshot",
    "ElevationBackend",
    "ElevationExportConfig",
    "ElevationFilteringPolicy",
    "ElevationGpxArtifact",
    "ElevationGpxWriter",
    "ElevationProfile",
    "ElevationSample",
    "ElevationSamplingConfig",
    "ElevationService",
    "ElevationTotals",
    "GeoPoint",
    "GraphCache",
    "RouteAlternativesRequest",
    "RouteAlternativesResult",
    "RouteCandidate",
    "RouteRequest",
    "RouteResult",
    "RouteService",
    "RouteStatus",
    "RoutingCacheConfig",
    "SkadiDatasetProfile",
    "Snapshot",
    "SnapshotDataFile",
    "SpikeResult",
    "StartContext",
    "StartContextPoint",
    "TrailRunningProfile",
    "ValhallaElevationBackend",
    "__version__",
    "compare_altitudes",
    "filter_elevations",
    "plan_coverage",
    "run_spike",
]
