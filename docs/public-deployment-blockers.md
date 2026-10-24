# Base deployment status

Yourwave is a local macOS station. The target first-run scenario is: clone, install Base, connect a supported voice backend, add your music, listen in a browser, import a briefing, hear it at a song boundary, then stop and restart cleanly. Hosted deployment, Windows/Linux, and optional integrations are outside this baseline.

## Repository and audio

Yourwave has moved to a new independent private repository, preserving the cleaned development history. No audio binaries belong in Git. The four Base WAVs are edited excerpts from Tech Live by Kevin MacLeod under CC BY 4.0, distributed as a separate Release archive with credits and the license. Code and documentation remain MIT.

The former PersonalRadio repository remains private. Its server still returned a former unverified WAV by direct authenticated SHA after history rewriting; that was a limitation of the old repository, even though its fresh clone was clean. The new repository is not a fork. Its fresh GitHub clone has none of the 72 old audio blobs, and the former unverified WAV SHA returns HTTP 404 through the new repository API. This was verified on October 24, 2026. Do not push old unfiltered branches or tags.

The pinned installer checks archive size, SHA-256, exact contents, WAV hashes and format. It preserves matching Base files, refuses customized sounds and symlinks, leaves Personal presets alone, and rolls back newly written files on a write failure. [Installation and provenance](../assets/RIGHTS.md).

## Remaining first-run work

| Area | Evidence and remaining check |
| --- | --- |
| Voice backend | VoiceStudio is installed separately; `voice.server_dir` must contain a compatible backend and environment. Document and test one installation path on a new machine. |
| Dependencies | Python 3.11+, Liquidsoap and FFmpeg are required; a full supported combination with the voice backend has not been established on another machine. |
| End-to-end acceptance | Test music → browser listening → imported briefing → speech between songs → pause/resume → stop → restart, without reusing an existing Application Support directory. |

## Verification scope

The renamed Yourwave code, including the latest compact-timeline-tabs commit, passed all 251 tests on this Mac. A fresh GitHub clone passed 22 additional checks after installing the archive downloaded back from its new Release; the pinned SHA-256 matched. Primary documentation, issue/PR templates, repository description and Release notes are in English. The base WAVs were rebuilt byte-for-byte with FFmpeg 9.0.2; duration, format, loudness, and peaks were checked.

These checks do not prove a new-machine VoiceStudio setup, human listening quality, or complete secret/licensing clearance for optional user content. Existing historical research and audio audits under `docs/` may be in Russian. [Third-party notices](../THIRD_PARTY_NOTICES.md).
