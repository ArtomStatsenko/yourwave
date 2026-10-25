# PersonalRadio audit, 24 October 2026

What this repo does: PersonalRadio collects news and reminders, turns them into Russian speech, and plays them between songs through Liquidsoap. A local browser controls the station and shows its history. Assumed load: one person, one radio daemon, several background jobs and browser requests on one Mac.

## Fixed in this change

DeepSeek used the 200-token answer allowance for reasoning and returned no spoken text. The editor then stored an empty cache entry forever. Live comparison reproduced the failure; four new GitHub stories were collected after disabling DeepSeek thinking. Empty legacy entries now retry, empty or truncated chat replies raise an error, and rejected text uses an explicit `SKIP` cache entry. Valid existing cached stories remain usable.

The provider's supported switch is documented in [DeepSeek's thinking guide](https://api-docs.deepseek.com/guides/thinking_mode/).

## Findings fixed in the follow-up

1. **A file-write failure permanently loses a story** (`radio/content.py:189-203`).
   - **What this is:** `add_item` stores the story, writes its spoken script, and creates packages for playback. SQLite commits each statement separately.
   - **Problem:** If writing the script fails, the story remains in `items` with no package. GitHub and inbox collection then treat it as already imported. An isolated run with `Path.write_text` raising `OSError('disk full')` left one `NEW` item and zero packages. Public-news imports already protect this operation with a savepoint; GitHub, Horizon and inbox imports do not.
   - **Fix:** Put the item and package inserts inside a savepoint in `add_item` and roll it back on failure. Keep script files outside the database transaction's guarantees; leftover files can be overwritten on retry.
   - **If we skip it:** A full disk, permission error or interrupted write can silently remove a story from all future editions.

2. **Failed retellings appear as a healthy source with no news** (`radio/daemon.py:601-605`, `radio/daemon.py:758-776`).
   - **What this is:** The fetch job reports source health after collecting and retelling candidate stories.
   - **Problem:** `_take_github` catches every retelling error and returns `nothing new` when none succeeds. `_fetch_github` then records `FETCH_SUCCEEDED` and displays `ok`. The live failure diagnosed today had available new repositories, no new stories and a healthy source status; the full test run also reached the retelling-error path without failing the fetch.
   - **Fix:** Count failed retellings and expose a degraded result when candidates failed. Preserve successful stories from a partially successful batch. Apply the same rule to `_take_horizon`.
   - **If we skip it:** Another provider outage or exhausted answer budget can stop news while the source still looks healthy. The new editor guard prevents bad cache entries but does not fix this status.

## Scope and verification

Reviewed the README and contribution/run instructions, CLI startup, local HTTP controls, editor and provider proxy, source collection, item/package storage, scheduling, timeline and playback boundaries. Sampled authentication, reminders, music and speech code and their tests. No third-party Python dependency manifest or CI workflow was present in the main checkout; Python code uses the standard library and launches installed audio/model tools.

The regression tests first failed on the old code. They cover a legacy empty cache, the DeepSeek request switch, successful cache reuse, truncated/empty reply rejection, and explicit `SKIP` reuse. All 265 tests passed (`python3 -m unittest discover -s tests`, 174.908 seconds); the run emitted resource-cleanup warnings. `git diff --check` passed. The working source-to-script path collected four real GitHub stories after the fix; the restarted daemon collected four more. Its previous paused state was restored.

Verdict: The diagnosed news-generation failure and both findings above are fixed. Story registration now rolls back all item/package inserts if script creation fails, including inside a caller's savepoint. Retelling failures report a degraded source while keeping successful stories. Exhausting the editor request budget raises an error instead of appearing as an intentional skipped story.

Live audio verification: `github:humblemuzzu/dumbtv#1` reached `READY` through the real configured speech engine and audio renderer, with 15.2 seconds of speech and 22.5 seconds total duration. It was not played; the station stayed paused.

## Independent sources and blocks

GitHub Trending and new repositories now receive separate fetch attempts, source statuses and retry deadlines. A failure in either no longer prevents the other's check. Musical stories no longer wait for all news sources to answer or for news to disappear. Weather and all-day calendar material no longer require Host links. Import or music-story errors do not stop scheduling the other blocks.

The later music-priority request replaces the 30-minute new-track schedule: unheard tracks of the active local or YouTube Music library go ahead of repeats immediately, including a single remaining track. Local tracks use newest-file order; YouTube Music keeps playlist order. The current song finishes, and news/manual inserts retain priority. Switching sources clears pending priority tracks of the previous source. The old `new_every_sets` key is kept as a 0/off or positive/on setting. Age and minimum-count gates no longer apply. Unmeasured new files use the same neutral gain/no-timing fallback as the normal playlist. The queue writes a `NEW_TRACKS_QUEUED` history event.

Verification includes a real isolated Liquidsoap recording: with no news and Host links disabled, a newly selected song reached `MUSIC_STARTED`. Additional tests exercise partial file-write rollback and retry, nested savepoints, failed source attempts in both directions, partial successful batches, weather/calendar independence, source errors followed by scheduling, repeated ticks in one music interval and disabling only the new-track queue. No orphan `NEW` items without packages were found in the working database.

Final verification: all 275 tests passed in 186.317 seconds. The final source-cycle guard was also covered by 11 focused passing tests: the first source queueing an edition cannot suppress the second source in the same cycle. The pause/preparation-window test passed separately, and `git diff --check` passed. Existing resource-cleanup warnings remain in the test output.

Music-priority verification: all 29 music tests and 43 scheduling tests passed after the priority change. A real Liquidsoap test with a local stand-in for the YouTube resolver recorded an unheard remote track immediately after the current track, before a repeat. The full 281-test run initially hit an obsolete queue-order assertion (fixed and rerun) and a Reddit UI/validation mismatch while those files were changing in parallel; the Reddit UI test still returns HTTP 400 where it expects 200. The priority change does not edit Reddit validation. Live YouTube account playback was not exercised for this follow-up.

Not checked: This was a risk-focused audit, not a line-by-line review of every file. No human listening, real speaker-output check, fresh-machine installation, full OAuth renewal exercise, external dependency internals, license re-audit, or review of historical `.github-writing` snapshots. Pause, playback order, shared speech/AI budgets and the configured voice backend remain common station constraints; separate source failures do not lift those limits.
