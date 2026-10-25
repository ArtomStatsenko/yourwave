# Yourwave

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The primary user is the owner of this personal station, listening in the background and occasionally adjusting the upcoming broadcast. Personal use is the main scenario; a multi-user service is not an established requirement.

## Product Purpose

Play the user's music with short Russian-language host appearances, useful reminders and selected information. Listening should remain effortless; managing the station should require only occasional intervention.

## Operating Context

The station runs locally on macOS. Python, Liquidsoap and FFmpeg provide scheduling and audio playback; VoiceStudio and a local language model prepare speech. The browser UI provides listening, the timeline and settings. YouTube Music can supply music alongside the local library. Calendar and Todoist provide reminders.

The existing browser entry is `./bin/radio ui`, normally served on port 8765. The UI is in `radio/ui.html`, served by `radio/ui.py`. This is an existing application, not a greenfield framework decision.

## Capabilities and Constraints

### Confirmed product model

- A **host edition** is one section in the broadcast timeline: a single host appearance containing several segments.
- A **segment** is part of an edition: technology news, GitHub trends, weather, local holidays or brief information about tracks and artists. An edition need not contain every segment.
- A **source** supplies material for a segment; it is not itself an edition. Selected, configured subreddits are a future source, not a currently delivered integration.
- A **material item** is a specific story, forecast or fact selected for an edition.
- The edition schedule can exist before its material is known. Unknown content is represented by the segment name, without generating speculative text or making extra model requests.
- Moving or skipping a timeline edition acts on the entire edition. Its internal composition and order belong inside its expanded view.

The timeline presents host appearances as Host edition rows, with source segments inside a disclosure. News, weather and all-day calendar material use the shared edition assembler. Clock-bound weather may anchor an edition; urgent timed reminders retain their own insertion path. Music stories prepare independently of news availability.

Enabled blocks and news sources do not require another block or source to succeed. Each source keeps its own status and retry deadline. Unplayed tracks from the active local or YouTube Music library take priority over repeats, without a clock-slot wait or minimum batch size. News preparation does not cancel the remaining priority songs. Switching music sources clears only the previous source's pending priority songs. Host links add presentation, not permission to play weather, calendar material or new tracks. Pause, available material, playback order and speech limits remain shared station constraints.

Every news source passes a shared Russian editorial policy before its material can air. Keep original source scripts separate from approved spoken text, and reuse approval during retries and edition assembly. News detail is a live three-position Settings slider (Brief, Standard, Discussion) for newly prepared stories; discussion must remain grounded in the source. Timed reminders, forecasts and authored notices retain their exact facts and wording.

### Listening and editing

- Start and stop the station only from the local Mac UI, separately from playback. Listen/Pause connects or disconnects this device without starting the station; the phone has no station-power controls, and its API rejects station startup and shutdown.
- The normal Timeline shows one subdued next entry with only the next track or block name, alongside the current broadcast and history.
- Editing expands the future above the current row in one continuous timeline with one scrollbar, compacts the player, and subdues readable history. Keep the `now` label and icon on the current row, subdued like history while editing; no separate editor/history heading or divider. History accepts no dragged blocks.
- Match schedule row spacing to broadcast history; insertion controls overlay the row boundary without reserving blank space. Use an English `Insert` tab in a reserved right gutter, connected to the insertion line.
- Add a block from the gap where it belongs: reveal the control on hover or keyboard focus, keep it visible on touch devices, and open the insertion form inline with its time prefilled.
- Time moves upward: later blocks above nearer blocks. Initially show three hours grouped by hour, with the nearest hour open; reset disclosures on reopening. Music plays between scheduled blocks and is omitted from the future program; insertion remains available in those gaps.
- Apply each change immediately with one-step Undo; Done exits editing. Move editions by dragging, arrows or clicking the time. Show scheduled and expected times when they differ.
- Show five recent aired history entries initially, with Show earlier for the rest. Keep radio startup events in the collapsed Activity log; they never take up an aired history row.
- Use the existing planner's known information and approximate times; do not build a separate predictive scheduler merely to display the editor.
- Text preparation precedes voice preparation. Show one current stage: `text pending` → `voice pending` → `ready`; queuing is a later operational state.
- Account for collection, text generation, synthesis and retries. Scheduling a block for now does not guarantee immediate speech; ready audio must still join the broadcast at a suitable boundary.
- Weather follows real three-hour clock slots independently of station uptime. Spoken weather is short: city, temperature, cloud cover and rain.
- Reminders can be skipped, but require explicit confirmation. Preserve original task titles and descriptions in the UI. URLs must never be spoken by the host, wherever they appear.

### Configuration and content

- Integrations and Experiments contain feature flags and descriptions. Corresponding configuration belongs in Settings and appears only when its flag is enabled.
- Keep the Experiments tab available even when no experiments are present. Graduated features should not retain an experimental toggle.
- Preserve both the repository's base audio preset and the user's local preset. Personal music, jingles, credentials and account data remain local.
- Prefer reliable sources with simple integrations. Reuse cached and prepared material, minimize model tokens, and avoid model calls for schedule display and rearrangement.
- When there is no suitable news, the host may give a short, light music-related link; there is no need to announce the absence of news. Factual claims need supporting sources.

## Brand Commitments

Keep the Yourwave identity and the existing compact interface. The host speaks Russian; timeline block labels and configuration UI use the established English vocabulary. Russian source descriptions are valid. Do not rewrite original user content merely to normalize the interface.

Repository presentation follows the [terminal/ASCII style guide](docs/style.md): compact radio-scale art, plain Markdown, monospace labels, and real interface screenshots. Keep new public-facing copy in English; avoid decorative raster covers, badge walls, and animated banners.

## Evidence on Hand

- `README.md`: existing application, integrations and local operation.
- `radio/ui.html`: incumbent interface and interaction patterns.
- `docs/audits/2026-10-22-broadcast-structure.md`: captured review of the mismatch between separate blocks and a composed host edition.
- `docs/audio.md` and `assets/audio-manifest.json`: audio assets and provenance; do not infer redistribution rights for the user's personal collection.

## Product Principles

1. Music and effortless background listening come first.
2. Model host editions, their segments and their sources as distinct concepts.
3. Keep the default view compact; reveal detail and controls when requested.
4. Make preparation and timing truthful without exposing unnecessary implementation detail.
5. Prefer existing mechanisms, local ownership and low token use over speculative flexibility.

## Open Decisions

- How clock-bound weather joins a composed edition without losing its three-hour cadence.
- How urgent reminders relate to an edition versus a separate timely interruption.
- Which segment selection and ordering rules are defaults for future editions, and which edits apply only once.
- Product-specific accessibility targets have not been established; do not claim a completed accessibility audit.
