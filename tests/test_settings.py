import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, plugins, settings


class SavedSettingsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-settings-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root / 'run'),
                            ('SOCK', self.root / 'run/radio.sock')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.cfg = config.load()

    def test_non_object_json_uses_defaults_without_overwriting_the_file(self):
        defaults = settings.base(self.cfg)
        default_plugins = plugins.state(self.cfg)
        for name, apply in [('settings', lambda: settings.apply(self.cfg, defaults)),
                            ('plugins', lambda: plugins.state(self.cfg))]:
            for value in ([], ['entry'], None, True, 1, 'text'):
                with self.subTest(name=name, value=value):
                    path = self.root / (name + '.json')
                    original = json.dumps(value)
                    path.write_text(original)
                    result = apply()
                    if name == 'plugins':
                        self.assertEqual(result, default_plugins)
                    self.assertEqual(path.read_text(), original)
                    self.assertEqual(settings.base(self.cfg), defaults)

    def test_infinite_number_is_rejected_and_does_not_replace_saved_settings(self):
        settings.set_setting('music_set_minutes', 35)
        original = (self.root / 'settings.json').read_text()
        for value in (float('inf'), float('-inf'), '1e999'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'number is expected'):
                settings.set_setting('music_set_minutes', value)
        self.assertEqual((self.root / 'settings.json').read_text(), original)
        (self.root / 'settings.json').write_text('{"music_set_minutes": 1e999}')
        defaults = settings.base(self.cfg)
        settings.apply(self.cfg, defaults)
        self.assertEqual(settings.base(self.cfg), defaults)

    def test_voice_state_checks_the_port_once(self):
        with patch('radio.settings.urllib.request.urlopen', side_effect=OSError), \
                patch('radio.tts.port_open', side_effect=[True, False]) as port_open:
            state = settings._voice_state(self.cfg)
        port_open.assert_called_once()
        self.assertEqual((state['state'], state['detail']), ('starting', 'Starting…'))
