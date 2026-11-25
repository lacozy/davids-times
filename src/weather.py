from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import requests

from .models import WeatherData, WeatherSlot

logger = logging.getLogger(__name__)

# WMO Weather interpretation codes -> crisp newspaper labels & icon keys
WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Clear Skies", "sun"),
    1: ("Mostly Clear", "sun"),
    2: ("Partly Cloudy", "partly_cloudy"),
    3: ("Overcast", "cloud"),
    45: ("Coastal Fog", "fog"),
    48: ("Dense Fog", "fog"),
    51: ("Light Drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Heavy Drizzle", "rain"),
    61: ("Light Rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy Rain", "rain"),
    71: ("Light Snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy Snow", "snow"),
    80: ("Passing Showers", "rain"),
    81: ("Rain Showers", "rain"),
    82: ("Heavy Showers", "rain"),
    95: ("Thunderstorm", "storm"),
    96: ("Thunderstorm", "storm"),
    99: ("Severe Storm", "storm"),
}

# Crisp monochrome vector icons designed specifically for 300 PPI e-ink
WEATHER_SVGS: dict[str, str] = {
    "sun": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.8" stroke-linecap="round">
        <circle cx="18" cy="18" r="6.5" fill="#111"/>
        <line x1="18" y1="3" x2="18" y2="7.5"/>
        <line x1="18" y1="28.5" x2="18" y2="33"/>
        <line x1="3" y1="18" x2="7.5" y2="18"/>
        <line x1="28.5" y1="18" x2="33" y2="18"/>
        <line x1="7.4" y1="7.4" x2="10.6" y2="10.6"/>
        <line x1="25.4" y1="25.4" x2="28.6" y2="28.6"/>
        <line x1="28.6" y1="7.4" x2="25.4" y2="10.6"/>
        <line x1="10.6" y1="25.4" x2="7.4" y2="28.6"/>
    </svg>""",
    "partly_cloudy": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="14" cy="13" r="4.5" fill="#111"/>
        <line x1="14" y1="4.5" x2="14" y2="6.5"/>
        <line x1="5.5" y1="13" x2="7.5" y2="13"/>
        <line x1="8" y1="7" x2="9.5" y2="8.5"/>
        <line x1="20" y1="7" x2="18.5" y2="8.5"/>
        <path d="M12 27h14.5a5 5 0 0 0 .8-9.9A6.5 6.5 0 0 0 15 16.5a4.5 4.5 0 0 0-3 10.5Z" fill="#fff"/>
    </svg>""",
    "cloud": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.8" stroke-linejoin="round">
        <path d="M10 26h17a5.5 5.5 0 0 0 .9-10.9A7.5 7.5 0 0 0 13.5 14a5.5 5.5 0 0 0-3.5 12Z" fill="#e8e8e8"/>
    </svg>""",
    "fog": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.8" stroke-linecap="round">
        <path d="M9 19h18a4.5 4.5 0 0 0 .5-9A6 6 0 0 0 16 8.5 4.5 4.5 0 0 0 9 19Z" fill="#f0f0f0"/>
        <line x1="6" y1="23.5" x2="30" y2="23.5"/>
        <line x1="9" y1="27.5" x2="27" y2="27.5"/>
        <line x1="12" y1="31.5" x2="24" y2="31.5"/>
    </svg>""",
    "drizzle": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.7" stroke-linecap="round">
        <path d="M10 21h16.5a4.8 4.8 0 0 0 .7-9.5A6.5 6.5 0 0 0 14.5 10.5 4.8 4.8 0 0 0 10 21Z" fill="#ececec"/>
        <line x1="13" y1="25" x2="11.5" y2="28.5"/>
        <line x1="19" y1="25" x2="17.5" y2="28.5"/>
        <line x1="25" y1="25" x2="23.5" y2="28.5"/>
    </svg>""",
    "rain": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.8" stroke-linecap="round">
        <path d="M10 20h16.5a4.8 4.8 0 0 0 .7-9.5A6.5 6.5 0 0 0 14.5 9.5 4.8 4.8 0 0 0 10 20Z" fill="#dcdcdc"/>
        <line x1="12" y1="24" x2="9.5" y2="30"/>
        <line x1="18" y1="24" x2="15.5" y2="30"/>
        <line x1="24" y1="24" x2="21.5" y2="30"/>
    </svg>""",
    "snow": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.7" stroke-linecap="round">
        <path d="M10 20h16.5a4.8 4.8 0 0 0 .7-9.5A6.5 6.5 0 0 0 14.5 9.5 4.8 4.8 0 0 0 10 20Z" fill="#f4f4f4"/>
        <circle cx="13" cy="26" r="1.4" fill="#111"/>
        <circle cx="19" cy="28" r="1.4" fill="#111"/>
        <circle cx="25" cy="26" r="1.4" fill="#111"/>
    </svg>""",
    "storm": """<svg viewBox="0 0 36 36" width="26" height="26" fill="none" stroke="#111" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M10 19h16.5a4.8 4.8 0 0 0 .7-9.5A6.5 6.5 0 0 0 14.5 8.5 4.8 4.8 0 0 0 10 19Z" fill="#d4d4d4"/>
        <polyline points="19,19 15,26 20,26 16.5,33" stroke-width="2"/>
    </svg>""",
}


def _format_iso_time(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return ""


def fetch_weather(weather_cfg: dict[str, Any], timezone_name: str) -> Optional[WeatherData]:
    """Fetch San Francisco daily weather via Open-Meteo (zero API key required)."""
    if not weather_cfg.get("enabled", True):
        return None

    lat = weather_cfg.get("latitude", 37.7749)
    lon = weather_cfg.get("longitude", -122.4194)
    city = weather_cfg.get("city", "San Francisco")
    unit = weather_cfg.get("unit", "fahrenheit").lower()
    temp_unit_param = "celsius" if unit == "celsius" else "fahrenheit"
    unit_symbol = "°C" if unit == "celsius" else "°F"

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "temperature_unit": temp_unit_param,
        "wind_speed_unit": "mph",
        "timezone": timezone_name,
        "forecast_days": 1,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        current = data["current"]
        daily = data["daily"]
        hourly = data["hourly"]

        code = int(daily["weather_code"][0])
        cond_label, icon_key = WMO_CODES.get(code, ("Partly Cloudy", "partly_cloudy"))

        # Build Morning (9 AM), Afternoon (2 PM), Evening (7 PM) slots
        slots: list[WeatherSlot] = []
        slot_targets = [("Morning", 9), ("Afternoon", 14), ("Evening", 19)]
        hourly_times = hourly.get("time", [])
        hourly_temps = hourly.get("temperature_2m", [])
        hourly_precip = hourly.get("precipitation_probability", [])
        hourly_codes = hourly.get("weather_code", [])

        for label, hour_idx in slot_targets:
            if hour_idx < len(hourly_times):
                h_code = int(hourly_codes[hour_idx]) if hour_idx < len(hourly_codes) else code
                h_cond, _ = WMO_CODES.get(h_code, (cond_label, icon_key))
                slots.append(
                    WeatherSlot(
                        label=label,
                        temp=round(float(hourly_temps[hour_idx])),
                        condition=h_cond,
                        precip_prob=int(hourly_precip[hour_idx] or 0),
                    )
                )

        return WeatherData(
            city=city,
            current_temp=round(float(current["temperature_2m"])),
            feels_like=round(float(current["apparent_temperature"])),
            high_temp=round(float(daily["temperature_2m_max"][0])),
            low_temp=round(float(daily["temperature_2m_min"][0])),
            condition=cond_label,
            weather_code=code,
            precip_prob=int(daily["precipitation_probability_max"][0] or 0),
            wind_mph=round(float(current.get("wind_speed_10m", 0))),
            sunrise=_format_iso_time(daily["sunrise"][0]),
            sunset=_format_iso_time(daily["sunset"][0]),
            unit_symbol=unit_symbol,
            icon_svg=WEATHER_SVGS.get(icon_key, WEATHER_SVGS["partly_cloudy"]),
            slots=slots,
        )
    except Exception as exc:
        logger.warning("Weather fetch failed: %s", exc)
        return None
