import asyncio
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, plugins
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class RerollTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-reroll-')
        self.addCleanup(tmp.cleanup)
        for name, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run'), ('SOCK', Path(tmp.name) / 'run/radio.sock')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.cfg = config.load()
        self.svc = Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)
        self.svc.state = 'PLAYING'
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': 1000, 'duration': 180}
        self.svc.next_track = '/tmp/next.wav'
        self.svc.library = {'/tmp/current.wav', '/tmp/next.wav', '/tmp/other.wav'}
        self.svc.engine = Mock(proc=None, music_choices=Mock(return_value={ref: 'annotate:kind="music",ref="' + ref + '":' + ref for ref in self.svc.library}), replace_music=AsyncMock(return_value='42'))
        self.clock = patch('radio.daemon.time.time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def reroll(self, expected='/tmp/next.wav'):
        """The answer, and for a YouTube Music song (swapped in the background) the outcome of that swap."""
        async def go():
            self.svc.reroll_task = None
            answer = await self.svc.dispatch('reroll-next', {'ref': expected})
            if self.svc.reroll_task and (fail := await self.svc.reroll_task):
                return {'ok': False, 'error': fail}
            return answer
        return asyncio.run(go())

    def test_disabled_experiment_does_not_change_playback(self):
        self.assertIsNone(self.svc.status()['next_reroll'])
        self.assertFalse(self.reroll()['ok'])
        self.svc.engine.replace_music.assert_not_awaited()

    def test_enabled_replaces_next_and_keeps_current(self):
        plugins.set_plugin('reroll_next', True)
        current = dict(self.svc.current)
        self.assertTrue(self.reroll()['ok'])
        self.assertEqual(self.svc.next_track, '/tmp/other.wav')
        self.assertEqual(self.svc.current, current)
        self.assertEqual(self.svc.engine.replace_music.call_args.args[0], '/tmp/next.wav')
        self.assertIn('ref="/tmp/other.wav"', self.svc.engine.replace_music.call_args.args[1])
        self.assertEqual(self.svc.editor.calls, 0)

    def test_youtube_reroll_answers_at_once_with_the_new_title(self):
        import threading
        plugins.set_plugin('reroll_next', True)
        refs = ['ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb', 'ytmusic:ccccccccccc']
        self.svc.current['ref'], self.svc.next_track = refs[0], refs[1]
        self.svc.library = set(refs)
        self.svc.engine.music_choices.return_value = {r: f'annotate:kind="music",ref="{r}",liq_amplify="0 dB":{r}' for r in refs}
        song = config.DATA_DIR / 'song2.webm'
        song.write_bytes(b'x')
        loaded = threading.Event()

        def slow(vid):
            loaded.wait(5)  # YouTube Music still downloading
            return str(song)

        async def go():
            with patch('radio.daemon.ytmusic.resolve', side_effect=slow):
                answer = await self.svc.dispatch('reroll-next', {'ref': refs[1]})
                pending = answer['status']['next_reroll'].get('pending')
                self.svc.engine.replace_music.assert_not_awaited()  # not swapped yet...
                loaded.set()
                await self.svc.reroll_task
            return pending
        pending = asyncio.run(go())
        self.assertTrue(pending and pending['title'])  # ...but the page already shows the new title
        self.svc.engine.replace_music.assert_awaited_once()
        self.assertIsNone(self.svc.reroll_pending)

    def test_a_second_click_while_loading_replaces_the_pick(self):
        import threading
        plugins.set_plugin('reroll_next', True)
        refs = ['ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb', 'ytmusic:ccccccccccc', 'ytmusic:ddddddddddd', 'ytmusic:eeeeeeeeeee']
        self.svc.current['ref'], self.svc.next_track = refs[0], refs[1]
        self.svc.library = set(refs)
        self.svc.engine.music_choices.return_value = {r: f'annotate:kind="music",ref="{r}",liq_amplify="0 dB":{r}' for r in refs}
        first_loaded, files, calls = threading.Event(), [], []

        def resolve(vid):
            calls.append(vid)
            if len(calls) == 1:
                first_loaded.wait(5)  # the first pick is still downloading when the second click comes
            f = config.DATA_DIR / f'{vid}.webm'
            f.write_bytes(b'x')
            files.append(f)
            return str(f)

        async def go():
            with patch('radio.daemon.ytmusic.resolve', side_effect=resolve):
                first = await self.svc.dispatch('reroll-next', {'ref': refs[1]})
                first_task = self.svc.reroll_task
                while not calls:
                    await asyncio.sleep(0.01)  # its download has started
                self.assertTrue(first['status']['next_reroll']['available'])  # the dice stays clickable
                second = await self.svc.dispatch('reroll-next', {'ref': refs[1]})
                self.assertNotEqual(first['status']['next_reroll']['pending'], second['status']['next_reroll']['pending'])
                latest = second['status']['next_reroll']['pending']['title']
                await asyncio.sleep(0.2)
                self.assertEqual(len(calls), 1)               # one download at a time: the second waits
                first_loaded.set()
                await first_task
                await self.svc.reroll_task
            return latest
        latest = asyncio.run(go())
        self.assertEqual(len(calls), 2)
        self.svc.engine.replace_music.assert_awaited_once()  # only the latest pick airs
        self.assertIn(calls[1], self.svc.engine.replace_music.call_args.args[1])
        self.assertFalse(files[0].exists())                  # the dropped download is deleted
        self.assertTrue(latest)
        self.assertIsNone(self.svc.reroll_pending)
        self.assertFalse(self.svc.rerolling)

    def test_a_replay_the_engine_dropped_is_forgotten(self):
        plugins.set_plugin('reroll_next', True)
        self.svc.replays = {'r1': {'kind': 'music', 'ref': '/tmp/other.wav', 'rid': '7', 'duration': 180}}
        from radio import daemon
        def look():  # a status poll, 10 s after the previous one (looks closer together are skipped)
            daemon.time.time.return_value += 10
            asyncio.run(self.svc._forget_dropped_replays())
        self.svc.engine.pending = AsyncMock(return_value=['7', '9'])   # still queued
        look(); look()
        self.assertIn('r1', self.svc.replays)
        self.svc.engine.pending = AsyncMock(return_value=['9'])     # YouTube did not serve it: dropped without an event
        look()
        self.assertIn('r1', self.svc.replays)                      # one miss: the queue may just be resolving it
        look()
        self.assertNotIn('r1', self.svc.replays)
        self.assertEqual(self.svc._reroll_state()['ref'], '/tmp/next.wav')  # the dice is back on the real next song

    def test_youtube_reroll_skips_an_unavailable_song(self):
        from radio import ytmusic
        plugins.set_plugin('reroll_next', True)
        refs = ['ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb', 'ytmusic:ccccccccccc', 'ytmusic:ddddddddddd']
        self.svc.current['ref'], self.svc.next_track = refs[0], refs[1]
        self.svc.library = set(refs)
        self.svc.engine.music_choices.return_value = {r: f'annotate:kind="music",ref="{r}",liq_amplify="0 dB":{r}' for r in refs}
        song = config.DATA_DIR / 'song3.webm'
        song.write_bytes(b'x')
        answers = iter([ytmusic.Unavailable('gone'), str(song)])

        def resolve(vid):
            a = next(answers)
            if isinstance(a, Exception):
                raise a
            return a
        with patch('radio.daemon.ytmusic.resolve', side_effect=resolve):
            self.assertTrue(self.reroll(refs[1])['ok'])  # the first pick is unavailable: the spare airs instead
        self.svc.engine.replace_music.assert_awaited_once()
        self.assertIsNone(self.svc.reroll_error)

    def test_youtube_music_replacement_is_downloaded_outside_the_engine(self):
        plugins.set_plugin('reroll_next', True)
        refs = ['ytmusic:aaaaaaaaaaa', 'ytmusic:bbbbbbbbbbb', 'ytmusic:ccccccccccc']
        self.svc.current['ref'], self.svc.next_track = refs[0], refs[1]
        self.svc.library = set(refs)
        self.svc.engine.music_choices.return_value = {r: f'annotate:kind="music",ref="{r}",liq_amplify="0 dB":{r}' for r in refs}
        song = config.DATA_DIR / 'song.webm'
        song.write_bytes(b'x')
        with patch('radio.daemon.ytmusic.resolve', return_value=str(song)) as resolve:
            self.assertTrue(self.reroll(refs[1])['ok'])
        resolve.assert_called_once_with('ccccccccccc')
        uri = self.svc.engine.replace_music.call_args.args[1]
        self.assertTrue(uri.endswith(':tmp:' + str(song)), uri)            # a file: the engine does not download
        self.assertIn('ref="ytmusic:ccccccccccc"', uri)
        self.assertTrue(song.exists())                                      # Liquidsoap owns it now
        self.svc.engine.replace_music.reset_mock()
        with patch('radio.daemon.ytmusic.resolve', side_effect=ValueError('offline')):
            self.assertIn('Could not load', self.reroll(refs[2])['error'])
        self.svc.engine.replace_music.assert_not_awaited()
        self.svc.engine.replace_music.side_effect = ValueError('STALE')
        with patch('radio.daemon.ytmusic.resolve', return_value=str(song)):
            self.assertFalse(self.reroll(refs[2])['ok'])
        self.assertFalse(song.exists())                                     # not handed over: removed

    def test_a_busy_radio_answers_with_an_error_not_a_crash(self):
        import socket
        from radio.__main__ import send
        path = config.RUN_DIR / 'busy.sock'
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen()
            with patch.object(config, 'SOCK', path):
                self.assertIn('did not answer in time', send('status', timeout=.2)['error'])

    def test_stale_or_locked_transition_cannot_change_queue(self):
        plugins.set_plugin('reroll_next', True)
        self.assertFalse(self.reroll('/tmp/stale.wav')['ok'])
        self.svc.queued_break = ('9', ['news#1'])
        self.assertFalse(self.reroll()['ok'])
        self.svc.queued_break = None
        self.svc.assembling = True
        self.assertFalse(self.reroll()['ok'])
        self.svc.assembling = False
        self.svc.current['duration'] = 5
        self.assertFalse(self.reroll()['ok'])
        self.svc.engine.replace_music.assert_not_awaited()

    def test_failure_keeps_previous_selection_and_clears_busy(self):
        plugins.set_plugin('reroll_next', True)
        self.svc.engine.replace_music.side_effect = ValueError('unavailable')
        self.assertFalse(self.reroll()['ok'])
        self.assertEqual(self.svc.next_track, '/tmp/next.wav')
        self.assertFalse(self.svc.rerolling)

    def test_queued_replay_is_replaced_in_place(self):
        plugins.set_plugin('reroll_next', True)
        self.svc.replays['r1'] = {'kind': 'music', 'ref': '/tmp/next.wav', 'rid': '8'}
        self.assertTrue(self.reroll()['ok'])
        self.assertEqual(self.svc.replays['r1'], {'kind': 'music', 'ref': '/tmp/other.wav', 'rid': '42', 'duration': 0.0})
        self.assertEqual(self.svc.engine.replace_music.call_args.args[2:], ('news', '8'))
        self.assertIn('replay_id="r1"', self.svc.engine.replace_music.call_args.args[1])

    def test_new_tracks_queue_is_replaced_in_place(self):
        plugins.set_plugin('reroll_next', True)
        self.svc.fresh_queued = {'/tmp/next.wav': '10', '/tmp/later.wav': '11'}
        self.assertTrue(self.reroll()['ok'])
        self.assertEqual(list(self.svc.fresh_queued.items()), [('/tmp/other.wav', '42'), ('/tmp/later.wav', '11')])

    def test_no_alternative_keeps_the_original_track(self):
        plugins.set_plugin('reroll_next', True)
        self.svc.library.remove('/tmp/other.wav')
        self.assertFalse(self.reroll()['ok'])
        self.svc.engine.replace_music.assert_not_awaited()

    def test_wait_music_keeps_waiting_until_prefetch_is_ready(self):
        engine = Liquidsoap(config.RUN_DIR / 'music.m3u', 3, 'x')
        engine.command = AsyncMock(return_value='false')
        engine.next_music = AsyncMock(side_effect=[None, '/tmp/ready.wav'])
        with patch('radio.engine.asyncio.sleep', new_callable=AsyncMock) as sleep:
            asyncio.run(engine.wait_music())
        self.assertEqual(engine.next_music.await_count, 2)
        sleep.assert_awaited_once_with(.1)

    def test_immediate_edition_joins_ready_audio_in_order(self):
        from radio import audio
        paths = []
        for i in (1, 2):
            path = config.DATA_DIR / f'part-{i}.wav'
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(i.to_bytes(2, 'little') * 2 * 4410)
            paths.append(path)
        self.assertEqual(audio.join_packages(paths[:1], config.DATA_DIR / 'unused.wav'), paths[0])
        joined = audio.join_packages(paths, config.DATA_DIR / 'joined.wav')
        with wave.open(str(joined), 'rb') as out:
            self.assertEqual(out.getnframes(), 8820)
            self.assertEqual(out.readframes(1), b'\x01\x00' * 2)
            out.setpos(8819)
            self.assertEqual(out.readframes(1), b'\x02\x00' * 2)

    def test_manual_skip_and_host_edition_keep_audio_continuous(self):
        import math
        from array import array
        refs = {}
        for frequency in (440, 880, 1320):
            path = config.DATA_DIR / f'tone-{frequency}.wav'
            samples = array('h', (int(4000 * math.sin(2 * math.pi * frequency * i / 44100)) for i in range(44100)))
            stereo = array('h', (v for sample in samples for v in (sample, sample)))
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(stereo.tobytes() * 20)
            refs[str(path)] = frequency
        m3u = config.RUN_DIR / 'skip.m3u'
        m3u.write_text(''.join('annotate:kind="music",ref="' + ref + '":' + ref + '\n' for ref in refs))
        bulletin = config.DATA_DIR / 'ready-bulletin.wav'
        with wave.open(str(bulletin), 'wb') as voice:
            voice.setnchannels(2)
            voice.setsampwidth(2)
            voice.setframerate(44100)
            voice.writeframes(stereo.tobytes() * 2)
        out = config.DATA_DIR / 'skip-output.wav'
        engine = Liquidsoap(m3u, 3, str(out))
        events = []
        async def scenario():
            with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}):
                await engine.start(events.append)
            try:
                deadline = time.monotonic() + 10
                while (not events or not out.exists() or out.stat().st_size < 44100 * 4) and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                self.assertTrue(events)
                current = events[-1]['ref']
                following = await engine.next_music()
                frame = max(0, (out.stat().st_size - 44) // 4)
                await engine.command('skip')
                await asyncio.sleep(1)
                rid = await engine.push_news(bulletin, 'ready-bulletin')
                await engine.wait_insert(rid)
                await engine.command('skip')
                await asyncio.sleep(5)
                return current, following, frame
            finally:
                await engine.stop()
        self.clock.stop()
        current, following, frame = asyncio.run(scenario())
        with wave.open(str(out), 'rb') as recording:
            recording.setpos(frame + int(.35 * 44100))
            samples = array('h', recording.readframes(4410))[::2]
            recording.setpos(frame + int(.2 * 44100))
            broadcast = array('h', recording.readframes(recording.getnframes() - recording.tell()))[::2]
        for start in range(0, len(broadcast) - 882, 882):
            window = broadcast[start:start + 882]
            self.assertGreater(sum(v*v for v in window) / len(window), 50**2,
                               'Silence during a music or host transition')
        def power(frequency):
            return abs(sum(v * complex(math.cos(2 * math.pi * frequency * i / 44100),
                                       math.sin(2 * math.pi * frequency * i / 44100)) for i, v in enumerate(samples)))
        self.assertGreater(power(refs[following]), power(refs[current]) * 10,
                           'The old song is still audible 350 ms after manual skip')

    def test_engine_rejects_expired_insert_without_scheduler(self):
        self.clock.stop()
        tone = config.DATA_DIR / 'tone.wav'
        with wave.open(str(tone), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44100)
            out.writeframes(b'\x10\x00' * 2 * 44100 * 5)
        playlist = config.RUN_DIR / 'expiry.m3u'
        playlist.write_text(f'annotate:kind="music",ref="tone":{tone}\n')
        engine = Liquidsoap(playlist, .1, str(config.DATA_DIR / 'expiry.wav'))
        events = []
        async def wait_for(predicate):
            deadline = time.monotonic() + 8
            while not predicate() and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            self.assertTrue(predicate(), events)
        async def scenario():
            with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}):
                await engine.start(events.append)
            try:
                await wait_for(lambda: bool(events))
                expired = await engine.push_news(tone, 'expired', expires=time.time() - 1)
                await asyncio.sleep(.3)
                await engine.command('skip')
                await asyncio.sleep(1)
                self.assertFalse(any(e['ref'] == 'expired' for e in events))
                await engine.remove(expired)
                rid = await engine.push_news(tone, 'valid', expires=time.time() + 30)
                await engine.wait_insert(rid)
                await engine.command('skip')
                await wait_for(lambda: any(e['ref'] == 'valid' for e in events))
            finally:
                await engine.stop()
        asyncio.run(scenario())

    def test_restart_within_minutes_continues_session_set_and_song(self):
        self.svc.set_started, self.svc.songs_since_break = 900, 3
        self.svc.current['started'] = 940
        session = self.svc.session
        self.svc._save_session()
        with patch('radio.daemon.time.time', return_value=1060):
            restarted = Service(self.cfg, tts=MockTTS(), output='x')
            self.addCleanup(restarted.db.close)
            restarted.engine = self.svc.engine
            self.assertTrue(restarted._restore_session())
            self.assertEqual(restarted.session, session)
            self.assertEqual(restarted.set_started, 960)                    # the set keeps its 100 s
            self.assertEqual(restarted.songs_since_break, 2)                # the song counts again when it airs
            self.assertIn('liq_cue_in="60.00",', restarted._resume_line())
            self.assertIn('ref="/tmp/current.wav"', restarted._resume_line())
            restarted.on_event({'kind': 'music', 'ref': '/tmp/current.wav'})
        self.assertEqual(restarted.current['started'], 1000)
        self.assertEqual(restarted.set_started, 960)
        self.assertFalse(restarted.resume_offset)

    def test_late_restart_starts_a_new_session(self):
        session = self.svc.session
        self.svc._save_session()
        with patch('radio.daemon.time.time', return_value=1000 + 301):
            restarted = Service(self.cfg, tts=MockTTS(), output='x')
            self.addCleanup(restarted.db.close)
            self.assertFalse(restarted._restore_session())
        self.assertNotEqual(restarted.session, session)
        self.assertFalse((config.RUN_DIR / 'session.json').exists())

    def test_engine_plays_local_music_while_the_primary_song_is_not_loaded(self):
        local = config.DATA_DIR / 'local.wav'
        with wave.open(str(local), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44100)
            out.writeframes(b'\1\0\1\0' * 44100 * 20)
        primary = config.RUN_DIR / 'primary.m3u'
        primary.write_text(f'annotate:kind="music",ref="missing":{config.DATA_DIR / "missing.wav"}\n')  # never loads
        fallback = config.RUN_DIR / 'fallback.m3u'
        fallback.write_text(f'annotate:kind="music",ref="{local}":{local}\n')
        engine = Liquidsoap(primary, .1, str(config.DATA_DIR / 'gap.wav'), fallback_m3u=fallback)
        events = []
        async def scenario():
            await engine.start(events.append)
            try:
                deadline = time.monotonic() + 10
                while not events and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
            finally:
                await engine.stop()
        self.clock.stop()
        asyncio.run(scenario())
        self.assertTrue(events, 'Silence: nothing played while the primary song could not load')
        self.assertEqual(events[0]['ref'], str(local))

    def test_engine_ends_a_liquidsoap_its_dead_daemon_left_and_stops_while_starting(self):
        song = config.DATA_DIR / 'song.wav'
        with wave.open(str(song), 'wb') as out:
            out.setnchannels(2)
            out.setsampwidth(2)
            out.setframerate(44100)
            out.writeframes(b'\1\0\1\0' * 44100 * 4)
        m3u = config.RUN_DIR / 'orphan.m3u'
        m3u.write_text(f'annotate:kind="music",ref="song":{song}\n')
        left, fresh = (Liquidsoap(m3u, .1, str(config.DATA_DIR / f'{n}.wav')) for n in ('left', 'fresh'))
        async def scenario():
            await left.start(lambda e: None)                                # its daemon dies: nobody stops it
            await fresh.start(lambda e: None)
            await asyncio.wait_for(left.proc.wait(), 5)
            await fresh.stop()
            waiting = Liquidsoap(m3u, .1, str(config.DATA_DIR / 'waiting.wav'))
            start = asyncio.create_task(waiting.start(lambda e: None, [f'annotate:kind="music",ref="x":{config.DATA_DIR / "missing.wav"}']))
            await asyncio.sleep(1.5)                                        # still waiting for a line that never loads
            await waiting.stop()
            await asyncio.wait_for(start, 5)                                # returns quietly, not with a socket error
        self.clock.stop()
        asyncio.run(scenario())

    def test_a_second_daemon_does_not_start(self):
        import fcntl
        from radio import daemon
        with open(config.RUN_DIR / 'daemon.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(daemon, 'Service') as service:
                daemon.main('x')
            service.assert_not_called()

    def test_engine_airs_the_opener_then_the_resumed_song_before_the_shuffle(self):
        paths = {}
        for name, seconds in (('opener', 2), ('song', 4), ('other', 4)):
            paths[name] = config.DATA_DIR / f'{name}.wav'
            with wave.open(str(paths[name]), 'wb') as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(b'\1\0\1\0' * 44100 * seconds)
        m3u = config.RUN_DIR / 'opener.m3u'
        m3u.write_text(f'annotate:kind="music",ref="other":{paths["other"]}\n')
        engine = Liquidsoap(m3u, .1, str(config.DATA_DIR / 'opener-output.wav'))
        events = []
        async def scenario():
            await engine.start(events.append, [f'annotate:kind="gap",ref="hello":{paths["opener"]}',
                                               f'annotate:kind="music",ref="song":{paths["song"]}'])
            try:
                deadline = time.monotonic() + 10
                while len(events) < 3 and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
            finally:
                await engine.stop()
        self.clock.stop()
        asyncio.run(scenario())
        self.assertEqual([e['ref'] for e in events[:3]], ['hello', 'song', 'other'])

    def test_opener_is_the_jingle_until_the_voice_is_rendered(self):
        self.svc.cfg['jingles']['stinger_in'] = ['/tmp/stinger.wav']
        self.assertEqual(self.svc._opener_line(), 'annotate:kind="id",ref="startup":/tmp/stinger.wav')
        def render(voice, chime, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b'RIFF')
        with patch('radio.daemon.cached_synthesize', return_value=Path('/tmp/voice.wav')), \
             patch('radio.audio.render_notice', side_effect=render):
            self.svc._prepare_opener()
        line = self.svc._opener_line()
        self.assertTrue(line.startswith('annotate:kind="gap",ref="В эфире «Personal Radio».'), line)
        self.assertTrue(line.endswith(str(self.svc._opener_file())))

    def test_engine_resumes_a_song_at_its_position_before_the_shuffle(self):
        import math
        from array import array
        def tone(frequency, seconds):
            return array('h', (v for i in range(44100 * seconds)
                               for v in (int(4000 * math.sin(2 * math.pi * frequency * i / 44100)),) * 2)).tobytes()
        song, other = config.DATA_DIR / 'song.wav', config.DATA_DIR / 'other.wav'
        for path, frames in ((song, tone(440, 10) + tone(880, 10)), (other, tone(1320, 20))):
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(frames)
        m3u = config.RUN_DIR / 'resume.m3u'
        m3u.write_text(f'annotate:kind="music",ref="{other}":{other}\n')
        out = config.DATA_DIR / 'resume-output.wav'
        engine = Liquidsoap(m3u, .1, str(out))
        events = []
        async def scenario():
            await engine.start(events.append, [f'annotate:kind="music",ref="{song}",liq_cue_in="12":{song}'])
            try:
                deadline = time.monotonic() + 10
                while not events and time.monotonic() < deadline:
                    await asyncio.sleep(.05)
                await asyncio.sleep(.5)
            finally:
                await engine.stop()
        self.clock.stop()
        asyncio.run(scenario())
        self.assertEqual(events[0]['ref'], str(song))
        with wave.open(str(out), 'rb') as recording:
            samples = array('h', recording.readframes(recording.getnframes()))[::2]
        start = next(i for i, v in enumerate(samples) if abs(v) > 500)
        window = samples[start + 4410:start + 8820]
        def power(frequency):
            return abs(sum(v * complex(math.cos(2 * math.pi * frequency * i / 44100),
                                       math.sin(2 * math.pi * frequency * i / 44100)) for i, v in enumerate(window)))
        self.assertGreater(power(880), power(440) * 10, 'The song did not resume at 12 s')
        self.assertGreater(power(880), power(1320) * 10, 'The shuffle took the first slot')

    def test_engine_replaces_prefetch_and_queued_replay_without_skipping_current(self):
        m3u = config.RUN_DIR / 'test.m3u'
        files = []
        for name in ('one', 'two', 'three'):
            path = config.DATA_DIR / (name + '.wav')
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(2)
                out.setsampwidth(2)
                out.setframerate(44100)
                out.writeframes(b'\0\0\0\0' * 44100 * 20)
            files.append(str(path))
        uris = {ref: 'annotate:kind="music",ref="' + ref + '":' + ref for ref in files}
        m3u.write_text('\n'.join(uris.values()) + '\n')
        engine = Liquidsoap(m3u, .1, str(config.DATA_DIR / 'out.wav'))
        events = []
        async def scenario():
            with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}):
                await engine.start(events.append)
            try:
                deadline = time.time() + 10
                while not events and time.time() < deadline:
                    await asyncio.sleep(.1)
                self.assertTrue(events)
                current = events[-1]['ref']
                before = await engine.next_music()
                self.assertIn(before, files)
                replacement = next(ref for ref in files if ref not in (current, before))
                await engine.replace_music(before, uris[replacement])
                self.assertEqual(await engine.next_music(), replacement)
                self.assertEqual(events[-1]['ref'], current)
                with self.assertRaises(ValueError):
                    await engine.replace_music(before, uris[replacement])
                first = await engine.push_replay(Path(before), 'music', before, 0, 'r1')
                later = await engine.push_replay(Path(current), 'music', current, 0, 'r2')
                deadline = time.time() + 3
                while first not in (await engine.command('news.queue')).split() and time.time() < deadline:
                    await asyncio.sleep(.1)
                new = await engine.replace_music(before, uris[replacement].replace('annotate:', 'annotate:replay_id="r1",', 1), 'news', first)
                await engine.wait_insert(new)
                queued = (await engine.command('news.pending')).split()
                self.assertEqual(queued, [new, later])
                self.assertEqual(events[-1]['ref'], current)
                await engine.wait_insert(new)
                await engine.command('skip')
                deadline = time.time() + 5
                while events[-1]['ref'] == current and time.time() < deadline:
                    await asyncio.sleep(.1)
                self.assertEqual(events[-1]['ref'], replacement)
                self.assertEqual(events[-1]['replay_id'], 'r1')
                await engine.remove(later)
                rid = await engine.push_news(Path(before), 'ready-edition')
                await engine.wait_insert(rid)
                started = time.monotonic()
                await engine.command('skip')
                while events[-1]['ref'] != 'ready-edition' and time.monotonic() - started < 2:
                    await asyncio.sleep(.02)
                self.assertEqual(events[-1]['kind'], 'news')
                self.assertEqual(events[-1]['ref'], 'ready-edition')
                self.assertLess(time.monotonic() - started, 2)
            finally:
                await engine.stop()
        self.clock.stop()
        asyncio.run(scenario())
