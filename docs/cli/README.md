# Yourwave CLI and shared API

Generated from the live argparse tree and shared operation registry. Regenerate with `python3 tools/generate_cli_docs.py --cli-anything-path /path/to/CLI-Anything`.

Use `bin/radio` or an installed `radio` symlink. Existing commands retain their syntax. Add `--json` anywhere before `--` for one machine-readable response; API v1 uses `ok` and optional `error`. Exit codes: 0 success, 1 operational failure, 2 usage error. Legacy import/paste/history/doctor JSON contains an `output` text report; new reads/mutations expose structured fields.

The API and CLI invoke radio/control.py. The HTTP trusted-Host/token checks and phone start/stop restrictions remain in radio/ui.py. `radio api serve --port 8765` serves the existing API without opening a browser; a running UI server already supplies it. The Unix-socket daemon remains the authority for live playback and planner validation.

## Command groups

| Command | Subcommands |
|---|---|
| `play` | `--help` |
| `pause` | `--help` |
| `resume` | `--help` |
| `stop` | `--help` |
| `next` | `--help` |
| `skip-news` | `--help` |
| `status` | `--help` |
| `history` | `--help` |
| `import` | `--help` |
| `paste` | `--help` |
| `source` | `--help` |
| `doctor` | `--help` |
| `ui` | `--help` |
| `capabilities` | `--help` |
| `snapshot` | `--help` |
| `timeline` | `--help` |
| `call` | `--help` |
| `plan` | `--help` |
| `settings` | `list`, `set`, `reset` |
| `plugins` | `list`, `set` |
| `experiments` | `list`, `set` |
| `audio` | `list`, `set` |
| `hooks` | `list`, `review` |
| `queue` | `list`, `remove` |
| `integrations` | `status`, `ytmusic-connect` |
| `auth` | `login`, `logout` |
| `keys` | `set`, `remove` |
| `sources` | `list`, `add`, `remove` |
| `replay` | `--help` |
| `reroll` | `--help` |
| `voice` | `--help` |
| `probe` | `--help` |
| `api` | `--help` |
| `repl` | `--help` |

## Complete API operation map

| Operation | Payload fields | CLI |
|---|---|---|
| `play` | — | `radio call play --data @payload.json` |
| `pause` | — | `radio call pause --data @payload.json` |
| `resume` | — | `radio call resume --data @payload.json` |
| `stop` | — | `radio call stop --data @payload.json` |
| `next` | — | `radio call next --data @payload.json` |
| `skip-news` | — | `radio call skip-news --data @payload.json` |
| `plugin` | `name`, `on` | `radio call plugin --data @payload.json` |
| `setting` | `key`, `value` | `radio call setting --data @payload.json` |
| `settings_reset` | `key` | `radio call settings_reset --data @payload.json` |
| `audio_preset` | `name` | `radio call audio_preset --data @payload.json` |
| `reddit_community` | `action`, `value` | `radio call reddit_community --data @payload.json` |
| `telegram_channel` | `action`, `value` | `radio call telegram_channel --data @payload.json` |
| `ytmusic_connect` | `cookie`, `playlist`, `browser` | `radio call ytmusic_connect --data @payload.json` |
| `voice_sample` | — | `radio call voice_sample --data @payload.json` |
| `llm_key` | `provider`, `key` | `radio call llm_key --data @payload.json` |
| `chatgpt_login` | — | `radio call chatgpt_login --data @payload.json` |
| `chatgpt_logout` | — | `radio call chatgpt_logout --data @payload.json` |
| `openrouter_login` | — | `radio call openrouter_login --data @payload.json` |
| `openrouter_logout` | — | `radio call openrouter_logout --data @payload.json` |
| `hook_review` | `id`, `approved` | `radio call hook_review --data @payload.json` |
| `reroll-next` | `ref` | `radio call reroll-next --data @payload.json` |
| `replay` | `kind`, `ref` | `radio call replay --data @payload.json` |
| `unqueue` | `kind`, `ref` | `radio call unqueue --data @payload.json` |
| `direction` | `action`, `kind`, `ref`, `text`, `at`, `id`, `token`, `changes`, `version`, `remember_undo` | `radio call direction --data @payload.json` |

Read operations: `status`, `snapshot`, `settings`, `plugins`, `experiments`, `timeline`, `queue`, `hooks`, `ytmusic`, `audio`, `capabilities`.

## Examples

```sh
radio --json status
radio --json settings list
radio --json experiments list
radio --json settings set music_shuffle false
radio --json experiments set gap_filler on
radio --json queue list
radio --json timeline --hours 12 --limit 120
radio --json plan catalog
radio --json plan insert --data '{"kind":"text","text":"Проверка связи","at":"2026-10-27T10:00:00+07:00"}'
radio --json probe --silent --seconds 5
radio repl
```

Planner save uses the existing changes/version/remember_undo fields; undo uses the returned token. Insert/time use at. Read a fresh status before each edit. Enable air_direction explicitly if planner editing is desired.

Secrets: use private files or stdin for keys, cookies and arbitrary sensitive payloads. No real credential mutation or sign-in is part of automated verification. Browser-only appearance, monitor volume and listening are reported in capabilities and are not remotely controlled by this CLI.

The probe accepts only an explicit local 127.0.0.1 HTTP URL, decodes 1–30 seconds with FFmpeg into temporary PCM, returns duration/RMS/peak and deletes the file. It returns failure for silence or decode failure. It can count as a listener; it does not start a stopped daemon or restore state automatically. When testing a paused station, inspect first, resume only if authorized, and restore pause afterward.

CLI-Anything adaptation: preserve the existing native argparse CLI instead of adding Click, a second project/session system or a PyPI package. The official upstream skill generator renders the agent guide from metadata extracted from the real parser. Validation results and external-service gaps are recorded in TEST.md.
