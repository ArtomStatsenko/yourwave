# Confirmation audit — 26 October 2026

Scope: production browser controls in `radio/ui.html`, with their handlers in `radio/ui.py`, `radio/daemon.py`, `radio/direction.py`, `radio/settings.py`, `radio/editor.py`, `radio/chatgpt.py` and `radio/openrouter.py`. This is a source review of the local working tree. Prototype screens, CLI workflows and external provider pages are outside scope. No live station actions or account changes were performed for this audit.

The interaction rule is in [DESIGN.md](../../DESIGN.md#inline-confirmation). The empty-Save key bug was subsequently fixed; other recommendations remain unimplemented.

## Prioritized gaps

| Priority | Action | Current evidence | Recommendation |
| --- | --- | --- | --- |
| High | Skip a reminder in the schedule | `directionView()` replaces × with `Confirm skip`; the click handler uses `planConfirm`, and `_save_broadcast_plan()` requires `skip_confirmed`. There is no explicit Cancel, and the editor's Escape handler only cancels dragging. | Adopt Confirm · Cancel in the same row, Escape dismissal, target/revision checks and focus preservation. Keep the existing server check. |
| High | Next while a reminder is on air | The Next control is enabled during playback regardless of `current_kind`. The daemon's `next` branch eventually skips the current request; only news receives `skip_flag`. A reminder can therefore be interrupted without the schedule's reminder confirmation. | Require confirmation for Next when it would interrupt a reminder. Bind it to the specific current playback occurrence; clear it if that occurrence ends. Ordinary music Next stays immediate. |
| High | Save key with an empty field | Fixed: Save key and Enter reject empty or whitespace-only input with field-level feedback. Only the explicit Remove key control opts into the empty-value removal path. | Regression coverage: `tests/test_key_ui.py`. Confirmation for explicit removal remains a separate recommendation. |
| High | Remove a saved API key | `[data-key-rm]` calls `saveKey(provider, '')` immediately; `editor.set_key()` deletes the stored value. The previous key is never returned to the page and has no Undo. | Confirm beside Remove key, naming the provider and explaining that a new paste will be needed to restore it. Do not put secret material in the confirmation state or logs. |
| Medium | Sign out of ChatGPT or OpenRouter | `[data-act]` dispatches logout directly. ChatGPT sign-out removes access/refresh data; OpenRouter sign-out unlinks its stored key. Neither has Undo. | Confirm in the provider action area and explain that reconnecting is required. Sign-in stays direct. |
| Medium | Stop radio | `#so` immediately issues `stop` from the local Mac UI; it stops the shared station rather than only this browser's listening. | Confirm beside Stop radio with `Stops the station for every listener`. Start radio and device Listen/Pause remain direct. Local-only authorization remains enforced independently. |

Reminder confirmation also needs consistent command coverage: the legacy `direction` action `skip` goes through `_direction_command()` and `direction.edit()`, which toggles the skipped state without the new save path's `skip_confirmed` check. The current browser editor uses `save`; verify remaining API/CLI callers before migrating or removing this legacy path. Adding a visual prompt alone would not cover it.

## Existing and low-friction actions

| Action | Current behavior | Decision |
| --- | --- | --- |
| Reroll announced next | Inline Confirm · Cancel, Escape, target-ref check, polling/focus preservation, unavailable-target reset and loading guard. | Keep. Confirmation is a UI interaction; the daemon still validates the expected next reference and availability. |
| Reroll ordinary next | Immediate reroll with pending/error feedback. | Keep direct. |
| Next during music | Immediate track switch; pending automatic material can be canceled/requeued by the daemon. | Keep direct. Clarify consequences in copy if needed rather than prompting for every skip. |
| Skip the current bulletin | Dedicated `skip bulletin` control, shown for news; daemon rejects it when no bulletin is on air. | Keep direct: interruption is the explicitly named action. |
| Move, retime or skip ordinary future blocks | Immediate save with a server-provided one-step Undo token. | Prefer Undo. Restoring a skipped reminder also remains direct. |
| Remove a queued replay | `queued ×` removes the queued request; the history row can enqueue it again. | Keep direct. |
| Remove a public Reddit/Telegram source | Immediate removal; the source name is public and can be added again. | No confirmation needed; an inline Undo would improve recovery if this becomes a frequent mistake. |
| Reset one ordinary setting | Reset removes that key's saved override and returns to config/default; the default is shown beside the control. No Undo is provided. | Keep direct for easily restored selections/sliders. If a future reset clears custom text or a collection that cannot be reconstructed, use Undo or confirmation for that specific action. |
| Reset all settings | `settings.reset(None)` and the API support it, but the current browser buttons send a specific key. | No current screen to change. Require confirmation if a Reset all control is introduced. |
| Music-source switch, audio preset, integration or experiment toggle | Explicit controls; selections/toggles can be changed back. Source switching may discard pending selection state, so reversing the setting does not promise an identical queue. | Keep direct with truthful current/target and apply-time feedback. Confirm only if an action gains an additional destructive effect. |
| Save/replace a key, connect a provider, review a hook, play a voice/hook sample | Explicitly named actions; hook decisions can be changed and samples have playback controls. | Keep direct. Replacing a saved key should explain replacement; the empty-Save deletion gap is listed above. |

## Limits and verification

No confirmations were added outside announced-next reroll. The recommendations do not promise that an interrupted or skipped reminder can still air before its deadline; Undo cannot reverse speech already missed or an announcement already heard.

The prior reroll implementation passed 30 focused UI/timeline/reroll tests and an isolated browser check at 390 and 1280 px. That browser check used demo state: at 390 px the title wrapped and increased the next row's height. It did not establish live station behavior, account flows or physical-phone interaction. The audit used source inspection; the later empty-Save fix adds a stub-API regression test covering clicks, Enter, whitespace, valid saves and explicit removal. The additional confirmation flows still need implementation and interaction tests.
