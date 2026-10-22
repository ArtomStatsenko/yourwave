import sqlite3
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY, kind TEXT, source TEXT, topic TEXT, url TEXT,
  published_at TEXT, file TEXT, state TEXT, created_at TEXT, origin_json TEXT);
CREATE TABLE IF NOT EXISTS packages(
  id TEXT PRIMARY KEY, item_id TEXT, part INTEGER, parts INTEGER,
  script_path TEXT, audio_path TEXT, est_speech_sec REAL, speech_sec REAL, duration_sec REAL,
  status TEXT, attempts INTEGER DEFAULT 0, error TEXT, played_at TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, session_id TEXT, at TEXT, type TEXT, ref TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS tracks(path TEXT PRIMARY KEY, sig TEXT, lufs REAL, ok INTEGER);
CREATE TABLE IF NOT EXISTS reminders(key TEXT PRIMARY KEY, fired_at TEXT);
CREATE TABLE IF NOT EXISTS air_plan(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
  not_before TEXT, rank INTEGER, skipped INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'PENDING',
  rid TEXT, error TEXT);
CREATE INDEX IF NOT EXISTS packages_status ON packages(status, played_at);
CREATE INDEX IF NOT EXISTS items_state ON items(state, published_at);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(config.DATA_DIR / "db/radio.sqlite3", isolation_level=None, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    # Migration for databases created before origin_json (source details shown in the UI timeline).
    if "origin_json" not in {r[1] for r in db.execute("PRAGMA table_info(items)")}:
        db.execute("ALTER TABLE items ADD COLUMN origin_json TEXT")
    if "tail_sec" not in {r[1] for r in db.execute("PRAGMA table_info(tracks)")}:
        db.execute("ALTER TABLE tracks ADD COLUMN tail_sec REAL")  # quiet ending: news starts over it
    if "lyrics" not in {r[1] for r in db.execute("PRAGMA table_info(tracks)")}:
        # synced | instrumental | none (NULL = not looked up yet); vocal_sec = first sung line (lyrics.py)
        db.execute("ALTER TABLE tracks ADD COLUMN lyrics TEXT")
        db.execute("ALTER TABLE tracks ADD COLUMN vocal_sec REAL")
    if "artist" not in {r[1] for r in db.execute("PRAGMA table_info(tracks)")}:
        db.execute("ALTER TABLE tracks ADD COLUMN artist TEXT")  # from the file's tags; NULL = not read yet
        db.execute("ALTER TABLE tracks ADD COLUMN title TEXT")
    if "outro_sec" not in {r[1] for r in db.execute("PRAGMA table_info(tracks)")}:
        db.execute("ALTER TABLE tracks ADD COLUMN outro_sec REAL")  # file end minus the end of the last sung line
    if "talkover_sec" not in {r[1] for r in db.execute("PRAGMA table_info(packages)")}:
        db.execute("ALTER TABLE packages ADD COLUMN talkover_sec REAL DEFAULT 0")
    return db


def log_event(db, session_id, type_, ref="", detail=""):
    db.execute("INSERT INTO events(session_id, at, type, ref, detail) VALUES(?,?,?,?,?)",
               (session_id, iso(now()), type_, ref, detail))


def spoken_last_hour(db) -> float:
    since = iso(now() - timedelta(hours=1))
    played = db.execute("SELECT COALESCE(SUM(speech_sec),0) FROM packages WHERE status='PLAYED' AND played_at>=?",
                        (since,)).fetchone()[0]
    # Queued/playing audio will be spoken soon, so it counts against the window too.
    pending = db.execute("SELECT COALESCE(SUM(speech_sec),0) FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone()[0]
    return played + pending
