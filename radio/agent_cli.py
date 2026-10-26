"""Agent-facing command groups generated from the station's shared operation registry."""
import argparse
import array
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import wave

from . import config, control


def emit(result, json_mode=False):
    result = {'api_version': 1, **result}
    print(json.dumps(result, ensure_ascii=False, indent=None if json_mode else 2))
    return 0 if result.get('ok') else 1


class Parser(argparse.ArgumentParser):
    json_errors = False

    def error(self, message):
        if self.json_errors:
            emit({'ok': False, 'error': message}, True)
            self.exit(2)
        super().error(message)


def value(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


def payload(text):
    if text == '-':
        raw = sys.stdin.read(65537)
    elif text.startswith('@'):
        with Path(text[1:]).expanduser().open() as stream:
            raw = stream.read(65537)
    else:
        raw = text
    if len(raw.encode()) > 65536:
        raise ValueError('JSON payload exceeds 65536 bytes')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('Expected a JSON object')
    return data


def secret(path):
    if path == '-':
        result = sys.stdin.read(60001)
    else:
        with Path(path).expanduser().open() as stream:
            result = stream.read(60001)
    if len(result) > 60000:
        raise ValueError('Credential input is too large')
    return result.strip()


def probe(url, seconds):
    """Decode the real stream to temporary PCM; never open a speaker/audio device."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Probe accepts only a local http://127.0.0.1:PORT/path URL without credentials or query')
    if not 1 <= seconds <= 30:
        raise ValueError('Probe duration must be 1..30 seconds')
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise ValueError('Install FFmpeg to inspect the actual stream')
    with tempfile.TemporaryDirectory(prefix='radio-probe-') as directory:
        out = Path(directory) / 'probe.wav'
        proc = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-rw_timeout', str((seconds + 3) * 1000000),
                               '-i', url, '-t', str(seconds), '-ac', '1', '-ar', '8000', '-c:a', 'pcm_s16le', '-y', str(out)],
                              capture_output=True, timeout=seconds + 15)
        if proc.returncode:
            raise ValueError('Could not decode the local stream; check the API server and radio playback')
        with wave.open(str(out)) as audio:
            rate = audio.getframerate()
            samples = array.array('h', audio.readframes(audio.getnframes()))
            if sys.byteorder != 'little':
                samples.byteswap()
        if not samples:
            raise ValueError('The stream returned no decoded audio')
        rms = math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768
        peak = max(abs(x) for x in samples) / 32768
        return {'ok': rms > 0, 'silent_output': True, 'probe': {
            'duration_sec': round(len(samples) / rate, 3), 'sample_rate': rate, 'signal': rms > 0,
            'rms_dbfs': round(20 * math.log10(rms), 2) if rms else None,
            'peak_dbfs': round(20 * math.log10(peak), 2) if peak else None},
            **({'error': 'Decoded audio is silent'} if not rms else {})}


def run(args):
    try:
        if args.command == 'probe':
            result = probe(args.url, args.seconds)
        elif args.command == 'call':
            data = payload(args.data)
            if 'cmd' in data:
                raise ValueError('Pass the command name separately, without cmd in the payload')
            result, _ = control.execute({'cmd': args.operation, **data}, port=args.port)
        elif args.command == 'plan':
            result, _ = control.execute({'cmd': 'direction', **payload(args.data), 'action': args.action})
        elif args.command in ('settings', 'plugins', 'experiments', 'audio', 'hooks', 'queue', 'integrations', 'auth', 'keys', 'sources'):
            command, action = args.command, args.action
            if action in ('list', 'status'):
                name = {'integrations': 'ytmusic', 'sources': 'status'}.get(command, command)
                result = control.read(name)
            elif command == 'settings':
                data = {'cmd': 'setting', 'key': args.key, 'value': value(args.value)} if action == 'set' else {'cmd': 'settings_reset', 'key': args.key}
                result, _ = control.execute(data)
            elif command in ('plugins', 'experiments'):
                if command == 'experiments' and args.name not in control.plugins.EXPERIMENTS:
                    raise ValueError('Choose an experiment from experiments list')
                result, _ = control.execute({'cmd': 'plugin', 'name': args.name, 'on': args.state == 'on'})
            elif command == 'audio':
                result, _ = control.execute({'cmd': 'audio_preset', 'name': args.name})
            elif command == 'hooks':
                result, _ = control.execute({'cmd': 'hook_review', 'id': args.id, 'approved': args.decision == 'approve'})
            elif command == 'queue':
                result, _ = control.execute({'cmd': 'unqueue', 'kind': args.kind, 'ref': args.ref})
            elif command == 'integrations':
                result, _ = control.execute({'cmd': 'ytmusic_connect', 'playlist': args.playlist, 'browser': args.browser,
                                             'cookie': secret(args.cookie_file) if args.cookie_file else ''})
            elif command == 'auth':
                result, _ = control.execute({'cmd': args.service + '_' + action}, port=args.port)
            elif command == 'keys':
                result, _ = control.execute({'cmd': 'llm_key', 'provider': args.provider,
                                             'key': '' if action == 'remove' else secret(args.file)})
            else:
                result, _ = control.execute({'cmd': 'reddit_community' if args.source == 'reddit' else 'telegram_channel',
                                             'action': action, 'value': args.name})
        elif args.command == 'replay':
            result, _ = control.execute({'cmd': 'replay', 'kind': args.kind, 'ref': args.ref})
        elif args.command == 'reroll':
            result, _ = control.execute({'cmd': 'reroll-next', 'ref': args.ref})
        elif args.command == 'voice':
            result, _ = control.execute({'cmd': 'voice_sample'})
            if result.get('ok'):
                result['file'] = str(config.RUN_DIR / 'voice-sample.wav')
                result.pop('url', None)
        elif args.command == 'api':
            from . import ui
            server = ui.make_server(args.port)
            print('Yourwave API: http://127.0.0.1:' + str(server.server_port), file=sys.stderr)
            server.serve_forever()
            return 0
        elif args.command == 'repl':
            return repl(args.json)
        else:
            result = control.read(args.command, hours=getattr(args, 'hours', 12), limit=getattr(args, 'limit', 120))
    except ValueError as error:
        return emit({'ok': False, 'error': str(error)}, args.json)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        # Never echo third-party errors: they may include credential-bearing URLs or headers.
        return emit({'ok': False, 'error': 'Operation failed; check arguments, provider availability and local permissions.'}, args.json)
    return emit(result, args.json)


def repl(json_mode=False):
    from .__main__ import main
    while True:
        try:
            if json_mode:
                print('radio> ', end='', file=sys.stderr, flush=True)
                line = sys.stdin.readline()
                if not line:
                    return 0
            else:
                line = input('radio> ')
        except EOFError:
            return 0
        if line.strip() in ('exit', 'quit'):
            return 0
        if not line.strip():
            continue
        try:
            main((['--json'] if json_mode else []) + shlex.split(line))
        except (SystemExit, ValueError):
            continue


def register(sub):
    """Generate full API coverage plus ergonomic domain commands from one registry."""
    def command(name, **kw):
        parser = sub.add_parser(name, **kw)
        parser.set_defaults(fn=run)
        return parser
    for name in ('capabilities', 'snapshot'):
        command(name)
    timeline = command('timeline')
    timeline.add_argument('--hours', type=int, default=12)
    timeline.add_argument('--limit', type=int, default=120)
    call = command('call', help='invoke any shared API operation; secrets through --data @file or -')
    call.add_argument('operation', choices=sorted(control.OPERATIONS))
    call.add_argument('--data', default='{}', help='JSON object, @file or - for stdin')
    call.add_argument('--port', type=int, default=8765, help='OAuth callback port')
    plan = command('plan')
    plan.add_argument('action', choices=['catalog', 'insert', 'up', 'down', 'skip', 'time', 'reset', 'save', 'undo'])
    plan.add_argument('--data', default='{}', help='JSON object, @file or -; fields match the planner API')
    for name in ('settings', 'plugins', 'experiments', 'audio', 'hooks', 'queue', 'integrations', 'auth', 'keys', 'sources'):
        group = command(name).add_subparsers(dest='action', required=True)
        actions = {'settings': ['list', 'set', 'reset'], 'plugins': ['list', 'set'], 'experiments': ['list', 'set'],
                   'audio': ['list', 'set'], 'hooks': ['list', 'review'], 'queue': ['list', 'remove'],
                   'integrations': ['status', 'ytmusic-connect'], 'auth': ['login', 'logout'], 'keys': ['set', 'remove'],
                   'sources': ['list', 'add', 'remove']}[name]
        for action in actions:
            p = group.add_parser(action)
            if name == 'settings' and action != 'list':
                p.add_argument('key', **({'nargs': '?'} if action == 'reset' else {}))
                if action == 'set':
                    p.add_argument('value', help='JSON value or literal text')
            elif name in ('plugins', 'experiments') and action == 'set':
                p.add_argument('name'); p.add_argument('state', choices=['on', 'off'])
            elif name == 'audio' and action == 'set':
                p.add_argument('name', choices=['base', 'personal'])
            elif name == 'hooks' and action == 'review':
                p.add_argument('id', type=int); p.add_argument('decision', choices=['approve', 'reject'])
            elif name == 'queue' and action == 'remove':
                p.add_argument('kind'); p.add_argument('ref')
            elif name == 'integrations' and action == 'ytmusic-connect':
                p.add_argument('--playlist', default=''); p.add_argument('--cookie-file'); p.add_argument('--browser', default='', choices=['', 'chrome'])
            elif name == 'auth':
                p.add_argument('service', choices=['chatgpt', 'openrouter']); p.add_argument('--port', type=int, default=8765)
            elif name == 'keys':
                p.add_argument('provider', choices=['openai', 'openrouter', 'deepseek', 'xai'])
                if action == 'set':
                    p.add_argument('--file', required=True, help='private file or - for stdin; no keys in argv')
            elif name == 'sources' and action != 'list':
                p.add_argument('source', choices=['reddit', 'telegram']); p.add_argument('name')
    p = command('replay'); p.add_argument('kind'); p.add_argument('ref')
    p = command('reroll'); p.add_argument('ref')
    command('voice').add_argument('action', choices=['sample'])
    p = command('probe'); p.add_argument('--seconds', type=int, default=5)
    p.add_argument('--url', default='http://127.0.0.1:8765/stream')
    p.add_argument('--silent', action='store_true', help='always silent; accepted for explicit intent')
    command('api', help='serve the existing API without opening a browser').add_argument('action', choices=['serve'])
    # Port belongs to the server, not a separately configured second API implementation.
    sub.choices['api'].add_argument('--port', type=int, default=8765)
    command('repl', help='interactive shell using the same command handlers')
