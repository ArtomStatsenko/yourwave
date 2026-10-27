import asyncio
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import test_direction
from radio import broadcast_clock, plugins, settings, store
from radio.daemon import Service
from radio.tts import MockTTS


class ClockPlayoutTests(unittest.TestCase):
    def setUp(self):
        fixture = test_direction.DirectionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.svc, self.clock = fixture, fixture.svc, fixture.clock
        self.at = fixture.AT
        self.svc.engine = Mock(remove=AsyncMock(), command=AsyncMock(return_value='OK'),
                               wait_music=AsyncMock(), push_news=AsyncMock(return_value='42'),
                               next_music=AsyncMock(return_value=''), push_station_id=AsyncMock(return_value='43'))
        plugins.set_plugin('host_links', False)
        self.svc.current = {'kind': 'music', 'ref': '/tmp/a.wav', 'started': 1000, 'duration': 180}

    def test_grid_is_identical_after_restart_and_ignores_session_counters(self):
        before = self.svc._clock_rows()
        self.svc.songs_since_break = 100
        self.svc.set_started = -12345
        restarted = Service(self.fixture.cfg, tts=MockTTS(), output='x')
        self.addCleanup(restarted.db.close)
        self.assertEqual(before, restarted._clock_rows())
        self.assertEqual([r['at'][14:16] for r in before[:3]], ['00', '30', '00'])

    def test_late_start_and_sleep_join_current_grid_without_catchup(self):
        key = self.svc._clock_rows()[0]['id']
        self.clock.return_value = self.at + timedelta(minutes=14, seconds=59)
        self.assertEqual(self.svc._clock_slot()['id'], key)
        self.clock.return_value = self.at + timedelta(minutes=15)
        self.assertIsNone(self.svc._clock_slot())
        self.clock.return_value = self.at + timedelta(hours=9, minutes=20)
        self.assertIsNone(self.svc._clock_slot())
        self.assertTrue(all(r['at'] > store.iso(self.clock.return_value) for r in self.svc._clock_rows()))

    def queue(self, ids=None):
        slot = self.svc._clock_rows()[0]
        self.svc.queued_clock, self.svc.queued_break = slot, ('42', ids or [])
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before,status,rid) VALUES(?,'news',?,'QUEUED','42')", (slot['id'], slot['at']))
        return slot

    def test_next_cancels_queued_edition_and_leaves_material_unconsumed(self):
        pid = self.fixture.add_news('one')
        self.svc.db.execute("UPDATE packages SET status='QUEUED' WHERE id=?", (pid,))
        slot = self.queue([pid])
        asyncio.run(self.svc.dispatch('next'))
        self.svc.engine.remove.assert_awaited_once_with('42')
        self.svc.engine.wait_music.assert_awaited_once()
        self.assertIsNone(self.svc.queued_break)
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'READY')
        self.assertEqual(self.svc._clock_slot()['id'], slot['id'])

    def test_pause_cancels_queue_and_resume_does_not_shift_slots(self):
        self.queue()
        with patch('radio.daemon.time.time', return_value=1000):
            asyncio.run(self.svc._pause())
        self.clock.return_value = self.at + timedelta(minutes=50)
        with patch('radio.daemon.time.time', return_value=3400):
            asyncio.run(self.svc._resume())
        self.svc.engine.remove.assert_awaited_once_with('42')
        self.assertIsNone(self.svc._clock_slot())
        self.assertEqual(self.svc._clock_rows()[0]['at'], store.iso(self.at + timedelta(hours=1)))

    def test_queued_audio_is_removed_when_its_window_expires(self):
        slot = self.queue()
        self.clock.return_value = self.at + timedelta(minutes=15)
        with patch('radio.daemon.time.time', return_value=1000):
            asyncio.run(self.svc.schedule())
        self.svc.engine.remove.assert_awaited_once_with('42')
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (slot['id'],)).fetchone()[0], 'EXPIRED')

    def test_ready_news_can_start_six_minutes_late_but_not_after_fifteen(self):
        pid=self.fixture.add_news('late')
        self.clock.return_value=self.at+timedelta(minutes=6)
        with patch('radio.daemon.time.time',return_value=1175):
            asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_awaited_once()
        self.assertEqual(self.svc.engine.push_news.call_args.kwargs['expires'],(self.at+timedelta(minutes=15)).timestamp())
        self.clock.return_value=self.at+timedelta(minutes=16)
        self.svc.engine.push_news.reset_mock()
        with patch('radio.daemon.time.time',return_value=1175):
            asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_not_awaited()
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?',(pid,)).fetchone()[0],'READY')

    def test_confirmed_start_is_never_replayed_even_after_clock_moves_back(self):
        slot = self.queue()
        self.svc.on_event({'kind': 'news', 'ref': 'one#1'})
        restarted = Service(self.fixture.cfg, tts=MockTTS(), output='x')
        self.addCleanup(restarted.db.close)
        self.assertFalse(any(r['id'] == slot['id'] for r in restarted._clock_rows()))
        self.clock.return_value = self.at - timedelta(minutes=1)
        self.assertFalse(any(r['id'] == slot['id'] for r in restarted._clock_rows()))

    def test_ten_manual_starts_outside_window_do_not_trigger_id(self):
        plugins.set_plugin('station_ids', True)
        self.clock.return_value = self.at + timedelta(minutes=10)
        with patch('radio.daemon.time.time', return_value=1000), patch.object(self.svc, '_song_seconds', return_value=180):
            for n in range(10):
                self.svc.on_event({'kind': 'music', 'ref': f'/tmp/{n}.wav'})
                asyncio.run(self.svc.schedule())
        self.svc.engine.push_station_id.assert_not_awaited()

    def test_branding_in_a_recent_edition_suppresses_separate_id(self):
        plugins.set_plugin('station_ids', True)
        self.svc.db.execute("INSERT INTO events(at,type) VALUES(?,'STATION_BRANDING')", (store.iso(self.at - timedelta(minutes=1)),))
        with patch('radio.daemon.time.time', return_value=1175):
            asyncio.run(self.svc.schedule())
        self.svc.engine.push_station_id.assert_not_awaited()

    def test_late_synthesis_cannot_queue_after_next(self):
        import threading
        pid = self.fixture.add_news('one')
        plugins.set_plugin('host_links', True)
        entered, release = threading.Event(), threading.Event()
        def assemble(*args):
            entered.set()
            release.wait(2)
            return Path('/tmp/test.wav'), 0
        async def scenario():
            task = asyncio.create_task(self.svc.schedule())
            while not entered.is_set():
                await asyncio.sleep(0)
            await self.svc.dispatch('next')
            release.set()
            await task
        with patch('radio.daemon.time.time', return_value=1150), patch.object(self.svc, '_assemble_break', side_effect=assemble):
            asyncio.run(scenario())
        self.svc.engine.push_news.assert_not_awaited()
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'READY')

    def test_short_edition_has_smaller_content_budget(self):
        for n in range(3):
            self.fixture.add_news(str(n))
        self.svc.db.execute('UPDATE packages SET speech_sec=20')
        main = self.svc._pick_break()
        self.clock.return_value = self.at + timedelta(minutes=30)
        light = self.svc._pick_break()
        self.assertEqual(len(main), 3)
        self.assertEqual(len(light), 1)

    def test_larger_editions_pick_more_stories_and_keep_the_hourly_limit(self):
        self.svc.cfg['broadcast']['max_break_speech_sec']=240
        self.svc.cfg['broadcast']['max_spoken_seconds_per_rolling_hour']=600
        for n in range(8):
            self.fixture.add_news(str(n))
        self.svc.db.execute('UPDATE packages SET speech_sec=30')
        self.assertEqual([r['duration'] for r in self.svc._clock_rows()[:2]],[240,90])
        self.assertEqual(len(self.svc._pick_break()),7)
        self.clock.return_value=self.at+timedelta(minutes=30)
        self.assertEqual(len(self.svc._pick_break()),2)
        with patch('radio.daemon.store.spoken_last_hour',return_value=570):
            self.assertEqual(len(self.svc._pick_break()),1)

    def test_preparation_continues_past_two_stories_until_the_edition_is_full(self):
        settings.set_setting('max_break_speech_sec',240)
        for n in range(8):
            self.fixture.add_news(str(n))
        self.svc.db.execute('UPDATE packages SET speech_sec=30')
        self.svc.db.execute("UPDATE packages SET status='PENDING' WHERE id NOT IN ('0#1','1#1')")
        async def tick():
            await self.svc.content_tick()
            await asyncio.sleep(0)
        with patch.object(self.svc,'schedule',new_callable=AsyncMock), \
                patch.object(self.svc,'_render_task',new_callable=AsyncMock) as render:
            asyncio.run(tick())
            render.assert_awaited_once()
            self.svc.rendering=False
            self.svc.db.execute("UPDATE packages SET status='READY' WHERE id!='7#1'")
            render.reset_mock()
            asyncio.run(tick())
            self.assertTrue(render.await_count,'Seven 30-second stories still leave room in a 228-second content budget')
            self.svc.rendering=False
            self.svc.db.execute("UPDATE packages SET status='READY'")
            render.reset_mock()
            asyncio.run(tick())
            render.assert_not_awaited()

    def test_preparation_skips_news_that_expires_before_its_edition(self):
        expiring=self.fixture.add_news('expiring')
        fresh=self.fixture.add_news('fresh')
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        self.svc.db.execute("UPDATE items SET published_at=? WHERE id='expiring'",
                            (store.iso(self.at-timedelta(hours=23,minutes=33)),))
        self.clock.return_value=self.at+timedelta(minutes=25)
        async def tick():
            await self.svc.content_tick()
            await asyncio.sleep(0)
        with patch.object(self.svc,'schedule',new_callable=AsyncMock), \
                patch.object(self.svc,'_render_task',new_callable=AsyncMock) as render:
            asyncio.run(tick())
            self.assertEqual(render.call_args.args[0]['id'],fresh)
            self.svc.rendering=False
            self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before,status) VALUES(?,'news',?,'PENDING')",
                                (expiring,store.iso(self.at+timedelta(minutes=30))))
            render.reset_mock()
            asyncio.run(tick())
            self.assertEqual(render.call_args.args[0]['id'],expiring,'Keep an explicit scheduled override')
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?',(expiring,)).fetchone()[0],'PENDING')

    def test_larger_editions_start_voice_preparation_earlier(self):
        self.fixture.add_news('one')
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        self.clock.return_value=self.at+timedelta(minutes=20)
        async def tick():
            await self.svc.content_tick()
            await asyncio.sleep(0)
        with patch.object(self.svc,'schedule',new_callable=AsyncMock), \
                patch.object(self.svc,'_render_task',new_callable=AsyncMock) as render:
            asyncio.run(tick())
            render.assert_not_awaited()
            settings.set_setting('max_break_speech_sec',240)
            asyncio.run(tick())
            render.assert_awaited_once()

    def test_silence_alarm_does_not_change_the_program_or_track(self):
        before = dict(self.svc.current)
        self.svc.on_event({'kind': 'health', 'ref': 'silence'})
        self.assertEqual(self.svc.current, before)
        self.assertIn('silent', self.svc.last_error)
        self.svc.on_event({'kind': 'health', 'ref': 'audible'})
        self.assertEqual(self.svc.last_error, '')

    def test_next_defers_reminder_without_leaving_a_stale_queue_lock(self):
        notice = {'kind': 'task', 'key': 'todoist:call', 'title': 'Call', 'start': self.at + timedelta(minutes=10)}
        self.svc.queued_reminders['todoist:call'] = ('7', notice)
        self.svc.id_pending = True
        self.svc.db.execute("INSERT INTO air_plan(id,kind,ref,status,rid) VALUES('reminder:call','reminder','todoist:call','QUEUED','7')")
        asyncio.run(self.svc.dispatch('next'))
        self.assertFalse(self.svc.id_pending)
        self.assertFalse(self.svc.db.execute("SELECT 1 FROM air_plan WHERE status='QUEUED'").fetchone())
        self.assertIn('todoist:call', self.svc.reminders_due)

    def test_queue_push_racing_with_next_is_removed_before_it_can_air(self):
        self.fixture.add_news('one')
        async def pushed(*args, **kwargs):
            self.svc.schedule_generation += 1
            return '42'
        self.svc.engine.push_news.side_effect = pushed
        with patch('radio.daemon.time.time', return_value=1000):
            asyncio.run(self.svc.schedule())
        self.svc.engine.remove.assert_awaited_once_with('42')
        self.assertIsNone(self.svc.queued_break)

    def test_clock_rows_distinguish_routine_slots_from_manual_edits(self):
        row = self.svc._clock_rows()[0]
        self.assertTrue(row['clock'])
        self.assertFalse(row['edited'])
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,'news',?)",
                            (row['id'], store.iso(self.at + timedelta(minutes=10))))
        edited = next(r for r in self.svc._clock_rows() if r['id'] == row['id'])
        self.assertTrue(edited['clock'])
        self.assertTrue(edited['edited'])
