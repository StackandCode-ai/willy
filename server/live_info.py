"""
Live information for the brain, from free keyless APIs:

    get_weather(place)   Open-Meteo geocoding + current conditions and today's forecast
    web_lookup(query)    DuckDuckGo instant answers, then a Wikipedia summary
"""

import asyncio
import json
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

_UA = {"User-Agent": "WillyHub/3.1 (personal assistant)"}
_TIMEOUT = 8

_WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorm",
    96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


def _get_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as res:
        return json.loads(res.read().decode("utf-8"))


def _weather(place: str) -> Dict[str, Any]:
    geo = _get_json("https://geocoding-api.open-meteo.com/v1/search?"
                    + urllib.parse.urlencode({"name": place, "count": 1, "language": "en"}))
    hits = geo.get("results") or []
    if not hits:
        return {"success": False, "error": f"I couldn't find a place called '{place}'."}
    loc = hits[0]
    params = {
        "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto",
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "forecast_days": 2,
    }
    data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params))
    cur, daily = data.get("current") or {}, data.get("daily") or {}
    name = ", ".join(x for x in (loc.get("name"), loc.get("admin1"), loc.get("country")) if x)
    sky = _WEATHER_CODES.get(cur.get("weather_code"), "")
    result = {
        "success": True, "place": name, "local_time": cur.get("time"),
        "now": {"temp_c": cur.get("temperature_2m"), "feels_like_c": cur.get("apparent_temperature"),
                "humidity_pct": cur.get("relative_humidity_2m"), "wind_kmh": cur.get("wind_speed_10m"), "sky": sky},
        "today": {"max_c": (daily.get("temperature_2m_max") or [None])[0],
                  "min_c": (daily.get("temperature_2m_min") or [None])[0],
                  "rain_chance_pct": (daily.get("precipitation_probability_max") or [None])[0]},
        "tomorrow": {"max_c": (daily.get("temperature_2m_max") or [None, None])[1],
                     "min_c": (daily.get("temperature_2m_min") or [None, None])[1],
                     "sky": _WEATHER_CODES.get((daily.get("weather_code") or [None, None])[1], "")},
    }
    t = result["now"]["temp_c"]
    result["message"] = (f"In {loc.get('name')} it's {round(t)}°C and {sky}" if t is not None else f"Weather for {name}")
    return result


def _lookup(query: str) -> Dict[str, Any]:
    ddg = _get_json("https://api.duckduckgo.com/?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "no_html": 1, "skip_disambig": 1}))
    answer = ddg.get("Answer") or ddg.get("AbstractText") or ddg.get("Definition")
    if answer:
        return {"success": True, "source": ddg.get("AbstractURL") or "DuckDuckGo", "answer": str(answer)[:1500]}
    search = _get_json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "query", "list": "search", "srsearch": query, "srlimit": 1, "format": "json"}))
    hits = (search.get("query") or {}).get("search") or []
    if hits:
        title = hits[0]["title"]
        page = _get_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")))
        extract = page.get("extract")
        if extract:
            return {"success": True, "source": f"Wikipedia: {title}", "answer": extract[:1500]}
    return {"success": False, "error": "I couldn't find that online. For news or prices I can open a web search on your PC or phone."}


async def get_weather(place: str) -> Dict[str, Any]:
    if not (place or "").strip():
        return {"success": False, "error": "Which place?"}
    try:
        return await asyncio.to_thread(_weather, place.strip())
    except Exception as e:  # network trouble: say so instead of guessing
        return {"success": False, "error": f"The weather service didn't answer ({type(e).__name__})."}


async def web_lookup(query: str) -> Dict[str, Any]:
    if not (query or "").strip():
        return {"success": False, "error": "What should I look up?"}
    try:
        return await asyncio.to_thread(_lookup, query.strip())
    except Exception as e:
        return {"success": False, "error": f"The lookup failed ({type(e).__name__})."}


def _place_time(place: str) -> Dict[str, Any]:
    import datetime as dt

    geo = _get_json("https://geocoding-api.open-meteo.com/v1/search?"
                    + urllib.parse.urlencode({"name": place, "count": 1, "language": "en"}))
    hits = geo.get("results") or []
    if not hits:
        return {"success": False, "error": f"I couldn't find a place called '{place}'."}
    loc = hits[0]
    data = _get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(
        {"latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": 1}))
    offset = int(data.get("utc_offset_seconds") or 0)
    local = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=offset)
    clock = local.strftime("%I:%M %p").lstrip("0")
    return {"success": True, "place": loc.get("name"), "timezone": data.get("timezone"),
            "local_time": local.strftime("%A %H:%M"),
            "message": f"It's {clock} on {local.strftime('%A')} in {loc.get('name')}."}


async def place_time(place: str) -> Dict[str, Any]:
    if not (place or "").strip():
        return {"success": False, "error": "Which place?"}
    try:
        return await asyncio.to_thread(_place_time, place.strip())
    except Exception as e:
        return {"success": False, "error": f"The time service didn't answer ({type(e).__name__})."}
