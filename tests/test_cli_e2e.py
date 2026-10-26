"""Real installed CLI, isolated state files and real FFmpeg decoding."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
import wave
import array


def _resolve_cli():
    found = shutil.which('radio')
    local = Path.home() / '.local/bin/radio'
    if not found and local.is_file():
        found = str(local)
    if found:
        print('[_resolve_cli] Using installed command:', found)
        return [found]
    if os.environ.get('CLI_ANYTHING_FORCE_INSTALLED') == '1':
        raise RuntimeError('Install bin/radio on PATH before release validation')
    return [str(Path(__file__).resolve().parents[1] / 'bin/radio')]


class TestCLISubprocess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.command = _resolve_cli()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {**os.environ, 'RADIO_DATA_DIR': str(self.root / 'station')}

    def run_cli(self, *args, ok=True):
        result = subprocess.run(self.command + ['--json', *args], env=self.env, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
        self.assertEqual(data['api_version'], 1)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + str(data))
            self.assertTrue(data['ok'])
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(data['ok'])
        return data

    def test_installed_launcher_works_outside_checkout(self):
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            data = self.run_cli('capabilities')
            self.assertEqual(len(data['operations']), 24)
            self.assertEqual(self.run_cli('status')['status']['state'], 'STOPPED')
        finally:
            os.chdir(previous)

    def test_real_setting_file_roundtrip_and_rejected_update(self):
        self.run_cli('settings', 'set', 'station_name', 'CLI E2E')
        path = self.root / 'station/settings.json'
        self.assertEqual(json.loads(path.read_text())['station_name'], 'CLI E2E')
        rows = self.run_cli('settings', 'list')['settings']
        self.assertEqual(next(r['value'] for r in rows if r['key'] == 'station_name'), 'CLI E2E')
        before = path.read_bytes()
        self.run_cli('settings', 'set', 'music_source', 'invalid', ok=False)
        self.assertEqual(path.read_bytes(), before)
        self.run_cli('settings', 'reset', 'station_name')
        self.assertNotIn('station_name', json.loads(path.read_text()))

    def test_real_experiment_toggle_and_inspection(self):
        self.run_cli('experiments', 'set', 'gap_filler', 'on')
        rows = self.run_cli('experiments', 'list')['experiments']
        self.assertTrue(next(r['on'] for r in rows if r['name'] == 'gap_filler'))
        self.run_cli('experiments', 'set', 'gap_filler', 'off')
        self.assertFalse(json.loads((self.root / 'station/plugins.json').read_text())['gap_filler'])
        self.assertEqual(self.run_cli('timeline')['timeline'], [])
        self.assertEqual(self.run_cli('queue', 'list')['upcoming'], [])
        self.run_cli('call', 'next', ok=False)

    def test_concurrent_setting_writes_preserve_both_keys(self):
        commands = [('station_name', 'Concurrent'), ('host_name', 'Host')]
        processes = [subprocess.Popen(self.command + ['--json', 'settings', 'set', key, value], env=self.env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for key, value in commands]
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertTrue(json.loads(stdout)['ok'])
        saved = json.loads((self.root / 'station/settings.json').read_text())
        self.assertEqual(saved['station_name'], 'Concurrent'); self.assertEqual(saved['host_name'], 'Host')

    def audio_server(self, silent=False):
        samples = array.array('h', [0 if silent else int(16000 * math.sin(2 * math.pi * 440 * i / 8000)) for i in range(16000)])
        path = self.root / 'stream'
        with wave.open(str(path), 'wb') as out:
            out.setparams((1, 2, 8000, 0, 'NONE', 'not compressed')); out.writeframes(samples.tobytes())
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(self.root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:' + str(server.server_port) + '/stream'

    def test_real_ffmpeg_probe_measures_pcm_without_speakers(self):
        self.assertIsNotNone(shutil.which('ffmpeg'), 'FFmpeg is a required backend')
        result = self.run_cli('probe', '--silent', '--seconds', '1', '--url', self.audio_server())
        self.assertTrue(result['silent_output']); self.assertTrue(result['probe']['signal'])
        self.assertAlmostEqual(result['probe']['duration_sec'], 1, places=1)
        self.assertGreater(result['probe']['rms_dbfs'], -12); self.assertLess(result['probe']['rms_dbfs'], -6)

    def test_real_ffmpeg_probe_distinguishes_silence(self):
        result = self.run_cli('probe', '--seconds', '1', '--url', self.audio_server(silent=True), ok=False)
        self.assertFalse(result['probe']['signal']); self.assertIsNone(result['probe']['rms_dbfs'])
