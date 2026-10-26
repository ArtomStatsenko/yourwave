# Yourwave interface design

This document records interaction conventions for the existing compact radio UI. [PRODUCT.md](PRODUCT.md) defines the product model; [docs/style.md](docs/style.md) governs repository presentation. Preserve the current typography, palette, player and timeline when applying these conventions.

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
