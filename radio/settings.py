"""Numbers you tune while listening (how long music plays between news, how long a news break is, the speech limit
per hour...), set from the UI. Like plugins.json: the values live in DATA_DIR/settings.json, config.toml only gives
the defaults, and the daemon picks a change up live, without a restart."""
import json
import os

from . import chatgpt, config, editor, openrouter

# key -> (section, name, title, hint, min, max, step, unit); step None: a text, min and max are its length
SETTINGS = {
    "station_name": ("broadcast", "station_name", "Station name",
                     "What the host calls the station in the set openers. A Latin name is read as English", 1, 40, None, ""),
    "llm_provider": ("editor", "provider", "AI provider",
                     "Who retells news, repositories and calendar days", 1, 20, None, ""),
    # one model row per provider; the page shows the selected provider's
    "ollama_model": ("editor", "model", "Model", "A local Ollama model", 1, 200, None, ""),
    "openai_model": ("editor", "openai_model", "Model", "An OpenAI model", 1, 200, None, ""),
    "deepseek_model": ("editor", "deepseek_model", "Model", "A DeepSeek model", 1, 200, None, ""),
    "xai_model": ("editor", "xai_model", "Model", "A Grok model from xAI", 1, 200, None, ""),
    "openrouter_model": ("editor", "openrouter_model", "Model", "Any model on OpenRouter, billed to your OpenRouter "
                         "credits", 1, 200, None, ""),
    "music_set_minutes": ("broadcast", "music_set_minutes", "Music between breaks",
                          "How much music plays in a row before the next news break. More — breaks come less often",
                          5, 90, 5, "min"),
    "max_break_speech_sec": ("broadcast", "max_break_speech_sec", "News break length",
                             "How much speech one break holds while there are enough ready stories. More — a longer "
                             "break", 30, 600, 10, "s"),
    "max_spoken_seconds_per_rolling_hour": ("broadcast", "max_spoken_seconds_per_rolling_hour", "Speech limit per hour",
                                            "How many seconds the host speaks in a rolling hour, in total. Breaks "
                                            "wait when it is used up", 60, 900, 30, "s"),
    "new_every_sets": ("broadcast", "new_every_sets", "New tracks: every Nth set",
                       "Every Nth set is made of new songs. 0 — never", 0, 10, 1, ""),
    "station_id_every_songs": ("broadcast", "station_id_every_songs", "Station IDs: every N songs",
                               "A station ID after every N songs inside a set. 0 — none", 0, 10, 1, ""),
    "lead_min": ("reminders", "lead_min", "Reminders: minutes ahead",
                 "How many minutes before a calendar event or a Todoist task to remind", 1, 60, 1, "min"),
}


CHOICES = {"llm_provider": ("ollama", "openai", "openrouter", "deepseek", "xai")}
MODEL_ROWS = {"ollama": "ollama_model", "openai": "openai_model", "openrouter": "openrouter_model",
              "deepseek": "deepseek_model", "xai": "xai_model"}  # a text setting that is one of these: a drop-down on the page


def _path():
    return config.DATA_DIR / "settings.json"


def _clamp(key: str, value) -> int | str:
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
        return {k: _clamp(k, v) for k, v in data.items() if k in SETTINGS}
    except (FileNotFoundError, ValueError, TypeError):
        return {}


def base(cfg: dict) -> dict:
    """The config.toml values (the defaults)."""
    return {k: cfg[s[0]][s[1]] for k, s in SETTINGS.items()}


def set_setting(key: str, value) -> None:
    if key not in SETTINGS:
        raise ValueError(f"unknown setting {key}")
    try:
        number = _clamp(key, value)
    except (TypeError, ValueError) as e:
        raise ValueError(str(e) if SETTINGS[key][6] is None else f"{key}: a number is expected")
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
        cfg[s[0]][s[1]] = saved.get(k, defaults[k])


def listing(cfg: dict) -> list[dict]:
    """For the page. cfg is a freshly loaded config (the defaults)."""
    saved, d = _saved(), base(cfg)
    value = {k: saved.get(k, d[k]) for k in SETTINGS}
    live = {s[0]: dict(cfg[s[0]]) for s in SETTINGS.values()}
    for k, s in SETTINGS.items():
        live[s[0]][s[1]] = value[k]
    provider = value["llm_provider"]
    c = editor.connection(live)
    rows = []
    for k, s in SETTINGS.items():
        if k in MODEL_ROWS.values() and (k != MODEL_ROWS[provider] or c["state"] not in ("ok", "no_model")):
            continue  # a model is picked only once the provider answers, from what it offers
        row = {"key": k, "title": s[2], "hint": s[3], "min": s[4], "max": s[5], "step": s[6], "unit": s[7],
               "options": CHOICES.get(k), "value": value[k], "default": d[k]}
        if k == MODEL_ROWS[provider]:
            row["options"] = c["ids"] or None  # None: a text field (a ChatGPT sign-in lists no models)
        if k == "llm_provider":
            row["connection"] = {"state": c["state"], "detail": c["detail"], "models": len(c["ids"])}
            row["active"] = {"provider_title": editor.TITLES[provider], "model": value[MODEL_ROWS[provider]]}
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
