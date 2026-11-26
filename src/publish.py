from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from .models import NewspaperEdition


def _issue_filename(date_iso: str) -> str:
    return f"{date_iso}-The-David-Times.pdf"


def publish_static_site(
    pdf_path: Path,
    edition: NewspaperEdition,
    page_count: int,
    publish_dir: Path,
    base_url: str = "",
    keep_days: int = 14,
) -> None:
    """Build GitHub Pages site with latest.pdf, rolling archive, OPDS 1.2 feed, and latest.json."""
    publish_dir.mkdir(parents=True, exist_ok=True)
    issues_dir = publish_dir / "issues"
    issues_dir.mkdir(parents=True, exist_ok=True)

    # Copy today's PDF as both latest.pdf and dated archive
    latest_pdf = publish_dir / "latest.pdf"
    dated_name = _issue_filename(edition.date_iso)
    dated_pdf = issues_dir / dated_name
    shutil.copy2(pdf_path, latest_pdf)
    shutil.copy2(pdf_path, dated_pdf)

    # Prune archive older than `keep_days` issues
    all_issues = sorted(issues_dir.glob("*.pdf"), reverse=True)
    for stale in all_issues[keep_days:]:
        try:
            stale.unlink()
        except OSError:
            pass
    all_issues = sorted(issues_dir.glob("*.pdf"), reverse=True)

    clean_base = base_url.rstrip("/")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def full_url(rel_path: str) -> str:
        rel = rel_path.lstrip("/")
        return f"{clean_base}/{rel}" if clean_base else rel

    # 1. latest.json — polled by KOReader plugin (davidtimes.koplugin)
    latest_meta = {
        "title": edition.title,
        "date_iso": edition.date_iso,
        "date_display": edition.date_display,
        "volume": edition.volume_roman,
        "issue_number": edition.issue_number,
        "pages": page_count,
        "articles": edition.total_articles,
        "filename": dated_name,
        "pdf_url": full_url("latest.pdf"),
        "archive_url": full_url(f"issues/{dated_name}"),
        "updated_at": now_iso,
        "size_bytes": latest_pdf.stat().st_size,
    }
    (publish_dir / "latest.json").write_text(
        json.dumps(latest_meta, indent=2),
        encoding="utf-8",
    )

    def _fmt_mmddyyyy(iso_str: str) -> str:
        try:
            y, m, d = iso_str.split("-")
            return f"{m}/{d}/{y}"
        except Exception:
            return iso_str

    # 2. opds.xml — OPDS 1.2 Atom catalog natively supported by KOReader!
    entries_xml = []
    for idx, issue_file in enumerate(all_issues):
        stem = issue_file.stem
        date_part = stem[:10]
        date_slash = _fmt_mmddyyyy(date_part)
        is_today = idx == 0
        entry_title = (
            f"{edition.title} - {date_slash} (Latest)"
            if is_today
            else f"{edition.title} - {date_slash}"
        )
        summary_text = (
            f"{edition.date_display} · {page_count} pages · {edition.total_articles} articles"
            if is_today
            else f"Archived Kindle edition from {date_slash}"
        )
        href = full_url(f"issues/{issue_file.name}")
        size_bytes = issue_file.stat().st_size
        entries_xml.append(
            f"""  <entry>
    <title>{escape(entry_title)}</title>
    <id>urn:davids-times:issue:{escape(date_part)}</id>
    <updated>{now_iso}</updated>
    <author><name>{escape(edition.title)}</name></author>
    <summary>{escape(summary_text)}</summary>
    <link rel="http://opds-spec.org/acquisition" href="{escape(href)}" type="application/pdf" length="{size_bytes}"/>
  </entry>"""
        )

    opds_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:davids-times:catalog</id>
  <title>{escape(edition.title)}</title>
  <subtitle>{escape(edition.motto)}</subtitle>
  <updated>{now_iso}</updated>
  <author><name>{escape(edition.title)}</name></author>
  <link rel="self" href="{escape(full_url('opds.xml'))}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="{escape(full_url('opds.xml'))}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
{chr(10).join(entries_xml)}
</feed>
"""
    (publish_dir / "opds.xml").write_text(opds_xml, encoding="utf-8")

    # 3. index.html — simple newspaper-styled archive landing page
    issue_rows = "\n".join(
        f'<li><a href="issues/{escape(f.name)}">{escape(edition.title)} - {escape(_fmt_mmddyyyy(f.stem[:10]))}</a> '
        f'<span class="meta">({f.stat().st_size // 1024} KB)</span></li>'
        for f in all_issues
    )
    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(edition.title)} — Kindle Archive</title>
  <style>
    body {{ max-width: 680px; margin: 2.5rem auto; padding: 0 1.2rem; font-family: Georgia, serif; color: #111; line-height: 1.55; }}
    header {{ text-align: center; border-bottom: 3px double #111; padding-bottom: 1rem; margin-bottom: 1.5rem; }}
    h1 {{ font-size: 2.2rem; margin: 0 0 0.3rem 0; }}
    .sub {{ font-style: italic; color: #444; }}
    .cta {{ display: inline-block; background: #111; color: #fff; padding: 0.65rem 1.2rem; text-decoration: none; font-family: sans-serif; font-size: 0.9rem; letter-spacing: 0.04em; margin: 0.4rem 0.3rem; }}
    .cta.secondary {{ background: #fff; color: #111; border: 1.5px solid #111; }}
    ul {{ list-style: none; padding: 0; }}
    li {{ padding: 0.5rem 0; border-bottom: 1px solid #ddd; }}
    a {{ color: #111; }}
    .meta {{ font-family: sans-serif; font-size: 0.8rem; color: #555; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(edition.title)}</h1>
    <div class="sub">“{escape(edition.motto)}” · {escape(edition.date_display)}</div>
    <p>
      <a class="cta" href="latest.pdf">Download Today’s PDF ({page_count} pages)</a>
      <a class="cta secondary" href="opds.xml">KOReader OPDS Feed</a>
    </p>
  </header>
  <h2>Recent Editions</h2>
  <ul>
    {issue_rows}
  </ul>
</body>
</html>
"""
    (publish_dir / "index.html").write_text(index_html, encoding="utf-8")
