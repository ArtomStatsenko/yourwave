# Yourwave interface design

This document records interaction conventions for the existing compact radio UI. [PRODUCT.md](PRODUCT.md) defines the product model; [docs/style.md](docs/style.md) governs repository presentation. Preserve the current typography, player and timeline when applying these conventions; the appearance controls below govern palette changes.

## Settings reset

Group each host's name and character together: keep the name visible and place its character editor inside that name's existing Details disclosure, with its hint and field-specific Reset. Details starts closed and preserves its open state while editing or polling; avoid a second nested disclosure for the character.

Show only the current value. Omit `Default: …` labels and default-value previews throughout Settings, including characters, script providers, models and voices. When a value differs from its configured default, place a compact **Reset** button at the right of the field heading, beside the current slider value when present. Give it an accessible name identifying the field. Reset restores that field immediately and returns keyboard focus to it; the button disappears once the default is restored. Keep the existing Appearance section reset. Preserve hints, validation and error feedback.

## Inline confirmation

Use confirmation when an accidental click would discard something difficult to recover, miss a reminder, interrupt the station, or contradict a track announcement already heard on air. Routine playback and easily reversible edits should stay direct; prefer the existing one-step Undo where it can restore the result.

Replace the triggering control in its own row with **Confirm · Cancel**. Keep the affected track, reminder, provider or setting identifiable. Use a brief consequence hint when the label alone is insufficient, such as `Already announced on air` or `Stops the station for every listener`. Do not open a modal or use the browser's native confirm dialog for these local actions.

- Confirm uses the existing accent; Cancel uses the muted text color. Keep both as readable text buttons, with the existing focus ring and at least 44 px touch targets on coarse pointers.
- Preserve control height and avoid horizontal overflow. On narrow screens, titles may wrap as the pair takes more width than the original icon; fixed row height is not a guarantee of the current implementation.
- The first click only opens confirmation. Cancel and Escape close it without issuing a command and return focus to the initiating control when it still exists. Keyboard users can reach both buttons; Enter or Space activates only the focused button.
- Bind confirmation to the specific target and action, including any revision needed to detect a changed setting or schedule. A stale click must never apply to a replacement target. Server-side validation remains necessary.
- Status polling preserves the pending choice and keyboard focus while the same action remains valid. Changing the target, losing eligibility or leaving the relevant context closes it. Opening confirmation for another action cancels the previous pending choice.
- After Confirm, close the pair, show the existing loading state and prevent duplicate requests. On failure, show an error beside the action without claiming success or automatically retrying it.

### Current implementation and adoption

The announced-next reroll in [radio/ui.html](radio/ui.html) is the initial implementation: ordinary next rerolls immediately; an announced next requires Confirm; Cancel and Escape dismiss; a changed or unavailable next clears confirmation. Its pending replacement and error states reuse the existing reroll flow.

This convention is the target for other qualifying actions, not a claim that they have already been migrated. Reminder skipping currently has a second-click `Confirm skip` but lacks the full Cancel/Escape interaction. The [confirmation audit](docs/audits/2026-10-26-confirmations.md) records other gaps and actions that should remain direct.

## Appearance

Appearance lives at the top of Settings and belongs to the current browser, independently of the station and other devices. Offer System / Light / Dark and the Ember, Forest and Ocean palettes. Ember preserves the original default; System follows the device. Apply the saved preference before painting the page. If browser storage is unavailable, keep changes for the current tab and explain that they could not be saved.

Keep custom accent controls in a disclosure. Valid colours preview immediately; Apply saves, Cancel, Escape or closing the disclosure restores the saved appearance. Require at least 4.5:1 against both page and raised surfaces. An incompatible saved accent falls back to the chosen palette with an explicit status message and remains saved for compatible modes. Custom accents never recolour warning or success roles. Reset restores System + Ember + the theme accent.

Use the existing semantic CSS variables for text, surfaces, controls, focus, links, selection and visualization. Palette choices use native radio buttons, labelled previews and a selected outline. Keep typography, spacing, playback, timeline editing and the audio analyser unchanged.

### Calm and Terminal

Player style is a browser-local Appearance preference. Calm is the default and keeps the existing player. Terminal adds a compact ASCII transmitting mast, the configured station name, and a status line based on the displayed broadcast frame. MUSIC, HOST, WEATHER, REMINDER, STATION ID and PREVIEW describe the actual block; unknown content reads CONNECTING. Pause, automatic standby, startup, shutdown and off-air states stop the mast motion. Monitor status distinguishes station playback from listening on this device.

Keep the single decorative wave loop confined to the mast; stop it when the page is hidden and under prefers-reduced-motion. Collapse the identity while editing the program. Retain the existing audio-driven visualizer and title animation; no extra requests, scheduling changes or playback commands belong to this preference.

Timeline effect is a browser-local Appearance preference: Off, Fade, or Terminal line print (default). Animate only newly aired rows, preserve timeline order, and stay still during initial load, editing, hidden tabs and reduced motion. Reset restores Terminal line print.
