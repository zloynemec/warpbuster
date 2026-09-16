"""Static website navigation and asset regressions; no web runtime in Core."""

import base64
import hashlib
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

SITE = Path(__file__).resolve().parents[1] / "web" / "dist"


class Page(HTMLParser):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.nodes: list[tuple[str, dict[str, str | None]]] = []
        self.feed(path.read_text(encoding="utf-8"))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.nodes.append((tag, dict(attrs)))


@pytest.mark.parametrize(
    "route", ["index.html", "fix/index.html", "res/index.html", "faq/index.html", "404/index.html"]
)
def test_local_links_assets_and_fragments_resolve(route: str) -> None:
    """All page shells resolve their navigation and assets from the same static root."""
    page = Page(SITE / route)
    for _tag, attrs in page.nodes:
        for key in ("href", "src"):
            value = attrs.get(key)
            if not value:
                continue
            url = urlsplit(value)
            if url.scheme or url.netloc:
                continue
            target = SITE / url.path.lstrip("/") if url.path else SITE / route
            if target.is_dir():
                target /= "index.html"
            assert target.is_file(), f"Broken reference in {route}: {value}"
            if url.fragment:
                assert any(node.get("id") == url.fragment for _, node in Page(target).nodes), (
                    f"Missing anchor: {value}"
                )


def test_landing_links_to_fix_and_has_three_steps() -> None:
    page = Page(SITE / "index.html")
    assert any(tag == "a" and attrs.get("href") == "/fix" for tag, attrs in page.nodes)
    assert sum(tag == "li" for tag, _attrs in page.nodes) == 3


def test_result_script_references_existing_elements_after_copy_changes() -> None:
    """Removed status notices must not leave callbacks targeting missing DOM elements."""
    page = Page(SITE / "res/index.html")
    ids = {attrs.get("id") for _, attrs in page.nodes}
    script = "\n".join(
        (SITE / "assets" / name).read_text(encoding="utf-8")
        for name in ("result.mjs", "result-map.mjs", "run-summary.mjs")
    )
    references = set(re.findall(r'byId\("([^"]+)"\)', script))
    assert references <= ids, f"Missing result elements: {references - ids}"


def test_result_copy_does_not_expose_approximate_route_ids() -> None:
    script = (SITE / "assets" / "result.mjs").read_text(encoding="utf-8")
    assert "OSM-маршрут выбран приблизительно, фактический путь не подтверждён." in script
    assert "Выбранные варианты" not in script
    assert "${item.selected_route_id}" not in script


@pytest.mark.parametrize(
    "route", ["index.html", "fix/index.html", "faq/index.html", "404/index.html"]
)
def test_accessible_page_basics(route: str) -> None:
    page = Page(SITE / route)
    assert any(tag == "html" and attrs.get("lang") == "ru" for tag, attrs in page.nodes)
    assert sum(tag == "h1" for tag, _attrs in page.nodes) == 1
    assert any(tag == "main" and attrs.get("id") == "main" for tag, attrs in page.nodes)
    for tag, attrs in page.nodes:
        if tag == "img":
            if attrs.get("role") == "presentation":
                assert attrs.get("alt") == ""
            else:
                assert attrs.get("alt")


def test_fix_has_two_accessible_file_pickers_and_no_enabled_analysis() -> None:
    page = Page(SITE / "fix/index.html")
    inputs = [attrs for tag, attrs in page.nodes if tag == "input"]
    assert len(inputs) == 2
    assert {attrs["accept"] for attrs in inputs} == {".fit", ".gpx"}
    ids = {attrs.get("id") for _, attrs in page.nodes}
    for attrs in inputs:
        assert attrs["type"] == "file"
        assert "multiple" not in attrs
        assert "disabled" in attrs  # Enabled only when local event handlers are ready.
        for reference in ("aria-labelledby", "aria-describedby"):
            assert set((attrs.get(reference) or "").split()) <= ids
            assert attrs.get(reference)
    assert any(
        tag == "button" and attrs.get("id") == "analyze-button" and "disabled" in attrs
        for tag, attrs in page.nodes
    )
    assert not any(tag == "form" for tag, _attrs in page.nodes)


def test_original_logo_preserved_byte_for_byte() -> None:
    original = (SITE.parent / "assets" / "warpbuster-logo-original.png").read_bytes()
    assert hashlib.sha256(original).hexdigest() == (
        "234ae3f6b6e0841a3e9f95d69ab88fef584933d76fae20e3d2136f2099829dc1"
    )


@pytest.mark.parametrize("asset", ["warpbuster-logo.svg", "favicon.svg"])
def test_brand_assets_embed_original_and_preserve_alpha(asset: str) -> None:
    svg = ET.parse(SITE / "assets" / asset).getroot()
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    image = svg.find("svg:image", namespace)
    assert image is not None
    encoded = image.attrib["href"].removeprefix("data:image/png;base64,")
    assert (
        base64.b64decode(encoded, validate=True)
        == (SITE.parent / "assets" / "warpbuster-logo-original.png").read_bytes()
    )
    matrix = svg.find(".//svg:feColorMatrix", namespace)
    assert matrix is not None
    assert matrix.attrib["values"].split()[-5:] == ["0", "0", "0", "1", "0"]
    assert svg.find(".//svg:feFuncA", namespace) is None


@pytest.mark.parametrize("route", ["index.html", "fix/index.html", "faq/index.html"])
def test_shared_branding_on_both_pages(route: str) -> None:
    page = Page(SITE / route)
    logos = [
        attrs for tag, attrs in page.nodes if tag == "img" and attrs.get("class") == "brand-logo"
    ]
    assert len(logos) == 2  # Header and footer.
    assert all(logo.get("src") == "/assets/warpbuster-logo.svg" for logo in logos)
    assert any(
        tag == "link"
        and attrs.get("rel") == "icon"
        and urlsplit(attrs.get("href") or "").path == "/assets/favicon.svg"
        for tag, attrs in page.nodes
    )


@pytest.mark.parametrize("route", ["index.html", "fix/index.html", "res/index.html"])
def test_faq_is_reachable_from_each_user_flow(route: str) -> None:
    assert any(
        tag == "a" and attrs.get("href") == "/faq" for tag, attrs in Page(SITE / route).nodes
    )
