"""Switchable radio features ("plugins"). The on/off state lives in DATA_DIR/plugins.json, set from the UI;
config.toml only gives the defaults. The daemon reads it on every check, so a switch works without a restart."""
import json
import os

from . import config

# name -> (title, what it does, default from the config)
PLUGINS = {
    "ytmusic": ("YouTube Music", "Use the connected playlist from the next radio start; local music is the fallback",
                lambda c: False),
    "github_trending": ("GitHub: trending", "Trend of the day and small projects from GitHub Trending",
                        lambda c: c["sources"]["github_trending"]["enabled"]),
    "github_new": ("GitHub: new this week", "New projects of the week from GitHub search",
                   lambda c: c["sources"]["github_new"]["enabled"]),
    "horizon": ("Horizon", "News picked by Horizon (Hacker News, RSS)",
                lambda c: c["sources"]["horizon"]["enabled"]),
    "station_ids": ("Station IDs", "A station call sign between songs",
                    lambda c: c["broadcast"]["station_id_every_songs"] > 0),
    "host_links": ("Host links", "The host says the time before a news break and names the songs around it",
                   lambda c: c["broadcast"]["host_links"]),
    "new_tracks": ("New tracks", "Every few sets, a set of songs added to the library recently that have not "
                   "aired yet", lambda c: c["broadcast"]["new_every_sets"] > 0),
    "reminders_calendar": ("Reminders: calendar", "A short reminder 15 minutes before a calendar event, at the next "
                           "song boundary. All-day events become host mentions, up to three a day, four hours apart; "
                           "a short story uses the calendar description when the local editor is enabled. "
                           "Needs Host links and the secret iCal link in config.toml (reminders.calendar_url) "
                           "for all-day mentions",
                           lambda c: bool(c["reminders"]["calendar_url"])),
    "reminders_todoist": ("Reminders: Todoist", "The same for Todoist tasks that have a time. Needs the token in "
                          "config.toml (reminders.todoist_token)", lambda c: bool(c["reminders"]["todoist_token"])),
    "voicestudio": ("VoiceStudio server", "Starts the VoiceStudio voice server with the radio, without the app "
                    "window, and stops it with the radio. A VoiceStudio that is already running is left alone",
                    lambda c: True),
    "weather": ("Weather", "A short local forecast between songs, at most once every three hours. "
                "Temperature, rain outlook and wind from Open-Meteo (open-meteo.com). "
                "Set weather.city in config.toml; needs Host links", lambda c: bool(c["weather"]["city"])),
    "voice_over": ("Host over music", "Announcements over the end of a song (after its last sung line) and the intro "
                   "of the next (before its vocals); the lines come from LRCLIB's synced lyrics, without them some "
                   "breaks simply end over the start of the song. The ending part applies from the next restart",
                   lambda c: c["broadcast"]["voice_over"]),
}
SOURCE_PLUGIN = {"github_trending": "github_trending", "github_new": "github_new", "horizon": "horizon"}
INTEGRATIONS = {*SOURCE_PLUGIN.values(), "reminders_calendar", "reminders_todoist", "voicestudio", "weather", "ytmusic"}
EXPERIMENTS = {"voice_over"}


def _path():
    return config.DATA_DIR / "plugins.json"


def _saved() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return {k: bool(v) for k, v in data.items() if k in PLUGINS}
    except (FileNotFoundError, ValueError):
        return {}


def state(cfg: dict) -> dict[str, bool]:
    saved = _saved()
    return {name: saved.get(name, bool(default(cfg))) for name, (_, _, default) in PLUGINS.items()}


def on(cfg: dict, name: str) -> bool:
    return state(cfg)[name]


def set_plugin(name: str, enabled: bool) -> None:
    if name not in PLUGINS:
        raise ValueError(f"unknown plugin {name}")
    if name == "ytmusic" and enabled:
        from . import ytmusic
        if not ytmusic.status()["connected"]:
            raise ValueError("Connect YouTube Music first")
    saved = _saved()
    saved[name] = bool(enabled)
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _path())


def listing(cfg: dict) -> list[dict]:
    st = state(cfg)
    return [{"name": n, "title": t, "description": d, "on": st[n],
             "section": "integrations" if n in INTEGRATIONS else "experiments" if n in EXPERIMENTS else "features"}
            for n, (t, d, _) in PLUGINS.items()]
