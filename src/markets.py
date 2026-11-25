from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Optional

import requests

from .models import EarningsEvent, IndexQuote, MarketsData, StockMover

logger = logging.getLogger(__name__)

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

NASDAQ_HEADERS = {
    **YF_HEADERS,
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
    "Accept-Language": "en-US,en;q=0.9",
}

INDICES = [
    ("^GSPC", "S&P 500"),
    ("^DJI", "Dow Jones"),
    ("^IXIC", "Nasdaq"),
]


def _shorten_company(name: str, max_len: int = 20) -> str:
    if not name:
        return ""
    # Strip trailing corporate boilerplate for clean newspaper tables
    cleaned = re.sub(
        r",?\s*\b(Inc\.?|Corp\.?|Corporation|Company|Co\.?|Ltd\.?|Limited|PLC|Group|Holdings|Technologies|International)\b\.?",
        "",
        name,
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-&")
    if not cleaned:
        cleaned = name.strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip(" ,.-") + "…"
    return cleaned


def _parse_market_cap_num(cap_str: str) -> float:
    if not cap_str:
        return 0.0
    digits = re.sub(r"[^0-9.]", "", cap_str)
    try:
        return float(digits)
    except ValueError:
        return 0.0


def _format_market_cap(cap_val: float) -> str:
    if cap_val >= 1e12:
        return f"${cap_val / 1e12:.1f}T"
    if cap_val >= 1e9:
        return f"${cap_val / 1e9:.0f}B" if cap_val >= 10e9 else f"${cap_val / 1e9:.1f}B"
    if cap_val >= 1e6:
        return f"${cap_val / 1e6:.0f}M"
    return ""


def _fetch_single_index(symbol: str, label: str, timeout: int = 8) -> Optional[IndexQuote]:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        resp = requests.get(url, params={"range": "5d", "interval": "1d"}, headers=YF_HEADERS, timeout=timeout)
        resp.raise_for_status()
        res = resp.json()["chart"]["result"][0]
        meta = res["meta"]
        price = float(meta["regularMarketPrice"])

        # Use the prior trading session's close for accurate 1-day change
        closes = [
            float(c)
            for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
            if c is not None
        ]
        if len(closes) >= 2:
            prev_close = closes[-2]
        else:
            prev_close = float(meta.get("previousClose") or meta.get("chartPreviousClose") or price)

        change = price - prev_close
        pct = (change / prev_close * 100.0) if prev_close else 0.0
        return IndexQuote(
            symbol=symbol,
            name=label,
            price=price,
            change=change,
            change_pct=pct,
        )
    except Exception as exc:
        logger.debug("Index fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_movers(screener_id: str, count: int = 5, timeout: int = 8) -> list[StockMover]:
    try:
        url = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        params = {"formatted": "false", "scrIds": screener_id, "count": str(count + 4)}
        resp = requests.get(url, params=params, headers=YF_HEADERS, timeout=timeout)
        resp.raise_for_status()
        quotes = resp.json()["finance"]["result"][0].get("quotes", [])

        movers: list[StockMover] = []
        for q in quotes:
            sym = (q.get("symbol") or "").strip()
            if not sym or len(sym) > 6:
                continue
            price = q.get("regularMarketPrice")
            pct = q.get("regularMarketChangePercent")
            if price is None or pct is None:
                continue
            name = _shorten_company(q.get("shortName") or q.get("longName") or sym, max_len=16)
            mcap = _format_market_cap(float(q.get("marketCap") or 0))
            movers.append(
                StockMover(
                    symbol=sym,
                    name=name,
                    price=float(price),
                    change_pct=float(pct),
                    market_cap_str=mcap,
                )
            )
            if len(movers) >= count:
                break
        return movers
    except Exception as exc:
        logger.debug("Movers fetch failed for %s: %s", screener_id, exc)
        return []


def _format_timing(raw_time: str) -> str:
    low = (raw_time or "").lower()
    if "pre" in low:
        return "Pre-Mkt"
    if "after" in low:
        return "After-Hrs"
    return "TBD"


def _fetch_earnings_for_day(target_dt: datetime, is_today: bool, timeout: int = 8) -> list[tuple[float, str, EarningsEvent]]:
    d_iso = target_dt.strftime("%Y-%m-%d")
    date_label = "Today" if is_today else target_dt.strftime("%a %b %d").replace(" 0", " ")
    try:
        url = f"https://api.nasdaq.com/api/calendar/earnings?date={d_iso}"
        resp = requests.get(url, headers=NASDAQ_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return []
        rows = (resp.json().get("data") or {}).get("rows") or []
        parsed: list[tuple[float, str, EarningsEvent]] = []
        for row in rows:
            sym = (row.get("symbol") or "").strip()
            if not sym or len(sym) > 6:
                continue
            cap_num = _parse_market_cap_num(row.get("marketCap") or "")
            if cap_num < 1e9:  # Skip sub-$1B micro-caps so only notable companies appear
                continue
            eps_raw = (row.get("epsForecast") or "").strip()
            eps_clean = eps_raw if eps_raw and eps_raw.upper() != "N/A" else "—"
            parsed.append(
                (
                    cap_num,
                    d_iso,
                    EarningsEvent(
                        date_label=date_label,
                        symbol=sym,
                        name=_shorten_company(row.get("name") or sym, max_len=20),
                        timing=_format_timing(row.get("time") or ""),
                        eps_forecast=eps_clean,
                        market_cap_str=_format_market_cap(cap_num),
                    ),
                )
            )
        return parsed
    except Exception as exc:
        logger.debug("NASDAQ earnings fetch failed for %s: %s", d_iso, exc)
        return []


def fetch_markets_data(markets_cfg: dict[str, Any], now: datetime) -> Optional[MarketsData]:
    """Fetch live indices, biggest gainers/losers, and upcoming major earnings (zero API keys)."""
    if not markets_cfg.get("enabled", True):
        return None

    movers_count = int(markets_cfg.get("movers_count", 5))
    earnings_count = int(markets_cfg.get("earnings_count", 7))
    lookahead_days = int(markets_cfg.get("earnings_lookahead_days", 6))

    # Build weekday dates from today onward
    target_days: list[tuple[datetime, bool]] = []
    for off in range(0, lookahead_days + 2):
        dt = now + timedelta(days=off)
        if dt.weekday() < 5:  # Mon–Fri trading days
            target_days.append((dt, off == 0))
        if len(target_days) >= 5:
            break

    with ThreadPoolExecutor(max_workers=10) as pool:
        idx_futs = [pool.submit(_fetch_single_index, sym, label) for sym, label in INDICES]
        gain_fut = pool.submit(_fetch_movers, "day_gainers", movers_count)
        lose_fut = pool.submit(_fetch_movers, "day_losers", movers_count)
        earn_futs = [pool.submit(_fetch_earnings_for_day, dt, is_t) for dt, is_t in target_days]

        indices = [q for q in (f.result() for f in idx_futs) if q is not None]
        gainers = gain_fut.result() or []
        losers = lose_fut.result() or []

        all_earnings: list[tuple[float, str, EarningsEvent]] = []
        for ef in earn_futs:
            all_earnings.extend(ef.result() or [])

    # Pick the largest-cap upcoming earnings, then display them in chronological order!
    all_earnings.sort(key=lambda x: x[0], reverse=True)
    top_earnings = all_earnings[:earnings_count]
    top_earnings.sort(key=lambda x: (x[1], -x[0]))
    earnings_list = [ev for _, _, ev in top_earnings]

    if not indices and not gainers and not losers and not earnings_list:
        return None

    return MarketsData(
        indices=indices,
        gainers=gainers,
        losers=losers,
        earnings=earnings_list,
        as_of=now.strftime("%b %d · %I:%M %p %Z").replace(" 0", " "),
    )
