# Clock-based PersonalRadio: broadcast practice and proposed decisions

Research date: 2026-10-23. Primary documentation only. This is a design proposal; no runtime behavior was changed or tested.

## What broadcast automation actually separates

1. **Clock, daily plan, playout.** Rivendell defines a broadcast clock as an hour template of timed events and lengths, reusable in a service's hourly grid. This is a useful model for morning/day/evening programming, rather than scheduling by how many songs a daemon has started. [Rivendell: Clocks](https://opsguide.rivendellaudio.org/html/sect.rdlogmanager.clocks.html)
2. **Scheduled time and estimated time.** Rivendell exposes the scheduler's intended times separately from predicted times calculated from actual content durations. A future song's precise time is therefore a prediction, while a timed event has an editorial target. [Rivendell: Time and Time Types](https://opsguide.rivendellaudio.org/html/sect.rdlogedit.time_and_time_types.html)
3. **Three timing policies.** Rivendell supports immediate timed starts, Make Next (let current audio finish, skip intervening items), and waiting a bounded period before forcing a start. These are explicit editorial choices, not one ambiguous notion of "scheduled". [Rivendell: Time and Time Types](https://opsguide.rivendellaudio.org/html/sect.rdlogedit.time_and_time_types.html)
4. **Audio engine has the final handoff.** Liquidsoap documents track-sensitive switching (wait for track boundary), time predicates, and non-track-sensitive fallback for immediate switching. Exact audio transitions cannot be guaranteed by code that only runs when requesting the next song. [Liquidsoap book](https://www.liquidsoap.info/book/book.pdf)
5. **Fallback should contain audio.** Liquidsoap identifies queues, ordinary playlists and remote media as potentially unavailable. Its quickstart recommends a known-valid local security source for radio; `mksafe` guarantees a source by substituting silence, which does not meet a no-dead-air product goal. [Liquidsoap Quickstart](https://www.liquidsoap.info/doc-2.3.3/quick_start.html)
6. **Connected is different from audible.** `blank.strip` can mark a silent source unavailable to trigger fallback; `blank.detect` supports reporting silence. Thresholds must suit the program so intentional quiet music does not falsely trigger. [Liquidsoap: Blank detection](https://www.liquidsoap.info/doc-2.3.3/blank.html)
7. **Restart policy is a separate decision.** Rivendell offers configurable startup actions and optional restart of the event playing at an unclean shutdown. Its docs do not imply that stale material should universally resume. [Rivendell: Managing Hosts](https://opsguide.rivendellaudio.org/html/sect.rdadmin.manage_hosts.html)
8. **Civil-time discontinuities need a policy.** Rivendell explicitly documents skipped and duplicated times around DST. Even if the current station timezone has no DST, time-zone changes and clock corrections must not duplicate a scheduled event. [Rivendell: Daylight Saving Time Considerations](https://opsguide.rivendellaudio.org/html/sect.rdcatch.daylight_saving_time_considerations.html)

## Recommended PersonalRadio decisions

The parameters below are proposed starting values for this station, not universal FM standards.

### Time is the source of truth; queues are disposable

- Keep a station timezone (initially `Asia/Ho_Chi_Minh`); expand schedule rules into concrete UTC occurrences with stable IDs, target time and expiry.
- Reconstruct the currently valid program from schedule + wall clock + durable playback facts. Daemon uptime, number of song starts, open browser tabs and Next presses never advance the program clock.
- Keep only the next playable items in the audio queue. Generation/downloading happens ahead of time but does not itself make a segment eligible to air.
- A scheduler restart at 14:23 joins the 14:23 program. It does not run the 14:00 ID, 14:05 weather and every missed link first.
- Independence of the program calendar does not mean audio can exist while every broadcaster is off. Continuous sound during host downtime requires a separately running broadcaster; it is a different infrastructure requirement.

### Default to soft time, expire instead of catching up

| Content | Proposed start policy | Missed/restart behavior |
| --- | --- | --- |
| Music | Fill the current program block; normal track boundaries | Start an appropriate fresh track; do not replay a vanished queue |
| Station ID | Opportunities around `:00` and `:30`; between songs, within 5 min | Expire; no catch-up; enforce at least 20 min since actual branding |
| Weather/news | Near the selected quarter/hour; after current song; within 5 min | Expire stale edition; next scheduled fresh edition wins |
| Calendar reminder | Explicit deadline/window from the reminder | If still useful, air once; otherwise expire and expose in history |
| Daypart change | New selection rules take effect now; current song may finish | Join current daypart immediately on restart |
| Deliberately exact program/live feed | Hard time, engine-level fade/switch; prepare in advance | Explicit join-in-progress or skip policy per program |

If a news/weather break already names the station, it satisfies that ID opportunity. Never play a separate ID directly adjacent to that break. A missed ID is less harmful than an extra one.

### Manual controls must have predictable editorial meaning

`Next song` cancels/skips music and starts another music item; it never pulls a waiting ID forward. Defer an eligible nonurgent break until a naturally completed song, expiring it if its window closes. Scheduled reminders keep their independently selected policy. Keep `Play break now` as an explicit separate action only where such a control already exists.

### Durable facts prevent duplicates

Record occurrence ID, intended time, actual audio start/end and outcome (`played`, `skipped`, `expired`, `failed`, `interrupted`). Update "last branding" only on an engine-confirmed playback fact, not generation/enqueue. Define a minimum audible duration before counting interrupted branding as delivered. Claiming an occurrence must be atomic; persisted claimed/started state plus engine reconciliation is needed because a database write and an external audio start cannot be one transaction. On uncertain recovery, prefer skipping noncritical branding to replaying it.

Use wall time for calendar eligibility and monotonic elapsed time for short playback/timeout intervals. On clock jumps, re-evaluate eligible occurrences and deduplicate by stable occurrence ID rather than testing whether the scheduler observed one exact second.

### Keep speaking and downloading off the critical audio path

Generate and validate speech before its window. If it misses its deadline or its file is invalid, continue music and record a failure. Liquidsoap should retain a verified local emergency music source when API, Python, network or TTS fails; recovery should return at an intentional boundary. Monitor output silence separately from queue/engine health, with thresholds calibrated against quiet tracks.

## Smallest useful acceptance proof

- Restart after 40 minutes offline: correct current daypart, zero stale IDs or expired forecasts.
- Ten rapid Next presses: zero automatic IDs, no schedule advancement.
- Broken TTS/download/API: local music continues; expired break never airs later.
- Duplicate callback/process recovery: a single scheduled ID cannot be audibly replayed from stale state.
- Slow or oversized song: soft break airs inside its window or expires; exact programs follow their explicit cut policy.
- UI displays target, estimated and actual times distinctly; the ordinary timeline remains human-readable rather than exposing program syntax.

Not researched here: music-format rules, loudness targets, detailed transition processing, licensing, and the current implementation's exact integration points. No claimed guarantee of uninterrupted audio during complete broadcaster/host downtime.
