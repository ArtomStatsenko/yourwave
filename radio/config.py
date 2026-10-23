import os
import tomllib
from pathlib import Path

DATA_DIR = Path(os.environ.get("RADIO_DATA_DIR", Path.home() / "Library/Application Support/PersonalRadioClaude"))
PKG_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PKG_DIR.parent / "assets"
RUN_DIR = DATA_DIR / "run"
SOCK = RUN_DIR / "radio.sock"
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav"}

# TOML instead of YAML: stdlib only, no PyYAML to install.
DEFAULT_CONFIG = """\
[music]
path = "~/Music/Radio"
crossfade_sec = 3.0
target_lufs = -16.0
max_boost_db = 4.0          # cap for quiet tracks

[output]
mode = "browser"            # speakers | browser | both. browser = the page plays the live stream (radio ui)
stream_port = 8767          # local UDP port: the audio sink sends the mp3 stream to the UI server on it
stream_bitrate = "192k"
idle_pause_sec = 30         # browser only: with no listener for this long the station pauses (no news, no speech
                            # budget spent) and resumes when a page starts listening

[broadcast]
max_spoken_seconds_per_rolling_hour = 300
max_package_speech_sec = 90 # long digests are split verbatim into parts of about this size
package_cache_ttl_hours = 48
voice_bed_gain_db = -23     # bed level under the voice, relative to the voice
separator_heavy_chars = 350 # a digest paragraph longer than this (~25 s) gets a separator sound around it
host_name = "Артём"         # plugin "Two hosts": the main voice and the co-host (voice.cohost_*) call each other by name
cohost_name = "Маша"
station_name = "Personal Radio"  # said in host links; a Latin name is voiced in «quotes» (reads as English)
host_links = true           # news break: back-sell of the last songs, time check, ... set opener for the next music
talkover_share = 0.5        # without lyrics: share of bulletins that end with the host talking over the next song's start
music_set_minutes = 20      # length of new-tracks selections; editions follow :00/:30 clock slots
max_break_speech_sec = 120  # one break gathers the ready bulletins up to this much speech (rolling-hour limit applies)
new_every_sets = 3          # plugin "new tracks": every Nth set is made of songs added to the library in the last
new_days = 14               # new_days days that have not been on air yet; 0 = off. Needs at least new_min_tracks
new_min_tracks = 3          # such songs, else the set is a normal one
station_id_every_songs = 0  # legacy key: 0 disables; positive enables clock ID opportunities (30 min cooldown)

[sources]
max_age_hours = 24
inbox_poll_sec = 30
# Repos with any of these in the name or description are not aired. [] = no filter at all.
blocked_words = ["nsfw", "porn", "hentai", "xxx", "onlyfans", "18+", "sex", "erotic", "nude"]

[sources.github_trending]   # every repo is its own short bulletin
enabled = true
refresh_minutes = 60        # github.com/trending?since=daily, overall and per language
top_picks = 1               # best-ranked new repos of the overall page ("тренд дня")
indie_picks = 2             # small repos that made a trending page today ("находка дня")
indie_max_total_stars = 3000
indie_languages = ["python", "rust", "swift", "go"]  # extra trending pages to find small projects on
daily_min = 1000            # say the daily growth only when it is at least this

[sources.horizon]           # news picked by Horizon (github.com/Thysrael/Horizon), installed next to the radio data
enabled = false
dir = "horizon"             # relative to the radio data folder
refresh_minutes = 60
picks = 4                   # bulletins per run, Horizon's own ranking order
hours = 24
language = "en"             # Horizon's briefing language (its ai.languages); the radio retells it in Russian

[sources.github_new]        # GitHub search: repos created in the last N days, by stars ("новинка недели")
enabled = true
picks = 1
created_within_days = 7

[sources.reddit]            # experiment: public RSS, scheduled to end November 13, 2026
communities = []            # bare names, e.g. ["python", "technology"]; up to 20

[sources.telegram]          # experiment: public t.me/s web previews only
channels = []               # bare usernames, e.g. ["telegram"]; up to 20

[reminders]                 # plugins "calendar" and "Todoist": a short insert at the next song boundary shortly before
lead_min = 15               # an event or a task with a time starts. Read only; the links and tokens stay in this file
# All-day calendar events: host mentions up to 3 times/day, at least 4 hours apart (requires Host links).
# DESCRIPTION can supply a short story through the local editor; without it the host just names the event.
poll_minutes = 5            # how often the calendar / Todoist are read
calendar_url = ""           # the calendar's secret address in iCal format (Google Calendar: calendar settings ->
                            # "Secret address in iCal format"). Anyone with the link can read the calendar: keep it private
todoist_token = ""          # Todoist personal API token (Settings -> Integrations -> Developer)

[weather]                  # Open-Meteo: a short host forecast at local 00:00, 03:00, ...
timezone = "Asia/Ho_Chi_Minh"
city = ""                  # e.g. "Da Lat"; set a city to enable the Weather integration, no API key needed

[jingles]                   # a file or a folder (one file from it per news package); relative to the project
stinger_in = "assets/stinger_in.wav"
stinger_out = "assets/stinger_out.wav"
stinger_out_max_sec = 4.0
bed = "assets/bed.wav"
separator = "assets/separator.wav"  # between meaning blocks: repositories, digest sections
station_id = "assets/stinger_out.wav"  # unused until a spoken ID and interval are configured
github_intro = ""           # no separate section recording: use the normal intro and a spoken topic
reminder = "assets/separator.wav"
horizon_intro = ""

[editor]                    # used only to retell GitHub descriptions in Russian; never for digests
llm_enabled = true
base_url = "http://127.0.0.1:11434"   # local Ollama
model = "qwen3.5:4b"
provider = "ollama"         # ollama (the base_url/model above), openai, openrouter, deepseek or xai; set on the Settings tab
openai_base_url = "https://api.openai.com/v1"   # any OpenAI-compatible endpoint (OpenRouter, Groq, LM Studio...)
openai_model = "gpt-4.1-mini"
openai_api_key = ""         # or the OPENAI_API_KEY environment variable; the key stays in this file
openrouter_model = "openai/gpt-4.1-mini"  # the key comes from "Sign in with OpenRouter" on the Settings tab
deepseek_model = "deepseek-flash"   # DeepSeek and xAI (Grok): the API key is pasted on the Settings tab
xai_model = "grok-4.3"
max_llm_calls_per_session = 16  # per rolling hour (the name is kept for old configs); answers are cached

[voice]
base_url = "http://127.0.0.1:3900"
voice_id = "default"        # built-in voice, shaped by `instruct` (voice design); not a recorded profile
language = "ru"
speed = 1.1                 # 0.25..4.0
instruct = "male, young adult, moderate pitch"
seed = 42                   # fixed seed keeps the designed voice the same in every clip
engine = "voicestudio"     # "voicestudio" (OmniVoice, a voice by description) or "qwen3" (Qwen3-TTS cloning
                            # voices/host.wav + .txt in the data folder, via mlx-audio); switched on the Settings tab
mlx_audio_dir = "mlx-audio" # relative to the data folder: a venv with mlx-audio[server] (.venv/bin/mlx_audio.server),
                            # the radio starts it. Install: uv venv .venv && uv pip install "mlx-audio[server]==0.6.1"
mlx_audio_url = "http://127.0.0.1:3901"
qwen_model = "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit"  # as good as bf16, twice as fast, less memory (A/B test)
cohost_instruct = "female, young adult, moderate pitch"  # plugin "Two hosts": the second designed voice
cohost_seed = 7            # picked by ear from 7 / 21 / 99
chars_per_sec = 14.0        # speech-length estimate, measured on this voice
server_dir = "~/Library/Application Support/com.debpalash.omnivoice-studio/project"  # plugin "VoiceStudio": the
                            # radio starts the voice server from here (installed by the VoiceStudio app), without the app
"""


