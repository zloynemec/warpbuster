"""Bounded Overpass HTTP acquisition with injectable transport."""

from __future__ import annotations

import gzip
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from warpbuster_osm_manager.config import DATASET_PROFILE, OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import BoundingBox
from warpbuster_osm_manager.osm_validation import OsmValidationResult, validate_osm_xml

OVERPASS_QUERY_TEMPLATE_VERSION = "pedestrian-routing-v1-overpass-ql-v1"


@dataclass(frozen=True, slots=True)
class HttpDownload:
    """Metadata returned by one streamed HTTP download."""

    final_url: str
    size_bytes: int
    status_code: int


class HttpTransport(Protocol):
    """Injectable streaming transport for deterministic tests."""

    def download(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        destination: Path,
        timeout_seconds: float,
        maximum_bytes: int,
        chunk_bytes: int,
    ) -> HttpDownload: ...


class UrlLibTransport:
    """TLS-verifying standard-library HTTP transport."""

    def download(
        self,
        *,
        url: str,
        body: bytes,
        headers: dict[str, str],
        destination: Path,
        timeout_seconds: float,
        maximum_bytes: int,
        chunk_bytes: int,
    ) -> HttpDownload:
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        started = time.monotonic()
        size = 0
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_encoding = response.headers.get("Content-Encoding", "").casefold()
            source = gzip.GzipFile(fileobj=response) if content_encoding == "gzip" else response
            with destination.open("wb") as output:
                while chunk := source.read(chunk_bytes):
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise OsmManagerError(
                            ErrorCode.RESPONSE_LIMIT_EXCEEDED,
                            "Overpass response exceeds maximum_download_bytes",
                            {"size_bytes": size, "limit_bytes": maximum_bytes},
                        )
                    if time.monotonic() - started > timeout_seconds:
                        raise TimeoutError("Overpass total download timeout exceeded")
                    output.write(chunk)
            status = getattr(response, "status", 200)
            return HttpDownload(
                final_url=response.geturl(), size_bytes=size, status_code=int(status)
            )


@dataclass(frozen=True, slots=True)
class DownloadedOsm:
    """One validated temporary Overpass response."""

    path: Path
    size_bytes: int
    validation: OsmValidationResult


class OsmFetcher(Protocol):
    """Minimal acquisition interface consumed by the cache service."""

    def fetch(self, bounds: BoundingBox, destination: Path) -> DownloadedOsm: ...


class OverpassClient:
    """Fetch dataset-profile OSM XML within bounded non-crossing boxes."""

    def __init__(
        self,
        config: OsmManagerConfig,
        *,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = config
        self.transport = transport or UrlLibTransport()
        self._sleep = sleep
        self._random_uniform = random_uniform

    def fetch(self, bounds: BoundingBox, destination: Path) -> DownloadedOsm:
        """Download and validate one Overpass bbox with bounded retry."""
        query = build_overpass_query(bounds, self.config.network_timeout_seconds)
        body = urllib.parse.urlencode({"data": query}).encode()
        headers = {
            "Accept": "application/xml",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.config.user_agent,
        }
        last_error: Exception | None = None
        for attempt in range(self.config.maximum_retry_count + 1):
            try:
                result = self.transport.download(
                    url=self.config.overpass_url,
                    body=body,
                    headers=headers,
                    destination=destination,
                    timeout_seconds=self.config.network_timeout_seconds,
                    maximum_bytes=self.config.maximum_download_bytes,
                    chunk_bytes=self.config.http_read_chunk_bytes,
                )
                _validate_final_url(self.config.overpass_url, result.final_url)
                validation = validate_osm_xml(destination, self.config)
                return DownloadedOsm(
                    path=destination, size_bytes=result.size_bytes, validation=validation
                )
            except OsmManagerError as error:
                if error.code in {
                    ErrorCode.RESPONSE_LIMIT_EXCEEDED,
                    ErrorCode.OSM_DATA_INVALID,
                }:
                    raise
                last_error = error
            except urllib.error.HTTPError as error:
                if error.code != 429 and error.code < 500:
                    raise OsmManagerError(
                        ErrorCode.OVERPASS_UNAVAILABLE,
                        f"Overpass request failed with HTTP {error.code}",
                        {"http_status": error.code, "attempt": attempt + 1},
                    ) from error
                last_error = error
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last_error = error
            if attempt < self.config.maximum_retry_count:
                backoff = self.config.retry_backoff_seconds * (2**attempt)
                jitter = self._random_uniform(0.0, self.config.retry_jitter_seconds)
                self._sleep(backoff + jitter)
        raise OsmManagerError(
            ErrorCode.OVERPASS_UNAVAILABLE,
            "Overpass is unavailable after bounded retries",
            {
                "attempt_count": self.config.maximum_retry_count + 1,
                "endpoint": self.config.overpass_url,
                "last_error": type(last_error).__name__ if last_error else "unknown",
            },
        ) from last_error


def build_overpass_query(bounds: BoundingBox, timeout_seconds: float) -> str:
    """Build the stable dataset profile v1 Overpass QL request."""
    timeout = max(1, int(timeout_seconds))
    return (
        f'[out:xml][timeout:{timeout}];(way["highway"]({bounds.as_overpass()}););(._;>;);out meta;'
    )


def _validate_final_url(expected: str, actual: str) -> None:
    expected_parts = urllib.parse.urlparse(expected)
    actual_parts = urllib.parse.urlparse(actual)
    if actual_parts.scheme != "https" or actual_parts.hostname != expected_parts.hostname:
        raise OsmManagerError(
            ErrorCode.OVERPASS_UNAVAILABLE,
            "Overpass redirected to an unexpected host or scheme",
            {"expected_host": expected_parts.hostname, "actual_host": actual_parts.hostname},
        )


def dataset_profile_metadata() -> dict[str, str]:
    """Return stable acquisition-profile identifiers for manifests/capabilities."""
    return {
        "dataset_profile": DATASET_PROFILE,
        "query_template_version": OVERPASS_QUERY_TEMPLATE_VERSION,
    }
