import os
import re
import time
from pathlib import Path

from . import audio, lyrics

EXTS = {".mp3", ".m4a", ".flac", ".wav"}
TALK_UP_SEC = 1.5  # the host starts this long before a song's audible end, over its fade
MAX_TAIL_SEC = 10.0  # radio.liq gives a news break a 12 s start window; longer quiet endings are cut short
VOCAL_MARGIN_SEC = 2.5  # the host is done this long before the first sung line
MAX_INTRO_TALK_SEC = 12.0  # longest set opener over an intro (an instrumental track has no vocal limit)
OUTRO_MARGIN_SEC = 1.5  # the host starts this long after the last sung line has ended
MAX_OUTRO_TALK_SEC = 10.0  # ...and at most this long before the end of the file
# Songs LRCLIB does not know are heard by the local Whisper (lyrics="heard"). Its word times are off by up to ~4 s
# on music (checked against LRCLIB on 5 songs), so the host keeps a wider distance from the singing.
HEARD_VOCAL_MARGIN_SEC = 4.0
HEARD_OUTRO_MARGIN_SEC = 2.5


def intro_room(row) -> float | None:
    """Seconds of a song's intro the host may talk over: up to VOCAL_MARGIN_SEC before its first sung line;
    None if unknown. Also the song's start window in the playlist, so the overlap really happens."""
    if row is None or row["lyrics"] not in ("synced", "heard", "instrumental"):
        return None
    margin = HEARD_VOCAL_MARGIN_SEC if row["lyrics"] == "heard" else VOCAL_MARGIN_SEC
    room = MAX_INTRO_TALK_SEC if row["lyrics"] == "instrumental" else min(MAX_INTRO_TALK_SEC, row["vocal_sec"] - margin)
    return room if room >= 2.0 else None


def scan(root: Path) -> list[Path]:
    files = []
    for d, _, names in os.walk(root, followlinks=True):
        # a quote breaks the playlist line's ref="..." (the song then has no length for the daemon): left out
        audio_names = [n for n in names if Path(n).suffix.lower() in EXTS and not n.startswith(".") and '"' not in n]
        stems = {Path(n).stem for n in audio_names}
        # "Song.enhanced-....wav" is a copy of "Song.mp3": keep only the original.
        files += [Path(d) / n for n in audio_names if not (".enhanced" in n and n.split(".enhanced")[0] in stems)]
    return sorted(files)


def _sig(p: Path) -> str:
    st = p.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def song_tags(row, xfade: float, outro_talk: bool) -> tuple[str, float | None]:
    """Start/end window annotations of a measured song (each starting with a comma), and its intro room."""
    tags = ""
    if row["tail_sec"] is not None:
        back = min(row["tail_sec"], MAX_TAIL_SEC) + TALK_UP_SEC  # the break starts this long before the file end
        if outro_talk and row["lyrics"] in ("synced", "heard") and row["outro_sec"] is not None:
            margin = HEARD_OUTRO_MARGIN_SEC if row["lyrics"] == "heard" else OUTRO_MARGIN_SEC
            over = min(row["outro_sec"] - margin, MAX_OUTRO_TALK_SEC)
            back = over if over >= back + 2.0 else back
        window = max(xfade + 1, back)
        tags = (f',liq_cross_end_duration="{window:.2f}"' if window > xfade + 1 else "") + \
               f',liq_talk_up="{window - back:.2f}"'
    room = intro_room(row)
    if room and room > xfade + 1:
        tags += f',liq_cross_start_duration="{room + 1:.2f}"'
    return tags, room


def unaired_new(db, files: list[Path], days: float) -> list[Path]:
    """Songs added to the library in the last `days` days that have not been on air yet, newest first."""
    aired = {r[0] for r in db.execute("SELECT DISTINCT ref FROM events WHERE type='MUSIC_STARTED'")}
    since = time.time() - days * 86400
    found = []
    for f in files:
        if str(f) not in aired:
            st = f.stat()
            added = getattr(st, "st_birthtime", st.st_mtime)
            if added >= since:
                found.append((added, f))
    return [f for _, f in sorted(found, reverse=True)]


