"""What was on air, for the terminal UI: songs, news (spoken text, links, original), call signs, sessions."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from . import music, store, ytmusic


def _links(item) -> list[dict]:
    if item["origin_json"]:
        return [{"title": o["repo"], "url": o["url"], "original": o.get("description") or "",
                 "stars_total": o.get("stars_total"), "stars_today": o.get("stars_today")}
                for o in json.loads(item["origin_json"])]
    return [{"title": u.removeprefix("https://").removeprefix("www."), "url": u, "original": ""}
            for u in (item["url"] or "").split() if u.startswith("http")]


def timeline(hours=12, limit=120) -> list[dict]:
    """What was on air, newest first: songs, news (with spoken text, links, original), call signs, sessions.
    Called every 2 s by the page: the connection is closed each time (left open, they ran the server out of files)."""
    try:
        db = store.connect()
    except Exception:
        return []
    try:
        return _build(db, hours, limit)
    finally:
        db.close()


# What the page shows for the spoken topic names (the bulletin text itself stays as it was said).
TOPICS = {"GitHub, тренд дня": "GitHub: trend of the day", "GitHub, находка дня": "GitHub: find of the day",
          "GitHub, новый проект": "GitHub: new project", "GitHub, новый релиз": "GitHub: release",
          "GitHub, новинка недели": "GitHub: new of the week", "Новости технологий": "Tech news",
          "Сводка новостей": "News digest", "GitHub Trending за сутки": "GitHub Trending, today", "Музыкальная пауза": "Music break"}


def topic_name(topic: str | None) -> str:
    return TOPICS.get(topic, topic or "Announcement")


def edition_title(sources) -> str:
    sources = set(sources)
    if sources == {'catchup'}:
        return 'What I missed · 48h'
    if 'weather' in sources:
        return 'Host edition · Weather'
    if sources == {'music_facts'}:
        return 'Host edition · Music stories'
    return 'Host edition'


def _build(db, hours: int, limit: int) -> list[dict]:
    remote_refs = {"ytmusic:" + t["id"] for t in ytmusic.library()["tracks"]}
    since = store.iso(store.now() - timedelta(hours=hours))
    rows = db.execute("""SELECT id, at, type, ref, detail FROM events WHERE at>=? AND type IN
                         ('SESSION_STARTED','MUSIC_STARTED','PACKAGE_STARTED','STATION_ID','REPLAY','REMINDER','WEATHER','GAP','MUSIC_TEASER')
                         ORDER BY id DESC LIMIT ?""", (since, limit)).fetchall()
    out = []
    for r in rows:
        # id = the event row: stable across refreshes, so the UI can keep a row expanded by it.
        e = {"id": r["id"], "time": datetime.fromisoformat(r["at"]).astimezone().strftime("%H:%M"), "type": r["type"]}
        if r["type"] == "MUSIC_STARTED":
            p = Path(r["ref"])
            e.update(kind="music", title=music.label(db, r["ref"]), ref=r["ref"],
                     replayable=p.exists() or r["ref"] in remote_refs)
        elif r["type"] in ("PACKAGE_STARTED", "REPLAY"):
            pk = db.execute("SELECT p.*, i.topic, i.source, i.url, i.origin_json FROM packages p JOIN items i "
                            "ON i.id=p.item_id WHERE p.id=?", (r["ref"],)).fetchone()
            if not pk:
                continue
            script = Path(pk["script_path"]) if pk["script_path"] else None
            text = pk['prepared_text'] if pk['prepared_text'] is not None else script.read_text(encoding='utf-8') if script and script.exists() else ''
            text = text.replace('⁂', '').replace('⁑', '')
            text = re.sub(r"\[[a-z-]+\]\s*", "", text)  # voice tags ([laughter]...) are heard, not shown
            links = _links(pk)
            part = {"title": links[0]["title"] if links and links[0]["title"] else topic_name(pk["topic"]),
                    "source": pk["source"], "text": re.sub(r"\n{3,}", "\n\n", text).strip(), "links": links}
            e.update(kind="host" if pk["source"] == "music_facts" else "news", title="Host edition", ref=pk["id"], outcome=pk["status"],
                     replay=r["type"] == "REPLAY", parts=[part], _at=r["at"])
            e["_topics"] = [pk["topic"]]
            prev = out[-1] if out else None
            if (e["kind"] == "news" and prev and prev["kind"] == "news" and r["type"] == "PACKAGE_STARTED" and not prev["replay"]
                    and abs((datetime.fromisoformat(prev["_at"]) - datetime.fromisoformat(r["at"])).total_seconds()) <= 1):
                # The stories of one break start together: one entry, listed in the order they were said.
                prev["parts"].insert(0, part)
                prev["_topics"].insert(0, pk["topic"])
                prev["id"] = r["id"]
                prev['title'] = 'Host edition'
                continue
        elif r["type"] in ("REMINDER", "WEATHER"):
            e.update(kind=r["type"].lower(), title=r["detail"] or r["type"].lower())
            if r['type'] == 'WEATHER' and not r['detail']:
                requested = db.execute("SELECT detail FROM events WHERE type='WEATHER_REQUESTED' AND ref=? ORDER BY id DESC LIMIT 1", (r['ref'],)).fetchone()
                if requested:
                    e['title'] = requested['detail']
            if r["type"] == "REMINDER":
                try:
                    detail = json.loads(r["detail"] or "")
                except (ValueError, TypeError):
                    detail = None
                if isinstance(detail, dict) and isinstance(detail.get("text"), str):
                    e["title"] = detail["text"]
                    if isinstance(detail.get("description"), str):
                        e["description"] = detail["description"]
                    elif isinstance(detail.get("links"), list):
                        e["links"] = detail["links"]
        elif r['type'] == 'MUSIC_TEASER':
            e.update(kind='host', title='Coming up', parts=[{'text': r['detail'], 'links': []}])
        elif r["type"] == "GAP":
            e.update(kind="host", title=r["detail"] or "Loading gap")
        elif r["type"] == "STATION_ID":
            e.update(kind="id", title="Station ID")
        else:
            e.update(kind="session", title="broadcast started")
        out.append(e)
    for e in out:
        if e.get('parts') and e['type'] != 'MUSIC_TEASER':
            e['title'] = edition_title(part['source'] for part in e['parts'])
        e.pop("_at", None)
        e.pop("_topics", None)
    out = fold_host_appearances(out)
    for e in out:
        link_sources(e)
    return out


def fold_host_appearances(events: list[dict]) -> list[dict]:
    """One host appearance is one entry: a teaser after its edition, or editions back to back, join it (parts in
    the order they were said, event_ids of every event). A song or a session in between keeps them apart."""
    rows = []
    for event in reversed(events):  # oldest first
        prev = rows[-1] if rows else None
        if prev and ((event["type"] == "MUSIC_TEASER" and prev["type"] in ("PACKAGE_STARTED", "REPLAY"))
                     or event["type"] == "PACKAGE_STARTED" and prev["type"] == "PACKAGE_STARTED"):
            rows[-1] = {**prev, "parts": prev.get("parts", []) + event.get("parts", []),
                        "kind": "news" if "news" in (prev["kind"], event["kind"]) else "host",
                        "event_ids": prev.get("event_ids", [prev["id"]]) + [event["id"]]}
        else:
            rows.append(event)
    return rows[::-1]


def source_label(link: dict) -> str:
    """How a source link is named: its title, a Reddit post's slug, or the site."""
    label, address = link.get("title") or "", re.sub(r"^https?://(?:www\.)?", "", link["url"])
    post = re.match(r"reddit\.com/r/[^/]+/comments/[^/]+/([^/?#]+)", address)
    if post and (not label or re.match(r"(?i)reddit:", label)):
        label = "Reddit · " + post[1].replace("_", " ")
    if not label or re.sub(r"^https?://(?:www\.)?", "", label) == address:
        return "Source · " + re.split(r"[/?#]", address)[0]
    return label


