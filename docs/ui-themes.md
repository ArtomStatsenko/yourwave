# Page themes

The station page is a core without a look plus a theme. A theme decides only how things look and feel; what the
page knows, says and does comes from the core, so every theme behaves and speaks the same.

```text
radio/web/
  core/core.js     RadioCore: API client, 2 s poll, shared state, listening on this device
  core/model.js    RadioModel: what to show, computed from the snapshot, with its words
  themes/<name>/   index.html, theme.css, view.js (and any assets)
```

`radio ui` serves the folder at `/ui/` ([api.md](api.md)). A theme is picked with `[ui] theme` in config.toml
or `?theme=<name>` in the page address; `terminal` is the default and the reference.

## Writing a theme

1. Copy `radio/web/themes/terminal` to `radio/web/themes/<name>` (lowercase letters, digits, hyphens).
2. Keep these lines in `index.html`: in `<head>`, before anything is drawn,

   ```html
   <script src="/ui/core/core.js"></script>
   <script>RadioCore.followSavedTheme('<name>')</script>
   ```

   and at the end of `<body>`, in this order:

   ```html
   <script src="/ui/core/model.js"></script>
   <script src="/ui/themes/<name>/view.js"></script>
   <script>RadioTheme.mount(RadioCore.create({token:"__TOKEN__",localControl:"__LOCAL_CONTROL__"==="true",theme:"<name>"}),document.body);</script>
   ```

   The server fills in `__TOKEN__` and `__LOCAL_CONTROL__`. Load other files by absolute path
   (`/ui/themes/<name>/...`).
3. In `view.js`, define `RadioTheme.mount(core, root)`: build or find the markup, subscribe, then
   `core.start()`.
4. Open `http://127.0.0.1:8765/?theme=<name>`.

No inline `on...=""` handlers: a theme's functions live inside `mount()`, out of their reach. A test checks this.

### A new look on the terminal behavior

A theme that only changes the look can reuse the terminal theme's behavior instead of writing its own, as `lime`
does: its `index.html` keeps every element id the terminal script uses (arranged and styled its own way), loads
`/ui/themes/terminal/view.js` before its own `view.js`, and its `theme.css` starts with
`@import url("/ui/themes/terminal/theme.css")` for the editor's layout. The page's inline `appearance` object
replaces terminal's appearance settings: `formatTime(value)`, `mount()`, and `typing: false` to show a new title
at once instead of typing it. Its own `view.js` wraps the terminal `RadioTheme.mount` to add what the look needs
(lime: the spectrum bars; prism: the colour split moving with the sound, a rising title on a new track, a
settle on a new tab or switch, a still red split on an error). `tests/test_lime_theme.py` checks that no id the behavior uses is missing.

## What the core gives

`RadioCore.create(options)` returns `core`:

| Member | |
| --- | --- |
| `core.start()` | Poll every 2 s from now on (first answer at once) and load the capabilities |
| `core.subscribe(fn)` | `fn(core, change)` with `'snapshot'`, `'error'` (a poll failed, `core.error` says why), `'capabilities'`, `'listening'`. Returns an unsubscribe function |
| `core.snapshot` | The last good `/api/status` answer. A late answer never replaces a newer one; a failed poll keeps the last one |
| `core.running()` | The station is playing or paused |
| `core.heard(fallback)` | `{t, s}`: the status this device hears now (its buffer is a few seconds behind the station) |
| `core.command(name, fields)`, `core.commands.<name>(fields)` | A station command; resolves with `{ok, error, ...}` |
| `core.api(path, body)`, `core.fetch(path, init)` | A request with the page token (JSON, or anything) |
| `core.capabilities` | Operations, reads and installed themes |
| `core.localControl` | Start and stop belong to this page (false on the phone port) |
| `core.kinds` | What each kind of air entry is called |
| `core.themes()`, `core.setTheme(name)`, `core.themeNote` | The installed themes `[{name, title, current, modes, accent}]` (`modes`: the colour modes the theme offers, `accent`: it lets the listener pick an accent); switch this browser to one (remembered here, the page opens again in it); the note to show beside the picker |
| `RadioCore.followSavedTheme(name)` | In `<head>`: a page opened without `?theme=` goes once to the theme this browser chose |
| `RadioCore.contrast(a, b)`, `RadioCore.onFill(accent)`, `RadioCore.textAccent(accent, ground, dark)` | Colours as `#rrggbb`: the WCAG contrast ratio; the label colour on an accent fill (`#ffffff` or `#111111`, whichever reads better); the accent as text on a ground, moved toward black (light) or white (dark) in 2% steps until it reaches 4.5:1 |
| `core.audio` | Listening on this device: `listen()`, `unlisten()`, `listening`, `volume`, `setVolume(v)` (saved per browser), `lag()`, `catchUp()` (after next), `read(n)` → `{bands: 32 values, wave: n values}` or null, `media(status)` (Media Session), `onnext` (lock-screen Next) |

