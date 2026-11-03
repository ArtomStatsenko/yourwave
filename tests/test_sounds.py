import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch

from radio import config, control, sounds, ui
from radio.daemon import Service
from radio.tts import MockTTS


def tone(path: Path, seconds=.2) -> bytes:
    with wave.open(str(path), 'wb') as out:
        out.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\1\0' * int(44100 * seconds))
    return path.read_bytes()


class OwnSoundsTests(unittest.TestCase):
    """A slot's own sounds replace Base; an empty slot plays Base; the page uploads, lists and removes them."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-sounds-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root / 'run'), ('SOCK', self.root / 'run/radio.sock')]:
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        config.load()

    def upload(self, server, slot, name, data):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/sound?slot={slot}&name={name}', data=data,
                                         headers={'Host': f'127.0.0.1:{server.server_port}', 'X-Radio-Token': server.token})
        try:
            with urllib.request.urlopen(request, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_upload_listen_remove_and_the_station_picks_them_up_live(self):
        base = config.load()['jingles']['station_id']
        with patch.object(ui, 'Stream'):
            server = ui.make_server(0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        svc = Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)

        code, answer = self.upload(server, 'station_id', 'My%20ID.wav', tone(self.root / 'id.wav'))
        self.assertEqual((code, answer['name']), (200, 'My ID.wav'))
        slot = next(s for s in answer['sounds'] if s['slot'] == 'station_id')
        self.assertEqual((slot['own'], slot['base']), (['My ID.wav'], [Path(f).name for f in base]))
        self.assertEqual(config.load()['jingles']['station_id'], [self.root / 'sounds/station_id/My ID.wav'], 'own sounds replace Base')
        with patch.object(svc, '_refresh_audio_cache') as rebuild:
            svc._live_settings()
        rebuild.assert_called_once()
        self.assertEqual(svc.cfg['jingles']['station_id'], [self.root / 'sounds/station_id/My ID.wav'])

        listen = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/sound?slot=station_id&name=My%20ID.wav',
                                        headers={'Host': f'127.0.0.1:{server.server_port}', 'X-Radio-Token': server.token})
        with urllib.request.urlopen(listen, timeout=5) as r:
            self.assertEqual((r.headers['Content-Type'], r.read()), ('audio/x-wav', (self.root / 'id.wav').read_bytes()))

        for slot, name, data, error in (('station_id', 'notes.wav', b'not audio', 'no audio'),
                                        ('station_id', '..%2F..%2Fescape.txt', b'x', 'Choose an MP3'),
                                        ('nowhere', 'a.wav', b'x', 'Unknown sound slot')):
            code, answer = self.upload(server, slot, name, data)
            self.assertEqual(code, 400)
            self.assertIn(error, answer['error'])
        self.assertEqual(sorted(p.name for p in (self.root / 'sounds').rglob('*')), ['My ID.wav', 'station_id'], 'nothing else saved')

        resp, code = control.execute({'cmd': 'sound_remove', 'slot': 'station_id', 'name': 'My ID.wav'})
        self.assertEqual(code, 200)
        self.assertEqual(config.load()['jingles']['station_id'], base, 'an empty slot plays Base again')
        svc._live_settings()
        self.assertEqual(svc.cfg['jingles']['station_id'], base)


if __name__ == '__main__':
    unittest.main()
