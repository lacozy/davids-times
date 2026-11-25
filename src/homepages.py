from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Non-local lifestyle/travel slugs to skip on SFGate homepage so SF section stays punchy & local
SFGATE_SKIP_PATH_TOKENS = (
    "/shopping/",
    "/coupons/",
    "/sponsored/",
    "/las-vegas/",
    "/hawaii/",
    "/disneyland/",
    "/travel/article/las-vegas",
    "/horoscopes/",
)

VIDEO_STUB_PREFIXES = ("watch:", "listen:", "video:", "live:", "photos:", "quiz:")


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _is_video_only_title(title: str) -> bool:
    low = title.lower().strip()
    return low.startswith(("watch:", "video:", "listen:", "in pictures:"))


def scrape_bbc_homepage(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Extract top front-page stories in exact editorial hero order from bbc.com/news."""
    url = "https://www.bbc.com/news"
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for a in soup.select('a[href*="/news/articles/"]'):
        h = a.select_one("h2, h3")
        if not h:
            continue
        title = _clean_text(h.get_text(" "))
        if len(title) < 20 or _is_video_only_title(title):
            continue
        href = urljoin("https://www.bbc.com", a["href"]).split("?")[0]
        if href in seen_urls:
            continue
        seen_urls.add(href)

        # Optional summary paragraph inside the same card
        summary = ""
        p = a.select_one('p[data-testid="card-description"]') or a.select_one("p")
        if p:
            summary = _clean_text(p.get_text(" "))

        items.append(
            {
                "title": title,
                "url": href,
                "published": now,
                "author": "",
                "rss_summary": summary,
                "rss_content": "",
                "image_url": None,
                "editorial_rank": len(items),
                "is_homepage": True,
            }
        )
        if len(items) >= 20:
            break
    return items


def scrape_cnn_homepage(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Extract top CNN front-page stories in exact hero order from CNN's official lite.cnn.com."""
    url = "https://lite.cnn.com/"
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for a in soup.select("li.card--lite a"):
        href_rel = (a.get("href") or "").strip()
        if not href_rel.startswith("/20"):
            continue
        title = _clean_text(a.get_text(" "))
        if len(title) < 20 or _is_video_only_title(title):
            continue
        # Canonical CNN URL gives us high-res og:image; fetcher also uses lite.cnn.com for crisp text
        canonical_url = urljoin("https://www.cnn.com", href_rel).split("?")[0]
        if canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)

        items.append(
            {
                "title": title,
                "url": canonical_url,
                "published": now,
                "author": "",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": len(items),
                "is_homepage": True,
            }
        )
        if len(items) >= 22:
            break
    return items


