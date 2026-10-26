import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from radio import agent_cli, config, control
from radio.__main__ import build_parser, main


class CliTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for name, target in [('DATA_DIR', root), ('RUN_DIR', root / 'run'), ('SOCK', root / 'run/radio.sock')]:
            patcher = patch.object(config, name, target)
            patcher.start(); self.addCleanup(patcher.stop)
        config.load()

    def invoke(self, argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit:
                main(argv)
        return exit.exception.code, json.loads(output.getvalue())

    def test_json_usage_errors_are_machine_readable(self):
        code, data = self.invoke(['--json', 'settings', 'set'])
        self.assertEqual(code, 2); self.assertFalse(data['ok']); self.assertEqual(data['api_version'], 1)

    def test_discovery_covers_every_shared_operation(self):
        code, data = self.invoke(['capabilities', '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(set(data['operations']), set(control.OPERATIONS))
        parser = build_parser()
        for name in control.OPERATIONS:
            args = parser.parse_args(['call', name])
            self.assertEqual(args.operation, name)

    def test_stopped_status_json_is_success_not_failure(self):
        with patch('radio.__main__.send', return_value=None):
            code, data = self.invoke(['status', '--json'])
        self.assertEqual(code, 0); self.assertEqual(data['status']['state'], 'STOPPED')

    def test_bad_operation_returns_failure_exit(self):
        code, data = self.invoke(['--json', 'call', 'plugin', '--data', '{"name":"ytmusic","on":"false"}'])
        self.assertEqual(code, 1); self.assertFalse(data['ok'])

    def test_cli_routes_typed_values_to_shared_operation(self):
        with patch.object(control, 'execute', return_value=({'ok': True}, 200)) as call:
            code, _ = self.invoke(['--json', 'settings', 'set', 'music_shuffle', 'false'])
            self.assertEqual(code, 0)
            self.assertIs(call.call_args.args[0]['value'], False)
            self.invoke(['--json', 'settings', 'set', 'station_name', '--', '--json'])
            self.assertEqual(call.call_args.args[0]['value'], '--json')
        self.assertEqual(agent_cli.value('[1,2]'), [1, 2])
        self.assertEqual(agent_cli.value('Yourwave'), 'Yourwave')

    def test_bounded_private_payloads_from_file_and_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'payload.json'; path.write_text('{"key":"private"}')
            self.assertEqual(agent_cli.payload('@' + str(path)), {'key': 'private'})
        with patch('sys.stdin', io.StringIO('{}')):
            self.assertEqual(agent_cli.payload('-'), {})
        for bad in ['[]', 'not json', '{' + ' ' * 65536 + '}']:
            with self.assertRaises(ValueError):
                agent_cli.payload(bad)

    def test_credentials_have_no_argv_value_option(self):
        args = build_parser().parse_args(['keys', 'set', 'deepseek', '--file', '-'])
        self.assertEqual(args.file, '-')
        with patch('sys.stdin', io.StringIO('private-key\n')):
            self.assertEqual(agent_cli.secret('-'), 'private-key')

    def test_probe_rejects_remote_and_credential_urls_before_ffmpeg(self):
        for url in ['file:///etc/passwd', 'http://evil.test/stream', 'http://private@127.0.0.1:8765/stream', 'http://127.0.0.1/stream?token=private']:
            with self.assertRaises(ValueError):
                agent_cli.probe(url, 1)
        with self.assertRaises(ValueError):
            agent_cli.probe('http://127.0.0.1:8765/stream', 31)

    def test_failed_provider_message_does_not_echo_a_secret(self):
        with patch.object(control, 'execute', side_effect=OSError('Bearer private-key')):
            code, data = self.invoke(['--json', 'call', 'next'])
        self.assertEqual(code, 1); self.assertNotIn('private-key', json.dumps(data))

    def test_every_friendly_mutation_routes_to_the_shared_api(self):
        cases = [
            (['settings', 'reset', 'station_name'], 'settings_reset'),
            (['plugins', 'set', 'ytmusic', 'off'], 'plugin'),
            (['experiments', 'set', 'gap_filler', 'on'], 'plugin'),
            (['audio', 'set', 'base'], 'audio_preset'),
            (['hooks', 'review', '1', 'approve'], 'hook_review'),
            (['queue', 'remove', 'music', 'track'], 'unqueue'),
            (['integrations', 'ytmusic-connect', '--playlist', 'list'], 'ytmusic_connect'),
            (['auth', 'login', 'chatgpt'], 'chatgpt_login'),
            (['auth', 'logout', 'openrouter'], 'openrouter_logout'),
            (['keys', 'remove', 'deepseek'], 'llm_key'),
            (['sources', 'add', 'reddit', 'python'], 'reddit_community'),
            (['sources', 'remove', 'telegram', 'channel'], 'telegram_channel'),
            (['replay', 'music', 'track'], 'replay'), (['reroll', 'track'], 'reroll-next'),
            (['voice', 'sample'], 'voice_sample'),
        ]
        cases += [(['plan', action], 'direction') for action in ['catalog', 'insert', 'up', 'down', 'skip', 'time', 'reset', 'save', 'undo']]
        for argv, operation in cases:
            with self.subTest(command=argv), patch.object(control, 'execute', return_value=({'ok': True}, 200)) as call:
                code, _ = self.invoke(['--json', *argv])
                self.assertEqual(code, 0)
                self.assertEqual(call.call_args.args[0]['cmd'], operation)

    def test_every_friendly_read_and_generated_schema(self):
        cases = [(['settings', 'list'], 'settings'), (['plugins', 'list'], 'plugins'),
                 (['experiments', 'list'], 'experiments'), (['audio', 'list'], 'audio'),
                 (['hooks', 'list'], 'hooks'), (['queue', 'list'], 'queue'),
                 (['integrations', 'status'], 'ytmusic'), (['sources', 'list'], 'status'),
                 (['timeline'], 'timeline'), (['snapshot'], 'snapshot')]
        for argv, operation in cases:
            with self.subTest(command=argv), patch.object(control, 'read', return_value={'ok': True}) as read:
                code, _ = self.invoke(['--json', *argv])
                self.assertEqual(code, 0); self.assertEqual(read.call_args.args[0], operation)
        schema = Path(__file__).resolve().parents[1] / 'docs/cli/capabilities.json'
        self.assertEqual(json.loads(schema.read_text()), control.capabilities())

    def test_json_play_record_does_not_interrupt_running_station(self):
        with patch('radio.__main__.send', return_value={'ok': True, 'status': {'state': 'PLAYING'}}), patch('radio.__main__.start_daemon') as start:
            code, data = self.invoke(['--json', 'play', '--record', '/tmp/test.wav'])
        self.assertEqual(code, 1); start.assert_not_called()
