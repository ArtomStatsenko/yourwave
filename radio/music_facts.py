"""MusicBrainz recording/artist identity + Wikidata Russian descriptions; no LLM.

https://musicbrainz.org/doc/MusicBrainz_API
https://www.wikidata.org/w/api.php?action=help&modules=wbgetentities
"""
import hashlib
import json
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

from . import config

_mb_lock = threading.Lock()
_last_mb = 0.0
USER_AGENT = "PersonalRadio/0.1 (https://github.com/ArtomStatsenko/PersonalRadio)"


def _get(url: str, **params) -> dict:
    global _last_mb
    if url.startswith("https://musicbrainz.org/"):
        with _mb_lock:
            time.sleep(max(0, 1.1 - (time.monotonic() - _last_mb)))
            _last_mb = time.monotonic()
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=6) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("invalid music metadata")
    return data


def _name(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _lookup(artist: str, title: str) -> dict | None:
    quoted = lambda s: '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    result = _get("https://musicbrainz.org/ws/2/recording/", fmt="json", limit=5,
                  query=f"recording:{quoted(title)} AND artist:{quoted(artist)}")
    matches = {}
    for recording in result.get("recordings", []):
        credit = recording.get("artist-credit", [])
        if recording.get("score") != 100 or _name(recording.get("title", "")) != _name(title) or len(credit) != 1:
            continue
        person = credit[0].get("artist", {})
        if _name(person.get("name", "")) == _name(artist) and re.fullmatch(r"[a-f0-9-]{36}", person.get("id", "")):
            matches[person["id"]] = person
    if len(matches) != 1:
        return None  # an unlisted track or ambiguous names are not evidence about this performer
    aid = next(iter(matches))
    person = _get("https://musicbrainz.org/ws/2/artist/" + aid, fmt="json", inc="url-rels")
    if person.get("id") != aid or _name(person.get("name", "")) != _name(artist):
        return None
    facts, urls = [], ["https://musicbrainz.org/artist/" + aid]
    for relation in person.get("relations", []):
        url = relation.get("url", {}).get("resource", "")
        match = re.fullmatch(r"https?://(?:www\.)?wikidata.org/wiki/(Q[1-9][0-9]*)", url)
        if relation.get("type") != "wikidata" or not match:
            continue
        qid = match[1]
        try:
            entity = _get("https://www.wikidata.org/w/api.php", action="wbgetentities", ids=qid,
                          props="descriptions", languages="ru", format="json")["entities"][qid]
            description = entity.get("descriptions", {}).get("ru", {}).get("value", "")
            if 5 <= len(description) <= 180 and re.search(r"[А-Яа-яЁё]", description) and "http" not in description.lower():
                facts.append(f"«{artist}» — {description.rstrip('.')}.")
                urls.append("https://www.wikidata.org/wiki/" + qid)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass  # MusicBrainz facts remain usable if Wikidata is temporarily unavailable
        break
    began = (person.get("life-span") or {}).get("begin") or ""
    if person.get("type") == "Group" and re.fullmatch(r"\d{4}(?:-\d{2}){0,2}", began):
        year = int(began[:4])
        if 1 <= year <= datetime.now().year:
            facts.append(f"Коллектив «{artist}» появился в {year} году.")
    return {"text": " ".join(facts), "urls": urls} if facts else None


def fetch(artist: str, title: str) -> dict | None:
    key = hashlib.sha256((_name(artist) + "|" + _name(title)).encode()).hexdigest()[:32]
    path = config.DATA_DIR / "cache/music-facts" / (key + ".json")
    try:
        cached = json.loads(path.read_text())
        if cached["expires"] > time.time():
            return cached["facts"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    ttl = 7 * 86400
    try:
        facts = _lookup(artist, title)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        facts, ttl = None, 15 * 60
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"expires": time.time() + ttl, "facts": facts}, ensure_ascii=False))
    tmp.replace(path)
    return facts
