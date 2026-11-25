from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from .fetcher import FeedCandidate
from .models import Article, BriefingItem, MarketsData, NewspaperEdition, SectionEdition, WeatherData

# Avoid splitting on common newspaper abbreviations like U.S., S.F., Mr., Dr., St.
SENTENCE_SPLIT = re.compile(r"(?<!\bU\.S)(?<!\bU\.K)(?<!\bS\.F)(?<!\bMr)(?<!\bMs)(?<!\bDr)(?<!\bSt)(?<!\bJr)(?<!\bSr)(?<!\bGov)(?<!\bSen)(?<!\bRep)(?<=[.!?])\s+")


def _roman_numeral(n: int) -> str:
    vals = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out = []
    for v, sym in vals:
        while n >= v:
            out.append(sym)
            n -= v
    return "".join(out) or "I"


def _compute_volume_issue(start_date_str: str, today: datetime) -> tuple[str, int]:
    try:
        start = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        delta_days = max(0, (today.date() - start).days)
        vol = (delta_days // 365) + 1
        issue = (delta_days % 365) + 1
        return _roman_numeral(vol), issue
    except Exception:
        return "I", 1


def extractive_summary(article: Article, max_chars: int = 165) -> str:
    """Zero-API-key extractive summarizer tuned for crisp NYT 'In Brief' blurbs."""
    if article.deck and 40 <= len(article.deck) <= max_chars:
        return article.deck

    headline_words = set(re.findall(r"[a-z]{4,}", article.title.lower()))
    candidates: list[tuple[float, str]] = []

    for p_idx, para in enumerate(article.paragraphs[:4]):
        for s_idx, sent in enumerate(SENTENCE_SPLIT.split(para)):
            sent = sent.strip()
            words = sent.split()
            if len(words) < 9 or len(words) > 36:
                continue
            if sent.endswith("?") or sent.startswith(("“", '"', "'")):
                continue

            # Favor strong opening sentences that reinforce key headline entities
            score = 0.0
            if p_idx == 0 and s_idx == 0:
                score += 3.2
            elif p_idx == 0:
                score += 2.0
            elif p_idx == 1 and s_idx == 0:
                score += 1.4

            sent_words = set(re.findall(r"[a-z]{4,}", sent.lower()))
            overlap = len(headline_words & sent_words)
            score += min(2.5, overlap * 0.7)

            if 14 <= len(words) <= 26:
                score += 1.0
            if len(sent) <= max_chars:
                score += 0.8

            candidates.append((score, sent))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
    elif article.paragraphs:
        best = SENTENCE_SPLIT.split(article.paragraphs[0])[0].strip()
    else:
        best = article.title

    if len(best) > max_chars:
        best = best[: max_chars - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return best


def _brief_from_candidate(cand: FeedCandidate, max_chars: int = 135) -> str:
    if cand.rss_summary:
        sent = SENTENCE_SPLIT.split(cand.rss_summary)[0].strip()
        if len(sent) > max_chars:
            sent = sent[: max_chars - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
        return sent
    return ""


def assemble_edition(
    config: dict[str, Any],
    now: datetime,
    weather: Optional[WeatherData],
    articles_by_section: dict[str, list[Article]],
    runner_ups: list[FeedCandidate],
    markets: Optional[MarketsData] = None,
) -> NewspaperEdition:
    """Assemble the Front Page, Morning Briefing, Section Editions, and Back-Page Digest."""
    paper_cfg = config["paper"]
    limits = config.get("limits", {})
    briefing_limit = int(limits.get("briefing_items", 5))
    also_limit = int(limits.get("also_in_news_items", 8))

    # Build working copies of section lists
    sec_pools: dict[str, list[Article]] = {
        sec["id"]: list(articles_by_section.get(sec["id"], []))
        for sec in config["sections"]
    }

    # 1. Pick Front-Page Lead Story (prefer Top News with a verified hydrated e-ink image!)
    lead_story: Optional[Article] = None
    top_pool = sec_pools.get("top_news", [])
    if top_pool:
        with_img = [a for a in top_pool if a.image_data_uri] or [a for a in top_pool if a.image_url]
        lead_story = with_img[0] if with_img else top_pool[0]
        top_pool.remove(lead_story)
        lead_story.is_front_page = True
    else:
        for sec in config["sections"]:
            pool = sec_pools.get(sec["id"], [])
            if pool:
                lead_story = pool.pop(0)
                lead_story.is_front_page = True
                break

    # Trim front-page lead slightly so Page 1 + Morning Briefing stay crisp and tight
    front_lead_words = int(limits.get("front_lead_words", 320))
    if lead_story and sum(len(p.split()) for p in lead_story.paragraphs) > front_lead_words:
        kept, word_acc = [], 0
        for p in lead_story.paragraphs:
            pw = len(p.split())
            if word_acc + pw <= front_lead_words or not kept:
                kept.append(p)
                word_acc += pw
            else:
                lead_story.truncated = True
                break
        lead_story.paragraphs = kept
        lead_story.read_minutes = max(1, round(word_acc / 220))

    # 2. Pick Front-Page Secondary Story (prefer San Francisco or Gaming with image so Page 1 feels personal!)
    secondary_story: Optional[Article] = None
    for preferred_sec in ("san_francisco", "gaming", "top_news"):
        pool = sec_pools.get(preferred_sec, [])
        if pool:
            with_img = [a for a in pool if a.image_data_uri]
            secondary_story = with_img[0] if with_img else pool[0]
            pool.remove(secondary_story)
            secondary_story.is_front_page = True
            break

    # 3. Build inside SectionEditions
    sections: list[SectionEdition] = []
    for sec_cfg in config["sections"]:
        arts = sec_pools.get(sec_cfg["id"], [])
        for a in arts:
            a.summary_blurb = extractive_summary(a)
        sections.append(
            SectionEdition(
                id=sec_cfg["id"],
                title=sec_cfg["title"],
                subtitle=sec_cfg.get("subtitle", ""),
                articles=arts,
            )
        )

    if lead_story:
        lead_story.summary_blurb = extractive_summary(lead_story)
    if secondary_story:
        secondary_story.summary_blurb = extractive_summary(secondary_story)

    # 4. Build Front-Page "Morning Briefing" (round-robin across all sections so every interest is represented!)
    briefing_items: list[BriefingItem] = []
    section_cursors = [0] * len(sections)
    while len(briefing_items) < briefing_limit:
        added_any = False
        for idx, sec in enumerate(sections):
            if len(briefing_items) >= briefing_limit:
                break
            cur = section_cursors[idx]
            if cur < len(sec.articles):
                art = sec.articles[cur]
                section_cursors[idx] += 1
                briefing_items.append(
                    BriefingItem(
                        section_id=sec.id,
                        section_title=sec.title,
                        headline=art.title,
                        summary=art.summary_blurb or extractive_summary(art, max_chars=140),
                        source=art.source,
                        article_id=art.id,
                    )
                )
                added_any = True
        if not added_any:
            break

    # 5. Build Back-Page "Also in the News" quick digest from runners-up
    also_in_news: list[BriefingItem] = []
    # Balance runners-up across sections
    by_sec_runners: dict[str, list[FeedCandidate]] = {}
    for c in runner_ups:
        by_sec_runners.setdefault(c.section_id, []).append(c)

    sec_ids = [s["id"] for s in config["sections"]]
    r_cursors = {sid: 0 for sid in sec_ids}
    while len(also_in_news) < also_limit:
        added = False
        for sid in sec_ids:
            if len(also_in_news) >= also_limit:
                break
            pool = by_sec_runners.get(sid, [])
            idx = r_cursors[sid]
            if idx < len(pool):
                cand = pool[idx]
                r_cursors[sid] += 1
                also_in_news.append(
                    BriefingItem(
                        section_id=cand.section_id,
                        section_title=cand.section_title,
                        headline=cand.title,
                        summary=_brief_from_candidate(cand),
                        source=cand.source,
                    )
                )
                added = True
        if not added:
            break

    vol_roman, issue_no = _compute_volume_issue(paper_cfg.get("start_date", "2026-11-24"), now)
    date_display = now.strftime("%A, %B %d, %Y").replace(" 0", " ")
    date_iso = now.strftime("%Y-%m-%d")

    total_articles = sum(len(s.articles) for s in sections)
    if lead_story:
        total_articles += 1
    if secondary_story:
        total_articles += 1

    return NewspaperEdition(
        title=paper_cfg.get("title", "The David Times"),
        motto=paper_cfg.get("motto", "All David’s News That’s Fit to Read"),
        edition_label=paper_cfg.get("edition", "San Francisco Edition"),
        price_line=paper_cfg.get("price_line", "Kindle Edition · Free"),
        date_display=date_display,
        date_iso=date_iso,
        volume_roman=vol_roman,
        issue_number=issue_no,
        weather=weather,
        lead_story=lead_story,
        secondary_story=secondary_story,
        briefing_items=briefing_items,
        sections=sections,
        also_in_news=also_in_news,
        markets=markets,
        generated_at=now.strftime("%I:%M %p %Z").lstrip("0"),
        total_articles=total_articles,
    )
