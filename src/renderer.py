from __future__ import annotations

import base64
import io
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import Browser, sync_playwright
from pypdf import PdfReader, PdfWriter

from .models import NewspaperEdition

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
FONTS_CSS_PATH = FONTS_DIR / "fonts.css"


def _load_inline_fonts_css() -> str:
    """Inline bundled .woff2 newspaper fonts as base64 data URIs so Chromium renders them 100% reliably."""
    if not FONTS_CSS_PATH.exists():
        return ""
    css = FONTS_CSS_PATH.read_text(encoding="utf-8")

    def _replace_url(match: re.Match[str]) -> str:
        fname = match.group(1).strip("'\"")
        fpath = FONTS_DIR / fname
        if not fpath.exists():
            return match.group(0)
        b64 = base64.b64encode(fpath.read_bytes()).decode("ascii")
        return f"url('data:font/woff2;base64,{b64}')"

    return re.sub(r"url\(([^)]+\.woff2)\)", _replace_url, css)


def _normalize_search(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())).strip()


def _launch_chromium(pw: Any) -> Browser:
    """Launch Chromium via Playwright bundled binary, falling back to system Chrome/Edge."""
    launch_errors = []
    try:
        return pw.chromium.launch(headless=True)
    except Exception as exc:
        launch_errors.append(f"default chromium: {exc}")

    for channel in ("chrome", "msedge"):
        try:
            logger.info("Using system browser channel: %s", channel)
            return pw.chromium.launch(headless=True, channel=channel)
        except Exception as exc:
            launch_errors.append(f"{channel}: {exc}")

    raise RuntimeError(
        "Could not launch Chromium/Chrome/Edge for PDF rendering. "
        "Run `playwright install chromium` or install Chrome/Edge.\n"
        + "\n".join(launch_errors)
    )


def _render_html_to_pdf_bytes(browser: Browser, html_content: str, transparent: bool = False) -> bytes:
    """Write HTML to a temp file and load via file:// so local bundled fonts always load crisply."""
    page = browser.new_page()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", encoding="utf-8", delete=False) as tf:
        tf.write(html_content)
        tmp_path = Path(tf.name)
    try:
        page.goto(tmp_path.as_uri(), wait_until="networkidle", timeout=30000)
        try:
            page.evaluate("() => document.fonts.ready")
        except Exception:
            pass
        return page.pdf(
            prefer_css_page_size=True,
            print_background=not transparent,
        )
    finally:
        page.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _render_running_chrome_overlay(
    browser: Browser,
    edition: NewspaperEdition,
    total_pages: int,
    section_pages: dict[str, int],
    digest_page: Optional[int],
    page_w: float,
    page_h: float,
    fonts_css_inline: str,
) -> bytes:
    """Render crisp vector running headers (pages 2..N) & page folios (pages 1..N) as a transparent overlay."""
    # Determine which section name belongs in the top-right running header of each page
    page_section_labels: dict[int, str] = {}
    ordered_starts: list[tuple[int, str]] = [(1, edition.edition_label)]
    for sec in edition.sections:
        if sec.page_number:
            ordered_starts.append((sec.page_number, sec.title))
    if digest_page:
        ordered_starts.append((digest_page, "News Summary"))
    ordered_starts.sort(key=lambda x: x[0])

    for p in range(1, total_pages + 1):
        label = edition.edition_label
        for start_p, sec_label in ordered_starts:
            if p >= start_p:
                label = sec_label
        page_section_labels[p] = label

    pages_html = []
    for p in range(1, total_pages + 1):
        if p == 1:
            pages_html.append(
                f"""<div class="chrome-page">
                  <div class="chrome-footer-first">PAGE 1 &nbsp;·&nbsp; {edition.edition_label.upper()}</div>
                </div>"""
            )
        else:
            sec_label = page_section_labels[p].upper()
            pages_html.append(
                f"""<div class="chrome-page">
                  <div class="chrome-header">
                    <span class="chrome-left">{edition.title.upper()}</span>
                    <span class="chrome-center">{sec_label}</span>
                    <span class="chrome-right">{edition.date_display.upper()}</span>
                  </div>
                  <div class="chrome-footer">— {p} —</div>
                </div>"""
            )

    font_style = f"<style>{fonts_css_inline}</style>" if fonts_css_inline else ""
    overlay_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  {font_style}
  <style>
    @page {{
      size: {page_w}mm {page_h}mm;
      margin: 0;
    }}
    html, body {{
      margin: 0;
      padding: 0;
      background: transparent;
    }}
    .chrome-page {{
      position: relative;
      width: {page_w}mm;
      height: {page_h}mm;
      page-break-after: always;
      overflow: hidden;
    }}
    .chrome-page:last-child {{
      page-break-after: auto;
    }}
    .chrome-header {{
      position: absolute;
      top: 3.4mm;
      left: 6mm;
      right: 6mm;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      border-bottom: 0.45pt solid #222;
      padding-bottom: 1.1mm;
      font-family: 'Libre Franklin', Arial, sans-serif;
      font-size: 5.5pt;
      letter-spacing: 0.55pt;
      color: #222;
    }}
    .chrome-left {{
      font-weight: 800;
      color: #000;
    }}
    .chrome-center {{
      font-weight: 700;
      color: #111;
    }}
    .chrome-right {{
      font-weight: 500;
      color: #333;
    }}
    .chrome-footer {{
      position: absolute;
      bottom: 3.2mm;
      left: 6mm;
      right: 6mm;
      text-align: center;
      font-family: 'Newsreader', Georgia, serif;
      font-size: 6.8pt;
      color: #222;
    }}
    .chrome-footer-first {{
      position: absolute;
      bottom: 3mm;
      left: 6mm;
      right: 6mm;
      text-align: center;
      font-family: 'Libre Franklin', Arial, sans-serif;
      font-size: 5.4pt;
      font-weight: 600;
      letter-spacing: 0.7pt;
      color: #333;
    }}
  </style>
