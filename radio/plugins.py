"""Switchable radio features ("plugins"). The on/off state lives in DATA_DIR/plugins.json, set from the UI;
config.toml only gives the defaults. The daemon reads it on every check, so a switch works without a restart."""
import json

from . import config

# name -> (title, what it does, default from the config)
PLUGINS = {
    "holiday_previews": ("Upcoming holidays", 'One short calendar preview per day, 3–5 days before annual all-day events or entries tagged Holiday. Birthdays and family holidays may get an eve reminder. Requires Calendar and Host links. Restart after installation.', lambda c: False),
    "energy_wave": ("Energy wave", 'Local music rises and eases smoothly with listening time, across set boundaries. All energy levels participate; prefer smooth transitions and similar genres. Keeps unplayed-track priority. Analyze the library before first use; restart once after installation.', lambda c: False),
    "track_previews": ("Coming up", 'Close host editions with one later song hook, then introduce the first song. No standalone announcements between songs. Missing hooks use a short first-song introduction. Restart after installation.', lambda c: False),
    "ytmusic": ("YouTube Music", 'Make YouTube Music available as a music source. Select the active source in Settings.',
                lambda c: False),
    "github_trending": ("GitHub: releases", 'Recent stable releases from projects discovered through GitHub Trending. Popularity alone is not news.',
                        lambda c: c["sources"]["github_trending"]["enabled"]),
    "github_new": ("GitHub: new projects", 'Newly created public projects, dated by creation time rather than discovery time.',
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
    "news_reactions": ("News reactions", 'Let the editor choose one subtle reaction for new news text. OmniVoice only. No extra AI requests; prepared audio stays unchanged.', lambda c: False),
    "host_opinions": ("Host opinions", 'Give new news scripts a short, source-grounded personal comment. Edit both characters in Settings. Two hosts gives each character its own voice. Prepared audio stays unchanged. Restart once after installation.', lambda c: False),
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

EXPERIMENTS = {"holiday_previews", "reddit", "telegram", "air_direction", "gap_filler", "duo_hosts", "news_reactions", "host_opinions", "track_previews", "energy_wave"}


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
    result['news_reactions'] = result['news_reactions'] and settings._saved().get('voice_engine', cfg['voice']['engine']) == 'voicestudio'
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
    from . import energy, settings
    st = state(cfg)
    analyzed = len(energy.cached())
    reactions_available = settings._saved().get('voice_engine', cfg['voice']['engine']) == 'voicestudio'
    return [{"name": n, "title": t, "description": d + (f' Cached analysis: {analyzed} tracks.' if n == 'energy_wave' else ''), "on": st[n],
             "available": n != 'news_reactions' or reactions_available,
             "unavailable_reason": 'Select VoiceStudio · OmniVoice in Settings → Voice engine. Qwen3 does not support reaction tags.' if n == 'news_reactions' and not reactions_available else '',
             "section": "integrations" if n in INTEGRATIONS else "experiments" if n in EXPERIMENTS else "features"}
            for n, (t, d, _) in PLUGINS.items()]
