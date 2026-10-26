"""Generate CLI reference and agent skill using CLI-Anything's upstream skill generator.

Pass --cli-anything-path to a checkout of HKUDS/CLI-Anything.
No station/provider calls, installation, publishing or user config changes occur here.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from radio import control
from radio.__main__ import build_parser


def groups(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                if name.startswith('_'):
                    continue
                children = [item for a in child._actions if isinstance(a, argparse._SubParsersAction) for item in a.choices]
                yield name, child, children


def generate(upstream):
    path = upstream / 'cli-anything-plugin/skill_generator.py'
    spec = importlib.util.spec_from_file_location('yourwave_upstream_skill_generator', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    commands = list(groups(build_parser()))
    metadata_groups = [module.CommandGroup(name='Station and inspection', description='Native one-shot commands.',
        commands=[module.CommandInfo(name=name, description=parser.prog + ' --help') for name, parser, children in commands if not children])]
    metadata_groups += [module.CommandGroup(name=name, description='Use radio ' + name + ' --help for arguments.',
        commands=[module.CommandInfo(name=child, description='radio ' + name + ' ' + child + ' --help') for child in children]) for name, parser, children in commands if children]
    metadata = module.SkillMetadata(
        skill_name='yourwave-cli', software_name='yourwave', version='API v1',
        skill_description='Operate the local Yourwave radio through its native CLI: inspect playback, settings, experiments, queue and planner; run silent stream diagnostics.',
        skill_intro='Use the existing radio command and real station backend. Prefer CLI for state and mutations; use the browser to inspect visual appearance or complete user sign-in.',
        command_groups=metadata_groups,
        examples=[module.Example(title='Inspect before changing', description='No browser needed.', code='radio --json status\nradio --json capabilities\nradio --json settings list\nradio --json queue list'),
                  module.Example(title='Edit and verify a setting', description='Reuse the same validation as the UI.', code='radio --json settings set music_shuffle false\nradio --json settings list'),
                  module.Example(title='Silent actual-stream inspection', description='Requires the API/UI server and a playing stream. FFmpeg writes only temporary PCM, never to speakers.', code='radio --json probe --silent --seconds 5')])
    content = module.generate_skill_md_simple(metadata)
    start = content.index('## Installation'); end = content.index('## Command Groups')
    content = content[:start] + '''## Native installation and discovery

Run the repository's `bin/radio` from any directory, or install a symlink on PATH as `radio`. Python 3.11+ is required by the existing station; FFmpeg is required for probes. This is an extension of the native argparse CLI, not a separate PyPI package.

Start with `radio --json capabilities`, `radio --help` and group `--help`. `radio repl` enters the optional interactive shell. Commands use the current RADIO_DATA_DIR; tests must set it before importing modules.

''' + content[end:]
    content = content.replace('3. **Parse stderr** for error messages on failure', '3. **Read the JSON error on stdout**; stderr is diagnostics only')
    content = content.replace('5. **Verify outputs exist** after export operations\n', '')
    content += '''
## Operational boundaries

- Exit codes: 0 success; 1 operation/backend failure; 2 invalid CLI usage. Status STOPPED is a successful read, not an error.
- `radio call OP --data @private.json` or `--data -` covers every existing API command; do not place credentials in argv. Prefer `keys set PROVIDER --file -` and `integrations ytmusic-connect --cookie-file -` for secrets.
- Use `plan catalog` and the live `status.broadcast_plan.version` before plan edits. Native undo tokens and optimistic versions remain authoritative; do not invent a separate CLI undo stack.
- Appearance, monitor volume and listening are browser-local preferences and are explicitly outside the station API. The CLI cannot claim to change them. A silent probe never opens a speaker device.
- Test live playback only when authorized. Preserve running/paused state and do not interrupt existing listeners. For isolated tests, use a temporary RADIO_DATA_DIR.
- Login commands return a URL and may write local OAuth state; completing provider sign-in remains a user browser step. Never send keys, cookies or credential-bearing errors into logs or commits.
- Generator provenance and adaptations: docs/cli/TEST.md. Full command reference: docs/cli/README.md. No automatic commit, push or publication is implied.
'''
    skill = ROOT / '.agents/skills/yourwave-cli/SKILL.md'
    skill.parent.mkdir(parents=True, exist_ok=True); skill.write_text(content)
    docs = ROOT / 'docs/cli'; docs.mkdir(exist_ok=True)
    schema = json.dumps(control.capabilities(), ensure_ascii=False, indent=2) + '\n'
    (docs / 'capabilities.json').write_text(schema)
    rows = ['# Yourwave CLI and shared API', '',
            'Generated from the live argparse tree and shared operation registry. Regenerate with `python3 tools/generate_cli_docs.py --cli-anything-path /path/to/CLI-Anything`.', '',
            'Use `bin/radio` or an installed `radio` symlink. Existing commands retain their syntax. Add `--json` anywhere before `--` for one machine-readable response; API v1 uses `ok` and optional `error`. Exit codes: 0 success, 1 operational failure, 2 usage error. Legacy import/paste/history/doctor JSON contains an `output` text report; new reads/mutations expose structured fields.', '',
            'The API and CLI invoke radio/control.py. The HTTP trusted-Host/token checks and phone start/stop restrictions remain in radio/ui.py. `radio api serve --port 8765` serves the existing API without opening a browser; a running UI server already supplies it. The Unix-socket daemon remains the authority for live playback and planner validation.', '',
            '## Command groups', '', '| Command | Subcommands |', '|---|---|']
    rows += [f'| `{name}` | '+(', '.join('`' + c + '`' for c in children) or '`--help`')+' |' for name, _, children in commands]
    rows += ['', '## Complete API operation map', '', '| Operation | Payload fields | CLI |', '|---|---|---|']
    rows += [f'| `{name}` | '+(', '.join('`' + f + '`' for f in fields) or '—')+f' | `radio call {name} --data @payload.json` |' for name, fields in control.OPERATIONS.items()]
    rows += ['', 'Read operations: ' + ', '.join('`' + name + '`' for name in control.READS) + '.', '',
             '## Examples', '', '```sh', 'radio --json status', 'radio --json settings list', 'radio --json experiments list',
             'radio --json settings set music_shuffle false', 'radio --json experiments set gap_filler on', 'radio --json queue list',
             'radio --json timeline --hours 12 --limit 120', 'radio --json plan catalog',
             'radio --json plan insert --data \'{"kind":"text","text":"Проверка связи","at":"2026-10-27T10:00:00+07:00"}\'',
             'radio --json probe --silent --seconds 5', 'radio repl', '```', '',
             'Planner save uses the existing changes/version/remember_undo fields; undo uses the returned token. Insert/time use at. Read a fresh status before each edit. Enable air_direction explicitly if planner editing is desired.', '',
             'Secrets: use private files or stdin for keys, cookies and arbitrary sensitive payloads. No real credential mutation or sign-in is part of automated verification. Browser-only appearance, monitor volume and listening are reported in capabilities and are not remotely controlled by this CLI.', '',
             'The probe accepts only an explicit local 127.0.0.1 HTTP URL, decodes 1–30 seconds with FFmpeg into temporary PCM, returns duration/RMS/peak and deletes the file. It returns failure for silence or decode failure. It can count as a listener; it does not start a stopped daemon or restore state automatically. When testing a paused station, inspect first, resume only if authorized, and restore pause afterward.', '',
             'CLI-Anything adaptation: preserve the existing native argparse CLI instead of adding Click, a second project/session system or a PyPI package. The official upstream skill generator renders the agent guide from metadata extracted from the real parser. Validation results and external-service gaps are recorded in TEST.md.']
    (docs / 'README.md').write_text('\n'.join(rows) + '\n')
    print('Generated native command reference, capabilities and agent skill with CLI-Anything.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cli-anything-path', type=Path, required=True)
    generate(parser.parse_args().cli_anything_path)
