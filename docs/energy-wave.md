# Energy wave (experiment)

Experiments → **Energy wave** follows a continuous energy target across local music, independently of sets and queue size.
It preserves unplayed-track priority, existing song annotations, voice slots and manual replays.
Every energy level, including 4, participates in weighted selection; smooth transitions, genre tags,
similar timbre and tracks heard less recently are preferred.
No new AI requests, music downloads or audio assets are needed.

## Offline preparation

Use Python 3.13 in an isolated environment; the running radio needs no additional Python dependencies:

```sh
python3.13 -m venv .venv/energy
.venv/energy/bin/python -m pip install -r tools/energy-requirements.txt
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/energy/bin/python -m radio.energy analyze
python3 -m radio.energy report
```

Analysis decodes the middle minute of each track with FFmpeg. Librosa measures BPM, mean onset strength
and eight timbre coefficients. Relative ranks (40% tempo, 60% onset activity) produce energy quintiles.
Up to five timbre clusters provide sound groups for untagged tracks. These groups are **not recognized genres**;
genre tags are normalized to broad families (for example trap/rap → hip-hop), and manual labels take precedence.
Long production descriptions in genre tags use the first recognizable musical family. This is an approximate
label based on metadata, not an audio genre classifier. BPM can be estimated at half/double tempo, and perceived energy
can disagree with these measurements. Calibrate after listening.

The cache is `energy.json` in `RADIO_DATA_DIR` (the normal local station data directory by default).
Unchanged tracks reuse analysis; rerun the command after adding or replacing music. Changed or missing
files cannot use stale measurements. With fewer than two valid profiles, normal playback continues.

Override a rating or genre without importing librosa:

```sh
python3 -m radio.energy set --path '/absolute/path/to/song.mp3' --energy 4 --genre 'indie rock'
```

Overrides survive reanalysis. Reanalysis may move automatic ratings/groups as the library changes.

## On air

Restart the radio once after installing the code. The switch then applies live to the next selection;
disabling removes queued ordinary waves and leaves unplayed-track selections intact.
The current track finishes normally. YouTube Music selection is unchanged.

The target follows a smooth 30-minute cycle of music time, from energy 1 to 5 and back. Pauses and voice
do not advance it, and set boundaries do not reset it. Playback events account for the amount actually heard;
skipping counts only the part played. A short restart preserves this clock when the session is resumed.
Before a known voice slot, the target gently lowers during the last five minutes; the voice schedule stays unchanged.

Each candidate is scored at its projected midpoint using its cached duration. Energy distance, the transition
from the previous track, genre/timbre similarity and playback recency determine a positive weight. A deterministic
weighted draw uses the track reference and projected music time, so splitting the same plan into batches does not
reset random state, genre context or the energy phase. Very different energies remain possible, with lower weight.
The two-track queue reserve is a preparation buffer, not a wave or a set. Its length does not define the target.
Unknown track lengths use a three-minute estimate; prepared future selections can differ from later decisions after
a skip, a manual insert or a changed voice schedule. Actual elapsed time is corrected at every playback event.

The existing priority music queue runs ahead of the fallback playlist and behind news and manual inserts.
Unplayed tracks are selected first, using the same weighted selector. Tracks already heard during this process are excluded until the available local library
has completed a rotation; currently playing and manually queued songs are excluded too. Rotation state resets
with the daemon. Unknown tracks remain eligible, with neutral energy until analyzed.
News follows the existing clock, independently of track counts.

The experiment defaults off. It takes precedence over Shuffle while enabled. The normal playlist remains
available during preparation, queue failures, or source changes. `ENERGY_WAVE_QUEUED` records preparation;
only `MUSIC_STARTED` events prove actual playback.
