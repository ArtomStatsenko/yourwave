import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from radio import config, control


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, target in [('DATA_DIR', root), ('RUN_DIR', root / 'run'), ('SOCK', root / 'run/radio.sock')]:
            patcher = patch.object(config, name, target)
            patcher.start(); self.addCleanup(patcher.stop)
        self.cfg = config.load()

    def test_invalid_command_shapes_do_not_mutate(self):
        for body in (None, [], {}, {'cmd': []}, {'cmd': 'missing'}, {'cmd': 'plugin', 'name': 'ytmusic', 'on': 'false'}):
            with self.subTest(body=body):
                result, code = control.execute(body)
                self.assertEqual(code, 400)
                self.assertFalse(result['ok'])
        self.assertFalse((config.DATA_DIR / 'plugins.json').exists())

    def test_setting_roundtrip_and_rejection_preserve_file(self):
        with patch.object(control.settings, 'listing', return_value=[]):
            result, code = control.execute({'cmd': 'setting', 'key': 'station_name', 'value': 'CLI Radio'})
            self.assertEqual(code, 200); self.assertTrue(result['ok'])
            path = config.DATA_DIR / 'settings.json'; before = path.read_bytes()
            self.assertEqual(json.loads(before)['station_name'], 'CLI Radio')
            result, code = control.execute({'cmd': 'setting', 'key': 'music_source', 'value': 'invalid'})
            self.assertEqual(code, 400); self.assertFalse(result['ok'])
            self.assertEqual(path.read_bytes(), before)

    def test_stopped_status_is_successful_inspection_and_mutation_fails(self):
        sender = Mock(return_value=None)
        self.assertEqual(control.read('status', sender=sender)['status']['state'], 'STOPPED')
        result, code = control.execute({'cmd': 'next'}, sender=sender)
        self.assertFalse(result['ok'])
        self.assertEqual(code, 200)  # Compatibility with the existing HTTP command endpoint.
        tracks = [{'ref': 'ytmusic:abc', 'title': 'Prepared song'}]
        replays = [{'kind': 'music', 'ref': 'track'}]
        queued = control.read('queue', sender=Mock(return_value={'ok': True, 'status': {
            'state': 'PLAYING', 'upcoming_tracks': tracks, 'replay_queue': replays, 'next_reroll': {'ref': 'track'}}}))
        self.assertEqual(queued['upcoming_tracks'], tracks)
        self.assertEqual(queued['replay_queue'], replays)
        self.assertEqual(queued['next_reroll'], {'ref': 'track'})

    def test_busy_daemon_is_not_reported_as_stopped(self):
        result = control.read('snapshot', sender=Mock(return_value={'ok': False, 'error': 'busy'}))
        self.assertFalse(result['ok']); self.assertEqual(result['error'], 'busy')

    def test_credentials_are_never_returned(self):
        with patch.object(control.editor, 'set_key') as save, patch.object(control.settings, 'listing', return_value=[]):
            result, _ = control.execute({'cmd': 'llm_key', 'provider': 'deepseek', 'key': 'private-value'})
            save.assert_called_once_with('deepseek', 'private-value')
            self.assertNotIn('private-value', json.dumps(result))

    def test_all_registered_operations_route_to_real_handler_boundaries(self):
        from contextlib import ExitStack
        sender = Mock(return_value={'ok': True, 'status': {'state': 'PAUSED'}})
        with ExitStack() as stack:
            for target, value in [('settings.listing', []), ('settings.set_setting', None), ('settings.reset', None),
                                  ('editor.set_key', None), ('ytmusic.connect', {'connected': True}),
                                  ('chatgpt.login_url', 'http://localhost/login'), ('openrouter.login_url', 'http://localhost/login'),
                                  ('chatgpt.sign_out', None), ('openrouter.sign_out', None), ('hooks.review', None),
                                  ('hooks.listing', []), ('config.select_audio_preset', None), ('config.audio_presets', {'selected': 'base'}),
                                  ('reddit.validate', ['python']), ('telegram.fetch', [])]:
                module, name = target.split('.')
                stack.enter_context(patch.object(getattr(control, module), name, return_value=value))
            voice = Mock(); voice.health.return_value = True
            stack.enter_context(patch.object(control.tts, 'make_voice', return_value=voice))
            body = {'name': 'ytmusic', 'on': True, 'key': 'station_name', 'value': 'python', 'provider': 'deepseek',
                    'cookie': 'private-cookie', 'id': 1, 'approved': True, 'kind': 'music', 'ref': 'track', 'action': 'add'}
            for name in control.OPERATIONS:
                with self.subTest(operation=name):
                    data = {**body, 'cmd': name}
                    if name == 'direction':
                        data['action'] = 'catalog'
                    result, code = control.execute(data, sender=sender, starter=sender)
                    self.assertEqual(code, 200)
                    self.assertTrue(result['ok'], result)
                    self.assertNotIn('private-cookie', json.dumps(result))

    def test_all_read_operations_have_inspectable_output(self):
        with patch.object(control.settings, 'listing', return_value=[]), patch.object(control.hooks, 'listing', return_value=[]):
            for name in control.READS:
                with self.subTest(read=name):
                    result = control.read(name, sender=Mock(return_value=None))
                    self.assertTrue(result['ok'])
                    json.dumps(result)

    def test_inspection_bounds(self):
        for hours, limit in [(0, 1), (169, 1), (12, 0), (12, 1001)]:
            with self.assertRaises(ValueError):
                control.read('timeline', hours=hours, limit=limit)
