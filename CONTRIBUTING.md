# Contributing to Yourwave

Check existing issues and [TODO.md](TODO.md) before starting work. Discuss substantial changes in an issue with a concrete use case and expected behavior.

## Setup

Follow the [quick start](README.md#quick-start), including the separate Base audio installation. Python 3.11+, FFmpeg, and Liquidsoap are required for audio checks.

Use a separate data directory so experiments do not alter your station:

```sh
RADIO_DATA_DIR="$HOME/.yourwave-dev" ./bin/radio ui --port 8766
```

Use the same `RADIO_DATA_DIR` for that station's CLI commands. If another station is running, configure a separate `output.stream_port` before starting playback.

## Issues and pull requests

- Describe reproduction steps, expected and actual behavior, the commit, macOS/Python/audio-tool versions, and the affected source or integration.
- Keep each PR focused on one problem. Reuse the existing structure; explain any new dependency.
- Add meaningful checks for new logic and update affected instructions.
- Report commands and results, including anything not checked. Include a screenshot for UI changes or a short non-private listening example for audio changes.
- Remove tokens, cookies, secret calendar links, personal events, and private paths from evidence. Do not attach your full configuration, database, or data directory.

## Checks

```sh
python3 -m unittest discover -s tests
git diff --check
```

Tests do not need a model or VoiceStudio, but some launch audio tools and need the installed Base WAVs. State any environmental limitation in the PR. Passing tests alone does not establish live audio quality.

For an explicitly configured voice check:

```sh
RADIO_DATA_DIR="$HOME/.yourwave-dev" ./bin/radio doctor
```

`doctor` contacts VoiceStudio and synthesizes a sample. Listen to changes in the audio path.

## License and materials

Code and documentation are [MIT](LICENSE). Audio and third-party materials have separate terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Provide the author, original source, and redistribution permission when adding such materials.
