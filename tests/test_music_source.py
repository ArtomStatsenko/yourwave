import asyncio
import array
import os
import shlex
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, plugins, settings
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class MusicSourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-source-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root / 'run')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.svc = Service(config.load(), tts=MockTTS())
        self.addCleanup(self.svc.db.close)
        self.svc.library = set()
        self.svc.state = 'PLAYING'
        self.svc.files = [self.root / 'local.wav']
        self.svc.current = {'kind': 'music', 'ref': '/local.wav', 'started': time.time(), 'duration': 60}
        self.svc.engine = AsyncMock()
        self.svc.engine.command.return_value = 'OK'

    def test_integration_enable_does_not_select_it_and_choice_does_not_disable_it(self):
        plugins.set_plugin('ytmusic', True)
        asyncio.run(self.svc._sync_music_source())
        self.svc.engine.command.assert_not_awaited()
        self.assertEqual(settings.music_source(self.svc.cfg), 'local')
        settings.set_setting('music_source', 'ytmusic')
        self.assertTrue(plugins.on(self.svc.cfg, 'ytmusic'))
        settings.set_setting('music_source', 'local')
        self.assertTrue(plugins.on(self.svc.cfg, 'ytmusic'))
        self.assertEqual(settings.music_source(self.svc.cfg), 'local')

    def test_disabling_integration_resets_selection_and_reenable_keeps_local(self):
        plugins.set_plugin('ytmusic', True)
        settings.set_setting('music_source', 'ytmusic')
        plugins.set_plugin('ytmusic', False)
        self.assertEqual(settings.music_source(self.svc.cfg), 'local')
        with self.assertRaisesRegex(ValueError, 'Enable'):
            settings.set_setting('music_source', 'ytmusic')
        plugins.set_plugin('ytmusic', True)
        self.assertEqual(settings.music_source(self.svc.cfg), 'local')

    def test_disabling_integration_stops_selecting_remote_with_no_local_files(self):
        self.svc.files = []
        self.svc.ytmusic_active = True
        plugins.set_plugin('ytmusic', False)
        asyncio.run(self.svc._sync_music_source())
        self.svc.engine.command.assert_awaited_with('music.source local')
        self.assertFalse(self.svc.ytmusic_active)
        self.assertIn('No local music', self.svc.music_source_error)

    def test_switch_does_not_claim_new_source_before_audio_event(self):
        async def scenario():
            plugins.set_plugin('ytmusic', True)
            settings.set_setting('music_source', 'ytmusic')
            with patch('radio.daemon.ytmusic.write_m3u', return_value={'ytmusic:abcdefghijk': None}):
                await self.svc._sync_music_source()
            self.assertEqual(self.svc._music_source_state()['current'], 'local')
            self.assertEqual(self.svc._music_source_state()['phase'], 'loading')
            self.svc.music_source_ready = True
            self.assertEqual(self.svc._music_source_state()['phase'], 'waiting')
            self.svc.current['ref'] = 'ytmusic:abcdefghijk'
            self.assertEqual(self.svc._music_source_state()['phase'], 'playing')
            plugins.set_plugin('ytmusic', False)
            await self.svc._sync_music_source()
            self.assertEqual(self.svc._music_source_state()['current'], 'ytmusic')
            self.svc.engine.command.assert_awaited_with('music.source local')
        asyncio.run(scenario())

    def test_failed_connection_keeps_local_audio(self):
        plugins.set_plugin('ytmusic', True)
        settings.set_setting('music_source', 'ytmusic')
        with patch('radio.daemon.ytmusic.write_m3u', side_effect=ValueError('Connect YouTube Music')):
            asyncio.run(self.svc._sync_music_source())
        self.svc.engine.command.assert_not_awaited()
        self.assertEqual(self.svc._music_source_state()['phase'], 'error')
        self.assertEqual(self.svc._music_source_state()['current'], 'local')

    def test_host_is_not_reported_as_music_and_overlap_expires(self):
        self.svc.current['kind'] = 'news'
        self.assertIsNone(self.svc._music_source_state()['current'])
        self.svc.current['kind'] = 'music'
        self.svc.source_overlap = {'from': 'ytmusic', 'until': time.time() + 10}
        self.assertEqual(self.svc._music_source_state()['phase'], 'crossfade')
        self.svc.source_overlap['until'] = 0
        self.assertEqual(self.svc._music_source_state()['phase'], 'playing')

    def test_disabled_remote_does_not_mask_an_empty_local_library_at_start(self):
        with patch('radio.daemon.music.scan', return_value=[]), patch('radio.daemon.ytmusic.write_m3u', return_value={'ytmusic:abcdefghijk': None}):
            with self.assertRaisesRegex(SystemExit, 'NO_MUSIC'):
                asyncio.run(self.svc.run())

    def test_empty_local_library_does_not_interrupt_remote(self):
        self.svc.files = []
        self.svc.ytmusic_active = True
        plugins.set_plugin('ytmusic', True)
        asyncio.run(self.svc._sync_music_source())
        self.svc.engine.command.assert_not_awaited()
        self.assertTrue(self.svc.ytmusic_active)
        self.assertIn('No local music', self.svc.music_source_error)

    def test_status_forgets_unavailable_priority_tracks_before_showing_next(self):
        dropped, ready = 'ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb'
        self.svc.ytmusic_active = True
        self.svc.current['ref'] = 'ytmusic:ccccccccccc'
        self.svc.fresh_queued = {dropped: '21', ready: '24'}
        self.svc.engine.proc = Mock(returncode=None)
        self.svc.engine.pending.return_value = ['24']
        self.svc.engine.next_music.return_value = 'ytmusic:ddddddddddd'
        self.svc.engine.upcoming_music.return_value = []
        asyncio.run(self.svc.dispatch('status'))
        status = asyncio.run(self.svc.dispatch('status'))['status']
        next_music = next(r for r in status['upcoming'] if r['stage'] == 'music')
        self.assertEqual(next_music['ref'], ready)
        self.assertEqual(status['next_reroll']['ref'], ready)
        self.assertEqual(self.svc.fresh_queued, {ready: '24'})
        self.svc.engine.pending.return_value = []
        asyncio.run(self.svc.dispatch('status'))
        status = asyncio.run(self.svc.dispatch('status'))['status']
        next_music = next(r for r in status['upcoming'] if r['stage'] == 'music')
        self.assertEqual(next_music['ref'], 'ytmusic:ddddddddddd')
        self.assertEqual(status['next_reroll']['ref'], next_music['ref'])

    def test_priority_request_survives_a_transient_handoff_gap(self):
        ref = 'ytmusic:aaaaaaaaaaa'
        self.svc.fresh_queued = {ref: '21'}
        self.svc.engine.pending.side_effect = [[], ['21'], []]
        async def scenario():
            for _ in range(3):
                await self.svc._forget_dropped_fresh()
                self.assertEqual(self.svc.fresh_queued, {ref: '21'})
        asyncio.run(scenario())

    def test_status_clears_stale_next_when_playlist_is_not_ready(self):
        self.svc.next_track = '/old-next.wav'
        self.svc.engine.proc = Mock(returncode=None)
        self.svc.engine.next_music.return_value = None
        status = asyncio.run(self.svc.dispatch('status'))['status']
        self.assertEqual(self.svc.next_track, '')
        self.assertIsNone(status['next_reroll'])
        self.assertFalse(any(r['stage'] == 'music' for r in status['upcoming']))

    def test_live_switch_with_slow_download_keeps_audio_and_switches_back(self):
        tone = self.root / 'tone.wav'
        with wave.open(str(tone), 'wb') as stream:
            stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03' * (44100 * 3))
        local = self.root / 'local.m3u'
        local.write_text(f'annotate:kind="music",ref="local-test":{tone}\n')
        remote = self.root / 'remote.m3u'
        remote.write_text('#EXTM3U\n')
        helper = self.root / 'resolve.py'
        helper.write_text('import pathlib, shutil, sys, tempfile, time\n'
                          'time.sleep(6)\n'
                          'f=tempfile.NamedTemporaryFile(suffix=".wav",delete=False);f.close()\n'
                          'shutil.copyfile(sys.argv[1],f.name); print(f.name)\n')
        output = self.root / 'output.wav'
        events = []

        async def scenario():
            engine = Liquidsoap(remote, .3, str(output), fallback_m3u=local)
            engine.music_source = 'local'
            async def wait_for(ref, start=0):
                deadline = time.monotonic() + 25
                while not any(e.get('ref', '').startswith(ref) for e in events[start:]):
                    if time.monotonic() > deadline:
                        self.fail(f'No {ref} event: {events}')
                    await asyncio.sleep(.1)
            try:
                await engine.start(events.append)
                await wait_for('local-test')
                remote.write_text('annotate:kind="music",ref="ytmusic:abcdefghijk":ytmusic:abcdefghijk\n')
                await engine.command('music.reload ' + str(remote))
                self.assertEqual(await engine.command('music.source ytmusic'), 'OK')
                first = await engine.push_replay(tone, 'music', 'priority-first', 0, '', queue='fresh')
                second = await engine.push_replay(tone, 'music', 'priority-second', 0, '', queue='fresh')
                self.assertEqual(set(await engine.pending('fresh')), {first, second})
                await engine.remove(first, 'fresh')
                await engine.remove(second, 'fresh')
                await asyncio.sleep(4)
                self.assertFalse(any(e.get('ref', '').startswith('ytmusic:') for e in events))
                self.assertGreaterEqual(sum(e.get('ref') == 'local-test' for e in events), 2)
                await wait_for('ytmusic:')
                start = len(events)
                self.assertEqual(await engine.command('music.source local'), 'OK')
                await wait_for('local-test', start)
            finally:
                await engine.stop()
        original = shlex.join
        with patch.dict(os.environ, RADIO_FILE_SYNC='realtime'), patch('radio.engine.shlex.join', wraps=original) as join:
            # Use a delayed local resolver: no credentials or external network are involved.
            join.side_effect = lambda args: original([sys.executable, str(helper), str(tone)]) if args[-1] == 'radio.ytmusic' else original(args)
            asyncio.run(scenario())
        with wave.open(str(output)) as stream:
            samples = array.array('h', stream.readframes(stream.getnframes()))
            width = stream.getframerate() * stream.getnchannels() // 4
        windows = [max(abs(v) for v in samples[i:i + width]) for i in range(0, len(samples) - width, width)]
        self.assertTrue(windows)
        self.assertTrue(all(value > 30 for value in windows[2:-2]), 'Silence during loading or switching')


if __name__ == '__main__':
    unittest.main()
