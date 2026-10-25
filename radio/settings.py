"""Numbers you tune while listening (how long music plays between news, how long a news break is, the speech limit
per hour...), set from the UI. Like plugins.json: the values live in DATA_DIR/settings.json, config.toml only gives
the defaults, and the daemon picks a change up live, without a restart."""
import json
import os
import urllib.request
from copy import deepcopy

from . import chatgpt, config, editor, openrouter, plugins, public_news

# key -> (section, name, title, hint, min, max, step, unit); step None: a text, min and max are its length
SETTINGS = {
    "music_source": ("music", "source", "Music source", "", 1, 20, None, ""),
    "reddit_communities": ("sources.reddit", "communities", "Reddit · Subreddits",
                           "Add one name or link at a time. Only the new subreddit is checked; up to 20 total.", 0, 4000, None, ""),
    "reddit_posts": ("sources.reddit", "posts", "Reddit · Threads and posts",
                    "Public post links, one per line. RSS is checked before saving; reads headlines and available comments.", 0, 4000, None, ""),
    "telegram_channels": ("sources.telegram", "channels", "Telegram · Channels",
                          "Add one public channel name or link at a time. Only the new channel is checked; up to 20 total.", 0, 4000, None, ""),
    "telegram_posts": ("sources.telegram", "posts", "Telegram · Posts",
                       "Public post links, one per line. Private posts and forum-topic links are unavailable.", 0, 4000, None, ""),
    "host_name": ("broadcast", "host_name", "Host name", "How the co-host addresses the main host when Two hosts is enabled.",
                  1, 30, None, ""),
    "cohost_name": ("broadcast", "cohost_name", "Co-host name", "How the main host addresses the co-host when Two hosts is enabled.", 1, 30, None, ""),
    "station_name": ("broadcast", "station_name", "Station name",
                     "Used in spoken introductions. Names in Latin letters are pronounced in English.", 1, 40, None, ""),
    "voice_engine": ("voice", "engine", "Voice engine", "Applies to new speech. Prepared audio keeps its current voice.",
                     1, 20, None, ""),
    "llm_provider": ("editor", "provider", "Script provider", "Writes scripts for the host.", 1, 20, None, ""),
    # one model row per provider; the page shows the selected provider's
    "ollama_model": ("editor", "model", "Model", "", 1, 200, None, ""),
    "openai_model": ("editor", "openai_model", "Model", "", 1, 200, None, ""),
    "deepseek_model": ("editor", "deepseek_model", "Model", "", 1, 200, None, ""),
    "xai_model": ("editor", "xai_model", "Model", "", 1, 200, None, ""),
    "openrouter_model": ("editor", "openrouter_model", "Model", "Usage is charged to your OpenRouter balance.", 1, 200, None, ""),
    "music_set_minutes": ("broadcast", "music_set_minutes", "New music set length",
                          "Length of a new-tracks selection. Host editions follow the clock at :00 and :30.",
                          5, 90, 5, "min"),
    "max_break_speech_sec": ("broadcast", "max_break_speech_sec", "Speech per edition",
                             "Maximum speech length when enough prepared stories are available.", 30, 600, 10, "s"),
    "max_spoken_seconds_per_rolling_hour": ("broadcast", "max_spoken_seconds_per_rolling_hour", "Speech per hour",
                                            "Total speech allowed in any 60-minute window. Further speech waits when the limit is reached.", 60, 900, 30, "s"),
    "new_every_sets": ("broadcast", "new_every_sets", "Unplayed tracks first",
                       "1 prioritizes unplayed tracks from the active music source; 0 disables priority.", 0, 1, 1, ""),
    "station_id_every_songs": ("broadcast", "station_id_every_songs", "Station ID opportunities",
                               "0 disables; positive values allow a clock ID after 30 minutes without branding.", 0, 10, 1, ""),
    "lead_min": ("reminders", "lead_min", "Reminder lead time",
                 "How far ahead to announce timed calendar events and Todoist tasks.", 1, 60, 1, "min"),
}

SOURCE_LISTS = {
    "reddit_communities": ("reddit", "names", "python\nr/technology"),
    "reddit_posts": ("reddit", "posts", "https://www.reddit.com/r/python/comments/abc123/title/"),
    "telegram_channels": ("telegram", "names", "@telegram\nhttps://t.me/bbcrussian"),
    "telegram_posts": ("telegram", "posts", "https://t.me/telegram/83"),
}


CHOICES = {"music_source": ("local", "ytmusic"), "llm_provider": ("ollama", "openai", "openrouter", "deepseek", "xai"), "voice_engine": ("qwen3", "voicestudio")}
VOICE_LABELS = {"qwen3": "Qwen3-TTS · cloned voice", "voicestudio": "VoiceStudio · OmniVoice"}
MODEL_ROWS = {"ollama": "ollama_model", "openai": "openai_model", "openrouter": "openrouter_model",
              "deepseek": "deepseek_model", "xai": "xai_model"}  # a text setting that is one of these: a drop-down on the page


def _path():
    return config.DATA_DIR / "settings.json"


