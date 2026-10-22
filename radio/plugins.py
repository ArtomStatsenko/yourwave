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
    "air_direction": ("Air direction", "Plan future blocks: insert, skip, reorder or postpone. Changes stay local",
                      lambda c: False),
    "gap_filler": ("Host covers loading gaps", "When YouTube Music has not loaded the next song by the end of the "
                   "current one, a ready host edition airs now; with nothing ready, the host says so in one line "
                   "(at most every 10 minutes, within the speech limit) before a song from the local library",
                   lambda c: False),
    "duo_hosts": ("Two hosts", "Two voices share a news break: stories alternate and the hosts hand over to each other "
                  "by name. The co-host's lines are voiced when the break is assembled, so it takes a little longer",
                  lambda c: False),
    "reroll_next": ("Reroll next track", "Choose a different random next track from Next or the air editor; the current song keeps playing. Applies immediately", lambda c: False),
    "music_facts": ("Music stories", "Light music links when no news is waiting. MusicBrainz and Wikidata facts, "
                    "cached locally, without LLM calls. Uses the normal music-set interval", lambda c: False),
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
    "weather": ("Weather", "A short local report at 00:00, 03:00, ... station time; plays between songs. "
                "City, temperature and current conditions from Open-Meteo (open-meteo.com). "
                "Set weather.city in config.toml; needs Host links", lambda c: bool(c["weather"]["city"])),

}
SOURCE_PLUGIN = {"weather": "weather", "calendar": "reminders_calendar", "github_trending": "github_trending", "github_new": "github_new", "horizon": "horizon", "music_facts": "music_facts", "direction": "air_direction"}
INTEGRATIONS = {*SOURCE_PLUGIN.values(), "reminders_calendar", "reminders_todoist", "weather", "ytmusic"} - {"air_direction"}

EXPERIMENTS = {"air_direction", "reroll_next", "gap_filler", "duo_hosts"}


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