The stream watchdog and the Media Session handlers run inside the core.

## What the model gives

`RadioModel` turns a snapshot into what is shown, with no markup:

| Function | |
| --- | --- |
| `player(status, core.heard(status), {listening, localControl, busy, nextBusy, lag})` | Air line, title (and whether it is a track title), position, time text, progress, speech budget, voice line, and label, state and title of Listen, Start/Stop, Next |
| `timeline(snapshot, running)` | `entries` (each with `current`, `queued`, `kindLabel`, `text`, `expandable`), `shown` (entries before "Show earlier"), `activity`, `background` |
| `entryDetails(entry)` | An unfolded entry part by part: `title`, `segments` (text; a mention of a source carries its `url`), `sources`, `description`, `links` (with `label` and `stars`), `oldStars` |
| `upcoming(status, localRerollError)` | The next block: `next`, `title`, `pending` (a reroll still loading), `tags` (`announced`, `set`, `energy`, `genre`), `reroll` (the dice applies), `error` |
| `planGroups(rows, status, hours)`, `planRow(row)`, `planGapTime(rows, i)`, `planNow(status)` | The Air direction plan: hours to fold, each block's title, readiness, detail and allowed actions, insertion times |
| `settings(rows)` | Each setting with `type` (`switch`, `select`, `text`, `stepped`, `slider`), `valueText`, `defaultText`, `changed`; the AI provider, model and voice rows as blocks |
| `ai(providerRow)`, `keySaved`, `keyChecked`, `KEY_EMPTY` | AI summary, connection tone and what to do next; API key feedback |
| `voice(row, pending, sample)`, `sampleFeedback` | Voice engine summary and the sample's progress |
| `catchup(info, running, busy, message)`, `musicSourceLabel(...)`, `clockText(s)` | "What I missed", where the music comes from, `m:ss` |

Tones are `['ok'|'wait'|'bad', glyph]`. Times stay ISO strings or epoch milliseconds: the theme formats them.

## What a theme must keep

- The theme picker: Settings → Appearance, its first row "Theme": a `<select id="theme-select" aria-label="Theme">`
  of `core.themes()` titles (this theme selected) that calls `core.setTheme(name)`, and `core.themeNote` beside it in
  muted 16px text (`id="theme-note"`). A test checks every theme for it.

- Keyboard: every control reachable and usable with Tab, Enter, Space and arrows (the tabs and the plan editor
  use arrows), visible focus, focus kept on the same control across the 2 s redraw.
- Announcements: `aria-live="polite"` on the AI and Voice summaries, the key and sample feedback, and source
  feedback; `role="alert"` on a reroll error. Buttons whose text is an icon carry an `aria-label`.
- Motion: no marquee, typing or row animation with `prefers-reduced-motion: reduce`.
- Redraws: do not redraw a control the listener is typing in, dragging or holding open.
- Words: show the model's texts as they are, so every theme says the same thing.

Appearance preferences (palette, accent, player style, timeline effect, time format) belong to a theme; the
terminal theme keeps its own in this browser, among them the "CRT screen" switch (on by default; the old preset
values subtle, classic and heavy read as on): scan lines, glow, vignette and glass over the page, and on wide windows
with a mouse a barrel bend (an SVG displacement map on body, which then scrolls inside the screen). Phones get light
lines and a vignette only; it is off under `prefers-contrast: more`. A safety net for slow machines: once the bend
shows, the page times 120 frames; if the median frame takes over 22 ms it drops only the bend (lines, vignette, glare
and glow stay) for the rest of the browser session (`sessionStorage` `yourwave.crt-bend`, not the saved setting), and
does not turn it back on in that session. A theme whose `core.themes()` entry has `modes` or `accent` puts the
rows `id="appearance-mode-row"` (Mode) and `id="appearance-accent-row"` (Accent) after Theme, `hidden`; the shared
behavior shows them from those flags. Soft has both (Light, Dark, System; six accents and a custom one, saved in this
browser); lime, synthwave and prism have neither.

## Checking a theme

`python3 -m unittest discover -s tests` covers the core, the model and the terminal theme. Tests that run theme
code in node read it through `tests/ui_page.py` (`page_source`, `theme_source`, `core_source`).