def scrape_npr_homepage(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Extract top NPR front-page stories in hero order from text.npr.org."""
    url = "https://text.npr.org/"
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for a in soup.select("a.topic-title"):
        title = _clean_text(a.get_text(" "))
        href_rel = (a.get("href") or "").strip()
        if len(title) < 20 or not href_rel:
            continue
        # Convert /nx-s1-... text.npr.org path to full www.npr.org article URL for photos & metadata
        full_url = urljoin("https://www.npr.org", href_rel).split("?")[0]
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)

        items.append(
            {
                "title": title,
                "url": full_url,
                "published": now,
                "author": "",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": len(items),
                "is_homepage": True,
            }
        )
        if len(items) >= 16:
            break
    return items


def scrape_sfgate_homepage(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Extract top San Francisco & Bay Area front-page stories in hero order from sfgate.com."""
    url = "https://www.sfgate.com/"
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for a in soup.select('a[href*="/article/"]'):
        href = urljoin("https://www.sfgate.com", a.get("href", "")).split("?")[0]
        low_href = href.lower()
        if any(skip in low_href for skip in SFGATE_SKIP_PATH_TOKENS):
            continue
        title = _clean_text(a.get_text(" "))
        # Skip short pull-quotes or teaser sub-captions
        if len(title) < 28 or title.startswith(('"', "“", "'")):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        items.append(
            {
                "title": title,
                "url": href,
                "published": now,
                "author": "",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": len(items),
                "is_homepage": True,
            }
        )
        if len(items) >= 20:
            break
    return items


def scrape_rps_news(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Extract top gaming news stories from Rock Paper Shotgun's /news front page."""
    url = "https://www.rockpapershotgun.com/news"
    resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")

    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for a in soup.select("a.link_overlay, h2 a, p.title a"):
        title = _clean_text(a.get_text(" "))
        href = urljoin("https://www.rockpapershotgun.com", a.get("href", "")).split("?")[0]
        if len(title) < 22 or "rockpapershotgun.com/" not in href:
            continue
        if href.rstrip("/") in ("https://www.rockpapershotgun.com/news", "https://www.rockpapershotgun.com"):
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        items.append(
            {
                "title": title,
                "url": href,
                "published": now,
                "author": "",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": len(items),
                "is_homepage": True,
            }
        )
        if len(items) >= 18:
            break
    return items


def scrape_reddit_rss(feed_url: str, now: datetime, timeout: int = 10) -> list[dict[str, Any]]:
    """Best-effort Reddit RSS reader (supports reddit.com/.rss or self-hosted RSS bridge URLs)."""
    headers = {"User-Agent": "davids-times-personal-newspaper/1.0"}
    resp = requests.get(feed_url, headers=headers, timeout=timeout)
    if resp.status_code in (403, 429):
        logger.info("Reddit RSS rate-limited/blocked (%d) for %s — skipping gracefully.", resp.status_code, feed_url)
        return []
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)

    items: list[dict[str, Any]] = []
    for idx, entry in enumerate(parsed.entries[:15]):
        title = _clean_text(getattr(entry, "title", ""))
        link = (getattr(entry, "link", "") or "").strip()
        summary_html = getattr(entry, "summary", "") or ""
        # If the Reddit post links to an external news article inside the RSS body, follow that article!
        external_url = link
        if summary_html:
            soup = BeautifulSoup(summary_html, "lxml")
            for a in soup.find_all("a"):
                if "link" in a.get_text(" ", strip=True).lower() and a.get("href", "").startswith("http"):
                    if "reddit.com" not in a["href"]:
                        external_url = a["href"]
                        break
        if not title or not external_url:
            continue
        items.append(
            {
                "title": title,
                "url": external_url,
                "published": now,
                "author": "",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": idx,
                "is_homepage": True,
            }
        )
    return items


def fetch_cnn_lite_article_text(cnn_url: str, timeout: int = 12) -> list[str]:
    """Fetch ultra-clean article paragraphs from CNN's official lite.cnn.com mirror."""
    if "cnn.com/" not in cnn_url:
        return []
    lite_url = re.sub(r"^https?://(www\.)?cnn\.com", "https://lite.cnn.com", cnn_url)
    try:
        resp = requests.get(lite_url, headers=BROWSER_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return []
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        paras = []
        for p in soup.select("p.paragraph--lite"):
            txt = _clean_text(p.get_text(" "))
            if len(txt) >= 28 and not txt.lower().startswith(("cnn's ", "read more", "related:")):
                paras.append(txt)
        return paras
    except Exception:
        return []


def fetch_consensus_headlines(consensus_feeds: list[dict[str, Any]], timeout: int = 10) -> list[tuple[str, str]]:
    """Fetch front-page benchmark headlines (e.g. NYTimes HomePage RSS) purely for consensus boosting."""
    headlines: list[tuple[str, str]] = []
    for cfg in consensus_feeds or []:
        name = cfg.get("name", "Wire")
        url = cfg.get("url", "")
        if not url:
            continue
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
            if resp.status_code != 200:
                continue
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries[:20]:
                t = _clean_text(getattr(entry, "title", ""))
                if len(t) >= 18:
                    headlines.append((name, t))
        except Exception as exc:
            logger.debug("Consensus feed %s skipped: %s", name, exc)
    return headlines


def scrape_hn_top(now: datetime, timeout: int = 12) -> list[dict[str, Any]]:
    """Fetch top-upvoted engineering & CS culture articles from the official Hacker News API (zero auth)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    resp = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=timeout)
    resp.raise_for_status()
    story_ids = resp.json()[:32]

    skip_domains = (
        "youtube.com",
        "youtu.be",
        "vimeo.com",
        "twitter.com",
        "x.com",
        "pdf",
        "arxiv.org/pdf",
        "news.ycombinator.com",
    )

    def _fetch_item(rank: int, sid: int) -> Optional[tuple[int, dict[str, Any]]]:
        try:
            r = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=6)
            if r.status_code != 200:
                return None
            item = r.json()
            if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
                return None
            url = (item.get("url") or "").strip()
            title = _clean_text(item.get("title") or "")
            score = int(item.get("score") or 0)
            if not url or not title or score < 55:
                return None
            low_title = title.lower()
            low_url = url.lower()
            if "[pdf]" in low_title or "[video]" in low_title or low_url.endswith(".pdf"):
                return None
            if any(d in low_url for d in skip_domains):
                return None
            return rank, {
                "title": title,
                "url": url,
                "published": now,
                "author": f"{score} pts on HN",
                "rss_summary": "",
                "rss_content": "",
                "image_url": None,
                "editorial_rank": rank,
                "is_homepage": True,
            }
        except Exception:
            return None

    fetched: list[tuple[int, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_fetch_item, idx, sid) for idx, sid in enumerate(story_ids)]
        for fut in as_completed(futs):
            res = fut.result()
            if res:
                fetched.append(res)

    fetched.sort(key=lambda x: x[0])
    # Re-index clean rank 0..N
    results = []
    for clean_rank, (_, item) in enumerate(fetched[:18]):
        item["editorial_rank"] = clean_rank
        results.append(item)
    return results


HOMEPAGE_SCRAPERS = {
    "bbc_home": scrape_bbc_homepage,
    "cnn_home": scrape_cnn_homepage,
    "npr_home": scrape_npr_homepage,
    "sfgate_home": scrape_sfgate_homepage,
    "rps_news": scrape_rps_news,
    "hn_top": scrape_hn_top,
}
