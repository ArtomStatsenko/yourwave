"""The station's own sounds: one folder per slot in DATA_DIR/sounds. A slot with files of its own plays them (one per
use, in rotation) instead of Base; an empty slot plays Base. The daemon picks up changes live (config.own_sounds)."""
import re
import subprocess
from pathlib import Path

from . import config

SLOTS = {  # slot (the [jingles] key): title, where it plays
    "stinger_in": ("Intro", "Opens a host edition"),
    "stinger_out": ("Outro", "Closes a host edition, back to the music"),
    "bed": ("Speech bed", "Plays quietly under the host's voice"),
    "separator": ("Separator", "Between the blocks of an edition"),
    "reminder": ("Reminder", "Before a calendar or Todoist reminder"),
    "station_id": ("Station ID", "Between songs, with Station IDs on"),
    "github_intro": ("GitHub intro", "Opens the GitHub section"),
    "horizon_intro": ("Horizon intro", "Opens the Horizon section"),
}
MAX_BYTES = 50 * 1024 * 1024  # shortcut: a jingle is seconds long, so 50 MB covers any format; raise it for long beds


def folder(slot: str) -> Path:
    if slot not in SLOTS:
        raise ValueError("Unknown sound slot")
    return config.DATA_DIR / "sounds" / slot


def _name(name: str) -> str:
    """A safe file name: no folders, an audio extension."""
    name = Path(name).name.strip()
    if not name or name.startswith(".") or Path(name).suffix.lower() not in config.AUDIO_EXTS:
        raise ValueError("Choose an MP3, M4A, FLAC or WAV file")
    return re.sub(r"[\x00-\x1f/\\:]", "_", name)


def listing(cfg: dict) -> list[dict]:
    """Every slot with its own files and the Base files it falls back to."""
    return [{"slot": slot, "title": title, "hint": hint, "own": [f.name for f in config.own_sounds(slot)],
             "base": [Path(f).name for f in cfg.get("sounds_base", {}).get(slot, [])]} for slot, (title, hint) in SLOTS.items()]


def add(slot: str, name: str, data: bytes) -> str:
    """Saves an uploaded sound once ffprobe reads it as audio; returns its file name."""
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError("The file is empty or larger than 50 MB")
    target = folder(slot) / _name(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name("." + target.name + ".part")  # hidden until checked: the station never picks it
    part.write_bytes(data)
    try:
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type",
                                "-of", "csv=p=0", str(part)], capture_output=True, text=True, timeout=30)
        if "audio" not in probe.stdout:
            raise ValueError("This file has no audio the station can play")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return target.name


def remove(slot: str, name: str) -> None:
    (folder(slot) / _name(name)).unlink(missing_ok=True)


def path(slot: str, name: str) -> Path | None:
    """A slot's own or Base file by name, for listening on the page."""
    own = folder(slot) / _name(name)
    if own.is_file():
        return own
    return next((Path(f) for f in config.load().get("sounds_base", {}).get(slot, []) if Path(f).name == name), None)
