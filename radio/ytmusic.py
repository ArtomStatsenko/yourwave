"""YouTube Music playlists and temporary song files, using the installed yt-dlp."""
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, store

ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
DEFAULT_PLAYLIST = "https://music.youtube.com/playlist?list=LM"


class Unavailable(ValueError):
    """This song cannot be played; other songs may still work."""


def directory():
    return config.DATA_DIR / "ytmusic"


def library():
    try:
        data = json.loads((directory() / "library.json").read_text())
        if isinstance(data, dict) and isinstance(data.get("playlist"), str):
            data["tracks"] = parse_tracks({"entries": data.get("tracks")})
            return data
    except (OSError, ValueError):
        pass
    return {"tracks": [], "playlist": DEFAULT_PLAYLIST}


def status():
    data = library()
    return {"connected": (directory() / "cookies.txt").exists() and bool(data["tracks"]),
            "tracks": len(data["tracks"]), "playlist": data["playlist"]}


def playlist_url(value):
    p = urlparse(value)
    ids = parse_qs(p.query).get("list", [])
    if p.scheme != "https" or p.netloc not in ("music.youtube.com", "www.youtube.com", "youtube.com") \
            or p.path != "/playlist" or len(ids) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", ids[0]):
        raise ValueError("Enter a YouTube Music playlist URL")
    return "https://music.youtube.com/playlist?list=" + ids[0]


def cookie_jar(value):
    value = value.strip().removeprefix("Cookie:").removeprefix("cookie:").strip()
    if not value or len(value) > 60000 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("Paste only the Cookie request header from YouTube Music")
    pairs = {}
    for part in value.split(";"):
        name, sep, data = part.strip().partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]+", name) or not data:
            raise ValueError("Invalid Cookie request header")
        pairs[name] = data
    if not {"SAPISID", "__Secure-3PAPISID"} & pairs.keys():
        raise ValueError("Use a Cookie header from a signed-in YouTube Music request")
    return "# Netscape HTTP Cookie File\n" + "".join(
        f".youtube.com\tTRUE\t/\tTRUE\t0\t{k}\t{v}\n" for k, v in pairs.items())


