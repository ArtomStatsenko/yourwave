# Yourwave

**Your music, short news breaks, and a station of your own.**

Yourwave builds a local radio broadcast from music sets, news, reminders, and a Russian-speaking host. Listen and manage the station in your browser; speech and the default language model run locally.

**macOS · Python 3.11+ · Liquidsoap · FFmpeg**

[Quick start](#quick-start) · [Integrations](#integrations) · [Settings](#settings) · [Commands](#commands) · [Development](#development) · [MIT](LICENSE)

This is a local macOS project, with no bundled application installer. You need your own music library and a separately installed voice backend. The four Base sounds are distributed as a versioned Release asset, outside Git, under CC BY 4.0. A complete first run on a new machine has not yet been verified; see [deployment status](docs/public-deployment-blockers.md).

## Quick start

### 1. Install the tools and Base sounds

```sh
brew install python liquidsoap ffmpeg
git clone https://github.com/ArtomStatsenko/yourwave.git
cd yourwave
python3 tools/install_base_audio.py
mkdir -p ~/Music/Radio
```

Add your music to `~/Music/Radio`. MP3, M4A, FLAC, and WAV are supported. The Python application uses the standard library; no `pip install` is needed.

The audio installer verifies a pinned SHA-256 and refuses to replace customized local sounds. While the repository is private, download the archive with an authenticated GitHub CLI first; the same archive also works offline:

```sh
gh release download base-audio-v1.0.0 --repo ArtomStatsenko/yourwave --pattern yourwave-base-audio-v1.0.0.zip --dir /tmp/yourwave-base-audio
python3 tools/install_base_audio.py /tmp/yourwave-base-audio/yourwave-base-audio-v1.0.0.zip
```

Base provides an intro, a speech bed, an outro, and a separator also used for reminders. Standalone station IDs are disabled by default; section names are spoken. [Audio credits and rebuild instructions](assets/RIGHTS.md).

### 2. Set up speech and the language model

| Component | Purpose | Default |
| --- | --- | --- |
| VoiceStudio | Host speech and bulletins | `http://127.0.0.1:3900`, voice `default` |
| Ollama | GitHub summaries and host links | `http://127.0.0.1:11434`, model `qwen3.5:4b` |

Install VoiceStudio separately. Yourwave starts its backend from `voice.server_dir`; set this path to your installation. It does not stop a server that was already running.

With Ollama installed, download the model and make sure its server is running:

```sh
ollama pull qwen3.5:4b
```

Model names and server addresses are configurable. Imported bulletins are read without an LLM rewrite.

### 3. Open the station

```sh
./bin/radio ui
```

On first launch, Yourwave creates a configuration pointing to the installed Base WAVs. Adjust `voice.server_dir` if needed, then start the station and enable browser listening at [localhost:8765](http://127.0.0.1:8765/).

To check the audio tools, music directory, and voice backend, including synthesis of a test phrase:

```sh
./bin/radio doctor
```

## How the broadcast works

- **Music sets:** local files or a connected YouTube Music playlist, with transitions and station identification.
- **Short host editions:** technology news, GitHub projects, weather, day notes, and music stories. Main editions are scheduled on the hour and shorter editions at half past, in the station timezone. They wait for a song boundary for up to five minutes.
- **Host links:** song announcements, time checks, reminders, and introductions between tracks. Speech length and hourly budgets are configurable.
- **Browser control:** listening, a broadcast timeline, settings, integrations, and experiments.

Browser output pauses after 30 seconds without listeners and resumes when one connects. The schedule continues to follow wall time; expired editions do not accumulate. Next switches music and cancels a pending automatic insert. Station IDs are eligible at hourly boundaries after 30 minutes without identification.

## Integrations

| Integration | Content | Setup |
| --- | --- | --- |
| GitHub Trending | Popular projects | Enabled by default; `sources.github_trending` |
| New GitHub repositories | Projects from the last week | Enabled by default; `sources.github_new` |
| Horizon | Prepared news briefings | Install separately, configure `sources.horizon`, enable the integration |
| iCal | Timed reminders and all-day mentions | Set `reminders.calendar_url`; enable Host links for all-day events |
| Todoist | Tasks with a due date and time | Set `reminders.todoist_token` |
| Open-Meteo | A short weather update at most every three hours | Set `weather.city`, enable Host links |
| YouTube Music | A connected playlist | Install `yt-dlp`, connect the playlist and cookies, enable the integration, restart playback |

For YouTube Music:

```sh
brew install yt-dlp
```

The station can fall back to local music when this source is unavailable. Cookies stay in the local data directory and may need renewal.

Todoist reminders require both a date and a time. Original descriptions and links remain in timeline details, while URLs are removed from spoken text. Reminders play at song boundaries; expired items are removed and time estimates are recalculated after a pause.

LRCLIB lyrics help choose speech windows around vocals. Optional Music stories use exact matches from MusicBrainz and Wikidata, with cached results and template-based host links. Enabling them sends artist and track names to those services.

Experimental public Reddit and Telegram news adapters are documented separately in [public-news.md](docs/public-news.md) (Russian).

## Settings

The existing data-directory name is retained for compatibility:

```text
~/Library/Application Support/PersonalRadioClaude/config.toml
```

Yourwave keeps the `radio` package and CLI names so existing commands continue to work. Existing station names and user settings are preserved; new configurations default to `Yourwave`.

In **Settings → Audio preset**, Base uses the separately installed sounds and `config.toml`; Personal overlays `presets/personal.toml` in the data directory. Personal stays unavailable until that file exists. Stop and restart playback after switching presets. Tokens and general configuration remain in `config.toml`.

Example values for the corresponding sections of an existing configuration:

```toml
[music]
path = "~/Music/Radio"

[broadcast]
station_name = "Yourwave"
music_set_minutes = 20
max_break_speech_sec = 120
max_spoken_seconds_per_rolling_hour = 300

[weather]
city = "Da Lat"
```

The station name can also be changed in Settings. Numeric broadcast settings and feature switches are saved separately from `config.toml`; most apply immediately, and feature descriptions identify restart requirements.

Use an isolated station directory for development, and use the same value for its UI and CLI commands:

```sh
RADIO_DATA_DIR="$HOME/.yourwave-dev" ./bin/radio ui --port 8766
```

### Timeline and experiments

**Air direction** enables Edit for future host editions and reminders. Music fills the gaps automatically. Edit works on a draft: drag, move, skip, reschedule, or add blocks, then Save together or Cancel. Preparation and queue changes can require reopening the latest plan. Skipping a reminder requires a second confirmation.

The normal timeline shows one muted next row above history. Future planning is visible inside Edit. A `~` time is an estimate; a scheduled time is a lower bound, since text and audio must be ready and playback waits for a track boundary. Saved edits survive restarts.

**Reroll next track** replaces the next track without interrupting the current one. **Host covers loading gaps** can fill YouTube Music loading pauses. Both are controlled through Experiments.

News text is prepared 15 minutes before a section and speech five minutes before it. During a pause, new preparation stops while existing material is retained. If preparation runs late, music continues. Next keeps the browser stream open; manual transitions take 100 ms and automatic transitions retain configured timing.

Weather follows `weather.timezone`, every three hours starting at midnight. Manual inserts do not shift that schedule; old missed windows are not replayed. Weather speech is checked against its text with the installed VoiceStudio Whisper environment and cached model; unavailable or mismatched checks suppress the insert.

### Data and external services

Configuration, credentials, cookies, audio caches, and broadcast history stay outside Git. Keep secret iCal links and cookies private. The UI binds to `127.0.0.1`. Enabled integrations make network requests; the default Ollama and VoiceStudio backends process text and speech locally.

## Commands

| Command | Action |
| --- | --- |
| `./bin/radio ui` | Open control and browser listening |
| `./bin/radio play` | Start playback |
| `./bin/radio pause` / `./bin/radio resume` | Pause / resume |
| `./bin/radio next` | Play the next item |
| `./bin/radio skip-news` | Skip news |
| `./bin/radio stop` | Stop the station |
| `./bin/radio status` | Show status |
| `./bin/radio history --last 20` | Show recent bulletins |
| `./bin/radio source list` | List sources |
| `./bin/radio doctor` | Check tools and synthesize a voice sample |

Record a new playback session:

```sh
./bin/radio play --record /tmp/yourwave.wav
```

### Import a briefing

Import Markdown or JSON, or use the macOS clipboard:

```sh
./bin/radio import examples/test-digest.md --published-at "$(date +%Y-%m-%dT%H:%M:%S%z)"
./bin/radio paste --topic "My briefing" --published-at "$(date +%Y-%m-%dT%H:%M:%S%z)"
```

Publication timestamps need a timezone. Briefings older than 24 hours are excluded. These examples assign the current time to test text; use the actual publication time for real news. File metadata can also supply the timestamp.

Yourwave removes markup and links from spoken text and splits long bulletins without rewriting their words. Input formats are defined in [`radio/content.py`](radio/content.py).

## Development

Install Base first, then run:

```sh
python3 -m unittest discover -s tests
git diff --check
```

Tests do not download audio or require VoiceStudio or an LLM, but some use FFmpeg, Liquidsoap, and the four installed WAVs. Automated checks do not prove live broadcast quality. See [CONTRIBUTING.md](CONTRIBUTING.md) and [TODO.md](TODO.md).

| Directory | Contents |
| --- | --- |
| [`radio/`](radio/) | Scheduler, integrations, audio, CLI, and browser UI |
| [`assets/`](assets/) | Audio manifest, licenses, pronunciation and gain settings; audio stays local |
| [`examples/`](examples/) | Example briefing; local audio previews are ignored |
| [`tests/`](tests/) | Checks and fixtures |
| [`launcher/`](launcher/) | Optional macOS AppleScript launcher |

## License

Code and documentation are [MIT](LICENSE). The four Base WAVs are edited excerpts from **Tech Live by Kevin MacLeod**, under **CC BY 4.0**; retain attribution, source, license, and descriptions of changes. They are distributed only through Releases. See [audio rights](assets/RIGHTS.md) and [third-party notices](THIRD_PARTY_NOTICES.md). Historical audio-audit and research documents under `docs/` may be in Russian.