def audio_presets() -> dict:
    try:
        selected = (DATA_DIR / "audio-preset.txt").read_text().strip()
    except FileNotFoundError:
        selected = "base"
    return {"selected": selected, "personal_available": (DATA_DIR / "presets/personal.toml").is_file()}


def select_audio_preset(name: str) -> None:
    load(audio_preset=name)  # validate every sound before changing the selection
    tmp = DATA_DIR / "audio-preset.tmp"
    tmp.write_text(name + "\n")
    tmp.replace(DATA_DIR / "audio-preset.txt")


def load(*, audio_preset: str | None = None) -> dict:
    path = DATA_DIR / "config.toml"
    if not path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
    path.chmod(0o600)  # it holds the Todoist token, the calendar's secret link, maybe an API key
    cfg = tomllib.loads(DEFAULT_CONFIG)
    for section, values in tomllib.loads(path.read_text()).items():
        cfg.setdefault(section, {}).update(values)
    preset = audio_presets()["selected"] if audio_preset is None else audio_preset
    if preset not in ("base", "personal"):
        raise ValueError("unknown audio preset")
    if preset == "personal":
        profile = DATA_DIR / "presets/personal.toml"
        if not profile.is_file():
            raise ValueError("Personal audio preset is not installed")
        for section, values in tomllib.loads(profile.read_text()).items():
            cfg.setdefault(section, {}).update(values)
    cfg["music"]["path"] = Path(cfg["music"]["path"]).expanduser()
    for k in ("stinger_in", "stinger_out", "bed", "separator", "station_id", "reminder", "github_intro", "horizon_intro"):
        if k.endswith("_intro") and not cfg["jingles"].get(k):
            cfg["jingles"][k] = []
            continue
        p = PKG_DIR.parent / Path(cfg["jingles"][k]).expanduser()
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXTS) if p.is_dir() else [p]
        if not files or not files[0].exists():
            raise ValueError(f"no audio for jingles.{k}: {p}")
        cfg["jingles"][k] = files
    b = cfg["broadcast"]
    # The spec's 300 s is the default; the settings page may raise it (settings.py) when breaks are made longer.
    if not 0 < b["max_spoken_seconds_per_rolling_hour"] <= 900:
        raise ValueError("max_spoken_seconds_per_rolling_hour must be in 1..900")
    if cfg["voice"]["engine"] not in ("voicestudio", "qwen3"):
        raise ValueError("voice.engine must be voicestudio or qwen3")
    if cfg["editor"]["provider"] not in ("ollama", "openai", "openrouter", "deepseek", "xai"):
        raise ValueError("editor.provider must be ollama, openai, openrouter, deepseek or xai")  # else a blank page
    if not 0 < cfg["sources"]["max_age_hours"] <= 24:
        raise ValueError("max_age_hours must be in 1..24")
    from .public_news import names
    for source, key in (("reddit", "communities"), ("telegram", "channels")):
        names(cfg["sources"][source][key], source)
    for d in ("inbox", "cache/tts", "cache/packages", "cache/scripts", "cache/edits", "logs", "run", "db"):
        (DATA_DIR / d).mkdir(parents=True, exist_ok=True)
    return cfg
