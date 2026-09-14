"""Server-rendered social metadata using only allowlisted public result metrics."""

import json
import math
from html import escape
from pathlib import Path

META_START = "<!-- social-meta:start -->"
META_END = "<!-- social-meta:end -->"
HOME_TITLE = "WarpBuster — твой трейл. Без GPS-сбоев."
HOME_DESCRIPTION = (
    "Исправь GPS-сбои в записи пробежки, сравни треки и скачай исправленный FIT "
    "для Strava или приложения часов с поддержкой импорта FIT."
)
RESULT_TITLE = "Результат обработки трека — WarpBuster"
RESULT_DESCRIPTION = "Карта трека, показатели забега и сравнение исходной и исправленной записи."
IMAGE_PATH = "/assets/social-preview.jpg"
IMAGE_ALT = "Трейлраннер на горной тропе среди туманных хребтов"


def render_page(path: Path, *, origin: str, route: str, title: str, description: str) -> str:
    """Replace one bounded metadata block; never use request Host or forwarded headers."""
    page = path.read_text(encoding="utf-8")
    before, rest = page.split(META_START, 1)
    _, after = rest.split(META_END, 1)
    url = origin + route
    image = origin + IMAGE_PATH
    tags = [
        f"<title>{escape(title)}</title>",
        f'<link rel="canonical" href="{escape(url, quote=True)}">',
    ]
    for attribute, key, value in (
        ("name", "description", description),
        ("property", "og:type", "website"),
        ("property", "og:site_name", "WarpBuster"),
        ("property", "og:locale", "ru_RU"),
        ("property", "og:title", title),
        ("property", "og:description", description),
        ("property", "og:url", url),
        ("property", "og:image", image),
        ("property", "og:image:type", "image/jpeg"),
        ("property", "og:image:width", "1200"),
        ("property", "og:image:height", "800"),
        ("property", "og:image:alt", IMAGE_ALT),
        ("name", "twitter:card", "summary_large_image"),
        ("name", "twitter:title", title),
        ("name", "twitter:description", description),
        ("name", "twitter:image", image),
        ("name", "twitter:image:alt", IMAGE_ALT),
    ):
        tags.append(f'<meta {attribute}="{key}" content="{escape(value, quote=True)}">')
    return before + META_START + "\n    " + "\n    ".join(tags) + "\n    " + META_END + after


def metric(value) -> float | None:
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    return None


def duration(value: float) -> str:
    minutes, seconds = divmod(round(value), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def result_metadata(path: Path) -> tuple[str, str]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return RESULT_TITLE, RESULT_DESCRIPTION
    if not isinstance(report, dict):
        return RESULT_TITLE, RESULT_DESCRIPTION
    title = {
        "repaired": "Трек обработан",
        "unchanged": "Трек без изменений",
        "unresolved": "Не удалось безопасно исправить трек",
    }.get(str(report.get("outcome")), "Результат обработки трека")
    performance = report.get("performance")
    performance = performance if isinstance(performance, dict) else {}
    details = []
    distance = metric(performance.get("distance_m"))
    if distance is not None:
        label = f"{distance / 1000:.2f}".replace(".", ",") + " км"
        title = f"{label} — {title}"
        details.append(f"Дистанция {label}")
    elapsed = metric(performance.get("timer_duration_seconds"))
    if elapsed is not None:
        details.append(f"Время {duration(elapsed)}")
    pace = metric(performance.get("average_pace_seconds_per_km"))
    if pace is not None:
        minutes, seconds = divmod(round(pace), 60)
        details.append(f"Темп {minutes}:{seconds:02} мин/км")
    ascent = metric(performance.get("total_ascent_m"))
    if ascent is not None:
        details.append(f"Набор {ascent:.0f} м")
    description = " · ".join(details) + ". " if details else ""
    if performance.get("distance_quality") in ("uncertain", "unknown"):
        description += "Дистанция и темп могут быть неточными. "
    if report.get("partial") is True:
        description += "Часть участков осталась невосстановленной. "
    return title + " — WarpBuster", description + RESULT_DESCRIPTION
