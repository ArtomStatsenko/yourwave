"""YouTube Music playlists and temporary song files, using the installed yt-dlp."""
import base64
import hashlib
import json
import math
import random
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import urllib.request
from urllib.parse import parse_qs, urlparse

from . import config, store

ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
DEFAULT_PLAYLIST = "https://music.youtube.com/playlist?list=LM"


class Unavailable(ValueError):
    """This song cannot be played; other songs may still work."""


def directory():
    return config.DATA_DIR / "ytmusic"


UNAVAILABLE_RECHECK_SEC = 24 * 3600  # a song YouTube would not serve stays out of sets this long, then is tried again


def mark_unavailable(vid: str) -> None:
    """Remembers that YouTube refused this song (removed, private, not in this country), for the sets."""
    path = directory() / "unavailable.json"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    with config.file_lock(path):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        data[vid] = time.time()
        path.write_text(json.dumps(data))


def unavailable() -> set[str]:
    """The songs YouTube refused within UNAVAILABLE_RECHECK_SEC, as ytmusic: refs."""
    try:
        data = json.loads((directory() / "unavailable.json").read_text())
    except (OSError, ValueError):
        return set()
    return {"ytmusic:" + vid for vid, at in data.items() if isinstance(at, (int, float)) and time.time() - at < UNAVAILABLE_RECHECK_SEC}


def library():
    """The connected playlist's songs, or a station's while one is on (station(): radio from a song, a mood)."""
    data = {"tracks": [], "playlist": DEFAULT_PLAYLIST}
    try:
        saved = json.loads((directory() / "library.json").read_text())
        if isinstance(saved, dict) and isinstance(saved.get("playlist"), str):
            data = {**saved, "tracks": parse_tracks({"entries": saved.get("tracks")})}
    except (OSError, ValueError):
        pass
    if on := station():
        data = {**data, "tracks": on["tracks"], "station": {k: on[k] for k in ("kind", "value", "title")}}
    return data


def status():
    data = library()
    return {"connected": (directory() / "cookies.txt").exists() and bool(data["tracks"]),
            "tracks": len(data["tracks"]), "playlist": data["playlist"], "station": data.get("station")}


# ---- stations: YouTube Music's radio from a song, or a mood's playlists, in place of the connected playlist ----
INNERTUBE = "https://music.youtube.com/youtubei/v1/browse?prettyPrint=false"
CLIENT = {"clientName": "WEB_REMIX", "clientVersion": "1.20241111.01.00", "hl": "en", "gl": "US"}
MOOD_PLAYLISTS = 4  # a mood station mixes this many of the mood's playlists
STATION_TRACKS = 200