def run(args, cookies, timeout=60):
    try:
        # yt-dlp rewrites its jar on exit. Per-call copies keep parallel prefetch/refresh from racing.
        with tempfile.TemporaryDirectory(dir=directory()) as tmp:
            jar = Path(tmp) / "cookies.txt"
            shutil.copyfile(cookies, jar)
            jar.chmod(0o600)
            p = subprocess.run(["yt-dlp", "--ignore-config", "--no-warnings", "--cookies", str(jar),
                                "--socket-timeout", "10", "--retries", "0", "--extractor-retries", "0", *args],
                               capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("YouTube Music did not respond; check yt-dlp and try again") from None
    if p.returncode:
        # Never put yt-dlp stderr (cookies, signed URLs or account details) into the UI/log.
        if any(reason in p.stderr.lower() for reason in (
                "video unavailable", "private video", "video has been removed", "not available in your country",
                "members-only", "requested format is not available")):
            raise Unavailable("This YouTube song is unavailable")
        reason = "YouTube is rate-limiting requests; try later" if "429" in p.stderr else \
                 "YouTube Music could not load; check the playlist and refresh cookies"
        raise ValueError(reason)
    return p.stdout


def parse_tracks(data):
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError("YouTube Music returned an invalid playlist")
    tracks, seen = [], set()
    for entry in data.get("entries", []):
        if not isinstance(entry, dict):
            continue
        vid = entry.get("id", "")
        title = entry.get("title")
        if not isinstance(vid, str) or not ID.fullmatch(vid) or vid in seen or not isinstance(title, str) \
                or not title.strip() or title in ("[Private video]", "[Deleted video]") or entry.get("is_live"):
            continue
        duration = entry.get("duration")
        duration = float(duration) if isinstance(duration, (int, float)) else 0.0
        if not math.isfinite(duration) or duration < 0 or duration > 7200:
            continue  # live streams/long mixes cannot supply the station's normal song boundaries
        seen.add(vid)
        artist = entry.get("artist") or ""
        tracks.append({"id": vid, "title": " ".join(title.split()),
                       "artist": " ".join(artist.split()) if isinstance(artist, str) else "",
                       "duration": duration})
    if not tracks:
        raise ValueError("No playable songs in this playlist; choose another playlist")
    return tracks


def connect(cookie, playlist=DEFAULT_PLAYLIST):
    url = playlist_url(playlist)
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    saved = root / "cookies.txt"
    if not cookie and not saved.exists():
        raise ValueError("Connect your YouTube Music session first")
    # Stage credentials: a failed connection must not replace a working session/library.
    fd, name = tempfile.mkstemp(dir=root)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(cookie_jar(cookie) if cookie else saved.read_text())
        # shortcut: import at most 500 songs; add pagination if a larger library is needed.
        data = json.loads(run(["--flat-playlist", "--dump-single-json", "--playlist-end", "500", url], staged))
        tracks = parse_tracks(data)
        manifest = root / "library.tmp"
        manifest.write_text(json.dumps({"playlist": url, "tracks": tracks}, ensure_ascii=False))
        manifest.chmod(0o600)
        staged.replace(saved)
        manifest.replace(root / "library.json")
    except json.JSONDecodeError:
        raise ValueError("YouTube Music returned an invalid playlist") from None
    finally:
        staged.unlink(missing_ok=True)
    return status()


def write_m3u(db, out):
    tracks = library()["tracks"]
    if not tracks:
        raise ValueError("Connect YouTube Music before enabling it")
    lines = ["#EXTM3U"]
    for t in tracks:
        ref = "ytmusic:" + t["id"]
        if not ID.fullmatch(t["id"]):
            raise ValueError("Invalid YouTube Music library; reconnect")
        db.execute("INSERT INTO tracks(path, sig, ok, artist, title) VALUES(?, 'ytmusic', 1, ?, ?) "
                   "ON CONFLICT(path) DO UPDATE SET "
                   "artist=coalesce(nullif(excluded.artist, ''), tracks.artist), "
                   "title=CASE WHEN excluded.artist!='' OR coalesce(tracks.artist,'')='' "
                   "THEN excluded.title ELSE tracks.title END",
                   (ref, t["artist"], t["title"]))
        lines.append(f'annotate:kind="music",ref="{ref}",liq_amplify="0 dB":{ref}')
    out.write_text("\n".join(lines) + "\n")
    return {"ytmusic:" + t["id"]: None for t in tracks}


def duration(ref):
    return next((t["duration"] for t in library()["tracks"] if ref == "ytmusic:" + t["id"]), 0)


def save_metadata(vid, data):
    if not isinstance(data, dict) or not ID.fullmatch(vid):
        return
    artist = data.get("artist")
    if not isinstance(artist, str) or not artist.strip():
        artists = data.get("artists")
        artist = ", ".join(a for a in artists if isinstance(a, str)) if isinstance(artists, list) else ""
    title = data.get("track") or data.get("title")
    if not artist.strip() or not isinstance(title, str) or not title.strip():
        return  # a video's uploader is not necessarily the performing artist
    db = store.connect()
    try:
        db.execute("UPDATE tracks SET artist=?, title=? WHERE path=?",
                   (" ".join(artist.split()), " ".join(title.split()), "ytmusic:" + vid))
    finally:
        db.close()


def resolve(vid):
    if not ID.fullmatch(vid) or not any(t["id"] == vid for t in library()["tracks"]):
        raise ValueError("Unknown YouTube Music song")
    root = directory() / "audio"
    root.mkdir(mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=root, suffix=".webm")
    os.close(fd)
    out = Path(name)
    try:
        # Chunked downloading avoids YouTube throttling Liquidsoap's full-file HTTP request.
        metadata = run(["--no-playlist", "-f", "bestaudio", "--http-chunk-size", "1M", "--no-part",
                        "--force-overwrites", "--no-progress", "--print", "after_move:%(.{artist,artists,track,title})j",
                        "-o", str(out), "https://www.youtube.com/watch?v=" + vid], directory() / "cookies.txt", timeout=20)
        if not out.stat().st_size:
            raise ValueError("YouTube did not return audio")
        try:
            save_metadata(vid, json.loads(metadata or "{}"))
        except (ValueError, TypeError, sqlite3.Error):
            pass  # missing credits must not discard an otherwise playable song
        return str(out.resolve())  # Liquidsoap owns and removes the file after the track.
    except Exception:
        out.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        print(resolve(sys.argv[1]))
    except Unavailable:
        print("!unavailable")
    except (ValueError, IndexError):
        print("!offline")  # report only a safe category to Liquidsoap
