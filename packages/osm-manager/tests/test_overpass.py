"""Bounded Overpass query, retry, redirects, and response validation."""

from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest
from conftest import osm_xml

from warpbuster_osm_manager.config import OsmManagerConfig
from warpbuster_osm_manager.errors import ErrorCode, OsmManagerError
from warpbuster_osm_manager.models import BoundingBox
from warpbuster_osm_manager.overpass import (
    HttpDownload,
    OverpassClient,
    UrlLibTransport,
    build_overpass_query,
)


class FakeTransport:
    def __init__(self, content: str, *, failures: int = 0, final_url: str | None = None) -> None:
        self.content = content
        self.failures = failures
        self.final_url = final_url
        self.calls: list[dict[str, object]] = []

    def download(self, **kwargs: object) -> HttpDownload:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise URLError("temporary")
        destination = kwargs["destination"]
        assert isinstance(destination, Path)
        destination.write_text(self.content, encoding="utf-8")
        return HttpDownload(
            final_url=self.final_url or str(kwargs["url"]),
            size_bytes=destination.stat().st_size,
            status_code=200,
        )


def test_query_fetches_highways_and_referenced_nodes() -> None:
    bounds = BoundingBox(33.6, 44.4, 33.7, 44.5)
    query = build_overpass_query(bounds, 180)
    assert 'way["highway"]' in query
    assert "(._;>;);" in query
    assert "out meta" in query
    assert bounds.as_overpass() in query


def test_client_sends_identified_post_and_retries_boundedly(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    bounds = BoundingBox(33.6, 44.4, 33.7, 44.5)
    transport = FakeTransport(osm_xml(bounds), failures=1)
    sleeps: list[float] = []
    from dataclasses import replace

    config = replace(manager_config, maximum_retry_count=1, retry_backoff_seconds=0.1)
    client = OverpassClient(
        config,
        transport=transport,
        sleep=sleeps.append,
        random_uniform=lambda _low, _high: 0,
    )
    result = client.fetch(bounds, tmp_path / "download.osm")
    assert result.validation.way_count == 1
    assert len(transport.calls) == 2
    assert sleeps == [0.1]
    headers = transport.calls[0]["headers"]
    assert isinstance(headers, dict)
    assert headers["User-Agent"] == config.user_agent


def test_unexpected_redirect_and_invalid_xml_are_not_published(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    bounds = BoundingBox(33.6, 44.4, 33.7, 44.5)
    redirect = FakeTransport(osm_xml(bounds), final_url="https://unexpected.test/data")
    with pytest.raises(OsmManagerError) as raised:
        OverpassClient(manager_config, transport=redirect).fetch(bounds, tmp_path / "redirect.osm")
    assert raised.value.code is ErrorCode.OVERPASS_UNAVAILABLE

    invalid = FakeTransport("<html>not osm</html>")
    with pytest.raises(OsmManagerError) as raised:
        OverpassClient(manager_config, transport=invalid).fetch(bounds, tmp_path / "invalid.osm")
    assert raised.value.code is ErrorCode.OSM_DATA_INVALID


def test_http_429_is_retried_only_within_configured_bound(
    tmp_path: Path, manager_config: OsmManagerConfig
) -> None:
    class RateLimitedTransport:
        def __init__(self) -> None:
            self.calls = 0

        def download(self, **_kwargs: object) -> HttpDownload:
            self.calls += 1
            raise HTTPError("https://overpass.test", 429, "rate limited", Message(), None)

    from dataclasses import replace

    config = replace(manager_config, maximum_retry_count=2)
    transport = RateLimitedTransport()
    with pytest.raises(OsmManagerError) as raised:
        OverpassClient(
            config,
            transport=transport,
            sleep=lambda _seconds: None,
            random_uniform=lambda _low, _high: 0,
        ).fetch(BoundingBox(33.6, 44.4, 33.7, 44.5), tmp_path / "limited.osm")
    assert raised.value.code is ErrorCode.OVERPASS_UNAVAILABLE
    assert transport.calls == 3


def test_streaming_transport_stops_at_response_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response(BytesIO):
        status = 200

        def __init__(self, value: bytes) -> None:
            super().__init__(value)
            self.headers: dict[str, str] = {}

        def geturl(self) -> str:
            return "https://overpass.test/api/interpreter"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda _request, timeout: Response(b"0123456789"),
    )
    with pytest.raises(OsmManagerError) as raised:
        UrlLibTransport().download(
            url="https://overpass.test/api/interpreter",
            body=b"data=query",
            headers={},
            destination=tmp_path / "response.osm",
            timeout_seconds=1,
            maximum_bytes=5,
            chunk_bytes=2,
        )
    assert raised.value.code is ErrorCode.RESPONSE_LIMIT_EXCEEDED
