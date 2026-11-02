"""A page's poll is cheap: the rest of the round is asked of Liquidsoap only when it may have changed."""
import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests import test_radio  # noqa: F401  (an isolated data folder)
from radio import config, engine, plugins
from radio.daemon import Service
from radio.tts import MockTTS


class StatusLoadTests(unittest.TestCase):
    def setUp(self):
        self.svc = Service(config.load(), tts=MockTTS(), output='x')
        self.svc.engine = Mock(proc=Mock(returncode=None), next_music=AsyncMock(return_value='/m/a.mp3'),
                               music_plan=AsyncMock(return_value=[{'ref': '/m/a.mp3', 'uri': '', 'kind': 'music', 'duration': 0}]),
                               upcoming_music=AsyncMock(return_value=['/m/a.mp3']), pending=AsyncMock(return_value=[]))

    def test_the_round_is_asked_again_only_after_a_song_a_command_or_10_seconds(self):
        svc, e = self.svc, self.svc.engine
        status = lambda: asyncio.run(svc.dispatch('status'))
        status(); status()
        self.assertEqual(e.music_plan.await_count, 1, 'two polls: one question')
        e.upcoming_music.assert_not_awaited()
        self.assertEqual(status()['status']['upcoming_tracks'][0]['ref'], '/m/a.mp3')
        svc.on_event({'kind': 'music', 'ref': '/m/a.mp3'}); status()
        self.assertEqual(e.music_plan.await_count, 2, 'a new song')
        asyncio.run(svc.dispatch('pause')); status()
        self.assertEqual(e.music_plan.await_count, 3, 'a command')
        with patch('radio.daemon.time.time', return_value=time.time() + 11):
            status()
        self.assertEqual(e.music_plan.await_count, 4, '10 s later')

    def test_an_engine_without_music_plan_still_lists_the_songs(self):
        self.svc.engine.music_plan = AsyncMock(return_value=None)
        tracks = asyncio.run(self.svc.dispatch('status'))['status']['upcoming_tracks']
        self.assertEqual([t['ref'] for t in tracks], ['/m/a.mp3'])

    def test_the_plan_is_built_once_and_its_raw_rows_are_not_sent(self):
        plugins.set_plugin('air_direction', True)
        self.addCleanup(plugins.set_plugin, 'air_direction', False)
        with patch.object(Service, '_broadcast_plan', autospec=True, return_value={'version': 'v', 'rows': []}) as plan:
            status = asyncio.run(self.svc.dispatch('status'))['status']
        self.assertEqual(plan.call_count, 1)
        self.assertNotIn('direction', status)


class RerollAnySongTests(unittest.TestCase):
    def test_a_later_song_becomes_one_not_in_the_plan_and_the_others_stay(self):
        svc = Service(config.load(), tts=MockTTS(), output='x')
        line = lambda ref: f'annotate:kind="music",ref="{ref}":{ref}'
        plan = [{'ref': '/m/next.mp3', 'uri': 'annotate:plan_queue="music":' + line('/m/next.mp3'), 'kind': 'music', 'duration': 0},
                {'ref': '/m/f1.mp3', 'uri': 'annotate:plan_queue="fresh":' + line('/m/f1.mp3'), 'kind': 'music', 'set': True, 'duration': 0},
                {'ref': '', 'uri': 'annotate:plan_queue="music":' + line('/m/c.mp3'), 'kind': 'music', 'duration': 0}]
        songs = ['/m/next.mp3', '/m/f1.mp3', '/m/c.mp3', '/m/new.mp3', 'ytmusic:abcdefghijk']
        svc.library = set(songs)
        svc.fresh_queued = {'/m/f1.mp3': '5'}
        svc.engine = Mock(proc=Mock(returncode=None), next_music=AsyncMock(return_value='/m/next.mp3'),
                          music_plan=AsyncMock(return_value=plan), pending=AsyncMock(return_value=['5']),
                          music_choices=Mock(return_value={r: line(r) for r in songs}),
                          swap_music=AsyncMock(return_value='OK'), replace_music=AsyncMock(return_value='77'))
        with patch.object(svc, '_next_music_slot', return_value={'ref': '/m/next.mp3'}), \
             patch('radio.daemon.random.choice', side_effect=lambda c: sorted(c)[0]):
            self.assertTrue(asyncio.run(svc.dispatch('reroll', {'ref': '/m/c.mp3'}))['ok'])
            svc.engine.swap_music.assert_awaited_once_with('music', '/m/c.mp3', line('/m/new.mp3'))
            self.assertTrue(asyncio.run(svc.dispatch('reroll', {'ref': '/m/f1.mp3'}))['ok'])
            svc.engine.replace_music.assert_awaited_once_with(
                '/m/f1.mp3', 'annotate:plan_queue="fresh":' + line('/m/new.mp3'), 'fresh', '5')
            self.assertEqual(svc.fresh_queued, {'/m/new.mp3': '77'}, 'a fresh-queue song loads now: no YouTube pick')
            answer = asyncio.run(svc.dispatch('reroll', {'ref': '/m/gone.mp3'}))
        self.assertEqual(answer, {'ok': False, 'error': 'This song is no longer coming up'})


class PluginSwitchTests(unittest.TestCase):
    def test_one_switch_reads_as_the_whole_state(self):
        cfg = config.load()
        for name, value in (('air_direction', True), ('track_previews', False), ('news_reactions', True)):
            plugins.set_plugin(name, value)
            self.addCleanup(plugins.set_plugin, name, not value)
        self.assertEqual({n: plugins.on(cfg, n) for n in plugins.PLUGINS}, plugins.state(cfg))
        with self.assertRaises(KeyError):
            plugins.on(cfg, 'no_such_plugin')


class LogTests(unittest.TestCase):
    def test_a_big_liquidsoap_log_is_moved_aside_at_a_start(self):
        log = config.DATA_DIR / 'logs/liquidsoap.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text('x' * 50)
        (config.RUN_DIR).mkdir(parents=True, exist_ok=True)
        with patch.object(engine, 'LOG_KEEP_BYTES', 10), \
                patch('radio.engine.asyncio.create_subprocess_exec', AsyncMock(side_effect=RuntimeError('stop here'))):
            with self.assertRaises(RuntimeError):
                asyncio.run(engine.Liquidsoap(Path('/dev/null'), .1)._start(lambda e: None, ''))
        self.assertFalse(log.exists())
        self.assertEqual(log.with_name('liquidsoap.log.1').read_text(), 'x' * 50)


if __name__ == '__main__':
    unittest.main()
