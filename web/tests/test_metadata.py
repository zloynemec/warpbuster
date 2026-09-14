"""Social crawlers get public previews without JavaScript or an owner cookie."""

import json
import secrets
import struct
import time
import uuid
from html.parser import HTMLParser

import pytest
from starlette.testclient import TestClient
from warpbuster_web.app import create_app
from warpbuster_web.config import WebConfig
from warpbuster_web.metadata import render_page, result_metadata

ORIGIN = "https://trail.example"


class Head(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nodes = []
        self.feed(html.split("</head>")[0])

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))

    def meta(self, key):
        values = [
            attrs["content"]
            for tag, attrs in self.nodes
            if tag == "meta" and key in (attrs.get("name"), attrs.get("property"))
        ]
        assert len(values) == 1, (key, values)
        return values[0]


@pytest.fixture
def app(tmp_path):
    return create_app(
        WebConfig(data_dir=tmp_path / "private", public_origin=ORIGIN), start_worker=False
    )


def ready_job(app):
    store = app.state.store
    token = store.new_session()
    uid, _ = store.reserve(store.owner(token), str(uuid.uuid4()))
    directory = app.state.config.data_dir / uid
    directory.mkdir()
    (directory / "result.json").write_text(
        json.dumps(
            {
                "outcome": "repaired",
                "partial": True,
                "performance": {
                    "distance_m": 12340,
                    "timer_duration_seconds": 3723,
                    "average_pace_seconds_per_km": 301,
                    "total_ascent_m": 420,
                    "distance_quality": "uncertain",
                },
                "source_path": "SECRET.fit",
                "athlete": '<script>alert("SECRET")</script>',
                "tracks": {"original": [[[55.123456, 37.654321]]]},
            }
        )
    )
    store.state(uid, "ready", has_fit=True)
    return uid, token


def test_home_has_unique_complete_metadata_and_public_image(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/?utm_source=ignored", headers={"X-Forwarded-Host": "evil.example"})
        head = Head(response.text)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Strava" in head.meta("description")
        assert head.meta("og:description") == head.meta("description")
        assert head.meta("twitter:title") == head.meta("og:title")
        assert head.meta("twitter:card") == "summary_large_image"
        assert head.meta("og:type") == "website"
        assert head.meta("og:locale") == "ru_RU"
        assert head.meta("og:url") == ORIGIN + "/"
        assert sum(tag == "title" for tag, _ in head.nodes) == 1
        assert ("link", {"rel": "canonical", "href": ORIGIN + "/"}) in head.nodes
        assert "evil.example" not in response.text
        image = client.get(head.meta("og:image"))
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", image.content[16:24]) == (
            int(head.meta("og:image:width")),
            int(head.meta("og:image:height")),
        )
        assert head.meta("twitter:image") == head.meta("og:image")
        assert head.meta("og:image:alt")
        assert "set-cookie" not in response.headers


def test_faq_is_public_without_javascript_and_has_canonical_metadata(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/faq/")
        assert response.status_code == 200
        assert response.url.path == "/faq"
        head = Head(response.text)
        assert head.meta("og:url") == ORIGIN + "/faq"
        assert head.meta("og:title").startswith("FAQ")
        assert head.meta("description") == head.meta("og:description")
        assert "set-cookie" not in response.headers
        assert not any(tag == "script" for tag, _ in head.nodes)
        page = Head(response.text.replace("</head>", ""))
        ids = {attrs.get("id") for _, attrs in page.nodes}
        assert {"algorithm", "privacy", "sharing", "stages"} <= ids
        assert sum(tag == "h3" for tag, _ in page.nodes) == 8


def test_ready_preview_is_server_rendered_and_identical_for_owner_and_crawler(app):
    uid, token = ready_job(app)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(f"/res/{uid}", headers={"User-Agent": "TelegramBot"})
        head = Head(response.text)
        assert response.status_code == 200
        assert "12,34 км" in head.meta("og:title")
        assert "Трек обработан" in head.meta("og:title")
        description = head.meta("og:description")
        for value in ("1:02:03", "5:01 мин/км", "420 м", "неточными", "невосстановленной"):
            assert value in description
        assert head.meta("description") == head.meta("twitter:description") == description
        assert head.meta("og:url") == ORIGIN + f"/res/{uid}"
        for private in (token, "SECRET", "55.123456", "37.654321", str(app.state.config.data_dir)):
            assert private not in response.text
        assert "no-store" in response.headers["cache-control"]
        assert "noindex" in response.headers["x-robots-tag"]
        assert "set-cookie" not in response.headers
        assert client.head(f"/res/{uid}").status_code == 200
        assert client.get(f"/api/results/{uid}/download").status_code == 403
        client.cookies.set(app.state.config.cookie_name, token)
        assert client.get(f"/res/{uid}").text == response.text


@pytest.mark.parametrize(
    ("status", "expired", "code", "title"),
    [
        ("uploading", False, 200, "обрабатывается"),
        ("queued", False, 200, "обрабатывается"),
        ("processing", False, 200, "обрабатывается"),
        ("failed", False, 200, "не завершилась"),
        ("expired", False, 410, "истёк"),
        ("ready", True, 410, "истёк"),
    ],
)
def test_non_ready_previews_never_include_stored_metrics(app, status, expired, code, title):
    uid, _ = ready_job(app)
    store = app.state.store
    store.state(uid, status)
    if expired:
        with store.connect() as db:
            db.execute("UPDATE jobs SET expires=? WHERE uid=?", (time.time() - 1, uid))
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(f"/res/{uid}")
        assert response.status_code == code
        head = Head(response.text)
        assert title in head.meta("og:title")
        assert "12,34" not in response.text
        assert "SECRET" not in response.text


def test_missing_result_has_generic_metadata_and_404(app):
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get(f"/res/{secrets.token_urlsafe(32)}")
        assert response.status_code == 404
        assert "не найден" in Head(response.text).meta("og:title")


@pytest.mark.parametrize("outcome", ["unchanged", "unresolved"])
def test_no_repair_and_missing_metrics_are_not_advertised_as_repaired(tmp_path, outcome):
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"outcome": outcome, "performance": {"distance_m": None}}))
    title, description = result_metadata(path)
    assert "Трек обработан" not in title
    assert "0,00 км" not in title
    assert "Темп" not in description
    path.write_text("{incomplete")
    assert result_metadata(path)[0] == "Результат обработки трека — WarpBuster"


def test_metadata_escapes_html_and_does_not_add_tags(app):
    hostile = '"><script>alert("test")</script>&'
    html = render_page(
        app.state.config.static_dir / "index.html",
        origin=ORIGIN,
        route="/",
        title=hostile,
        description=hostile,
    )
    head = Head(html)
    assert head.meta("og:title") == hostile
    assert head.meta("description") == hostile
    assert not any(tag == "script" for tag, _ in head.nodes)