def station():
    try:
        data = json.loads((directory() / "station.json").read_text())
        return {**data, "tracks": parse_tracks({"entries": data["tracks"]})} if data.get("kind") in ("song", "mood", "link") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _browse(body: dict) -> dict:
    """YouTube Music's own (undocumented) browse API, as its web page asks it; no account needed."""
    req = urllib.request.Request(INNERTUBE, json.dumps({"context": {"client": CLIENT}, **body}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
                                  "Origin": "https://music.youtube.com"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (OSError, ValueError):
        raise ValueError("YouTube Music did not answer; try again") from None


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            yield node[key]
        for value in node.values():
            yield from _find(value, key)
    elif isinstance(node, list):
        for value in node:
            yield from _find(value, key)


def like(vid: str, on: bool = True) -> None:
    """Likes a YouTube Music song (or takes the like back) with the connected session, as its web page does: the
    session cookies and a SAPISIDHASH from the SAPISID cookie. Songs of another site (a link station) are skipped."""
    track = next((t for t in library()["tracks"] if t["id"] == vid), None)
    if not ID.fullmatch(vid) or track and track.get("url"):
        return
    cookies = {}
    try:
        for line in (directory() / "cookies.txt").read_text().splitlines():
            parts = line.split("\t")
            if len(parts) >= 7 and parts[0].removeprefix("#HttpOnly_").endswith("youtube.com"):
                cookies[parts[5]] = parts[6]
    except OSError:
        raise ValueError("Connect YouTube Music first") from None
    sapisid = cookies.get("__Secure-3PAPISID") or cookies.get("SAPISID")
    if not sapisid:
        raise ValueError("The YouTube Music session has expired; reconnect it in Sources")
    origin, now = "https://music.youtube.com", str(int(time.time()))
    digest = hashlib.sha1(f"{now} {sapisid} {origin}".encode()).hexdigest()
    req = urllib.request.Request(f"{origin}/youtubei/v1/like/{'like' if on else 'removelike'}?prettyPrint=false",
                                 json.dumps({"context": {"client": CLIENT}, "target": {"videoId": vid}}).encode(),
                                 {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Origin": origin,
                                  "X-Origin": origin, "X-Goog-AuthUser": "0", "Authorization": f"SAPISIDHASH {now}_{digest}",
                                  "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())})
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except OSError:
        raise ValueError("YouTube Music did not take the like; try again or reconnect it in Sources") from None


def moods() -> list[dict]:
    """YouTube Music's moods and genres: [{title, params}], kept a day."""
    cache = directory() / "moods.json"
    try:
        if time.time() - cache.stat().st_mtime < 24 * 3600:
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    found = []
    for button in _find(_browse({"browseId": "FEmusic_moods_and_genres"}), "musicNavigationButtonRenderer"):
        try:
            found.append({"title": button["buttonText"]["runs"][0]["text"],
                          "params": button["clickCommand"]["browseEndpoint"]["params"]})
        except (KeyError, IndexError, TypeError):
            continue
    if not found:
        raise ValueError("YouTube Music returned no moods; try again later")
    directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    cache.write_text(json.dumps(found, ensure_ascii=False))
    return found


def _mood_playlists(params: str) -> list[str]:
    ids = []
    for item in _find(_browse({"browseId": "FEmusic_moods_and_genres_category", "params": params}), "musicTwoRowItemRenderer"):
        browse = item.get("navigationEndpoint", {}).get("browseEndpoint", {}).get("browseId", "")
        if browse.startswith("VL") and browse[2:] not in ids:
            ids.append(browse[2:])
    return ids


def _search(query: str) -> str:
    """The first YouTube match for a song that is not on YouTube Music (a local file): its video id."""
    vid = run(["--print", "id", "--no-playlist", "ytsearch1:" + query], directory() / "cookies.txt").strip()
    if not ID.fullmatch(vid):
        raise ValueError("No such song on YouTube")
    return vid


def start_station(kind: str, value: str, title: str = "", restore: str = "") -> dict:
    """Radio from a song (kind song, value a video id or "artist - title") or a mood (kind mood, value its params):
    its songs become the YouTube Music library until stop_station()."""
    directory().mkdir(mode=0o700, parents=True, exist_ok=True)
    cookies = directory() / "cookies.txt"
    if not cookies.exists() and kind != "link":
        raise ValueError("Connect YouTube Music first")
    if kind == "song":
        vid = value if ID.fullmatch(value) else _search(value)
        pages = [f"https://www.youtube.com/watch?v={vid}&list=RDAMVM{vid}"]
    elif kind == "link":  # a playlist, album, profile or mix on any site yt-dlp reads (SoundCloud, Bandcamp, Mixcloud...)
        if not re.fullmatch(r"https://[^\s]+", value):
            raise ValueError("Paste a link that starts with https://")
        data = json.loads(run(["--flat-playlist", "--dump-single-json", "--playlist-end", str(STATION_TRACKS), value], None, timeout=90))
        tracks = parse_tracks({"entries": [_linked(e) for e in (data.get("entries") or [data])]})
        return _save_station(kind, value, title or data.get("title") or value, tracks, restore)
    elif kind == "mood":
        pages = ["https://www.youtube.com/playlist?list=" + i for i in _mood_playlists(value)[:MOOD_PLAYLISTS]]
    else:
        raise ValueError("Choose a song, a mood or a link")
    entries = []
    for page in pages:
        data = json.loads(run(["--flat-playlist", "--dump-single-json", "--playlist-end", "100", page], cookies))
        entries += data.get("entries") or []
    if kind == "song" and len(entries) < 5:  # a little-known song: YouTube's "mix" is the song alone
        raise ValueError("YouTube Music has no radio for this song; try another song or a mood")
    tracks = parse_tracks({"entries": entries})
    if kind == "mood":
        random.shuffle(tracks)  # several playlists, not one after another
    return _save_station(kind, value, title or (tracks[0]["title"] if kind == "song" else kind), tracks, restore)


# A plain or HLS audio link FFmpeg can read (Mixcloud's DASH manifest it cannot)
LINK_FORMAT = "bestaudio[protocol^=http][protocol!*=dash]/bestaudio[protocol*=m3u8]/bestaudio"


def _linked(entry: dict) -> dict:
    """A song from another site, played like a YouTube one: an id of the same shape (from its link), and the link."""
    url = entry.get("webpage_url") or entry.get("url") or ""
    if not isinstance(url, str) or not url.startswith("https://") or ID.fullmatch(str(entry.get("id", ""))) and "youtube" in url:
        return entry
    vid = base64.urlsafe_b64encode(hashlib.sha256(url.encode()).digest()).decode()[:11]
    title = entry.get("title") or url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").capitalize()  # SoundCloud lists links only
    return {**entry, "id": vid, "url": url, "title": title, "artist": entry.get("artist") or entry.get("uploader") or ""}


def _save_station(kind, value, title, tracks, restore):
    on = {"kind": kind, "value": value, "title": title, "tracks": tracks[:STATION_TRACKS],
          "restore": (station() or {}).get("restore", restore)}
    path = directory() / "station.json"
    path.with_suffix(".tmp").write_text(json.dumps(on, ensure_ascii=False))
    path.with_suffix(".tmp").replace(path)
    return status()


def stop_station() -> str:
    """Back to the connected playlist; returns the music source to restore ("" to keep the current one)."""
    restore = (station() or {}).get("restore", "")
    (directory() / "station.json").unlink(missing_ok=True)
    return restore


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
    """yt-dlp with the YouTube Music session (a copy of the jar), or none when cookies is None (other sites)."""
    try:
        # yt-dlp rewrites its jar on exit. Per-call copies keep parallel prefetch/refresh from racing.
        with tempfile.TemporaryDirectory(dir=directory()) as tmp:
            jar = Path(tmp) / "cookies.txt"
            if cookies is not None:
                shutil.copyfile(cookies, jar)
                jar.chmod(0o600)
            p = subprocess.run(["yt-dlp", "--ignore-config", "--no-warnings", *(["--cookies", str(jar)] if cookies is not None else []),
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
                       "duration": duration, **({"url": entry["url"]} if isinstance(entry.get("url"), str) and entry["url"].startswith("https://") else {})})
    if not tracks:
        raise ValueError("No playable songs in this playlist; choose another playlist")
    return tracks


BROWSERS = ("chrome",)  # shortcut: only the user's browser; yt-dlp also reads brave, edge, firefox, safari...


def _blocked(folder: Path) -> bool:
    try:
        os.listdir(folder)
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def browser_jar(browser, url, out):
    """Copies the YouTube sign-in from a browser the user is signed in with into a cookie file (yt-dlp reads the
    browser's store; macOS asks once for Keychain access to Chrome's key). Only youtube.com cookies are kept."""
    if browser not in BROWSERS:
        raise ValueError("Unknown browser")
    try:
        p = subprocess.run(["yt-dlp", "--ignore-config", "--no-warnings", "--cookies-from-browser", browser,
                            "--cookies", str(out), "--skip-download", "--flat-playlist", "--playlist-end", "1", url],
                           capture_output=True, text=True, timeout=120)
        lines = Path(out).read_text(errors="replace").splitlines()
    except (OSError, subprocess.TimeoutExpired):
        lines, p = [], None
    err = (getattr(p, "stderr", "") or "").lower()  # only matched against, never shown: it may carry account details
    if not lines and "could not find" in err and _blocked(Path.home() / "Library/Application Support/Google/Chrome"):
        err += " not permitted"  # yt-dlp cannot list the folder it may not open, so it reports the store as missing
    if not lines and "not permitted" in err:  # macOS privacy: this app may not read another app's data
        raise ValueError(f"macOS blocked access to {browser.title()}'s data: in System Settings → Privacy & Security → "
                         "Full Disk Access, turn on Personal Radio, then connect again")
    if not lines and "could not find" in err:
        raise ValueError(f"Could not open {browser.title()}'s cookie store; quit {browser.title()} and try again")
    if not lines and ("keyring" in err or "keychain" in err or "safe storage" in err or "decrypt" in err):
        raise ValueError(f"macOS did not allow access to {browser.title()}'s key; connect again and choose Allow")
    if not lines:
        raise ValueError(f"Could not read the YouTube sign-in from {browser.title()}")
    def youtube(ln):
        domain = ln.split("\t", 1)[0].removeprefix("#HttpOnly_").lstrip(".")
        return "\t" in ln and (domain == "youtube.com" or domain.endswith(".youtube.com"))
    keep = [ln for ln in lines if youtube(ln)]
    if not any(ln.split("\t")[5:6] in (["SAPISID"], ["__Secure-3PAPISID"]) for ln in keep):
        raise ValueError(f"Sign in to YouTube Music in {browser.title()} first, then connect again")
    Path(out).write_text("# Netscape HTTP Cookie File\n" + "\n".join(keep) + "\n")


def connect(cookie, playlist=DEFAULT_PLAYLIST, browser=""):
    url = playlist_url(playlist)
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    saved = root / "cookies.txt"
    if not cookie and not browser and not saved.exists():
        raise ValueError("Connect your YouTube Music session first")
    # Stage credentials: a failed connection must not replace a working session/library.
    fd, name = tempfile.mkstemp(dir=root)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w") as f:
            if not browser:
                f.write(cookie_jar(cookie) if cookie else saved.read_text())
        if browser:
            browser_jar(browser, url, staged)
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
    track = next((t for t in library()["tracks"] if t["id"] == vid), None) if ID.fullmatch(vid) else None
    if track is None:
        raise ValueError("Unknown YouTube Music song")
    # another site: its link, no YouTube session, an audio format FFmpeg reads (an hour-long mix downloads in ~7 s)
    page, cookies, fmt, suffix = (track["url"], None, LINK_FORMAT, ".m4a") if track.get("url") else \
        ("https://www.youtube.com/watch?v=" + vid, directory() / "cookies.txt", "bestaudio", ".webm")
    root = directory() / "audio"
    root.mkdir(mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=root, suffix=suffix)
    os.close(fd)
    out = Path(name)
    try:
        # Chunked downloading avoids YouTube throttling Liquidsoap's full-file HTTP request.
        metadata = run(["--no-playlist", "-f", fmt, "--http-chunk-size", "1M", "--no-part",
                        "--force-overwrites", "--no-progress", "--print", "after_move:%(.{artist,artists,track,title,uploader})j",
                        "-o", str(out), page], cookies, timeout=20)
        if not out.stat().st_size:
            raise ValueError("YouTube did not return audio")
        try:
            meta = json.loads(metadata or "{}")
            save_metadata(vid, {**meta, "artist": meta.get("artist") or (meta.get("uploader") if track.get("url") else None)})
        except (ValueError, TypeError, sqlite3.Error):
            pass  # missing credits must not discard an otherwise playable song
        return str(out.resolve())  # Liquidsoap owns and removes the file after the track.
    except Unavailable:
        out.unlink(missing_ok=True)
        mark_unavailable(vid)
        raise
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
