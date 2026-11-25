from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .briefing import assemble_edition
from .fetcher import fetch_all_sections
from .images import hydrate_article_images
from .markets import fetch_markets_data
from .publish import publish_static_site
from .renderer import render_newspaper_pdf
from .weather import fetch_weather

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("davids-times")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate The David Times daily Kindle newspaper PDF.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to config.yaml")
    parser.add_argument("--output", type=Path, default=Path("output/latest.pdf"), help="Output PDF path")
    parser.add_argument("--save-html", type=Path, default=None, help="Optional path to save debug HTML")
    parser.add_argument("--publish-dir", type=Path, default=None, help="Optional static site dir (GitHub Pages/OPDS)")
    parser.add_argument("--base-url", type=str, default="", help="Public base URL for OPDS/latest.json links")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    tz_name = config.get("paper", {}).get("timezone", "America/Los_Angeles")
    now = datetime.now(ZoneInfo(tz_name))

    logger.info("Compiling %s for %s...", config["paper"]["title"], now.strftime("%A, %B %d, %Y"))

    # 1. Fetch San Francisco weather & live Markets snapshot (zero API keys)
    weather = fetch_weather(config.get("weather", {}), tz_name)
    if weather:
        logger.info("Weather: %s — %s, H %d° / L %d°", weather.city, weather.condition, weather.high_temp, weather.low_temp)

    markets = fetch_markets_data(config.get("markets", {}), now)
    if markets:
        logger.info(
            "Markets: %d indices, %d gainers, %d losers, %d upcoming earnings",
            len(markets.indices), len(markets.gainers), len(markets.losers), len(markets.earnings),
        )

    # 2. Fetch, rank, deduplicate & extract full articles from homepages & RSS sources
    articles_by_section, runner_ups = fetch_all_sections(config, now)
    for sec_id, arts in articles_by_section.items():
        logger.info("Section %-15s -> %d full articles", sec_id, len(arts))

    # 3. Download & optimize grayscale e-ink photos BEFORE selecting Front Page so hero image is guaranteed!
    all_candidates = [art for arts in articles_by_section.values() for art in arts]
    logger.info("Optimizing images for e-ink (%d articles)...", len(all_candidates))
    hydrate_article_images(all_candidates, config.get("device", {}))
    with_imgs = sum(1 for a in all_candidates if a.image_data_uri)
    logger.info("Embedded %d grayscale e-ink photographs.", with_imgs)

    # 4. Assemble Front Page, Morning Briefing, Section Editions & Back-Page Digest
    edition = assemble_edition(config, now, weather, articles_by_section, runner_ups, markets=markets)

    # 5. Render 2-pass PDF with exact Front Page jump numbers + KOReader TOC bookmarks
    page_count = render_newspaper_pdf(
        edition,
        config,
        output_path=args.output,
        save_html_path=args.save_html,
    )
    size_kb = args.output.stat().st_size // 1024
    logger.info("Generated %s (%d pages, %d KB)", args.output, page_count, size_kb)

    # 6. Optional GitHub Pages / OPDS / KOReader plugin bundle
    if args.publish_dir:
        publish_static_site(
            pdf_path=args.output,
            edition=edition,
            page_count=page_count,
            publish_dir=args.publish_dir,
            base_url=args.base_url,
        )
        logger.info("Published static OPDS + archive bundle to %s", args.publish_dir)


if __name__ == "__main__":
    main()
