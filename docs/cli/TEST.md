# Yourwave CLI test plan

## Provenance and scope

CLI-Anything refine/test/validate workflow, upstream revision 34f519533bc175d2fe287ab8316b0dd99bb9cc43. Sources: https://github.com/HKUDS/CLI-Anything/tree/34f519533bc175d2fe287ab8316b0dd99bb9cc43/cli-anything-plugin .

Adapt the workflow to the existing `radio` argparse launcher, Unix socket and Python application. Do not add a second Click application, project/session model, renderer or publish to PyPI. The actual station remains the backend. Cover every existing HTTP command; distinguish browser-only preferences and authentication callbacks from headless operations.

## Planned unit coverage

`tests/test_control.py`: command discovery, malformed input, strict plugin booleans, settings validation, unknown commands, stopped/live daemon responses, connection failure, secrets never echoed, shared HTTP/CLI handlers (about 10 tests).

`tests/test_cli.py`: JSON envelopes and exit codes, generated command coverage, argparse errors, stopped status, typed values, bounded stdin/file input, unsafe probe URLs, decode errors (about 10 tests).

## Real-file and backend workflows

`tests/test_cli_e2e.py`: execute the actual bin/radio launcher from another directory with an isolated RADIO_DATA_DIR; read discovery, mutate a real settings file and read it back, toggle a real experiment and restore it, inspect real SQLite history/timeline, handle invalid commands without changing files. Use the real FFmpeg/ffprobe backend to inspect a generated audio stream through a local HTTP server, verify duration/PCM levels, and test silence separately (about 6 workflows). Missing required audio tools is a failure.

## Regression and live validation

Run all existing tests after the shared-handler extraction. Preserve HTTP trusted Host/token/remote start-stop guards and browser settings. Check live status and a short silent stream probe without speaker output; restore paused state when a test had to resume it. Authentication, API-key and third-party integration mutations use mocks or isolated files, never real user credentials. Document remaining live provider gaps rather than claim every external service was tested.

## Validation outputs

Generated capabilities/command reference and agent skill must match the command registry. Record commands, exact passed/failed counts and limitations below after verification. No public publishing or automatic commit/push.

## Results — 26 October 2026

- Full suite: **425 passed**, 248.661 seconds, exit 0. Complete output is in [test-results.txt](test-results.txt), with the home-directory prefix normalized. Existing ResourceWarnings remain visible; no failures or skipped backend checks were hidden.
- Initial full run exposed one outdated Terminal playback-controls fixture; it was fixed and the complete suite rerun successfully.
- Final focused checks after queue metadata, JSON argument-boundary and import cleanup: control 8 passed, CLI 12 passed, installed-command E2E 6 passed, HTTP API 9 passed. These overlap the full suite and are not additional unique tests.
- The installed command was resolved at ~/.local/bin/radio and executed from outside the checkout with isolated state. Real files, concurrent settings updates, experiment toggles, real FFmpeg PCM decoding and silence detection passed.
- A separate CLI → settings file → authenticated HTTP snapshot roundtrip passed with an isolated API server; unauthenticated discovery returned 403.
- Live agent-only workflow: status → resume → silent 3-second probe → pause. The real local stream produced 8 kHz PCM, RMS -16.82 dBFS and peak -4.77 dBFS. No audio device was opened. The station ended PAUSED with Local selected and no error.
- Official skill-creator quick_validate.py accepted the generated agent skill. Its PyYAML dependency was installed only in a temporary validation environment, not in Yourwave.
- Capability JSON was compared with the runtime registry; all 24 shared operations are accepted by the generated call command. Typed command groups and all read paths are covered by tests.

## Explicit validation boundaries

This extends the real native radio command; it is not a stock CLI-Anything Click/PyPI harness. Its package/session/renderer/REPL-skin checklist is not claimed as passed. The existing station owns playback, queue, planner undo and persistence. The upstream skill generator was executed with metadata from the actual argparse tree.

Third-party credentials, OAuth completion, provider mutations and real account voice generation were mocked or excluded from live verification. Browser-local appearance, volume and listening have no new remote command. Legacy import/paste/history/doctor JSON wraps their existing human-readable report in output. A silent probe can count as a listener and should be followed by restoration of the prior paused/running state when testing. No commit, push or publication was performed.
