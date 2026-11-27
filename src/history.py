from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from .models import NewspaperEdition

logger = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    """Normalize article URLs so tracking params/protocol differences still match."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (parsed.path or "").rstrip("/").lower()
        return f"{host}{path}"
    except Exception:
        return url.strip().lower().rstrip("/")


def load_recent_history(
    history_path: Path,
    today_iso: str,
    *,
    url_days: int = 7,
    title_days: int = 3,
) -> tuple[set[str], list[str]]:
    """Return previously published (normalized URLs, headlines) from prior days' editions."""
    seen_urls: set[str] = set()
    seen_titles: list[str] = []

    if not history_path or not history_path.exists():
        return seen_urls, seen_titles

    try:
        today_dt = date.fromisoformat(today_iso)
    except ValueError:
        today_dt = datetime.utcnow().date()

    try:
        data = json.loads(history_path.read_text(encoding="utf-8"))
        items = data.get("items", []) if isinstance(data, dict) else []
    except Exception as exc:
        logger.warning("Could not read history file %s: %s", history_path, exc)
        return seen_urls, seen_titles

    for entry in items:
        entry_date_str = str(entry.get("date") or "")
        # Ignore entries from today so manual same-day rebuilds aren't blocked by themselves
        if entry_date_str == today_iso:
            continue
        try:
            entry_dt = date.fromisoformat(entry_date_str)
            age_days = (today_dt - entry_dt).days
        except ValueError:
            continue

        if age_days < 0:
            continue

        norm_u = normalize_url(entry.get("url") or "")
        title = (entry.get("title") or "").strip()

        if norm_u and age_days <= url_days:
            seen_urls.add(norm_u)
        if title and age_days <= title_days:
            seen_titles.append(title)

    return seen_urls, seen_titles


def save_edition_history(
    history_path: Path,
    edition: NewspaperEdition,
    *,
    keep_days: int = 10,
) -> None:
    """Append all full articles from `edition` into history.json and prune entries older than `keep_days`."""
    existing_items: list[dict[str, str]] = []
    if history_path.exists():
        try:
            data = json.loads(history_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                existing_items = data["items"]
        except Exception:
            existing_items = []

    try:
        today_dt = date.fromisoformat(edition.date_iso)
    except ValueError:
        today_dt = datetime.utcnow().date()
    cutoff = today_dt - timedelta(days=keep_days)

    # Keep prior days within retention window; replace any earlier run from today_iso cleanly
    kept: list[dict[str, str]] = []
    for item in existing_items:
        d_str = str(item.get("date") or "")
        if d_str == edition.date_iso:
            continue
        try:
            if date.fromisoformat(d_str) >= cutoff:
                kept.append(item)
        except ValueError:
            continue

    # Collect all full articles printed in today's edition
    todays_articles = []
    if edition.lead_story:
        todays_articles.append(edition.lead_story)
    if edition.secondary_story:
        todays_articles.append(edition.secondary_story)
    for sec in edition.sections:
        todays_articles.extend(sec.articles)

    for art in todays_articles:
        kept.append(
            {
                "date": edition.date_iso,
                "section": art.section_id,
                "url": normalize_url(art.url),
                "title": art.title,
            }
        )

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps({"updated_at": edition.date_iso, "items": kept}, indent=2),
        encoding="utf-8",
    )
