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
