"""Source inbox, verbatim source segmentation and package bookkeeping; editorial speech is stored separately."""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, plugins
from .store import iso, now

SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
SEP = "⁂"    # a paragraph of its own = separator sound between meaning blocks; never spoken
BREAK = "⁑"  # a paragraph of its own = new voice segment after a short pause (topic -> text); never spoken
_HDR = "\x01"


def parse_time(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None  # naive time is ambiguous -> unusable


def parse_inbox_file(path: Path) -> dict:
    """Inbox contract: .md with `key: value` front matter between --- lines, or .json with the same keys.
    Keys: topic, published_at (ISO with timezone), body (json only), source, url, id."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        meta = json.loads(text)
        if not isinstance(meta, dict):
            raise ValueError("json root must be an object")
        body = str(meta.get("body", ""))
    else:
        meta, body = {}, text
        m = re.match(r"\A---\n(.*?)\n---\n?(.*)\Z", text, re.S)
        if m:
            for line in m.group(1).splitlines():
                k, sep, v = line.partition(":")
                if sep:
                    meta[k.strip()] = v.strip().strip('"')
            body = m.group(2)
    body = body.strip()
    return {
        "id": "digest:" + str(meta.get("id") or hashlib.sha256(body.encode()).hexdigest()[:16]),
        "topic": str(meta.get("topic") or "Сводка"),
        "source": str(meta.get("source") or "inbox"),
        "url": str(meta.get("url") or ""),
        "published_at": parse_time(meta.get("published_at")),
        "body": body,
    }


def speakable(md: str, heavy_chars: int = 350) -> str:
    """Strips Markdown syntax only; words stay exactly as written. URLs are not read aloud (kept in the item).
    Marks separator points (SEP): before each header after the first block, and around heavy paragraphs."""
    s = re.sub(r"```.*?```", "", md, flags=re.S)
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<?https?://\S+>?", "", s)
    s = re.sub(r"^\s{0,3}#{1,6}\s*(.+)$", lambda m: f"\n{_HDR}{m.group(1)}\n", s, flags=re.M)
    s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", s, flags=re.M)
    s = re.sub(r"[*_`~]{1,3}", "", s)
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    s = re.sub(r"[ \t]+", " ", s)
    lines = [l.strip() for l in s.splitlines()]
    # Each list item / header becomes its own paragraph so splitting happens on item boundaries.
    out, para = [], []
    for l in lines:
        if not l:
            if para:
                out.append(" ".join(para)); para = []
        elif re.search(r"[.!?…:;]$", l) or len(l) < 80:
            para.append(l)
            out.append(" ".join(para)); para = []
        else:
            para.append(l)
    if para:
        out.append(" ".join(para))
    blocks, prev_header = [], False
    for p in out:
        if not p:
            continue
        header, p = p.startswith(_HDR), p.removeprefix(_HDR).strip()
        if p != SEP and not re.search(r"[.!?…:;]$", p):
            p += "."  # a header or list item without end punctuation: TTS pauses instead of running it on
        heavy = len(p) > heavy_chars
        if header or (heavy and not prev_header):  # a header stays glued to its own paragraph
            blocks.append(SEP)
        blocks.append(p)
        if heavy:
            blocks.append(SEP)
        prev_header = header
    return "\n\n".join(_tidy_seps(blocks))


def _tidy_seps(blocks: list[str]) -> list[str]:
    """No separator at the edges, never two in a row."""
    out = []
    for b in blocks:
        if b == SEP and (not out or out[-1] == SEP):
            continue
        out.append(b)
    while out and out[-1] == SEP:
        out.pop()
    return out


def split_parts(text: str, max_chars: int) -> list[str]:
    """Splits on paragraph, then sentence boundaries; never mid-sentence. Joining the units gives back the text."""
    units = []
    for p in text.split("\n\n"):
        units += [p] if len(p) <= max_chars else SENTENCE_END.split(p)
    parts, cur = [], ""
    for u in units:
        if cur and len(cur) + 2 + len(u) > max_chars:
            parts.append(cur)
            cur = u
        else:
            cur = f"{cur}\n\n{u}" if cur else u
    if cur:
        parts.append(cur)
    return ["\n\n".join(_tidy_seps(p.split("\n\n"))) for p in parts]


def scripts_for(item: dict, max_chars: int, heavy_chars: int = 350) -> list[str]:
    parts = split_parts(speakable(item["body"], heavy_chars), max_chars)
    topic = item["topic"].rstrip(".")
    if not topic or not item.get("speak_topic", True):  # e.g. a section intro jingle already says what this is
        return parts
    # Programmatic topic announcement is allowed by the spec; the digest text itself is untouched.
    # The topic is its own voice segment: same text -> same TTS cache entry, so it is generated once.
    return [(f"{topic}.\n\n{BREAK}\n\n" if i == 0 else f"Продолжаем: {topic}.\n\n{BREAK}\n\n") + p
            for i, p in enumerate(parts)]


def voice_segments(script: str) -> list[tuple[str, bool]]:
    """Script -> (text, separator_sound_before) chunks, voiced separately."""
    tokens = re.split(f"\n\n({SEP}|{BREAK})\n\n", script)
    out, sound = [], False
    for i, tok in enumerate(tokens):
        if i % 2:
            sound = sound or tok == SEP
        elif tok.strip():
            out.append((tok.strip(), sound and bool(out)))
            sound = False
    return out


def is_fresh(published_at: datetime | None, max_age_hours: float, at: datetime | None = None) -> bool:
    at = at or now()
    return published_at is not None and at - timedelta(hours=max_age_hours) <= published_at <= at + timedelta(minutes=10)


def scan_inbox(db, cfg) -> list[str]:
    """Registers new inbox files as items with PENDING packages. Returns ids of new items."""
    new = []
    for path in sorted((config.DATA_DIR / "inbox").glob("*")):
        if path.suffix not in (".md", ".json") or db.execute("SELECT 1 FROM items WHERE file=?", (str(path),)).fetchone():
            continue
        try:
            item = parse_inbox_file(path)
        except (ValueError, UnicodeDecodeError) as e:
            db.execute("INSERT OR IGNORE INTO items(id, kind, file, state, created_at) VALUES(?,?,?,?,?)",
                       (f"bad:{path.name}", "ready_digest", str(path), f"INVALID: {e}", iso(now())))
            continue
        if db.execute("SELECT 1 FROM items WHERE id=?", (item["id"],)).fetchone():
            db.execute("INSERT OR IGNORE INTO items(id, kind, file, state, created_at) VALUES(?,?,?,?,?)",
                       (f"dup:{path.name}", "ready_digest", str(path), "DUPLICATE", iso(now())))
            continue
        add_item(db, cfg, item, kind="ready_digest", file=str(path))
        new.append(item["id"])
    return new


def add_item(db, cfg, item: dict, kind: str, file: str | None = None) -> str:
    """Stores an item; if it is fresh and has text, splits it into PENDING packages. Returns its state."""
    max_chars = int(cfg["broadcast"]["max_package_speech_sec"] * cfg["voice"]["chars_per_sec"])
    if not item["body"]:
        state = "EMPTY"
    elif item["published_at"] is None:
        state = "NEEDS_METADATA"
    elif not is_fresh(item["published_at"], cfg["sources"]["max_age_hours"]):
        state = "EXPIRED"
    else:
        state = "NEW"
    db.execute("SAVEPOINT add_item")
    try:
        db.execute("INSERT INTO items(id, kind, source, topic, url, published_at, file, state, created_at, origin_json) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (item["id"], kind, item["source"], item["topic"], item["url"],
                    iso(item["published_at"]) if item["published_at"] else None, file, state, iso(now()),
                    json.dumps(item["origin"], ensure_ascii=False) if item.get("origin") else None))
        if state != "NEW":
            return state
        scripts = scripts_for(item, max_chars, cfg["broadcast"]["separator_heavy_chars"])
        for i, script in enumerate(scripts):
            pid = f"{item['id']}#{i + 1}"
            sp = config.DATA_DIR / "cache/scripts" / (hashlib.sha256(pid.encode()).hexdigest()[:16] + ".txt")
            sp.write_text(script, encoding="utf-8")
            prepared = script if item.get("text_prepared") or kind not in ("raw_post", "ready_digest") else None
            db.execute("INSERT INTO packages(id, item_id, part, parts, script_path, est_speech_sec, status,prepared_text) "
                       "VALUES(?,?,?,?,?,?,'PENDING',?)",
                       (pid, item["id"], i + 1, len(scripts), str(sp), len(script) / cfg["voice"]["chars_per_sec"], prepared))
        return state
    except Exception:
        db.execute("ROLLBACK TO add_item")
        raise
    finally:
        db.execute("RELEASE add_item")


def expire_stale(db, cfg) -> int:
    """TTL is re-checked continuously: anything not yet on air past its deadline is dropped."""
    cutoff = iso(now() - timedelta(hours=cfg["sources"]["max_age_hours"]))
    protected = set()
    if plugins.on(cfg, 'air_direction'):
        protected = {r[0] for r in db.execute("SELECT p.item_id FROM packages p JOIN air_plan a ON a.id=p.id "
                                             "WHERE a.not_before IS NOT NULL AND a.skipped=0")}
    stale = [r["id"] for r in db.execute("SELECT id FROM items WHERE state='NEW' AND published_at<? AND COALESCE(source,'')!='direction'", (cutoff,))
             if r['id'] not in protected]
    for item_id in stale:
        db.execute("UPDATE items SET state='EXPIRED' WHERE id=?", (item_id,))
        db.execute("UPDATE packages SET status='EXPIRED_UNPLAYED' WHERE item_id=? AND status IN ('PENDING','READY')",
                   (item_id,))
    return len(stale)


# Round-robin: the source that aired least recently (or never) goes first, then oldest item, then part order.
# A plain rotation, not a recommender.
ROTATION = """(SELECT MAX(q.played_at) FROM packages q JOIN items j ON j.id=q.item_id
               WHERE j.source=i.source AND q.status='PLAYED') IS NOT NULL,
              (SELECT MAX(q.played_at) FROM packages q JOIN items j ON j.id=q.item_id
               WHERE j.source=i.source AND q.status='PLAYED'), i.published_at, p.part"""


def next_to_air(db, sources_off: list[str] = ()):
    """A READY package whose earlier parts are all finished (played/skipped/failed); none from switched-off sources."""
    return (ready_to_air(db, sources_off) or [None])[0]


def ready_to_air(db, sources_off: list[str] = ()) -> list:
    """All packages next_to_air could pick, in airing order (at most one part per item)."""
    off = ",".join("?" * len(sources_off))
    return db.execute(f"""SELECT p.* FROM packages p JOIN items i ON i.id=p.item_id
                         WHERE p.status='READY' AND i.state='NEW' {f"AND i.source NOT IN ({off})" if off else ""}
                         AND (i.kind NOT IN ('raw_post','ready_digest') OR p.prepared_text IS NOT NULL)
                         AND NOT EXISTS(
                           SELECT 1 FROM packages q WHERE q.item_id=p.item_id AND q.part<p.part
                           AND q.status IN ('PENDING','READY','QUEUED','PLAYING'))
                         ORDER BY {ROTATION}""", tuple(sources_off)).fetchall()


def finish_item_if_done(db, item_id):
    if not db.execute("SELECT 1 FROM packages WHERE item_id=? AND status IN ('PENDING','READY','QUEUED','PLAYING')",
                      (item_id,)).fetchone():
        db.execute("UPDATE items SET state='DONE' WHERE id=? AND state='NEW'", (item_id,))
