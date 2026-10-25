import asyncio
import os
import subprocess
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, content, daemon, direction, horizon, music, reminders, settings, store
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class NewsReliabilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-news-reliability-')
        self.addCleanup(tmp.cleanup)
        data = patch.object(config, 'DATA_DIR', Path(tmp.name))
        data.start()
        self.addCleanup(data.stop)
        for name, value in [('RUN_DIR', config.DATA_DIR / 'run'), ('SOCK', config.DATA_DIR / 'run/radio.sock')]:
            setting = patch.object(config, name, value)
            setting.start()
            self.addCleanup(setting.stop)
        self.cfg = config.load()
        self.svc = daemon.Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)

    def test_partial_script_write_rolls_back_and_item_can_retry(self):
        item = {'id': 'atomic', 'source': 'github_trending', 'topic': 'Test', 'url': '',
                'published_at': store.now(), 'body': 'Первая новость.\n\nВторая новость.'}
        self.cfg['broadcast']['max_package_speech_sec'] = 1
        original = Path.write_text
        writes = 0

        def write(path, *args, **kw):
            nonlocal writes
            writes += 1
            if writes == 2:
                raise OSError('disk full')
            return original(path, *args, **kw)

        db = self.svc.db
        db.execute('SAVEPOINT caller')
        with patch.object(Path, 'write_text', write), self.assertRaises(OSError):
            content.add_item(db, self.cfg, item, 'raw_post')
        self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM packages').fetchone()[0], 0)
        self.assertEqual(content.add_item(db, self.cfg, item, 'raw_post'), 'NEW')
        self.assertGreater(db.execute('SELECT count(*) FROM packages').fetchone()[0], 1)
        db.execute('ROLLBACK TO caller')
        db.execute('RELEASE caller')
        self.assertEqual(db.execute('SELECT count(*) FROM items').fetchone()[0], 0)

    def test_failed_github_retellings_are_not_reported_as_success(self):
        repo = {'owner': 'audit', 'repo': 'tool', 'description': 'A useful tool.',
                'stars_total': 10, 'stars_today': None, 'rank': 1}
        with patch.object(daemon.github_new, 'fetch_new', return_value=[repo]), \
             patch.object(daemon.github_trending, 'fetch_readme', return_value=''), \
             patch.object(self.svc.editor, 'retell', side_effect=ValueError('empty reply')):
            asyncio.run(self.svc._fetch_github(False, True))
        self.assertTrue(self.svc.sources['github_new'].startswith('degraded'), self.svc.sources)
        events = [r[0] for r in self.svc.db.execute("SELECT type FROM events WHERE ref='github'")]
        self.assertEqual(events, ['FETCH_FAILED'])

    def test_trending_failure_does_not_block_new_repositories(self):
        self.svc.state = 'PLAYING'
        async def done():
            self.svc.stopping = True
        self.svc.done.wait = AsyncMock(side_effect=done)
        self.svc._news_preparation_due = lambda lead: True
        with patch.object(daemon.github_trending, 'fetch_html', side_effect=OSError('offline')), \
             patch.object(daemon.github_new, 'fetch_new', return_value=[]) as fetch, \
             patch.object(daemon.plugins, 'on', return_value=True):
            asyncio.run(self.svc.trending_loop())
        fetch.assert_called_once()
        self.assertTrue(self.svc.sources['github_trending'].startswith('error'))
        self.assertTrue(self.svc.sources['github_new'].startswith('ok'))

    def test_partial_horizon_batch_keeps_story_and_reports_error(self):
        items = horizon.parse((Path(__file__).parent / 'fixtures/horizon-en.md').read_text())[:2]
        briefing = config.DATA_DIR / 'briefing.md'
        briefing.write_text('fixture')
        self.svc._news_preparation_due = lambda lead: True

        async def done():
            self.svc.stopping = True

        self.svc.done.wait = AsyncMock(side_effect=done)
        with patch.object(daemon.plugins, 'on', return_value=True), \
             patch.object(horizon, 'run', return_value=briefing), \
             patch.object(horizon, 'parse', return_value=items), \
             patch.object(self.svc.editor, 'retell_news', side_effect=[ValueError('empty reply'), 'Короткая новость.']):
            asyncio.run(self.svc.horizon_loop())
        self.assertTrue(self.svc.sources['horizon'].startswith('degraded'), self.svc.sources)
        self.assertIn('1 new', self.svc.sources['horizon'])
        self.assertEqual(self.svc.db.execute('SELECT count(*) FROM packages').fetchone()[0], 1)

    def test_new_repository_failure_does_not_degrade_trending(self):
        self.svc.state = 'PLAYING'
        async def done():
            self.svc.stopping = True
        self.svc.done.wait = AsyncMock(side_effect=done)
        self.svc._news_preparation_due = lambda lead: True
        with patch.object(daemon.github_trending, 'fetch_html', return_value='fixture'), \
             patch.object(daemon.github_trending, 'parse_list', return_value=[]), \
             patch.object(daemon.github_new, 'fetch_new', side_effect=OSError('offline')), \
             patch.object(daemon.plugins, 'on', return_value=True):
            asyncio.run(self.svc.trending_loop())
        self.assertTrue(self.svc.sources['github_trending'].startswith('ok'))
        self.assertTrue(self.svc.sources['github_new'].startswith('degraded'))

    def test_first_source_queueing_an_edition_does_not_skip_second_source(self):
        self.svc.state = 'PLAYING'
        self.svc._news_preparation_due = lambda lead: True

        async def fetch(*args):
            self.svc._news_preparation_due = lambda lead: False
            return 3600

        async def done():
            self.svc.stopping = True

        self.svc.done.wait = AsyncMock(side_effect=done)
        with patch.object(daemon.plugins, 'on', return_value=True), \
             patch.object(self.svc, '_fetch_github', side_effect=fetch) as fetched:
            asyncio.run(self.svc.trending_loop())
        self.assertEqual([call.args for call in fetched.call_args_list], [(True, False), (False, True)])

    def test_disabling_new_tracks_cancels_only_its_own_queue(self):
        self.svc.engine = Mock(remove=AsyncMock())
        self.svc.fresh_queued = {'song.wav': '7'}
        self.svc.queued_break = ('8', ['news#1'])
        asyncio.run(self.svc.dispatch('plugin', {'name': 'new_tracks', 'on': False}))
        self.svc.engine.remove.assert_awaited_once_with('7', 'fresh')
        self.assertFalse(self.svc.fresh_queued)
        self.assertEqual(self.svc.queued_break, ('8', ['news#1']))

    def test_unplayed_priority_does_not_wait_for_clock_slots(self):
        svc = self.svc
        self.cfg['broadcast']['new_every_sets'] = 2
        svc.defaults = settings.base(self.cfg)
        svc.state = 'PLAYING'
        svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': time.time(), 'duration': 1}
        svc.engine = Mock()
        at = store.now().replace(minute=10, second=0, microsecond=0)  # outside the news edition's window

        async def queue(files):
            svc.fresh_queued = {str(files[0]): '7'}

        with patch.object(daemon.plugins, 'on', side_effect=lambda cfg, name: name == 'new_tracks'), \
             patch.object(svc, '_pick_new', return_value=[Path('/tmp/new.wav')]), \
             patch.object(svc, '_queue_fresh', side_effect=queue) as queued, \
             patch('radio.daemon.now', return_value=at) as clock:
            asyncio.run(svc.schedule())
            asyncio.run(svc.schedule())
            queued.assert_awaited_once_with([Path('/tmp/new.wav')])
            clock.return_value += timedelta(minutes=30)
            asyncio.run(svc.schedule())
            asyncio.run(svc.schedule())
            queued.assert_awaited_once_with([Path('/tmp/new.wav')])

    def test_content_source_errors_do_not_stop_scheduling(self):
        self.svc.rendering = True
        with patch.object(content, 'scan_inbox', side_effect=OSError('disk full')), \
             patch.object(self.svc, '_maybe_music_link', side_effect=OSError('offline')), \
             patch.object(self.svc, 'schedule', new_callable=AsyncMock) as schedule:
            asyncio.run(self.svc.content_tick())
        schedule.assert_awaited_once()
        self.assertTrue(self.svc.sources['inbox'].startswith('error'))
        self.assertTrue(self.svc.sources['music_facts'].startswith('error'))

    def test_weather_and_calendar_do_not_require_host_links(self):
        self.cfg['weather']['city'] = 'Da Lat'
        self.cfg['reminders']['calendar_url'] = 'https://example.test/calendar.ics'
        with patch.object(daemon.plugins, 'on', side_effect=lambda cfg, name: name != 'host_links'), \
             patch.object(reminders, 'day_due', return_value={'kind': 'day'}):
            self.assertTrue(self.svc._weather_enabled())
            self.assertEqual(self.svc._day_due(store.now()), {'kind': 'day'})
            self.assertTrue(any(r['kind'] == 'weather' for r in direction.rows(self.svc.db, self.cfg, store.now())))

    def test_new_tracks_reach_real_liquidsoap_without_news_or_host_links(self):
        svc = self.svc
        b = self.cfg['broadcast']
        b.update(new_every_sets=1, new_min_tracks=3, music_set_minutes=0.1, host_links=False)
        svc.defaults = settings.base(self.cfg)
        svc.files = []
        for i in range(4):
            f = config.DATA_DIR / f'track{i}.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                            f'sine=f={220 + i * 110}:d=12', '-ac', '2', str(f)], check=True)
            svc.files.append(f)
        m3u = config.RUN_DIR / 'music.m3u'
        svc.intro_rooms = music.write_m3u(svc.db, svc.files, m3u, -16, 6)
        svc.engine = Liquidsoap(m3u, 1, str(config.DATA_DIR / 'broadcast.wav'))
        at = store.now().replace(second=0, microsecond=0)
        at = at.replace(minute=at.minute // 30 * 30)

        async def scenario():
            try:
                await svc.engine.start(svc.on_event, [f'annotate:kind="music",ref="{svc.files[0]}":{svc.files[0]}'])
                svc.state = 'PLAYING'
                deadline = time.monotonic() + 10
                while not svc.current and time.monotonic() < deadline:
                    await asyncio.sleep(0.1)
                self.assertIsNotNone(svc.current)
                expected = svc._pick_new()[0]
                await svc.schedule()
                self.assertIn(str(expected), svc.fresh_queued)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    events = svc.db.execute("SELECT type,ref FROM events WHERE session_id=? ORDER BY id", (svc.session,)).fetchall()
                    if svc.current['ref'] == str(expected):
                        break
                    await asyncio.sleep(0.2)
                else:
                    self.fail('new-track set did not reach air without news')
                kinds = [(r['type'], r['ref']) for r in events]
                self.assertIn(('MUSIC_STARTED', str(expected)), kinds)
                self.assertFalse(any(kind == 'PACKAGE_STARTED' for kind, _ in kinds))
                self.assertIn('NEW_TRACKS_QUEUED', [kind for kind, _ in kinds])
            finally:
                await svc.engine.stop()

        with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}), patch('radio.daemon.now', return_value=at):
            asyncio.run(scenario())
