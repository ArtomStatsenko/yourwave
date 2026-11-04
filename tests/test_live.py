import asyncio
import json
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, live, music
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS

STATION = {"stationuuid": "u1", "name": " SomaFM  Groove Salad ", "url": "http://x", "url_resolved": "https://ice2.somafm.com/gs",
           "tags": "ambient,chillout", "countrycode": "US", "codec": "MP3", "bitrate": 128, "favicon": ""}


class LiveRadioTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-live-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in [('DATA_DIR', root), ('RUN_DIR', root / 'run'), ('SOCK', root / 'run/radio.sock')]:
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        config.load()
        config.RUN_DIR.mkdir(parents=True, exist_ok=True)

    def test_the_catalog_is_searched_and_a_station_is_kept_until_stopped(self):
        asked = []
        def get(path, params=None):
            asked.append((path, params))
            if path == 'tags':
                return [{'name': 'chillout'}, {'name': ''}, {'name': 'jazz'}]
            return [STATION, {"stationuuid": "u2", "url_resolved": "ftp://bad"}]
        with patch.object(live, '_get', side_effect=get):
            found = live.search(tag='chillout')
            self.assertEqual([(s['name'], s['url']) for s in found], [('SomaFM Groove Salad', 'https://ice2.somafm.com/gs')])
            self.assertEqual(asked[0][1]['order'], 'clickcount')
            self.assertEqual(live.tags(), ['chillout', 'jazz'])
            self.assertEqual(live.choose('u1')['url'], 'https://ice2.somafm.com/gs')
        self.assertEqual([a[0] for a in asked[-2:]], ['stations/byuuid/u1', 'url/u1'], 'the listen is counted')
        self.assertEqual(live.current()['name'], 'SomaFM Groove Salad')
        live.stop()
        self.assertIsNone(live.current())
        with patch.object(live, '_get', return_value=[]), self.assertRaisesRegex(ValueError, 'no longer in the catalog'):
            live.choose('gone')

    def test_the_page_reads_the_catalog_itself(self):
        from radio import control
        with patch.object(live, 'search', return_value=[{'id': 'u1'}]) as search, patch.object(live, 'tags', return_value=['jazz']):
            self.assertEqual(control.execute({'cmd': 'live', 'action': 'search', 'tag': 'jazz'})[0], {'ok': True, 'stations': [{'id': 'u1'}]})
            self.assertEqual(control.execute({'cmd': 'live', 'action': 'tags'})[0], {'ok': True, 'tags': ['jazz']})
        search.assert_called_once_with('', 'jazz')

    def test_the_station_plays_through_the_daemon_and_its_songs_have_no_file(self):
        svc = Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)
        svc.engine = Mock(proc=None, command=AsyncMock(return_value='OK'))
        svc.fresh_queued = {'/m/new.mp3': '5'}
        svc.engine.remove = AsyncMock()
        def choose(i):
            (config.DATA_DIR / 'live.json').write_text(json.dumps({'id': i, 'name': 'S', 'url': 'https://s'}))
            return live.current()
        with patch.object(live, 'choose', side_effect=choose):
            self.assertTrue(asyncio.run(svc.dispatch('live', {'action': 'start', 'id': 'u1'}))['ok'])
        svc.engine.command.assert_any_await('live.set https://s')
        self.assertEqual(svc.fresh_queued, {}, 'new songs put ahead would come before the station')
        self.assertTrue(svc._new_set_due() is not None)
        self.assertEqual(svc._song_seconds('live:Artist - Song'), 0.0)
        self.assertEqual(music.label(svc.db, 'live:Artist - Song'), 'Artist - Song')
        self.assertTrue(asyncio.run(svc.dispatch('live', {'action': 'stop'}))['ok'])
        svc.engine.command.assert_any_await('live.set ')
        self.assertIsNone(live.current())

    def test_an_untitled_bit_of_a_stream_is_not_a_song_in_the_history(self):
        from radio import store
        from radio.timeline import timeline
        svc = Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)
        for ref in ('', 'live:stream', 'live:Artist - Song'):
            svc.on_event({'kind': 'music', 'ref': ref})
        store.log_event(svc.db, svc.session, 'MUSIC_STARTED', '')  # one logged before this rule
        self.assertEqual([x['ref'] for x in timeline() if x['kind'] == 'music'], ['live:Artist - Song'])

    def test_liquidsoap_switches_to_the_stream_and_back(self):
        tone = config.DATA_DIR / 'tone.wav'
        stream = config.DATA_DIR / 'stream.wav'
        for path, seconds in ((tone, 20), (stream, 20)):
            with wave.open(str(path), 'wb') as out:
                out.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\1\0\1\0' * 44100 * seconds)
        m3u = config.RUN_DIR / 'live.m3u'
        m3u.write_text(f'annotate:kind="music",ref="song":{tone}\n')

        async def scenario(engine):
            events = []
            try:
                await engine.start(events.append)
                await engine.command('live.set ' + str(stream))
                deadline = time.monotonic() + 10
                while await engine.command('live.ready') != 'true' and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                await engine.command('skip')  # Start now, once the stream is ready: the next song is the stream
                deadline = time.monotonic() + 15
                while not any(e.get('ref', '').startswith('live:') for e in events) and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                self.assertTrue(any(e.get('ref') == 'live:stream' and e.get('kind') == 'music' for e in events), events)
                self.assertEqual(await engine.command('live.ready'), 'true')
                await engine.command('live.set ')
                self.assertEqual(await engine.command('live.ready'), 'false')
            finally:
                await engine.stop()

        with patch.dict(os.environ, RADIO_FILE_SYNC='realtime'), patch('radio.engine.ytmusic.library', return_value={'tracks': []}):
            asyncio.run(scenario(Liquidsoap(m3u, .1, str(config.DATA_DIR / 'out.wav'))))


if __name__ == '__main__':
    unittest.main()
