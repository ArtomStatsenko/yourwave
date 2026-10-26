# Yourwave

**Your music. Short briefings. A station of your own.**

```text
──┬────┬────┬────▼────┬────┬────┬──
              YOURWAVE
```

`macOS` · `Python 3.11+` · `Liquidsoap` · `FFmpeg`

[Quick start](#quick-start) · [On air](#on-air) · [Usage guide](docs/guide.md) · [Contributing](CONTRIBUTING.md)

Yourwave turns your music library into a personal radio station with Russian-speaking hosts, news breaks, and useful reminders. Listen in your browser, see what just played, and shape the next edition without leaving the music.

Built for **one station on a Mac**, with your own music and a separately installed speech backend. Speech and the default script editor run locally; optional integrations and selected cloud editors make network requests. The Python application uses the standard library.

## Quick start

### 1. Install the station

```sh
brew install python liquidsoap ffmpeg
git clone https://github.com/ArtomStatsenko/yourwave.git
cd yourwave
python3 tools/install_base_audio.py
mkdir -p ~/Music/Radio
```

Add MP3, M4A, FLAC, or WAV files to `~/Music/Radio`. The installer downloads four Base sounds and checks their pinned SHA-256 hashes; your music and recordings stay outside Git.

### 2. Prepare the voices and script editor

Install **VoiceStudio** separately for speech. For the default local script editor, install and run **Ollama**, then download the model:

```sh
ollama pull qwen3.5:4b
```

### 3. Open your station

```sh
./bin/radio ui
```

The UI opens at [localhost:8765](http://127.0.0.1:8765/) and creates your configuration on first launch. Set `voice.server_dir` in `config.toml` to your VoiceStudio backend directory if it differs from the default, then start the station and choose **Listen**.

Use `./bin/radio doctor` to check the tools and synthesize a voice sample. The [setup guide](docs/guide.md#quick-start) covers backend paths, offline installation, and configuration.

<details>
<summary>Where your station lives</summary>

Configuration, source connections, playback history, and caches live in `~/Library/Application Support/PersonalRadioClaude/`. The older directory name and `radio` command are retained for compatibility. Your music stays in the folder you select.

Use `RADIO_DATA_DIR` for a separate development station; see [Contributing](CONTRIBUTING.md#setup).

</details>

> [!NOTE]
> VoiceStudio is not bundled. A complete first run on another machine has not yet been verified; [deployment status](docs/public-deployment-blockers.md) records the remaining checks.

## On air

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/yourwave-dark.png">
  <img src="docs/images/yourwave-light.png" alt="Yourwave showing the current song, next track, and an expanded host edition in the broadcast timeline" width="960">
</picture>

*Demo data: the current song, the next track, and an expanded host edition. Light and dark themes follow your system.*

| Part of your station | What it does |
| --- | --- |
| Music | Local files or a connected YouTube Music playlist, with smooth transitions and local fallback |
| Hosts | Russian speech, an optional second host, and pronunciation overrides for foreign names |
| Editions | Main and short news opportunities at `:00` and `:30`, fitted around song boundaries |
| Reminders | Calendar and Todoist announcements, with details retained in the timeline |
| Timeline | What played, what comes next, and an editor for upcoming editions and inserts |
| Coming up | An optional experiment introducing two upcoming songs, with reviewable music hooks |

Editions depend on prepared content and the speech budget. Their displayed times are estimates until playback is determined; an unavailable source does not stop the music.

## Make it yours

- **Choose the stories.** GitHub, imported briefings, weather, calendar, Todoist, and optional public news sources. [Integrations](docs/guide.md#integrations).
- **Shape the sound.** Choose voices and a script provider, set the station name, and adjust speech limits. [Settings](docs/guide.md#settings).
- **Try an experiment.** Enable two hosts or Coming up when you want them. [Timeline and experiments](docs/guide.md#timeline-and-experiments).

**Listen / Pause** affects the current browser. **Stop radio** ends the shared station; starting and stopping it is controlled from the Mac. [Phone listening](docs/mobile-listening.md).

## At the terminal

```sh
./bin/radio status
./bin/radio next
./bin/radio history --last 10
```

Import a briefing, record an output, or inspect sources with the [command guide](docs/guide.md#commands).

## Inside the repository

| Path | Contents |
| --- | --- |
| [radio/](radio/) | Station process, source adapters, speech, playback, and browser UI |
| [tests/](tests/) | Isolated state, scheduling, UI, and audio-engine checks |
| [tools/](tools/) | Base audio installer and build tools |
| [docs/guide.md](docs/guide.md) | Setup, integrations, configuration, and everyday use |

## Development

Install Base and the audio tools, then run:

```sh
python3 -m unittest discover -s tests
git diff --check
```

The tests do not need VoiceStudio or an LLM; some use Liquidsoap and FFmpeg. See [CONTRIBUTING.md](CONTRIBUTING.md) for an isolated development setup and [TODO.md](TODO.md) for deferred work.

## License and credits

- **Code and documentation:** [MIT](LICENSE).
- **Base sounds:** edited from **Tech Live by Kevin MacLeod**, under **CC BY 4.0**; distributed separately as [Base audio v1.0.0](https://github.com/ArtomStatsenko/yourwave/releases/tag/base-audio-v1.0.0). [Credits and rebuild instructions](assets/RIGHTS.md).
- **English pronunciation data:** bundled CMUdict, under its [own license](assets/cmudict.LICENSE).

See [third-party notices](THIRD_PARTY_NOTICES.md) for attribution and distribution boundaries.

Detailed usage documentation is in English. Some historical research and audio-audit documents under `docs/` are in Russian.
