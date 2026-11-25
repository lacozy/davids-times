from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    id: str
    section_id: str
    section_title: str
    title: str
    url: str
    source: str
    published: Optional[datetime] = None
    author: str = ""
    deck: str = ""
    paragraphs: list[str] = field(default_factory=list)
    summary_blurb: str = ""
    image_url: Optional[str] = None
    image_data_uri: Optional[str] = None
    image_caption: str = ""
    image_is_wide: bool = True
    score: float = 0.0
    read_minutes: int = 2
    is_front_page: bool = False
    truncated: bool = False
    corroborating_sources: list[str] = field(default_factory=list)

    @property
    def byline(self) -> str:
        parts = []
        if self.author:
            clean_author = self.author.strip()
            low = clean_author.lower()
            if not low.startswith("by ") and "pts on hn" not in low:
                clean_author = f"By {clean_author}"
            parts.append(clean_author)
        if self.source:
            parts.append(self.source)
        return " · ".join(parts)

    @property
    def published_short(self) -> str:
        if not self.published:
            return ""
        return self.published.strftime("%b %d, %I:%M %p").replace(" 0", " ")


@dataclass
class BriefingItem:
    section_id: str
    section_title: str
    headline: str
    summary: str
    source: str
    article_id: str = ""
    page_number: Optional[int] = None


@dataclass
class WeatherSlot:
    label: str
    temp: int
    condition: str
    precip_prob: int


@dataclass
class WeatherData:
    city: str
    current_temp: int
    feels_like: int
    high_temp: int
    low_temp: int
    condition: str
    weather_code: int
    precip_prob: int
    wind_mph: int
    sunrise: str
    sunset: str
    unit_symbol: str
    icon_svg: str
    slots: list[WeatherSlot] = field(default_factory=list)


@dataclass
class IndexQuote:
    symbol: str
    name: str
    price: float
    change: float
    change_pct: float


@dataclass
class StockMover:
    symbol: str
    name: str
    price: float
    change_pct: float
    market_cap_str: str = ""


@dataclass
class EarningsEvent:
    date_label: str
    symbol: str
    name: str
    timing: str = ""
    eps_forecast: str = ""
    market_cap_str: str = ""


@dataclass
class MarketsData:
    indices: list[IndexQuote] = field(default_factory=list)
    gainers: list[StockMover] = field(default_factory=list)
    losers: list[StockMover] = field(default_factory=list)
    earnings: list[EarningsEvent] = field(default_factory=list)
    as_of: str = ""


@dataclass
class SectionEdition:
    id: str
    title: str
    subtitle: str
    articles: list[Article] = field(default_factory=list)
    page_number: Optional[int] = None


@dataclass
class NewspaperEdition:
    title: str
    motto: str
    edition_label: str
    price_line: str
    date_display: str
    date_iso: str
    volume_roman: str
    issue_number: int
    weather: Optional[WeatherData]
    lead_story: Optional[Article]
    secondary_story: Optional[Article]
    briefing_items: list[BriefingItem] = field(default_factory=list)
    sections: list[SectionEdition] = field(default_factory=list)
    also_in_news: list[BriefingItem] = field(default_factory=list)
    markets: Optional[MarketsData] = None
    generated_at: str = ""
    total_articles: int = 0
