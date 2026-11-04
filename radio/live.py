"""Internet radio: a station from the Radio Browser catalog (radio-browser.info, open, no account) plays in place of
the library. Liquidsoap reads its stream (radio.liq live.set); each title change in the stream is a track boundary,
so the host's editions still come between songs. The chosen station is kept in DATA_DIR/live.json."""
import json
import urllib.parse
import urllib.request

from . import config

API = "https://de1.api.radio-browser.info/json/"
AGENT = "Yourwave/1.0 (personal radio)"  # the catalog asks clients to name themselves


def _get(path: str, params: dict | None = None):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": AGENT}), timeout=15) as r:
            return json.load(r)
    except (OSError, ValueError):
        raise ValueError("The radio catalog did not answer; try again") from None


def _station(s: dict) -> dict | None:
    url = s.get("url_resolved") or s.get("url") or ""
    if not url.startswith(("https://", "http://")) or not s.get("stationuuid"):
        return None
    return {"id": s["stationuuid"], "name": " ".join(str(s.get("name", "")).split()) or url, "url": url,
            "tags": [t for t in str(s.get("tags", "")).split(",") if t][:6], "country": s.get("countrycode") or "",
            "codec": s.get("codec") or "", "bitrate": s.get("bitrate") or 0, "favicon": s.get("favicon") or ""}


def search(name: str = "", tag: str = "", limit: int = 40) -> list[dict]:
    """Working stations by name or tag, most listened first."""
    found = _get("stations/search", {"name": name.strip()[:80], "tag": tag.strip()[:40], "limit": max(1, min(limit, 100)),
                                     "order": "clickcount", "reverse": "true", "hidebroken": "true"})
    return [s for s in map(_station, found if isinstance(found, list) else []) if s]


def tags(limit: int = 40) -> list[str]:
    found = _get("tags", {"order": "stationcount", "reverse": "true", "limit": max(1, min(limit, 200)), "hidebroken": "true"})
    return [t["name"] for t in found if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()]


def current() -> dict | None:
    try:
        data = json.loads((config.DATA_DIR / "live.json").read_text())
        return data if isinstance(data, dict) and str(data.get("url", "")).startswith(("https://", "http://")) else None
    except (OSError, ValueError):
        return None


def choose(station_id: str) -> dict:
    """The station by its catalog id: kept as the one on air; the catalog counts the listen."""
    found = _get("stations/byuuid/" + urllib.parse.quote(station_id.strip()))
    station = next(filter(None, map(_station, found if isinstance(found, list) else [])), None)
    if not station:
        raise ValueError("This station is no longer in the catalog")
    try:
        _get("url/" + urllib.parse.quote(station["id"]))  # Radio Browser's click count: popular stations rise
    except ValueError:
        pass
    path = config.DATA_DIR / "live.json"
    path.with_suffix(".tmp").write_text(json.dumps(station, ensure_ascii=False))
    path.with_suffix(".tmp").replace(path)
    return station


def stop() -> None:
    (config.DATA_DIR / "live.json").unlink(missing_ok=True)