def write_m3u(db, files: list[Path], out: Path, target_lufs: float, max_boost_db: float, xfade: float = 3.0,
              outro_talk: bool = False) -> dict:
    """Playlist for Liquidsoap with a per-track gain from cached LUFS. Known-broken files are left out.
    A song with a long quiet ending gets a longer end window (liq_cross_end_duration), and liq_talk_up says
    where in that window a news break starts: TALK_UP_SEC before the audible end, or (outro_talk, synced
    lyrics) OUTRO_MARGIN_SEC after its last sung line, up to MAX_OUTRO_TALK_SEC before the file's end.
    radio.liq keeps song-to-song transitions as they were. shortcut: outro_talk is read when the playlist is
    written, so the plugin switch applies from the next start.
    A song with a known intro (intro_room) gets that as its start window (liq_cross_start_duration).
    Shuffling is Liquidsoap's (mode=randomize). Returns {path: intro room in this playlist} for every track
    written (None: no talking over it); the daemon must not plan a longer overlap than the playlist allows."""
    known = {r["path"]: r for r in db.execute("SELECT * FROM tracks")}
    lines, written = [], {}
    for f in files:
        row = known.get(str(f))
        fresh = row is not None and row["sig"] == _sig(f)
        if fresh and not row["ok"]:
            continue
        gain = min(max_boost_db, target_lufs - row["lufs"]) if fresh and row["lufs"] is not None else 0.0
        path = str(f).replace('"', "")
        cross, room = song_tags(row, xfade, outro_talk) if fresh else ("", None)
        written[str(f)] = room
        lines.append(f'annotate:kind="music",ref="{path}",liq_amplify="{gain:+.2f} dB"{cross}:{f}')
    out.write_text("#EXTM3U\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return written


def measure_missing(db, files: list[Path], stop) -> None:
    """Runs in a background thread: loudness and quiet ending of new tracks, then when
    their vocals start. shortcut: values found now apply from the next `radio play`; fine for a personal
    library that rarely changes."""
    known = {r["path"]: r for r in db.execute("SELECT path, sig, lufs, tail_sec, ok, artist FROM tracks")}
    for f in files:
        if stop.is_set():
            return
        row = known.get(str(f))
        fresh = row is not None and row["sig"] == _sig(f)
        if fresh and (row["tail_sec"] is not None or not row["ok"]) and row["artist"] is not None:
            continue
        try:
            artist, title = audio.tags(f)
            if fresh and (row["tail_sec"] is not None or not row["ok"]):
                db.execute("UPDATE tracks SET artist=?, title=? WHERE path=?", (artist, title, str(f)))
                continue
            value = row["lufs"] if fresh else audio.lufs(f)
            tail = audio.duration(f) - audio.active_span(f)[1] if value is not None else None
            db.execute("INSERT INTO tracks(path, sig, lufs, ok, tail_sec, artist, title) VALUES(?,?,?,?,?,?,?) "
                       "ON CONFLICT(path) DO UPDATE SET sig=excluded.sig, lufs=excluded.lufs, ok=excluded.ok, "
                       "tail_sec=excluded.tail_sec, artist=excluded.artist, title=excluded.title, "
                       "lyrics=CASE WHEN tracks.sig=excluded.sig THEN tracks.lyrics END",
                       (str(f), _sig(f), value, int(value is not None), tail, artist, title))
        except Exception as e:  # one bad file must not stop the measuring of the rest of the library
            print(f"measure {f.name}: {type(e).__name__}: {e}", flush=True)
    find_vocals(db, files, stop)


def song(db, path) -> tuple[str, str] | None:
    """(artist, title) from the file's tags (several artists as "A, B"); None for an untagged file, which is
    then not named on air."""
    r = db.execute("SELECT artist, title FROM tracks WHERE path=?", (str(path),)).fetchone()
    if not r or not r["artist"] or not r["title"]:
        return None
    return ", ".join(a.strip() for a in re.split(r"[/;]", r["artist"]) if a.strip()), r["title"]


def label(db, path) -> str:
    """How the UI shows a song: "Artist - Title" from the tags, else the file name."""
    s = song(db, path)
    if not s and str(path).startswith("ytmusic:"):
        row = db.execute("SELECT title FROM tracks WHERE path=?", (str(path),)).fetchone()
        if row and row["title"]:
            return row["title"]
    return f"{s[0]} - {s[1]}" if s else Path(path).stem


def find_vocals(db, files: list[Path], stop) -> None:
    for f in files:
        try:
            sig = _sig(f)
        except FileNotFoundError:
            continue  # renamed or deleted while the radio runs
        row = db.execute("SELECT ok, lyrics, outro_sec FROM tracks WHERE path=? AND sig=?", (str(f), sig)).fetchone()
        s = song(db, f)
        done = row and row["lyrics"] is not None and not (row["lyrics"] == "synced" and row["outro_sec"] is None)
        if stop.is_set() or not row or not row["ok"] or done or not s:
            continue
        dur = audio.duration(f)
        try:
            status, vocal, end = lyrics.lookup(*s, dur)
        except Exception:
            continue  # offline or LRCLIB down: tried again next start
        outro = max(0.0, dur - end) if end is not None and end <= dur + 2 else None
        db.execute("UPDATE tracks SET lyrics=?, vocal_sec=?, outro_sec=? WHERE path=?", (status, vocal, outro, str(f)))
        time.sleep(0.5)  # polite to a free service


def to_hear(db, files: list[Path]) -> Path | None:
    """The next song for the local Whisper: one LRCLIB did not know, the newest file first (new tracks get their
    timing soonest)."""
    unknown = {r[0] for r in db.execute("SELECT path FROM tracks WHERE ok=1 AND lyrics='none'")}
    def born(f):  # one stat: the age, and whether the file is still there (renamed or deleted while the radio runs)
        try:
            s = f.stat()
        except FileNotFoundError:
            return None
        return getattr(s, "st_birthtime", s.st_mtime)
    todo = [(t, f) for f in files if str(f) in unknown and (t := born(f)) is not None]
    return max(todo)[1] if todo else None


def hear(db, f: Path) -> None:
    """Where the vocals of one song start and end, by the local Whisper; "unheard" when it found no reliable words."""
    from . import tts
    span = tts.vocal_span(f)
    dur = audio.duration(f)
    if span and 0 <= span[0] < span[1] <= dur + 1:
        db.execute("UPDATE tracks SET lyrics='heard', vocal_sec=?, outro_sec=? WHERE path=?",
                   (span[0], max(0.0, dur - span[1]), str(f)))
    else:
        db.execute("UPDATE tracks SET lyrics='unheard' WHERE path=?", (str(f),))

