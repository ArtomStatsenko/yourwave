---
name: "yourwave-cli"
description: "Operate the local Yourwave radio through its native CLI: inspect playback, settings, experiments, queue and planner; run silent stream diagnostics."
---

# yourwave-cli

Use the existing radio command and real station backend. Prefer CLI for state and mutations; use the browser to inspect visual appearance or complete user sign-in.

## Native installation and discovery

Run the repository's `bin/radio` from any directory, or install a symlink on PATH as `radio`. Python 3.11+ is required by the existing station; FFmpeg is required for probes. This is an extension of the native argparse CLI, not a separate PyPI package.

Start with `radio --json capabilities`, `radio --help` and group `--help`. `radio repl` enters the optional interactive shell. Commands use the current RADIO_DATA_DIR; tests must set it before importing modules.

## Command Groups

### Station and inspection

Native one-shot commands.

| Command | Description |
|---------|-------------|
| `play` | radio play --help |
| `pause` | radio pause --help |
| `resume` | radio resume --help |
| `stop` | radio stop --help |
| `next` | radio next --help |
| `skip-news` | radio skip-news --help |
| `status` | radio status --help |
| `history` | radio history --help |
| `import` | radio import --help |
| `paste` | radio paste --help |
| `source` | radio source --help |
| `doctor` | radio doctor --help |
| `ui` | radio ui --help |
| `capabilities` | radio capabilities --help |
| `snapshot` | radio snapshot --help |
| `timeline` | radio timeline --help |
| `call` | radio call --help |
| `plan` | radio plan --help |
| `replay` | radio replay --help |
| `reroll` | radio reroll --help |
| `voice` | radio voice --help |
| `probe` | radio probe --help |
| `api` | radio api --help |
| `repl` | radio repl --help |

### settings

Use radio settings --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio settings list --help |
| `set` | radio settings set --help |
| `reset` | radio settings reset --help |

### plugins

Use radio plugins --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio plugins list --help |
| `set` | radio plugins set --help |

### experiments

Use radio experiments --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio experiments list --help |
| `set` | radio experiments set --help |

### audio

Use radio audio --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio audio list --help |
| `set` | radio audio set --help |

### hooks

Use radio hooks --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio hooks list --help |
| `review` | radio hooks review --help |

### queue

Use radio queue --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio queue list --help |
| `remove` | radio queue remove --help |

### integrations

Use radio integrations --help for arguments.

| Command | Description |
|---------|-------------|
| `status` | radio integrations status --help |
| `ytmusic-connect` | radio integrations ytmusic-connect --help |

### auth

Use radio auth --help for arguments.

| Command | Description |
|---------|-------------|
| `login` | radio auth login --help |
| `logout` | radio auth logout --help |

### keys

Use radio keys --help for arguments.

| Command | Description |
|---------|-------------|
| `set` | radio keys set --help |
| `remove` | radio keys remove --help |

### sources

Use radio sources --help for arguments.

| Command | Description |
|---------|-------------|
| `list` | radio sources list --help |
| `add` | radio sources add --help |
| `remove` | radio sources remove --help |

## Examples

### Inspect before changing

No browser needed.

```bash
radio --json status
radio --json capabilities
radio --json settings list
radio --json queue list
```

### Edit and verify a setting

Reuse the same validation as the UI.

```bash
radio --json settings set music_shuffle false
radio --json settings list
```

### Silent actual-stream inspection

Requires the API/UI server and a playing stream. FFmpeg writes only temporary PCM, never to speakers.

```bash
radio --json probe --silent --seconds 5
```

## For AI Agents

When using this CLI programmatically:

1. **Always use `--json` flag** for parseable output
2. **Check return codes** - 0 for success, non-zero for errors
3. **Read the JSON error on stdout**; stderr is diagnostics only
4. **Use absolute paths** for all file operations

## Version

API v1
## Operational boundaries

- Exit codes: 0 success; 1 operation/backend failure; 2 invalid CLI usage. Status STOPPED is a successful read, not an error.
- `radio call OP --data @private.json` or `--data -` covers every existing API command; do not place credentials in argv. Prefer `keys set PROVIDER --file -` and `integrations ytmusic-connect --cookie-file -` for secrets.
- Use `plan catalog` and the live `status.broadcast_plan.version` before plan edits. Native undo tokens and optimistic versions remain authoritative; do not invent a separate CLI undo stack.
- Appearance, monitor volume and listening are browser-local preferences and are explicitly outside the station API. The CLI cannot claim to change them. A silent probe never opens a speaker device.
- Test live playback only when authorized. Preserve running/paused state and do not interrupt existing listeners. For isolated tests, use a temporary RADIO_DATA_DIR.
- Login commands return a URL and may write local OAuth state; completing provider sign-in remains a user browser step. Never send keys, cookies or credential-bearing errors into logs or commits.
- Generator provenance and adaptations: docs/cli/TEST.md. Full command reference: docs/cli/README.md. No automatic commit, push or publication is implied.
