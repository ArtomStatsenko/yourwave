"""Switchable radio features ("plugins"). The on/off state lives in DATA_DIR/plugins.json, set from the UI;
config.toml only gives the defaults. The daemon reads it on every check, so a switch works without a restart."""
import json

from . import config

# name -> (title, what it does, default from the config)
PLUGINS = {
    "track_previews": ("Coming up", 'About once an hour: introduce two queued songs with short hooks, up to 25 seconds. Missing hooks use voice only. Analyzes the library in the background.', lambda c: False),
    "ytmusic": ("YouTube Music", 'Make YouTube Music available as a music source. Select the active source in Settings.',
                lambda c: False),
    "github_trending": ("GitHub: trending", 'Pick top trending repositories and smaller projects from GitHub Trending.',
                        lambda c: c["sources"]["github_trending"]["enabled"]),
    "github_new": ("GitHub: new this week", 'Pick popular repositories created in the past week.',
                   lambda c: c["sources"]["github_new"]["enabled"]),
    "horizon": ("Horizon", 'Select technology news from Hacker News and RSS feeds. Requires a configured Horizon installation.',
                lambda c: c["sources"]["horizon"]["enabled"]),
    "reddit": ("Reddit news", 'Read selected subreddits through RSS. Configure sources in Settings. RSS support ends November 13, 2026.', lambda c: False),
    "telegram": ("Telegram news", 'Read selected public channels. Add channels in Settings. Only public web previews are available.', lambda c: False),
    "air_direction": ("Air direction", 'Insert, skip, reorder or postpone upcoming broadcast blocks.',
                      lambda c: False),
    "gap_filler": ("Host covers loading gaps", 'Play a ready host edition while YouTube Music loads; otherwise, introduce a local track with a short message.',
                   lambda c: False),
    "duo_hosts": ("Two hosts", 'Alternate stories between two voices in each host edition. Preparing the second voice may take longer.',
                  lambda c: False),
    "news_reactions": ("News reactions", 'Let the editor choose one subtle reaction for new news text. VoiceStudio only. No extra AI requests; prepared audio stays unchanged. Restart after installing this experiment.', lambda c: False),
    "music_facts": ("Music stories", 'Add short music stories between sets independently of news availability.', lambda c: False),
    "station_ids": ("Station IDs", 'Play the station call sign between songs.',
                    lambda c: c["broadcast"]["station_id_every_songs"] > 0),
    "host_links": ("Host links", 'Announce the time before host editions and name the songs around them.',
                   lambda c: c["broadcast"]["host_links"]),
    "new_tracks": ("Unplayed tracks first", 'Prioritize unplayed tracks from the active local or YouTube Music library before repeats.', lambda c: c["broadcast"]["new_every_sets"] > 0),
    "reminders_calendar": ("Reminders: calendar", 'Remind you before timed events and mention all-day events. Set reminders.calendar_url in config.toml.',
                           lambda c: bool(c["reminders"]["calendar_url"])),
    "reminders_todoist": ("Reminders: Todoist", 'Remind you before tasks with a due time. Set reminders.todoist_token in config.toml.', lambda c: bool(c["reminders"]["todoist_token"])),
    "weather": ("Weather", 'Report local weather every three hours. Set weather.city in config.toml to enable forecasts.', lambda c: bool(c["weather"]["city"])),

}
SOURCE_PLUGIN = {"reddit": "reddit", "telegram": "telegram", "weather": "weather", "calendar": "reminders_calendar", "github_trending": "github_trending", "github_new": "github_new", "horizon": "horizon", "music_facts": "music_facts", "direction": "air_direction"}
INTEGRATIONS = {*SOURCE_PLUGIN.values(), "reminders_calendar", "reminders_todoist", "weather", "ytmusic"} - {"air_direction", "reddit", "telegram"}

EXPERIMENTS = {"reddit", "telegram", "air_direction", "gap_filler", "duo_hosts", "news_reactions", "track_previews"}


def _path():
    return config.DATA_DIR / "plugins.json"


def _saved() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {k: bool(v) for k, v in data.items() if k in PLUGINS}
    except (FileNotFoundError, ValueError):
        return {}


def state(cfg: dict) -> dict[str, bool]:
    from . import settings
    saved = _saved()
    result = {name: saved.get(name, bool(default(cfg))) for name, (_, _, default) in PLUGINS.items()}
    result['new_tracks'] = result['new_tracks'] and settings._saved().get('new_every_sets', cfg['broadcast']['new_every_sets']) > 0
    return result


def on(cfg: dict, name: str) -> bool:
    return state(cfg)[name]


def set_plugin(name: str, enabled: bool) -> None:
    if name not in PLUGINS:
        raise ValueError(f"unknown plugin {name}")
    if name == "ytmusic" and not enabled:
        from .settings import set_setting
        set_setting("music_source", "local")
    if name == 'new_tracks':
        from .settings import set_setting
        set_setting('new_every_sets', int(enabled))
    with config.file_lock(_path()):
        saved = _saved()
        saved[name] = bool(enabled)
        config.write_json(_path(), saved)


def listing(cfg: dict) -> list[dict]:
    st = state(cfg)
    return [{"name": n, "title": t, "description": d, "on": st[n],
             "section": "integrations" if n in INTEGRATIONS else "experiments" if n in EXPERIMENTS else "features"}
            for n, (t, d, _) in PLUGINS.items()]