WORD = re.compile(r"[^\W_]+(?:[-.][^\W_]+)*")  # letters and digits, joined by - or .
GENERIC = re.compile(r"(the|new|how|why|news|reddit|github|новости|проект|обсуждение)")


def _normal(word: str) -> str:
    return "".join(c for c in word.lower() if c.isalnum())


def mention_segments(part: dict, used: set) -> list[dict]:
    """A story's text in pieces, in order: {text} or {text, url} where the words name one of its sources
    ("MLX-Serve" -> the Reddit post about it, "Next.js" -> its GitHub repository). One link per source in an
    entry: used holds the urls already linked. A name of one short or generic word does not count."""
    text = part.get("text") or ""
    words = list(WORD.finditer(text))
    spans = []
    for link in part.get("links") or []:
        url = link.get("url") or ""
        if not re.match(r"https?://", url):
            continue
        parts = urlsplit(url)
        if not parts.hostname:
            continue
        slug = next((x for x in reversed(parts.path.split("/")) if x), "")
        names = [link.get("title") or "", re.sub(r"^www\.", "", parts.hostname)]
        if re.search(r"(^|\.)reddit\.com$", parts.hostname):
            names.append(slug.replace("_", " "))
        if parts.hostname == "github.com":
            path = parts.path.split("/")
            names.append(path[2] if len(path) > 2 else "")
        best = None
        for name in names:
            tokens = [_normal(t) for t in WORD.findall(name)]
            for n in range(len(tokens), 0, -1):
                if n == 1 and (len(tokens[0]) < 3 or GENERIC.fullmatch(tokens[0])):
                    continue
                for i in range(len(words) - n + 1):
                    if any(tokens[k] != _normal(words[i + k][0]) for k in range(n)):
                        continue
                    start, end = words[i].start(), words[i + n - 1].end()
                    if any(start < s["end"] and end > s["start"] for s in spans):
                        continue
                    if not best or n > best["size"]:
                        best = {"start": start, "end": end, "size": n, "url": url}
        if best:
            spans.append(best)
            used.add(url)
    out, at = [], 0
    for span in sorted(spans, key=lambda x: x["start"]):
        out += [{"text": text[at:span["start"]]}, {"text": text[span["start"]:span["end"]], "url": span["url"]}]
        at = span["end"]
    return out + [{"text": text[at:]}]


def link_sources(entry: dict) -> None:
    """Names every source link (label) and, in a host edition with news, splits each story into segments whose
    mentions link to their sources."""
    used = set()
    for part in entry.get("parts") or []:
        for link in part.get("links") or []:
            if re.match(r"https?://", link.get("url") or ""):
                link["label"] = source_label(link)
        if entry.get("kind") == "news":
            part["segments"] = mention_segments(part, used)
