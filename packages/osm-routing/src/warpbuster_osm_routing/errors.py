"""Stable failures returned by the OSM routing package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RoutingError(Exception):
    """One expected, machine-readable routing package failure."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


# Task 010A exposed this name. Keep it as a compatibility alias while production
# commands use the broader RoutingError name.
RoutingSpikeError = RoutingError
