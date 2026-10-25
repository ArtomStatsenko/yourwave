import asyncio
import os
import shlex
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, daemon, music, settings, store
from radio.tts import MockTTS
from radio.engine import Liquidsoap


class MusicPriorityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-priority-')
        self.addCleanup(tmp.cleanup)
        override = patch.object(config, 'DATA_DIR', Path(tmp.name))
        override.start()
        self.addCleanup(override.stop)
        self.svc = daemon.Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)

    def test_one_unplayed_local_track_has_priority_without_age_or_minimum_count(self):
        f = config.DATA_DIR / 'unplayed.wav'
        f.touch()
        self.svc.files = [f]
        self.svc.intro_rooms = {str(f): None}
        self.svc.cfg['broadcast'].update(new_days=0, new_min_tracks=3)
        with patch.object(daemon.audio, 'duration', return_value=180):
            self.assertEqual(self.svc._pick_new(), [f])
        store.log_event(self.svc.db, self.svc.session, 'MUSIC_STARTED', str(f))
        self.assertEqual(self.svc._pick_new(), [])

    def test_remote_priority_excludes_aired_and_current_without_treating_urls_as_files(self):
        refs = ['ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb', 'ytmusic:ccccccccccc']
        self.svc.ytmusic_active = True
        self.svc.current = {'ref': refs[1]}
        self.svc.intro_rooms = dict.fromkeys(refs)
        store.log_event(self.svc.db, self.svc.session, 'MUSIC_STARTED', refs[0])
        with patch.object(daemon.ytmusic, 'library', return_value={'tracks': [
                {'id': r.split(':')[1], 'duration': 180} for r in refs]}), \
             patch.object(daemon.audio, 'duration', side_effect=AssertionError('remote is not a file')):
            self.assertTrue(self.svc._new_set_due())
            self.assertEqual(self.svc._pick_new(), [refs[2]])

    def test_priority_is_queued_early_without_waiting_for_news_or_a_clock_slot(self):
        svc = self.svc
        svc.state = 'PLAYING'
        svc.current = {'kind': 'music', 'ref': '/current.wav', 'started': time.time(), 'duration': 300}
        svc.engine = Mock()
        svc.cfg['broadcast']['new_every_sets'] = 3
        svc.defaults = settings.base(svc.cfg)
        with patch.object(svc, '_pick_new', return_value=[Path('/new.wav')]), \
             patch.object(svc, '_queue_fresh', new_callable=AsyncMock) as queued, \
             patch.object(svc, '_clock_slot', return_value=None), \
             patch.object(daemon.plugins, 'on', side_effect=lambda cfg, name: name == 'new_tracks'):
            asyncio.run(svc.schedule())
        queued.assert_awaited_once_with([Path('/new.wav')])

    def test_source_switch_removes_only_the_old_music_priority(self):
        svc = self.svc
        svc.engine = AsyncMock()
        svc.engine.command.return_value = 'OK'
        svc.ytmusic_active = True
        svc.files = [Path('/local.wav')]
        svc.fresh_queued = {'ytmusic:aaaaaaaaaaa': '7'}
        with patch.object(daemon.plugins, 'on', return_value=False):
            asyncio.run(svc._sync_music_source())
        svc.engine.remove.assert_awaited_once_with('7', 'fresh')
        self.assertFalse(svc.fresh_queued)

    def test_remote_priority_reaches_real_liquidsoap_before_a_repeat(self):
        svc = self.svc
        root = config.DATA_DIR
        tone = root / 'tone.wav'
        with wave.open(str(tone), 'wb') as stream:
            stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03' * (44100 * 6))
        helper = root / 'resolver.py'
        helper.write_text('import shutil,sys,tempfile\nf=tempfile.NamedTemporaryFile(suffix=".wav",delete=False);f.close()\nshutil.copyfile(sys.argv[1],f.name);print(f.name)\n')
        old, new = 'ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb'
        tracks = [{'id': ref.split(':')[1], 'artist': 'Test', 'title': ref, 'duration': 6} for ref in (old, new)]
        run = root / 'run'
        run.mkdir(exist_ok=True)
        m3u = run / 'remote.m3u'
        svc.ytmusic_active = True

        async def scenario():
            svc.engine = Liquidsoap(m3u, .3, str(root / 'out.wav'))
            try:
                await svc.engine.start(svc.on_event, [f'annotate:kind="music",ref="{old}":{tone}'])
                svc.state = 'PLAYING'
                deadline = time.monotonic() + 10
                while not svc.current and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                self.assertIsNotNone(svc.current)
                await svc.schedule()
                self.assertIn(new, svc.fresh_queued)
                deadline = time.monotonic() + 20
                while svc.current['ref'] != new and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                refs = [r[0] for r in svc.db.execute("SELECT ref FROM events WHERE type='MUSIC_STARTED' ORDER BY id")]
                self.assertEqual(refs[:2], [old, new])
            finally:
                await svc.engine.stop()

        join = shlex.join
        with patch.object(config, 'RUN_DIR', run), \
             patch.object(daemon.ytmusic, 'library', return_value={'tracks': tracks}), \
             patch.dict(os.environ, RADIO_FILE_SYNC='realtime'), \
             patch('radio.engine.shlex.join', side_effect=lambda args: join([sys.executable, str(helper), str(tone)]) if args[-1] == 'radio.ytmusic' else join(args)):
            svc.intro_rooms = daemon.ytmusic.write_m3u(svc.db, m3u)
            asyncio.run(scenario())