def _clamp(key: str, value) -> int | str | list[str]:
    if key in SOURCE_LISTS:
        source, kind, _ = SOURCE_LISTS[key]
        return public_news.references(value, source, kind)
    lo, hi, step = SETTINGS[key][4], SETTINGS[key][5], SETTINGS[key][6]
    if step is None:
        text = " ".join(str(value).split())[:hi]  # one line: the host reads it aloud
        if len(text) < lo:
            raise ValueError(f"{key}: enter {lo}-{hi} characters")
        if key in CHOICES and text not in CHOICES[key]:
            raise ValueError(f"{key}: one of {', '.join(CHOICES[key])}")
        return text
    return max(lo, min(hi, int(round(float(value)))))


def _saved() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return {k: _clamp(k, v) for k, v in data.items() if k in SETTINGS}
    except (FileNotFoundError, ValueError, TypeError, OverflowError):
        return {}


def base(cfg: dict) -> dict:
    """The config.toml values (the defaults)."""
    return {k: _section(cfg, s[0])[s[1]] for k, s in SETTINGS.items()}


def _section(cfg, path):
    for part in path.split("."):
        cfg = cfg[part]
    return cfg


def set_setting(key: str, value) -> None:
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key}")
    try:
        number = _clamp(key, value)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(str(e) if SETTINGS[key][6] is None else f"{key}: a number is expected")
    if key == "music_source" and number == "ytmusic" and not plugins.on(config.load(), "ytmusic"):
        raise ValueError("Enable the YouTube Music integration before selecting it as a source.")
    saved = _saved()
    saved[key] = number
    _write(saved)


def reset(key: str | None = None) -> None:
    """Back to config.toml: one setting, or all of them."""
    saved = _saved()
    for k in ([key] if key else list(saved)):
        saved.pop(k, None)
    _write(saved)


def _write(saved: dict) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    os.replace(tmp, _path())


def apply(cfg: dict, defaults: dict) -> None:
    """Puts the saved values (else the defaults) into a loaded config. The daemon calls it before it decides."""
    saved = _saved()
    for k, s in SETTINGS.items():
        _section(cfg, s[0])[s[1]] = saved.get(k, defaults[k])


def listing(cfg: dict) -> list[dict]:
    """For the page. cfg is a freshly loaded config (the defaults)."""
    saved, d = _saved(), base(cfg)
    value = {k: saved.get(k, d[k]) for k in SETTINGS}
    live = deepcopy(cfg)
    for k, s in SETTINGS.items():
        _section(live, s[0])[s[1]] = value[k]
    provider = value["llm_provider"]
    c = editor.connection(live)
    rows = []
    for k, s in SETTINGS.items():
        if k in ("music_source", "reddit_posts", "telegram_posts"):
            continue
        if k in SOURCE_LISTS and not plugins.on(cfg, SOURCE_LISTS[k][0]):
            continue
        if k in MODEL_ROWS.values() and (k != MODEL_ROWS[provider] or c["state"] not in ("ok", "no_model")):
            continue  # a model is picked only once the provider answers, from what it offers
        row = {"key": k, "title": s[2], "hint": s[3], "min": s[4], "max": s[5], "step": s[6], "unit": s[7],
               "options": CHOICES.get(k), "value": value[k], "default": d[k]}
        if k in SOURCE_LISTS:
            row.update(multiline=True, placeholder=SOURCE_LISTS[k][2])
        if k == MODEL_ROWS[provider]:
            row["options"] = c["ids"] or None  # None: a text field (a ChatGPT sign-in lists no models)
        if k == "voice_engine":
            row["labels"] = VOICE_LABELS
            row["voice_state"] = _voice_state(live)
            row["action"] = {"label": "Play a sample", "cmd": "voice_sample"}
        if k == "llm_provider":
            row["connection"] = {"state": c["state"], "detail": c["detail"], "models": len(c["ids"])}
            row["active"] = {"provider_title": editor.TITLES[provider], "model": value[MODEL_ROWS[provider]]}
            row["labels"] = editor.TITLES
            if provider == "openrouter":
                row["action"] = ({"label": "Sign out of OpenRouter", "cmd": "openrouter_logout"} if openrouter.key()
                                 else {"label": "Sign in with OpenRouter", "cmd": "openrouter_login"})
            elif provider in editor.KEYED:
                row["key_field"] = {"provider": provider, "saved": bool(editor.saved_key(provider))}
            elif provider == "openai":
                row["action"] = ({"label": "Sign out of ChatGPT", "cmd": "chatgpt_logout"} if chatgpt.signed_in()
                                 else {"label": "Sign in with ChatGPT", "cmd": "chatgpt_login"})
        rows.append(row)
    return rows


def music_source(cfg: dict) -> str:
    selected = _saved().get("music_source", cfg["music"].get("source", "local"))
    return selected if plugins.on(cfg, "ytmusic") else "local"


def _voice_state(live: dict) -> dict:
    """Is the selected voice server answering? The radio starts it (and stops the other one)."""
    from . import tts
    v = live["voice"]
    title = VOICE_LABELS[v["engine"]]
    url = v["mlx_audio_url"] if v["engine"] == "qwen3" else v["base_url"]
    try:
        with urllib.request.urlopen(url.rstrip("/") + ("/" if v["engine"] == "qwen3" else "/health"), timeout=1):
            return {"state": "ready", "engine_title": title,
                    "detail": "Ready" + (" · first speech can take about a minute" if v["engine"] == "qwen3" else "")}
    except OSError:
        port = int(url.rsplit(":", 1)[-1])
        starting = tts.port_open(port)
        return {"state": "starting" if starting else "down", "engine_title": title,
                "detail": "Starting…" if starting
                else "Not running · starts with the station"}
