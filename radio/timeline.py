"""What was on air, for the terminal UI: songs, news (spoken text, links, original), call signs, sessions."""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

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
          "GitHub, новинка недели": "GitHub: new of the week", "Новости технологий": "Tech news",
          "Сводка новостей": "News digest", "GitHub Trending за сутки": "GitHub Trending, today", "Музыкальная пауза": "Music break"}


def topic_name(topic: str | None) -> str:
    return TOPICS.get(topic, topic or "Announcement")


def edition_title(sources) -> str:
    sources = set(sources)
    if 'weather' in sources:
        return 'Host edition · Weather'
    if sources == {'music_facts'}:
        return 'Host edition · Music stories'
    return 'Host edition'


def _build(db, hours: int, limit: int) -> list[dict]:
    remote_refs = {"ytmusic:" + t["id"] for t in ytmusic.library()["tracks"]}
    since = store.iso(store.now() - timedelta(hours=hours))
    rows = db.execute("""SELECT id, at, type, ref, detail FROM events WHERE at>=? AND type IN
                         ('SESSION_STARTED','MUSIC_STARTED','PACKAGE_STARTED','STATION_ID','REPLAY','REMINDER','WEATHER','GAP')
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
            text = script.read_text(encoding="utf-8").replace("⁂", "").replace("⁑", "") if script and script.exists() else ""
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
        elif r["type"] == "GAP":
            e.update(kind="host", title=r["detail"] or "Loading gap")
        elif r["type"] == "STATION_ID":
            e.update(kind="id", title="Station ID")
        else:
            e.update(kind="session", title="broadcast started")
        out.append(e)
    for e in out:
        if e.get('parts'):
            e['title'] = edition_title(part['source'] for part in e['parts'])
        e.pop("_at", None)
        e.pop("_topics", None)
    return out
