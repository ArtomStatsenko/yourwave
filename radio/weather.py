"""Short, source-backed weather links. Open-Meteo, no API key or LLM.

Sources: https://open-meteo.com/en/docs and https://open-meteo.com/en/docs/geocoding-api
"""
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

INTERVAL = timedelta(hours=3)
CONDITIONS = {
    0: ("ясно", "clear"), 1: ("преимущественно ясно", "mainly clear"),
    2: ("переменная облачность", "partly cloudy"), 3: ("пасмурно", "overcast"),
    45: ("туман", "fog"), 48: ("изморозь и туман", "rime fog"),
    51: ("слабая морось", "light drizzle"), 53: ("морось", "drizzle"), 55: ("сильная морось", "heavy drizzle"),
    56: ("переохлаждённая морось", "freezing drizzle"), 57: ("сильная переохлаждённая морось", "heavy freezing drizzle"),
    61: ("небольшой дождь", "light rain"), 63: ("дождь", "rain"), 65: ("сильный дождь", "heavy rain"),
    66: ("ледяной дождь", "freezing rain"), 67: ("сильный ледяной дождь", "heavy freezing rain"),
    71: ("небольшой снег", "light snow"), 73: ("снег", "snow"), 75: ("сильный снег", "heavy snow"),
    77: ("снежные зёрна", "snow grains"), 80: ("небольшой ливень", "light showers"),
    81: ("ливень", "showers"), 82: ("сильный ливень", "heavy showers"),
    85: ("снежные заряды", "snow showers"), 86: ("сильные снежные заряды", "heavy snow showers"),
    95: ("гроза", "thunderstorm"), 96: ("гроза с градом", "thunderstorm with hail"),
    99: ("сильная гроза с градом", "severe thunderstorm with hail"),
}


def _get(url: str, **params):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "PersonalRadio/0.1 (local weather)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _number(value, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError("invalid weather value")
    return value


def _unit(n: int, forms: tuple[str, str, str]) -> str:
    if 11 <= abs(n) % 100 <= 14:
        return forms[2]
    return forms[0] if abs(n) % 10 == 1 else forms[1] if abs(n) % 10 in (2, 3, 4) else forms[2]


def fetch(city: str, at: datetime) -> dict:
    found = _get("https://geocoding-api.open-meteo.com/v1/search", name=city, count=1, language="ru", format="json")
    if not found.get("results"):
        raise ValueError("weather city not found")
    place = found["results"][0]
    lat, lon = _number(place["latitude"], -90, 90), _number(place["longitude"], -180, 180)
    data = _get("https://api.open-meteo.com/v1/forecast", latitude=lat, longitude=lon,
                current="temperature_2m,weather_code,wind_speed_10m", hourly="precipitation_probability",
                forecast_hours=4, timeformat="unixtime", wind_speed_unit="ms")
    return report(data, place["name"], at, city)


def report(data: dict, city: str, at: datetime, display_city: str) -> dict:
    current, hourly = data["current"], data["hourly"]
    issued = datetime.fromtimestamp(_number(current["time"], 0, 4e9), timezone.utc)
    if not at - timedelta(minutes=45) <= issued <= at + timedelta(minutes=5):
        raise ValueError("stale weather")
    temp = round(_number(current["temperature_2m"], -90, 60))
    wind = round(_number(current["wind_speed_10m"], 0, 150))
    code = _number(current["weather_code"], 0, 99)
    if code not in CONDITIONS:
        raise ValueError("unknown weather code")
    if len(hourly["time"]) != len(hourly["precipitation_probability"]):
        raise ValueError("incomplete weather forecast")
    times = [_number(t, 0, 4e9) for t in hourly["time"]]
    if any(b - a != 3600 for a, b in zip(times, times[1:])):
        raise ValueError("invalid hourly weather times")
    future = [_number(p, 0, 100) for t, p in zip(times, hourly["precipitation_probability"])
              if at.timestamp() < t <= (at + INTERVAL).timestamp()]
    if len(future) != 3:
        raise ValueError("incomplete weather forecast")
    city = re.sub(r"[^\w\s-]", "", city, flags=re.UNICODE).strip()[:80]
    if not city:
        raise ValueError("missing weather city")
    degrees = ("плюс " if temp > 0 else "минус " if temp < 0 else "") + str(abs(temp))
    rain = ("высока вероятность осадков" if max(future) >= 60 else
            "возможны осадки" if max(future) >= 30 else "вероятность осадков невысокая")
    text = (f"Коротко о погоде. {city}: по прогнозу сейчас {degrees} {_unit(temp, ('градус', 'градуса', 'градусов'))}, "
            f"{CONDITIONS[code][0]}. В ближайшие три часа {rain}. "
            f"Ветер — {wind} {_unit(wind, ('метр', 'метра', 'метров'))} в секунду.")
    display_city = re.sub(r"[^\w\s-]", "", display_city).strip()[:80]
    summary = f"{display_city} · {temp}° · {CONDITIONS[code][1]} · wind {wind} m/s" + (" · rain likely" if max(future) >= 60 else "")
    return {"key": f"weather:{int(at.timestamp())}", "kind": "weather", "start": at, "text": text,
            "summary": summary,
            "expires": min(at + timedelta(minutes=30), issued + timedelta(hours=1))}
