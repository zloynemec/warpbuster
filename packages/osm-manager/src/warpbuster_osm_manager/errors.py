"""Stable operational errors exposed by the CLI protocol."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Machine-readable error codes in protocol v1."""

    INVALID_INPUT = "INVALID_INPUT"
    INVALID_GPX = "INVALID_GPX"
    REQUEST_LIMIT_EXCEEDED = "REQUEST_LIMIT_EXCEEDED"
    OFFLINE_CACHE_MISS = "OFFLINE_CACHE_MISS"
    FRESH_CACHE_REQUIRED = "FRESH_CACHE_REQUIRED"
    OVERPASS_UNAVAILABLE = "OVERPASS_UNAVAILABLE"
    RESPONSE_LIMIT_EXCEEDED = "RESPONSE_LIMIT_EXCEEDED"
    OSM_DATA_INVALID = "OSM_DATA_INVALID"
    CACHE_IO_ERROR = "CACHE_IO_ERROR"
    CACHE_LOCK_TIMEOUT = "CACHE_LOCK_TIMEOUT"
    PROTOCOL_UNSUPPORTED = "PROTOCOL_UNSUPPORTED"


_EXIT_CODES = {
    ErrorCode.INVALID_INPUT: 2,
    ErrorCode.INVALID_GPX: 2,
    ErrorCode.REQUEST_LIMIT_EXCEEDED: 2,
    ErrorCode.PROTOCOL_UNSUPPORTED: 2,
    ErrorCode.OFFLINE_CACHE_MISS: 3,
    ErrorCode.FRESH_CACHE_REQUIRED: 3,
    ErrorCode.OVERPASS_UNAVAILABLE: 3,
    ErrorCode.RESPONSE_LIMIT_EXCEEDED: 4,
    ErrorCode.OSM_DATA_INVALID: 4,
    ErrorCode.CACHE_IO_ERROR: 5,
    ErrorCode.CACHE_LOCK_TIMEOUT: 5,
}


@dataclass(slots=True)
class OsmManagerError(Exception):
    """Expected failure with a stable code and safe structured details."""

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None

    @property
    def exit_code(self) -> int:
        """Return the stable CLI exit code for this error category."""
        return _EXIT_CODES[self.code]

    def __str__(self) -> str:
        return self.message


class InvalidInputError(OsmManagerError):
    """Invalid user-controlled coverage or configuration input."""

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode = ErrorCode.INVALID_INPUT,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code, message, details)


class RequestLimitError(OsmManagerError):
    """A configured geographic or resource bound was exceeded."""

    def __init__(self, message: str, *, details: dict[str, Any]) -> None:
        super().__init__(ErrorCode.REQUEST_LIMIT_EXCEEDED, message, details)
