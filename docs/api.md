# Page API

The station page talks to the radio through a small HTTP API served by `radio ui` (`radio/ui.py`). Every theme
uses the same API, usually through the page core (`radio/web/core`, see [ui-themes.md](ui-themes.md)). The
command-line agent uses the same operations through `radio/control.py`.

## Where it listens

- `http://127.0.0.1:8765/`: the desktop page. Start and stop work here.
- `127.0.0.1:<listen_port>` (optional, e.g. 8766): the phone page through private Tailscale Serve
  ([mobile-listening.md](mobile-listening.md)). Same API and token; `play` and `stop` answer 403 there.

## Authentication

- `GET /` returns the selected theme's `index.html` with the per-run token in place of `__TOKEN__` and
  `true`/`false` in place of `__LOCAL_CONTROL__` (start and stop allowed on this port). The page is `no-store`.
- Every `/api/*` request and `/hook-preview.wav` needs the header `X-Radio-Token: <token>`; a missing or wrong
  token is 403. The token changes whenever `radio ui` restarts, so a page from an earlier run must reload.
- Every request needs a trusted `Host` header: `127.0.0.1:<port>` or `localhost:<port>`, or on the phone port the
  exact `[output] control_host`. Anything else is 403, so another site cannot reach the API through DNS rebinding.

## Reads

| Request | Answer |
| --- | --- |
| `GET /api/status` | The snapshot (below). 503 with `{ok: false, error}` when the radio cannot answer. |
| `GET /api/capabilities` | `{api_version, ok, reads, operations: {name: {fields}}, client_only, client_note, themes}` |

### Snapshot

`GET /api/status` answers `{ok, status, timeline, plugins, settings, ytmusic, audio_preset,
music_source_choice, hooks}`:

- `status`: the station now. `state` (`STARTING`, `PLAYING`, `PAUSED`, `STOPPING`, `STOPPED`), `auto_paused`,
  `paused_at`, `output` (`speakers`, `browser`, `both`), `current_kind`, `current_title`, `elapsed_sec`,
  `duration_sec`, `transition_sec`, `upcoming` (queued and next blocks, each with a `stage`), `upcoming_tracks`
  (the next songs, tentative after the first), `announced_tracks`, `next_reroll` (`ref`, `available`, `pending`,
  `error`), `replay_queue`, `broadcast_plan` (`rows`, `version`; Air direction only), `direction_timezone`,
  `music_source`, `music_source_state`, `ytmusic_active`, `catchup`, `spoken_last_hour_sec`, `speech_limit_sec`,
  `voicestudio`, `sources`, `last_error`, `news_ready`, `news_preparing`, `llm_calls`, `session`.
- `timeline`: what aired in the last 12 hours, newest first: `{id, type, kind, time, title, ref, replayable,
  replay, outcome, parts: [{title, text, description, links, source}]}`.
- `plugins`: `{name, title, description, on, section, available, unavailable_reason}`.
- `settings`: `{key, title, hint, value, default, min, max, step, unit, options, value_labels, multiline}`, and
  for the special rows `connection`, `active`, `labels`, `key_field`, `action` (AI provider) or `voice_state`
  (voice engine).
- `ytmusic`: `{connected, tracks, playlist}`; `audio_preset`: `{selected, personal_available}`.

Fields are added over time; a theme ignores what it does not know.

## Commands

`POST /api/cmd` with a JSON object `{cmd: <operation>, ...fields}` (at most 64 KiB). The answer is
`{ok: true, ...}` or `{ok: false, error}` with a sentence a listener can read. The operations and their fields
are listed by `/api/capabilities`:

| Operation | Fields | What it does |
| --- | --- | --- |
| `play`, `stop` | | Start or stop the station (desktop port only) |
| `pause`, `resume` | | Pause the station for every listener (speakers output) |
| `next`, `skip-news` | | Next track; skip the host edition on air |
| `reroll-next` | `ref` | Replace the next song with another random one |
| `replay`, `unqueue` | `kind`, `ref` | Play an aired song again at the next boundary; take it off the queue |
| `catchup` | | Prepare a "What I missed" host edition |
| `direction` | `action`, `kind`, `ref`, `text`, `at`, `id`, `token`, `changes`, `version`, `remember_undo` | Air direction: catalog, insert, save the plan, undo |
| `plugin` | `name`, `on` | Switch a feature |
| `setting`, `settings_reset` | `key`, `value` / `key` | Change a setting, or put it back to config.toml |
| `audio_preset` | `name` | Base or personal sounds (applies after a restart) |
| `reddit_community`, `telegram_channel` | `action` (`add`/`remove`), `value` | Edit public news sources |
| `ytmusic_connect` | `cookie`, `playlist`, `browser` | Connect YouTube Music |
| `llm_key` | `provider`, `key` | Save or remove an API key (never sent back) |
| `chatgpt_login`, `openrouter_login` | | Answer `{url}`: open it in this tab to sign in |
| `chatgpt_logout`, `openrouter_logout` | | Sign out |
| `voice_sample` | | Answer `{url}` of a short sample in the selected voice |
| `hook_review` | `id`, `approved` | Approve or reject a track preview |

## Audio

- `GET /stream`: the live air as MP3, no length, no ranges, same origin as the page (Web Audio can analyze it).
  A client may join between frames. Listening counts as a listener for auto-pause.
- `GET /voice-sample.wav`: the last voice sample. `GET /hook-preview.wav?id=<id>` (token): a track preview clip.

## Page files

- `GET /ui/core/<file>` and `GET /ui/themes/<name>/<file>`: static files of known types (html, css, js, json,
  svg, png, woff2) from `radio/web`, never outside it.
- `GET /?theme=<name>` picks a theme for this page; otherwise `[ui] theme` in config.toml, else `terminal`.
  `/api/capabilities` lists the installed themes.
