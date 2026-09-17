"""Shared file-to-result API for WarpBuster's user interfaces."""

from warpbuster.reconstruction.approximate_contract import DecisionReason
from warpbuster.reconstruction.endpoints import EndpointHints, UserEndpoint

from .config import (
    DEFAULT_REPAIR_POLICY,
    LEGACY_REPAIR_POLICY,
    DEMMode,
    OSMMode,
    PipelineConfig,
    RepairPolicy,
)
from .coverage import CoverageStatus, ObservedGpsCoverage
from .repair import PipelineError, RepairRun, run_repair

APPROXIMATE_DECISION_REASONS = frozenset(item.value for item in DecisionReason)

__all__ = [
    "APPROXIMATE_DECISION_REASONS",
    "DEFAULT_REPAIR_POLICY",
    "LEGACY_REPAIR_POLICY",
    "CoverageStatus",
    "DEMMode",
    "EndpointHints",
    "OSMMode",
    "ObservedGpsCoverage",
    "PipelineConfig",
    "PipelineError",
    "RepairPolicy",
    "RepairRun",
    "UserEndpoint",
    "run_repair",
]
