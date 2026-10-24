# Base audio: rights and provenance

The Yourwave Base pack contains four edited excerpts from **Tech Live by Kevin MacLeod**, distributed separately under **CC BY 4.0**. The application code remains MIT.

> "Tech Live" Kevin MacLeod (incompetech.com)
> Licensed under Creative Commons: By Attribution 4.0 License
> https://creativecommons.org/licenses/by/4.0/

[Author](https://incompetech.com/music/royalty-free/index.html?isrc=USUAN1700030) · [Attribution FAQ](https://incompetech.com/music/royalty-free/faq.html) · [License text](LICENSE-CC-BY-4.0.txt).

Changes: beat-aligned excerpts at 124 BPM, normalization, peak limiting and fades; PCM S16LE stereo at 44.1 kHz. The bed is eight bars long, with 5 ms edge fades. The author is not affiliated with Yourwave and does not endorse it.

| File | Role | SHA-256 |
| --- | --- | --- |
| `bed.wav` | bed | `46eb91eaba6132d8059b73cf67577c4ea09ad3396e423e3efe5a4ffd57c2d27d` |
| `separator.wav` | separator | `cb9c37833e1116096d0d3fd4e67ceec082daee39e024b5eb9fd3202ea7ca6711` |
| `stinger_in.wav` | stinger in | `23adedf570f72066e7da5c4b098c5742d6a53a1599ce95ae6f089e3e5f9c2e70` |
| `stinger_out.wav` | stinger out | `c10271d6d6c6c1f1938db0c554f5954f6be1d8c7f06edef59a6b9b0a1d1906c7` |

## Installation

Audio is not stored in Git. [base-audio.json](base-audio.json) pins the archive version, URL, size and SHA-256, as well as each WAV's size and hash.

From the clone root, for a publicly accessible release:

```sh
python3 tools/install_base_audio.py
```

For a private repository, download with an authenticated GitHub CLI first:

```sh
gh release download base-audio-v1.0.0 --repo ArtomStatsenko/yourwave --pattern yourwave-base-audio-v1.0.0.zip --dir /tmp/yourwave-base-audio
python3 tools/install_base_audio.py /tmp/yourwave-base-audio/yourwave-base-audio-v1.0.0.zip
```

The downloaded ZIP also works offline. The installer checks pinned hashes, size, exact contents and WAV format before writing. Matching files are preserved; custom WAVs and symlinks are rejected. Personal presets are untouched. If a write fails, newly installed files are removed. Move customized Base sounds into Personal or back them up before installing.

## Rebuild

[Download the original MP3 from the author](https://incompetech.com/music/royalty-free/mp3-royaltyfree/Tech%20Live.mp3), then run:

```sh
python3 tools/build_base_audio.py "/path/to/Tech Live.mp3"
```

Source SHA-256: `3fee678a805937e736449fe98347bbf3497258f6387b5569e49c97bae4c1072e`.

The builder verifies the source before writing and checks duration, PCM format, loudness and peaks before replacing the files. The WAV hashes above were produced with FFmpeg 9.0.2; another version may produce different bytes. Exact cuts and processing are recorded in [audio-manifest.json](audio-manifest.json).

Keep the title, author, source/license links and descriptions of changes when redistributing. Credits are also shown in Settings → Audio preset. Personal presets retain their own terms. Previous unverified audio is excluded from Yourwave's Git history and Releases; historical metadata remains in the manifest.