</head>
<body>
  {"".join(pages_html)}
</body>
</html>"""
    return _render_html_to_pdf_bytes(browser, overlay_html, transparent=True)


def _locate_pages(
    pdf_bytes: bytes,
    edition: NewspaperEdition,
) -> tuple[dict[str, int], dict[str, int], Optional[int]]:
    """Scan rendered PDF pages to find exact 1-based page numbers for sections & articles."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    page_texts = [_normalize_search(p.extract_text() or "") for p in reader.pages]

    section_pages: dict[str, int] = {}
    article_pages: dict[str, int] = {}
    digest_page: Optional[int] = None

    numerals = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii"]
    for idx, sec in enumerate(edition.sections):
        banner_key = _normalize_search(f"{numerals[idx]} {sec.title}")
        fallback_key = _normalize_search(sec.subtitle) if sec.subtitle else _normalize_search(sec.title)
        for p_idx, p_txt in enumerate(page_texts):
            if p_idx == 0:
                continue
            if banner_key in p_txt or (fallback_key and fallback_key in p_txt):
                section_pages[sec.id] = p_idx + 1
                break

        start_p = (section_pages.get(sec.id, 2)) - 1
        for art in sec.articles:
            needle = _normalize_search(art.title)[:50]
            if not needle:
                continue
            for p_idx in range(start_p, len(page_texts)):
                if needle in page_texts[p_idx]:
                    article_pages[art.id] = p_idx + 1
                    break

    for art in (edition.lead_story, edition.secondary_story):
        if not art:
            continue
        needle = _normalize_search(art.title)[:50]
        for p_idx, p_txt in enumerate(page_texts):
            if needle and needle in p_txt:
                article_pages[art.id] = p_idx + 1
                break

    for p_idx in range(len(page_texts) - 1, 0, -1):
        if "news summary" in page_texts[p_idx]:
            digest_page = p_idx + 1
            break

    return section_pages, article_pages, digest_page


