# Third-party notices

[MIT](LICENSE) covers Yourwave source code and documentation. It does not relicense music, jingles, speech recordings, or other third-party materials.

## Base audio

The separately distributed Base pack contains four edited excerpts from **Tech Live by Kevin MacLeod (incompetech.com)** under **Creative Commons Attribution 4.0 International (CC BY 4.0)**.

> "Tech Live" Kevin MacLeod (incompetech.com)
> Licensed under Creative Commons: By Attribution 4.0 License
> https://creativecommons.org/licenses/by/4.0/

[Author's source page](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1700030) · [Full license](assets/LICENSE-CC-BY-4.0.txt) · [Files, hashes, and processing](assets/RIGHTS.md).

Changes: beat-aligned excerpts at 124 BPM, normalization, peak limiting, fades, PCM S16LE stereo at 44.1 kHz. Keep attribution and describe further modifications. Do not imply the author's endorsement. Credits are also displayed in Settings → Audio preset.

## Excluded local recordings

The historical audit of October 21, 2026 covered 52 recordings. Four unverified fallback WAVs have been replaced; the other 48 recordings remain local. Their provenance is retained as metadata in the [audio manifest](assets/audio-manifest.json) and [historical audit](docs/audio.md) (Russian).

- **32 Pixabay recordings:** excluded because standalone stock redistribution is restricted by [section 5 of the terms](https://pixabay.com/service/terms/).
- **One Mixkit effect:** excluded because its [Sound Effects Free License](https://mixkit.co/license/modal/sfxFree/) restricts distribution with source files, tools, and templates.
- **Six Suno generations:** excluded pending original downloads and a separate audio-license decision; the owner reported a paid subscription and copies obtained through a third-party downloader. [Suno terms](https://suno.com/terms).
- **Four former fallback WAVs:** replaced on October 24, 2026; provenance was unverified.
- **Nine mixed audio examples:** excluded because rights for all components have not been established.

These are distribution decisions for this project, not a blanket clearance for public broadcasting or for every component of a mixed recording.

Audio has been removed from the published Git history. Base is installed from a versioned Release archive with a pinned SHA-256, license, and credits. Do not push branches or tags from the old unfiltered history. The former repository and local backups remain private.

## Tools and external services

Yourwave uses separately installed Python, Liquidsoap, FFmpeg, VoiceStudio, Ollama, and optionally yt-dlp. Enabled integrations may contact GitHub, Horizon, iCal sources, Todoist, Open-Meteo, YouTube Music, LRCLIB, MusicBrainz, and Wikidata. Their software, services, and retrieved content retain their own licenses and terms.
