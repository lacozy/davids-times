from __future__ import annotations

import hashlib
import html
import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import feedparser
import requests
import trafilatura
from bs4 import BeautifulSoup

from .homepages import (
    HOMEPAGE_SCRAPERS,
    fetch_cnn_lite_article_text,
    fetch_consensus_headlines,
    scrape_reddit_rss,
)
from .images import is_usable_image_url
from .models import Article

logger = logging.getLogger(__name__)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "has",
    "have", "had", "this", "that", "it", "its", "about", "into", "over", "after",
    "new", "says", "said", "will", "more", "out", "up", "how", "why", "what",
    "who", "where", "when", "they", "their", "them", "his", "her", "you", "your",
    "not", "all", "can", "just", "first", "now", "one", "two", "year", "years",
    "day", "days", "week", "news", "report", "live", "updates", "update",
}

BOILERPLATE_PATTERNS = [
    re.compile(r"^(sign up|subscribe|follow us|read more|related:|see also:|previously:|watch:|listen:|advertisement|sponsored|cookie|privacy policy|copyright ©|editor'?s note:)", re.I),
    re.compile(r"(sign up for our .* newsletter|get the latest .* delivered to your inbox|support local journalism|donate today|become a member)", re.I),
    re.compile(r"^(share this|print this|email this|comment|comments|tags:|filed under:)", re.I),
    re.compile(r"^(photo:|image:|credit:|getty images|associated press photo|file photo)", re.I),
]

INLINE_NOISE_RE = re.compile(r"\s*\((opens in (?:a )?new (?:tab|window)|external link)\)", re.I)

FLUFF_HEADLINE_RE = re.compile(
    r"^(watch:|listen:|quiz:|here'?s how to|here are the best|best \d+|top \d+ |review:|opinion:|podcast:)",
    re.I,
)


@dataclass
class FeedCandidate:
    section_id: str
    section_title: str
    source: str
    feed_weight: float
    title: str
    url: str
    published: Optional[datetime]
    author: str
    rss_summary: str
    rss_content: str
    image_url: Optional[str]
    editorial_rank: int = 10
    is_homepage: bool = False
    corroborating_sources: list[str] = field(default_factory=list)
    score: float = 0.0


def _clean_whitespace(text: str) -> str:
    text = html.unescape(text or "")
    text = INLINE_NOISE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "lxml")
    for tag in soup(["script", "style", "aside", "figure", "figcaption"]):
        tag.decompose()
    return _clean_whitespace(soup.get_text(" "))


def _parse_entry_datetime(entry: Any, tz: ZoneInfo) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        st = getattr(entry, attr, None)
        if st:
            try:
                dt = datetime(*st[:6], tzinfo=timezone.utc)
                return dt.astimezone(tz)
            except Exception:
                pass
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(tz)
            except Exception:
                pass
    return None


def _extract_rss_image(entry: Any, base_url: str) -> Optional[str]:
    media_content = getattr(entry, "media_content", None) or []
    best_url = None
    best_width = -1
    for item in media_content:
        url = item.get("url")
        if not is_usable_image_url(url):
            continue
        mtype = (item.get("type") or "").lower()
        medium = (item.get("medium") or "").lower()
        if mtype and not mtype.startswith("image") and medium != "image":
            continue
        try:
            width = int(item.get("width") or 0)
        except ValueError:
            width = 0
        if width >= best_width:
            best_width = width
            best_url = url
    if best_url:
        return best_url

    for enc in getattr(entry, "enclosures", None) or []:
        url = enc.get("href") or enc.get("url")
        etype = (enc.get("type") or "").lower()
        if is_usable_image_url(url) and (not etype or etype.startswith("image")):
            return url

    for thumb in getattr(entry, "media_thumbnail", None) or []:
        url = thumb.get("url")
        if is_usable_image_url(url):
            return url

    html_blobs = []
    if getattr(entry, "content", None):
        html_blobs.extend(c.get("value", "") for c in entry.content)
    html_blobs.append(getattr(entry, "summary", "") or "")
    for blob in html_blobs:
        if "<img" not in blob:
            continue
        soup = BeautifulSoup(blob, "lxml")
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src:
                full = urljoin(base_url, src)
                if is_usable_image_url(full):
                    return full
    return None


