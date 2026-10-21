"""When the vocals of a song start, from time-synced lyrics on LRCLIB (lrclib.net: open, no key). Only
"artist + title" leaves the machine, once per track; the answer is cached in the tracks table."""
import json
import re
import urllib.parse
import urllib.request

URL = "https://lrclib.net/api/search"
USER_AGENT = "PersonalRadio/0.1 (personal use; one lookup per track)"
LAST_LINE_SEC = 4.0  # guess for a last line without an end mark
MAX_DURATION_DIFF = 3.0  # a found version must be this close in length, or its timings are someone else's


def first_line_sec(synced: str) -> float | None:
    """[mm:ss.xx] of the first line that has words."""
    m = re.search(r"^\[(\d+):(\d+(?:\.\d+)?)\][^\S\n]*\w", synced or "", re.M)  # a word on the same line
    return int(m[1]) * 60 + float(m[2]) if m else None


def last_line_end_sec(synced: str) -> float | None:
    """When the last sung line ends: an empty last line is its end mark, else the last line plus a guess."""
    lines = re.findall(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$", synced or "", re.M)
    if not lines:
        return None
    m, s, text = lines[-1]
    t = int(m) * 60 + float(s)
    return t if not text.strip() else t + LAST_LINE_SEC


def pick(results: list, duration: float) -> tuple[str, float | None, float | None]:
    """("synced", first line, end of the last line) | ("instrumental", None, None) | ("none", None, None)
    from LRCLIB search results."""
    close = [r for r in results if isinstance(r, dict) and r.get("duration")
             and abs(r["duration"] - duration) <= MAX_DURATION_DIFF]
    for r in close:
        if (t := first_line_sec(r.get("syncedLyrics"))) is not None:
            return "synced", t, last_line_end_sec(r["syncedLyrics"])
    if close and all(r.get("instrumental") for r in close):
        return "instrumental", None, None
    return "none", None, None


def lookup(artist: str, title: str, duration: float) -> tuple[str, float | None, float | None]:
    """Raises on network errors (the track is tried again next time). "A, B - Song" is also tried as "A - Song"."""
    found = _search(artist, title, duration)
    if found[0] == "none" and "," in artist:
        found = _search(artist.split(",")[0].strip(), title, duration)
    return found


def _search(artist: str, title: str, duration: float) -> tuple[str, float | None, float | None]:
    q = urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        results = json.load(r)
    return pick(results if isinstance(results, list) else [], duration)
