"""Versioned request-time routing profiles independent from graph identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import valhalla

from warpbuster_osm_routing.errors import RoutingError

PROFILE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class TrailRunningProfile:
    """Explicit Valhalla pedestrian policy for reconstructing trail geometry."""

    profile_id: str = "warpbuster-trail-running-v1"
    engine_name: str = "Valhalla"
    minimum_engine_version: tuple[int, int, int] = (3, 8, 3)
    maximum_engine_version_exclusive: tuple[int, int, int] = (3, 9, 0)
    pedestrian_type: str = "foot"
    walking_speed_kph: float = 5.1
    maximum_hiking_difficulty: int = 3
    use_tracks: float = 1.0
    walkway_factor: float = 0.8
    use_hills: float = 1.0
    exclude_unpaved: bool = False
    use_ferry: float = 0.0
    step_penalty_seconds: float = 30.0
    alley_factor: float = 2.0
    driveway_factor: float = 5.0
    use_living_streets: float = 0.6

    def costing_options(self) -> dict[str, dict[str, Any]]:
        """Return a fresh Valhalla request fragment with no implicit profile defaults."""
        return {
            "pedestrian": {
                "type": self.pedestrian_type,
                "walking_speed": self.walking_speed_kph,
                "max_hiking_difficulty": self.maximum_hiking_difficulty,
                "use_tracks": self.use_tracks,
                "walkway_factor": self.walkway_factor,
                "use_hills": self.use_hills,
                "exclude_unpaved": self.exclude_unpaved,
                "use_ferry": self.use_ferry,
                "step_penalty": self.step_penalty_seconds,
                "alley_factor": self.alley_factor,
                "driveway_factor": self.driveway_factor,
                "use_living_streets": self.use_living_streets,
            }
        }

    def canonical_document(self) -> dict[str, Any]:
        """Return only machine semantics covered by the profile identity."""
        return {
            "profile_schema": PROFILE_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "engine": {
                "name": self.engine_name,
                "compatibility": ">=3.8.3,<3.9",
            },
            "costing": "pedestrian",
            "costing_options": self.costing_options(),
        }

    def policy_document(self) -> dict[str, list[str]]:
        """Describe guarantees and limitations without changing semantic identity."""
        return {
            "hard_rules": [
                "respect Valhalla pedestrian access parsed from OSM",
                "reject sac_scale above demanding_mountain_hiking (T3)",
            ],
            "soft_preferences": [
                "prefer pedestrian ways and tracks within bounded detour",
                "allow unpaved surfaces",
                "do not penalize natural trail hills",
                "allow steps with a transition penalty",
                "strongly avoid ferries when a connected land route exists",
            ],
            "limitations": [
                "private and destination access retain Valhalla 3.8.3 semantics",
                "use_ferry=0 is a preference and requires future route post-audit",
                "snap distance and remote correlation rejection are deferred to Task 010D",
                "directional pedestrian tags require a separate audited route contract",
                "profile does not encode athlete pace or observed activity evidence",
                "pedestrian areas require a future relation-capable dataset profile",
            ],
        }

    def sha256(self) -> str:
        encoded = json.dumps(
            self.canonical_document(), sort_keys=True, separators=(",", ":")
        ).encode()
        return hashlib.sha256(encoded).hexdigest()

    def inspection_document(self) -> dict[str, Any]:
        document = self.canonical_document()
        document.update(self.policy_document())
        document["profile_sha256"] = self.sha256()
        document["installed_engine_version"] = str(valhalla.__version__)
        document["engine_compatible"] = self.supports_engine(str(valhalla.__version__))
        return document

    def require_compatible_engine(self) -> None:
        installed = str(valhalla.__version__)
        if not self.supports_engine(installed):
            raise RoutingError(
                "PROFILE_ENGINE_INCOMPATIBLE",
                f"{self.profile_id} does not support Valhalla {installed}",
                {"supported": ">=3.8.3,<3.9", "installed": installed},
            )

    def supports_engine(self, version: str) -> bool:
        parsed = _parse_release(version)
        return self.minimum_engine_version <= parsed < self.maximum_engine_version_exclusive


TRAIL_RUNNING_V1 = TrailRunningProfile()


def apply_profile(request: dict[str, Any], profile: TrailRunningProfile = TRAIL_RUNNING_V1) -> None:
    """Mutate an internal Valhalla request with the complete versioned policy."""
    profile.require_compatible_engine()
    request["costing"] = "pedestrian"
    request["costing_options"] = profile.costing_options()


def _parse_release(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    try:
        parsed = tuple(int(part.split("-", maxsplit=1)[0]) for part in parts[:3])
    except ValueError:
        return (0, 0, 0)
    padded = (*parsed, 0, 0, 0)
    return padded[0], padded[1], padded[2]