def _title_tokens(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", title.lower())
    return {w for w in words if w not in STOPWORDS}


def _proper_tokens(title: str) -> set[str]:
    """Extract capitalized/entity-like words (excluding sentence start) for cross-outlet story matching."""
    words = re.findall(r"\b[A-Z][a-zA-Z0-9]{3,}\b", title)
    return {w.lower() for w in words if w.lower() not in STOPWORDS}


def _are_similar_titles(t1: str, t2: str) -> bool:
    s1, s2 = _title_tokens(t1), _title_tokens(t2)
    if not s1 or not s2:
        return False
    inter = len(s1 & s2)
    union = len(s1 | s2)
    if (inter / union) >= 0.40:
        return True
    if min(len(s1), len(s2)) >= 4 and inter >= 3:
        return True
    # Match when two headlines share 2+ strong proper-noun entities (e.g. "Nvidia" + "Trump" or "Abrego" + "Garcia")
    p1, p2 = _proper_tokens(t1), _proper_tokens(t2)
    if len(p1 & p2) >= 2 and inter >= 2:
        return True
    return False


def _fetch_source(
    section_id: str,
    section_title: str,
    feed_cfg: dict[str, Any],
    now: datetime,
    tz: ZoneInfo,
    timeout: int,
) -> list[FeedCandidate]:
    source = feed_cfg["name"]
    weight = float(feed_cfg.get("weight", 1.0))
    source_type = (feed_cfg.get("type") or "rss").lower()
    url = feed_cfg.get("url", "")
    candidates: list[FeedCandidate] = []

    try:
        if source_type in HOMEPAGE_SCRAPERS:
            raw_items = HOMEPAGE_SCRAPERS[source_type](now, timeout=timeout)
            for item in raw_items:
                candidates.append(
                    FeedCandidate(
                        section_id=section_id,
                        section_title=section_title,
                        source=source,
                        feed_weight=weight,
                        title=item["title"],
                        url=item["url"],
                        published=item.get("published") or now,
                        author=item.get("author", ""),
                        rss_summary=item.get("rss_summary", ""),
                        rss_content=item.get("rss_content", ""),
                        image_url=item.get("image_url"),
                        editorial_rank=item.get("editorial_rank", 10),
                        is_homepage=bool(item.get("is_homepage", True)),
                    )
                )
            return candidates

        if source_type == "reddit_rss":
            raw_items = scrape_reddit_rss(url, now, timeout=timeout)
            for item in raw_items:
                candidates.append(
                    FeedCandidate(
                        section_id=section_id,
                        section_title=section_title,
                        source=source,
                        feed_weight=weight,
                        title=item["title"],
                        url=item["url"],
                        published=item.get("published") or now,
                        author=item.get("author", ""),
                        rss_summary=item.get("rss_summary", ""),
                        rss_content="",
                        image_url=item.get("image_url"),
                        editorial_rank=item.get("editorial_rank", 10),
                        is_homepage=True,
                    )
                )
            return candidates

        # Default: standard RSS / Atom feed (preserving editorial feed rank!)
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        for idx, entry in enumerate(parsed.entries[:22]):
            title = _clean_whitespace(getattr(entry, "title", ""))
            link = (getattr(entry, "link", "") or "").strip()
            if not title or not link:
                continue

            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            raw_content = ""
            if getattr(entry, "content", None):
                raw_content = "\n".join(c.get("value", "") for c in entry.content if c.get("value"))

            author = _clean_whitespace(getattr(entry, "author", ""))
            if len(author) > 65 or "http" in author.lower():
                author = ""

            candidates.append(
                FeedCandidate(
                    section_id=section_id,
                    section_title=section_title,
                    source=source,
                    feed_weight=weight,
                    title=title,
                    url=link,
                    published=_parse_entry_datetime(entry, tz),
                    author=author,
                    rss_summary=_strip_html(raw_summary),
                    rss_content=_strip_html(raw_content),
                    image_url=_extract_rss_image(entry, link),
                    editorial_rank=idx,
                    is_homepage=False,
                )
            )
    except Exception as exc:
        logger.warning("Failed to fetch %s (%s): %s", source, source_type or url, exc)
    return candidates


def _score_candidate(
    cand: FeedCandidate,
    now: datetime,
    max_age_hours: float,
    boost_keywords: list[str],
) -> float:
    score = 10.0 * cand.feed_weight

    # 1. Editorial prominence: real homepage hero slots carry huge editorial weight!
    if cand.is_homepage:
        score += 13.0 * math.exp(-cand.editorial_rank / 4.5)
    else:
        score += 4.5 * math.exp(-cand.editorial_rank / 5.0)

    # 2. Recency decay
    if cand.published:
        age_hours = max(0.0, (now - cand.published).total_seconds() / 3600.0)
        if age_hours > max_age_hours * 1.75:
            score -= 10.0
        else:
            score += 6.0 * math.exp(-age_hours / max(12.0, max_age_hours * 0.55))
    else:
        score += 2.0

    # 3. Cross-outlet consensus boost (multiple major front pages covering the same story!)
    score += min(24.0, len(cand.corroborating_sources) * 8.5)

    # 4. Personal interest keyword boosts (modest so they refine rather than override major news)
    haystack = f"{cand.title} {cand.rss_summary}".lower()
    matched_kw = sum(1 for kw in boost_keywords if kw in haystack)
    score += min(4.5, matched_kw * 1.6)

    # 5. Penalize light fluff/listicles/video stubs so hard news rises to the top
    if FLUFF_HEADLINE_RE.search(cand.title):
        score -= 5.0

    if cand.image_url:
        score += 1.2

    return score


def _apply_cross_source_consensus(
    candidates: list[FeedCandidate],
    consensus_headlines: list[tuple[str, str]],
) -> None:
    """Detect when multiple distinct outlets (or NYT Front Page) lead with the same major story."""
    # 1. Compare candidates against each other within the section
    for i, c1 in enumerate(candidates):
        for j, c2 in enumerate(candidates):
            if i == j or c1.source == c2.source:
                continue
            if _are_similar_titles(c1.title, c2.title):
                if c2.source not in c1.corroborating_sources:
                    c1.corroborating_sources.append(c2.source)

    # 2. Compare Top News candidates against benchmark front pages (e.g. NYTimes HomePage RSS)
    for cand in candidates:
        if cand.section_id != "top_news":
            continue
        for bench_name, bench_title in consensus_headlines:
            if bench_name == cand.source:
                continue
            if _are_similar_titles(cand.title, bench_title):
                if bench_name not in cand.corroborating_sources:
                    cand.corroborating_sources.append(bench_name)


def _is_boilerplate(paragraph: str) -> bool:
    p = paragraph.strip()
    if len(p) < 28:
        return True
    for pat in BOILERPLATE_PATTERNS:
        if pat.search(p):
            return True
    alpha = [c for c in p if c.isalpha()]
    if alpha and (sum(1 for c in alpha if c.isupper()) / len(alpha)) > 0.72 and len(p) < 120:
        return True
    return False


def _trim_paragraphs_to_word_budget(paragraphs: list[str], max_words: int) -> tuple[list[str], bool]:
    kept: list[str] = []
    word_count = 0
    truncated = False

    for p in paragraphs:
        words = p.split()
        if not words:
            continue
        if word_count + len(words) <= max_words:
            kept.append(p)
            word_count += len(words)
        else:
            remaining = max_words - word_count
            if remaining >= 45:
                sentences = re.split(r"(?<=[.!?])\s+", p)
                partial = []
                p_words = 0
                for s in sentences:
                    sw = len(s.split())
                    if p_words + sw <= remaining:
                        partial.append(s)
                        p_words += sw
                    else:
                        break
                if partial:
                    kept.append(" ".join(partial))
            truncated = True
            break

    return kept, truncated


def _extract_page_image_and_caption(soup: BeautifulSoup, page_url: str) -> tuple[Optional[str], str]:
    image_url = None
    caption = ""

    for attrs in (
        {"property": "og:image"},
        {"name": "og:image"},
        {"name": "twitter:image"},
        {"property": "twitter:image"},
    ):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            cand_url = urljoin(page_url, meta["content"].strip())
            if is_usable_image_url(cand_url):
                image_url = cand_url
                break

    meta_alt = soup.find("meta", attrs={"property": "og:image:alt"}) or soup.find("meta", attrs={"name": "twitter:image:alt"})
    if meta_alt and meta_alt.get("content"):
        caption = _clean_whitespace(meta_alt["content"])

    if not caption:
        fig = soup.find("figcaption")
        if fig:
            cap_text = _clean_whitespace(fig.get_text(" "))
            if 12 <= len(cap_text) <= 150:
                caption = cap_text

    if len(caption) > 135:
        caption = caption[:132].rsplit(" ", 1)[0].rstrip(",;:") + "…"

    return image_url, caption


def _fetch_full_article(
    cand: FeedCandidate,
    max_words: int,
    min_words: int,
    timeout: int,
) -> Optional[Article]:
    paragraphs: list[str] = []
    image_url = cand.image_url
    image_caption = ""
    author = cand.author
    meta_desc = ""

    try:
        resp = requests.get(cand.url, headers=HTTP_HEADERS, timeout=timeout)
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", "").lower():
            if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "latin-1"):
                resp.encoding = "utf-8"
            html_text = resp.text
            soup = BeautifulSoup(html_text, "lxml")

            page_img, page_cap = _extract_page_image_and_caption(soup, cand.url)
            low_res_rss = bool(
                image_url
                and any(tok in image_url.lower() for tok in ("thumb", "/240/", "/320/", "/200/", "w=240", "w=300", "_s."))
            )
            if page_img and (not image_url or low_res_rss):
                image_url = page_img
            image_caption = page_cap

            og_desc = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
            if og_desc and og_desc.get("content"):
                meta_desc = _clean_whitespace(og_desc["content"])

            if not author:
                meta_author = soup.find("meta", attrs={"name": "author"})
                if meta_author and meta_author.get("content"):
                    a_txt = _clean_whitespace(meta_author["content"])
                    if 2 <= len(a_txt) <= 60 and "http" not in a_txt.lower():
                        author = a_txt

            extracted = trafilatura.extract(
                html_text,
                url=cand.url,
                include_comments=False,
                include_tables=False,
                include_links=False,
                favor_precision=True,
                deduplicate=True,
            )
            if extracted:
                raw_paras = [p.strip() for p in re.split(r"\n+", extracted) if p.strip()]
                paragraphs = [_clean_whitespace(p) for p in raw_paras if not _is_boilerplate(p)]

            if sum(len(p.split()) for p in paragraphs) < min_words:
                container = soup.find("article") or soup.find("main") or soup
                bs_paras = []
                for p_tag in container.find_all("p"):
                    txt = _clean_whitespace(p_tag.get_text(" "))
                    if not _is_boilerplate(txt):
                        bs_paras.append(txt)
                if sum(len(p.split()) for p in bs_paras) > sum(len(p.split()) for p in paragraphs):
                    paragraphs = bs_paras
    except Exception as exc:
        logger.debug("Full article fetch failed for %s: %s", cand.url, exc)

    # CNN Lite clean text fallback (ultra-reliable for CNN Homepage stories!)
    if "cnn.com/" in cand.url and sum(len(p.split()) for p in paragraphs) < min_words:
        cnn_paras = fetch_cnn_lite_article_text(cand.url, timeout=timeout)
        if cnn_paras:
            paragraphs = cnn_paras

    # RSS content/summary fallback
    if sum(len(p.split()) for p in paragraphs) < min_words:
        fallback_blob = cand.rss_content if len(cand.rss_content) > len(cand.rss_summary) else cand.rss_summary
        if fallback_blob:
            sentences = re.split(r"(?<=[.!?])\s+", fallback_blob)
            chunked = []
            buf: list[str] = []
            for s in sentences:
                buf.append(s)
                if len(" ".join(buf).split()) >= 42:
                    chunked.append(" ".join(buf))
                    buf = []
            if buf:
                chunked.append(" ".join(buf))
            paragraphs = [p for p in chunked if len(p) >= 24]

    if paragraphs and _are_similar_titles(paragraphs[0], cand.title) and len(paragraphs) > 1:
        paragraphs = paragraphs[1:]

    total_words = sum(len(p.split()) for p in paragraphs)
    if total_words < min_words:
        return None

    paragraphs, truncated = _trim_paragraphs_to_word_budget(paragraphs, max_words)
    final_words = sum(len(p.split()) for p in paragraphs)
    read_minutes = max(1, round(final_words / 220))

    # Craft a crisp italic deck (preferring RSS summary or og:description, never duplicating para 1)
    deck_source = cand.rss_summary or meta_desc
    deck = ""
    if deck_source and len(deck_source) >= 35:
        first_sent = re.split(r"(?<=[.!?])\s+", deck_source)[0].strip()
        if 30 <= len(first_sent) <= 185:
            if not paragraphs or not _are_similar_titles(first_sent, paragraphs[0][:160]):
                deck = first_sent

    art_id = hashlib.md5(cand.url.encode("utf-8")).hexdigest()[:10]
    return Article(
        id=art_id,
        section_id=cand.section_id,
        section_title=cand.section_title,
        title=cand.title,
        url=cand.url,
        source=cand.source,
        published=cand.published,
        author=author,
        deck=deck,
        paragraphs=paragraphs,
        image_url=image_url,
        image_caption=image_caption,
        score=cand.score,
        read_minutes=read_minutes,
        truncated=truncated,
        corroborating_sources=list(cand.corroborating_sources),
    )