def _finalize_pdf(
    body_pdf_bytes: bytes,
    overlay_pdf_bytes: bytes,
    output_path: Path,
    edition: NewspaperEdition,
    section_pages: dict[str, int],
    article_pages: dict[str, int],
    digest_page: Optional[int],
) -> int:
    """Merge running headers/folios onto body pages and embed nested KOReader TOC bookmarks."""
    body_reader = PdfReader(io.BytesIO(body_pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_pdf_bytes))
    writer = PdfWriter()

    total_pages = len(body_reader.pages)
    for idx, page in enumerate(body_reader.pages):
        out_page = writer.add_page(page)
        if idx < len(overlay_reader.pages):
            out_page.merge_page(overlay_reader.pages[idx])

    writer.add_metadata(
        {
            "/Title": f"{edition.title} — {edition.date_display}",
            "/Author": edition.title,
            "/Subject": f"Daily Personal Newspaper ({edition.edition_label})",
            "/Creator": "The David Times Kindle Publisher",
        }
    )

    front_node = writer.add_outline_item("Front Page", 0)
    if edition.lead_story:
        p = max(0, min(total_pages - 1, article_pages.get(edition.lead_story.id, 1) - 1))
        writer.add_outline_item(edition.lead_story.title[:70], p, parent=front_node)
    if edition.secondary_story:
        p = max(0, min(total_pages - 1, article_pages.get(edition.secondary_story.id, 1) - 1))
        writer.add_outline_item(edition.secondary_story.title[:70], p, parent=front_node)

    for sec in edition.sections:
        if not sec.articles:
            continue
        sec_p = max(0, min(total_pages - 1, section_pages.get(sec.id, 2) - 1))
        sec_node = writer.add_outline_item(sec.title, sec_p)
        for art in sec.articles:
            art_p = max(0, min(total_pages - 1, article_pages.get(art.id, sec_p + 1) - 1))
            writer.add_outline_item(art.title[:70], art_p, parent=sec_node)

    if digest_page:
        writer.add_outline_item("News Summary", max(0, min(total_pages - 1, digest_page - 1)))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)

    return total_pages


def render_newspaper_pdf(
    edition: NewspaperEdition,
    config: dict[str, Any],
    output_path: Path,
    save_html_path: Optional[Path] = None,
) -> int:
    """Two-pass HTML->PDF render with bundled fonts, exact page jumps, running headers & TOC."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    css_template = env.get_template("newspaper.css")
    html_template = env.get_template("newspaper.html")

    device = config.get("device", {})
    page_w = float(device.get("page_width_mm", 108))
    page_h = float(device.get("page_height_mm", 144))
    fonts_css_inline = _load_inline_fonts_css()

    def build_html(digest_page: Optional[int] = None) -> str:
        css_rendered = css_template.render(
            edition=edition,
            page_width_mm=page_w,
            page_height_mm=page_h,
        )
        return html_template.render(
            edition=edition,
            css_content=css_rendered,
            digest_page=digest_page,
            fonts_css_inline=fonts_css_inline,
        )

    with sync_playwright() as pw:
        browser = _launch_chromium(pw)
        try:
            # Pass 1: discover real page numbers for every section and briefing jump
            pass1_html = build_html(digest_page=None)
            pass1_pdf = _render_html_to_pdf_bytes(browser, pass1_html)
            section_pages, article_pages, digest_page = _locate_pages(pass1_pdf, edition)

            for sec in edition.sections:
                sec.page_number = section_pages.get(sec.id)
            for item in edition.briefing_items:
                item.page_number = article_pages.get(item.article_id)

            # Pass 2: final render with exact page numbers printed on Page 1
            final_html = build_html(digest_page=digest_page)
            if save_html_path:
                save_html_path.parent.mkdir(parents=True, exist_ok=True)
                save_html_path.write_text(final_html, encoding="utf-8")

            final_pdf = _render_html_to_pdf_bytes(browser, final_html)
            section_pages, article_pages, digest_page = _locate_pages(final_pdf, edition)
            for sec in edition.sections:
                sec.page_number = section_pages.get(sec.id)

            total_pages = len(PdfReader(io.BytesIO(final_pdf)).pages)
            overlay_pdf = _render_running_chrome_overlay(
                browser,
                edition,
                total_pages,
                section_pages,
                digest_page,
                page_w,
                page_h,
                fonts_css_inline,
            )
        finally:
            browser.close()

    return _finalize_pdf(
        final_pdf,
        overlay_pdf,
        output_path,
        edition,
        section_pages,
        article_pages,
        digest_page,
    )