def fetch_all_sections(
    config: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, list[Article]], list[FeedCandidate]]:
    """Fetch homepages & RSS feeds, score by editorial prominence + cross-outlet consensus, and hydrate full stories."""
    tz = ZoneInfo(config["paper"].get("timezone", "America/Los_Angeles"))
    limits = config.get("limits", {})
    max_age_hours = float(limits.get("max_age_hours", 48))
    max_words = int(limits.get("max_article_words", 500))
    min_words = int(limits.get("min_article_words", 65))
    timeout = int(limits.get("fetch_timeout_seconds", 12))

    interests = config.get("interests", {})
    boost_keywords = [k.lower() for k in interests.get("boost_keywords", [])]
    mute_keywords = [k.lower() for k in interests.get("mute_keywords", [])]

    # Step 1: Fetch all homepages, RSS feeds, and front-page consensus benchmarks concurrently
    all_raw_by_section: dict[str, list[FeedCandidate]] = {s["id"]: [] for s in config["sections"]}
    consensus_headlines: list[tuple[str, str]] = []

    with ThreadPoolExecutor(max_workers=14) as pool:
        consensus_fut = pool.submit(
            fetch_consensus_headlines,
            config.get("consensus_feeds", []),
            timeout,
        )
        feed_jobs = []
        for sec in config["sections"]:
            for feed_cfg in sec["feeds"]:
                feed_jobs.append(
                    pool.submit(_fetch_source, sec["id"], sec["title"], feed_cfg, now, tz, timeout)
                )

        try:
            consensus_headlines = consensus_fut.result()
        except Exception:
            consensus_headlines = []

        for fut in as_completed(feed_jobs):
            try:
                items = fut.result()
                for item in items:
                    all_raw_by_section[item.section_id].append(item)
            except Exception:
                pass

    # Step 2: Filter, compute cross-source consensus, score by editorial prominence, and deduplicate
    selected_candidates: list[FeedCandidate] = []
    runner_up_candidates: list[FeedCandidate] = []
    seen_titles_global: list[str] = []
    seen_urls_global: set[str] = set()

    for sec in config["sections"]:
        sec_id = sec["id"]
        max_articles = int(sec.get("max_articles", 5))
        max_per_source = int(sec.get("max_per_source", 2))
        raw_list = all_raw_by_section.get(sec_id, [])

        filtered: list[FeedCandidate] = []
        for cand in raw_list:
            haystack = f"{cand.title} {cand.rss_summary}".lower()
            if any(m in haystack for m in mute_keywords):
                continue
            filtered.append(cand)

        # Boost stories covered by multiple major front pages!
        _apply_cross_source_consensus(filtered, consensus_headlines)

        for cand in filtered:
            cand.score = _score_candidate(cand, now, max_age_hours, boost_keywords)

        filtered.sort(key=lambda c: c.score, reverse=True)

        source_counts: dict[str, int] = {}
        picked_for_sec: list[FeedCandidate] = []
        sec_runner_count = 0
        target_fetch = max_articles + 4

        for cand in filtered:
            if cand.url in seen_urls_global:
                continue
            if any(_are_similar_titles(cand.title, t) for t in seen_titles_global):
                continue

            seen_urls_global.add(cand.url)
            seen_titles_global.append(cand.title)

            if len(picked_for_sec) < target_fetch and source_counts.get(cand.source, 0) < max_per_source + 1:
                picked_for_sec.append(cand)
                source_counts[cand.source] = source_counts.get(cand.source, 0) + 1
            elif sec_runner_count < 6:
                runner_up_candidates.append(cand)
                sec_runner_count += 1

        selected_candidates.extend(picked_for_sec)

    # Step 3: Download & extract full article bodies concurrently
    hydrated_by_section: dict[str, list[Article]] = {s["id"]: [] for s in config["sections"]}
    with ThreadPoolExecutor(max_workers=10) as pool:
        fut_map = {
            pool.submit(_fetch_full_article, cand, max_words, min_words, timeout): cand
            for cand in selected_candidates
        }
        for fut in as_completed(fut_map):
            try:
                art = fut.result()
                if art:
                    hydrated_by_section[art.section_id].append(art)
            except Exception:
                pass

    # Step 4: Enforce final per-section limits and source diversity
    final_by_section: dict[str, list[Article]] = {}
    for sec in config["sections"]:
        sec_id = sec["id"]
        max_articles = int(sec.get("max_articles", 5))
        max_per_source = int(sec.get("max_per_source", 2))
        arts = sorted(hydrated_by_section.get(sec_id, []), key=lambda a: a.score, reverse=True)

        chosen: list[Article] = []
        src_counts: dict[str, int] = {}
        overflow: list[Article] = []

        for art in arts:
            if len(chosen) < max_articles and src_counts.get(art.source, 0) < max_per_source:
                chosen.append(art)
                src_counts[art.source] = src_counts.get(art.source, 0) + 1
            else:
                overflow.append(art)

        for art in overflow:
            if len(chosen) >= max_articles:
                break
            chosen.append(art)

        final_by_section[sec_id] = chosen

    return final_by_section, runner_up_candidates
