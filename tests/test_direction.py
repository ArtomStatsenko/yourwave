import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, content, direction, plugins, store, weather
from radio.daemon import Service
from radio.tts import MockTTS


class DirectionTests(unittest.TestCase):
    AT = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)  # 21:00 station time

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-direction-')
        self.addCleanup(tmp.cleanup)
        for key, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run'), ('SOCK', Path(tmp.name) / 'run/radio.sock')]:
            p = patch.object(config, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config.load()
        self.cfg['weather']['city'] = 'Da Lat'
        self.svc = Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)
        self.svc.library = set()
        self.svc.state = 'PLAYING'
        plugins.set_plugin('air_direction', True)
        clock = patch('radio.daemon.now', return_value=self.AT)
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        content_clock = patch('radio.content.now', return_value=self.AT)
        content_clock.start()
        self.addCleanup(content_clock.stop)

    def add_news(self, name, part_text='Текст новости.'):
        content.add_item(self.svc.db, self.cfg, {'id': name, 'topic': name, 'source': 'inbox', 'url': '',
                         'body': part_text, 'published_at': self.AT}, 'test')
        self.svc.db.execute("UPDATE packages SET status='READY',speech_sec=5,audio_path='/tmp/test.wav' WHERE item_id=?", (name,))
        return name + '#1'

    def command(self, **req):
        return asyncio.run(self.svc.dispatch('direction', req))

    def save_plan(self, snapshot, changes):
        return self.command(action='save', version=snapshot['version'], changes=changes)

    def preparation_section(self, minutes):
        pid = self.add_news('preparation')
        self.svc.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (pid,))
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': row['id'], 'at': store.iso(self.AT + timedelta(minutes=minutes))}])
        return pid

    def test_preparation_follows_section_time_and_skip(self):
        pid = self.preparation_section(240)
        self.assertFalse(self.svc._news_preparation_due(900))
        self.assertFalse(self.svc._news_preparation_due(300))
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'news' and pid in r.get('content_members', []))
        self.assertTrue(row['text_ready'])
        self.assertFalse(row['preparation_started'])
        self.clock.return_value = self.AT + timedelta(minutes=230)
        self.assertTrue(self.svc._news_preparation_due(900))
        self.assertFalse(self.svc._news_preparation_due(300))
        self.clock.return_value = self.AT + timedelta(minutes=236)
        self.assertTrue(self.svc._news_preparation_due(300))
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news' and not r['skipped'])
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        self.assertFalse(self.svc._news_preparation_due(300))
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'PENDING')

    def test_skipped_section_leaves_the_plan_once_its_time_passes(self):
        pid = self.preparation_section(10)
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if pid in r.get('content_members', []))
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        self.clock.return_value = self.AT + timedelta(minutes=10 + self.cfg['broadcast']['music_set_minutes'] - 1)
        self.assertTrue(any(r['skipped'] for r in self.svc._broadcast_plan()['rows']))
        self.clock.return_value = self.AT + timedelta(minutes=10 + self.cfg['broadcast']['music_set_minutes'])
        self.assertFalse(any(r['skipped'] for r in self.svc._broadcast_plan()['rows']))

    def test_voice_waits_until_five_minutes_and_keeps_existing_material(self):
        pid = self.preparation_section(10)
        async def tick():
            await self.svc.content_tick()
            await asyncio.sleep(0)
        with patch.object(self.svc, 'schedule', new_callable=AsyncMock), patch.object(self.svc, '_render_task', new_callable=AsyncMock) as render:
            asyncio.run(tick())
            render.assert_not_called()
            self.clock.return_value = self.AT + timedelta(minutes=6)
            asyncio.run(tick())
            self.assertEqual(render.call_args.args[0]['id'], pid)
        self.assertTrue(Path(self.svc.db.execute('SELECT script_path FROM packages WHERE id=?', (pid,)).fetchone()[0]).is_file())

    def test_voice_ignores_distant_and_disabled_packages_without_blocking_nearby_news(self):
        distant = self.add_news('distant')
        disabled = self.add_news('disabled')
        near = self.add_news('near')
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        self.svc.db.execute("UPDATE items SET source='horizon' WHERE id='disabled'")
        plugins.set_plugin('horizon', False)
        self.command(action='time', id=distant, at=store.iso(self.AT + timedelta(hours=4)))
        async def tick():
            await self.svc.content_tick()
            await asyncio.sleep(0)
        with patch.object(self.svc, 'schedule', new_callable=AsyncMock), patch.object(self.svc, '_render_task', new_callable=AsyncMock) as render:
            self.svc.state = 'PAUSED'
            asyncio.run(tick())
            render.assert_not_called()
            self.svc.state = 'PLAYING'
            asyncio.run(tick())
            self.assertEqual(render.call_args.args[0]['id'], near)

    def test_sources_wait_for_nearby_section_and_pause(self):
        self.preparation_section(240)
        plugins.set_plugin('horizon', True)
        async def finish_tick():
            self.svc.stopping = True
        for minutes, state, expected in [(0, 'PLAYING', 0), (230, 'PAUSED', 0), (230, 'PLAYING', 1)]:
            self.clock.return_value = self.AT + timedelta(minutes=minutes)
            self.svc.state = state
            for loop in (self.svc.trending_loop, self.svc.horizon_loop):
                self.svc.stopping = False
                briefing = Path(config.DATA_DIR / 'briefing.md')
                briefing.write_text('test')
                with patch.object(self.svc.done, 'wait', side_effect=finish_tick), \
                     patch.object(self.svc, '_fetch_github', new_callable=AsyncMock, return_value=3600) as github, \
                     patch('radio.daemon.horizon.run', return_value=briefing) as horizon_run, \
                     patch('radio.daemon.horizon.parse', return_value=[]), \
                     patch.object(self.svc, '_take_horizon', return_value='nothing new'):
                    asyncio.run(loop())
                self.assertEqual(github.call_count if loop == self.svc.trending_loop else horizon_run.call_count, expected)

    def test_weather_and_multiple_sources_share_one_edition_and_skip(self):
        news = [self.add_news(name) for name in ('technology', 'github')]
        self.svc.db.execute("UPDATE items SET source='horizon' WHERE id='technology'")
        self.svc.db.execute("UPDATE items SET source='github_trending' WHERE id='github'")
        plugins.set_plugin('horizon', True)
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': 1000, 'duration': 20}
        self.svc.set_started = 1000
        key = 'weather:slot:' + str(int(self.AT.timestamp()))
        report = {'key': key, 'kind': 'weather', 'start': self.AT, 'expires': self.AT + timedelta(minutes=30),
                  'text': 'Далат: плюс 20 градусов, без дождя.', 'summary': 'Da Lat · 20° · clear'}
        self.svc.reminders_due[key] = report
        self.svc.engine = Mock(next_music=AsyncMock(return_value=''), push_news=AsyncMock(return_value='51'), remove=AsyncMock())
        with patch('radio.daemon.time.time', return_value=1000), patch('radio.audio.duration', return_value=5), \
             patch.object(self.svc, '_render_weather', return_value=Path('/tmp/weather.wav')), \
             patch.object(self.svc, '_new_set_due', return_value=False), \
             patch.object(self.svc, '_assemble_break', return_value=(Path('/tmp/edition.wav'), 0)) as assemble:
            asyncio.run(self.svc.schedule())
            self.svc.engine.push_news.assert_awaited_once()
            expected = ['system:' + key + '#1', *news]
            self.assertEqual(self.svc.queued_break[1], expected)
            self.assertEqual([p['id'] for p in assemble.call_args.args[0]], expected)
            snap = self.svc._broadcast_plan()
            queued = [r for r in snap['rows'] if r['locked']]
            self.assertEqual(len(queued), 1)
            self.assertEqual(queued[0]['title'], 'Host edition · Weather')
            self.assertEqual({seg['title'] for seg in queued[0]['segments']}, {'Weather', 'Tech news', 'GitHub trends'})
            self.save_plan(snap, [{'id': queued[0]['id'], 'at': queued[0]['at'], 'skipped': True, 'skip_only': True}])
            self.svc.engine.remove.assert_awaited_once_with('51')
            self.assertIsNone(self.svc.queued_break)
            self.assertFalse(self.svc._break_due(20))
            asyncio.run(self.svc.schedule())
            self.svc.engine.push_news.assert_awaited_once()
            self.assertFalse(self.svc.db.execute('SELECT 1 FROM reminders WHERE key=?', (key,)).fetchone())

    def test_music_stories_are_optional_until_selected_for_an_edition(self):
        plugins.set_plugin('music_facts', True)
        pid = self.add_news('a story')
        before = [tuple(r) for r in self.svc.db.execute('SELECT id,status FROM packages')]
        rows = self.svc._broadcast_plan()['rows']
        future = next(r for r in rows if r['kind'] == 'news' and r.get('placeholder'))
        optional = next(s for s in future['segments'] if s['title'] == 'Music stories')
        self.assertTrue(optional['optional'])
        actual = next(r for r in rows if pid in r['members'])
        self.assertFalse(any(s['title'] == 'Music stories' for s in actual['segments']))
        self.svc.db.execute("UPDATE items SET source='music_facts'")
        selected = next(r for r in self.svc._broadcast_plan()['rows'] if pid in r['members'])
        self.assertEqual(selected['title'], 'Host edition · Music stories')
        self.assertNotIn('optional', selected['segments'][0])
        self.assertTrue(selected['segments'][0]['voice_ready'])
        self.assertEqual(before, [tuple(r) for r in self.svc.db.execute('SELECT id,status FROM packages')])
        self.assertEqual(self.svc.editor.calls, 0)

    def test_edited_weather_is_displayed_once(self):
        plugins.set_plugin('weather', True)
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'weather')
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,?,?)",
                            (row['id'], 'weather', store.iso(self.AT + timedelta(minutes=10))))
        rows = self.svc._broadcast_plan()['rows']
        self.assertEqual(sum(r['id'] == row['id'] for r in rows), 1)

    def test_clock_weather_is_identifiable_before_material_is_prepared(self):
        plugins.set_plugin('weather', True)
        rows = self.svc._broadcast_plan()['rows']
        weather_row = next(r for r in rows if r['kind'] == 'weather')
        self.assertEqual(weather_row['title'], 'Host edition · Weather')
        self.assertEqual(weather_row['segments'][0]['title'], 'Weather')
        self.assertFalse(weather_row['voice_ready'])

    def test_edition_material_is_expandable_without_model_calls(self):
        self.add_news('a story')
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.assertTrue(row['edition'])
        self.assertEqual(row['title'], 'Host edition')
        self.assertEqual(row['segments'][0]['items'], ['a story'])
        self.assertTrue(row['segments'][0]['voice_ready'])
        unknown = next(r for r in snap['rows'] if r['kind'] == 'news' and r.get('placeholder'))
        self.assertTrue(unknown['segments'])
        self.assertTrue(all(not seg['items'] for seg in unknown['segments']))
        self.assertFalse(unknown['preparation_started'])
        self.assertTrue(all(not seg['preparation_started'] for seg in unknown['segments']))
        self.assertTrue(row['preparation_started'])
        self.assertEqual(self.svc.editor.calls, 0)

    def test_timeline_plan_uses_ready_bulletins_player_selection_and_nearby_clock_slots(self):
        ids = [self.add_news(x) for x in ('one', 'two')]
        self.svc.next_track = '/tmp/Artist - Selected.wav'
        snapshot = self.svc._broadcast_plan()
        bulletin = next(r for r in snapshot['rows'] if r['kind'] == 'news')
        self.assertEqual(set(bulletin['members']), set(ids))
        self.assertEqual(bulletin['status'], 'READY')
        selected = next(r for r in snapshot['rows'] if r['kind'] == 'music' and r['content_title'])
        self.assertFalse(selected['locked'])
        self.assertEqual(selected['title'], 'Music')
        self.assertEqual(selected['content_title'], 'Artist - Selected')
        self.assertTrue(all(r['id'].startswith('section:music:') for r in snapshot['rows'] if r['kind'] == 'music'))
        self.assertEqual(len([r for r in snapshot['rows'] if r['kind'] == 'weather']), 2)
        self.assertGreaterEqual(len(snapshot['rows']), 12)
        self.assertTrue(any(r.get('placeholder') and r['kind'] == 'music' for r in snapshot['rows']))
        self.assertEqual(self.svc.editor.calls, 0)

    def test_queued_replay_is_planned_before_a_waiting_edition(self):
        self.add_news('one')
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': 1000, 'duration': 180}
        self.svc.next_track = '/tmp/Artist - Shuffle.wav'
        self.svc.replays['r1'] = {'kind': 'music', 'ref': '/tmp/Artist - Replay.wav', 'rid': '7', 'duration': 120}
        with patch('radio.daemon.time.time', return_value=1100):
            rows = self.svc._broadcast_plan()['rows']
        news = next(r for r in rows if r['kind'] == 'news' and not r.get('placeholder'))
        before = [r for r in rows if r['kind'] == 'music' and r['at'] < news['at']]
        after = next(r for r in rows if r['kind'] == 'music' and r['at'] >= news['at'])
        self.assertEqual(news['status'], 'READY')
        self.assertEqual(news['at'], store.iso(self.AT + timedelta(seconds=80 + 120)))  # after the song and the replay
        self.assertEqual([r['content_title'] for r in before], ['Artist - Replay'])
        self.assertEqual(after['content_title'], 'Artist - Shuffle')

    def task_reminder(self, minutes):
        plugins.set_plugin('reminders_todoist', True)
        start = self.AT + timedelta(minutes=minutes)
        self.svc.todoist_tasks = [{'id': 'call', 'content': 'Созвон с командой', 'due': {'date': start.strftime('%Y-%m-%dT%H:%M:%SZ')}}]
        return next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'reminder'), start

    def test_upcoming_reminder_is_planned_before_its_lead(self):
        row, start = self.task_reminder(60)
        self.assertEqual(row['at'], store.iso(start - timedelta(minutes=self.cfg['reminders']['lead_min'])))
        self.assertEqual(row['content_title'], 'Созвон с командой')
        self.assertFalse(self.svc.reminders_due)                            # shown, not yet due

    def test_reminder_moved_earlier_becomes_due(self):
        row, start = self.task_reminder(60)
        snap = self.svc._broadcast_plan()
        self.save_plan(snap, [{'id': row['id'], 'at': store.iso(self.AT + timedelta(minutes=5))}])
        self.clock.return_value = self.AT + timedelta(minutes=5)
        with patch.object(self.svc, '_expire_reminders', new_callable=AsyncMock):
            asyncio.run(self.svc._reminders_tick())
        self.assertIn(row['ref'], self.svc.reminders_due)

    def test_reminder_moved_past_the_start_airs_until_the_end(self):
        row, start = self.task_reminder(10)
        snap = self.svc._broadcast_plan()
        with self.assertRaises(ValueError):                                 # a task has 15 min after its time
            self.save_plan(snap, [{'id': row['id'], 'at': store.iso(start + timedelta(minutes=15))}])
        self.save_plan(snap, [{'id': row['id'], 'at': store.iso(start + timedelta(minutes=5))}])
        r = {'key': row['ref'], 'kind': 'task', 'start': start, 'end': start, 'title': 'Созвон с командой'}
        self.svc.reminders_due = {r['key']: r}
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='9'))
        self.svc._render_notice = lambda text, fallback='': Path('/tmp/notice.wav')
        self.svc.state = 'PLAYING'
        self.assertFalse(asyncio.run(self.svc._remind(30)))                 # not before its new time
        self.clock.return_value = start + timedelta(minutes=5)
        self.assertTrue(asyncio.run(self.svc._remind(30)))
        self.assertIn('шесть минут назад', self.svc.reminder_text[r['key']])  # airs 30 s later
        self.assertIn(r['key'], self.svc.queued_reminders)
        rows = self.svc._broadcast_plan()['rows']
        self.assertEqual(next(x for x in rows if x['kind'] == 'reminder')['status'], 'QUEUED')
        self.clock.return_value = start + timedelta(minutes=15)             # over: the queued notice is withdrawn
        self.svc.engine.remove = AsyncMock()
        asyncio.run(self.svc._expire_reminders())
        self.assertFalse(self.svc.queued_reminders)
        self.assertNotIn(r['key'], self.svc.reminders_due)

    def test_saved_slot_overlapping_the_edition_is_one_block(self):
        pid = self.add_news('one')
        plugins.set_plugin('horizon', True)
        rows = self.svc._broadcast_plan()['rows']
        edition = next(r for r in rows if pid in [*r['members'], *r.get('content_members', [])])
        slot = next(r for r in rows if r['id'].startswith('section:news:future:'))
        when = self.AT + timedelta(minutes=20)
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,?,?)", (slot['id'], 'news', store.iso(when - timedelta(seconds=15))))
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,?,?)", (edition['id'], 'news', store.iso(when)))
        news = [r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'news' and content.parse_time(r['at']) < when + timedelta(minutes=5)]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]['id'], edition['id'])
        self.assertEqual(news[0]['at'], store.iso(when))
        self.assertEqual(news[0]['status'], 'READY')

    def test_saved_order_remains_the_same_and_does_not_duplicate_music_placeholder(self):
        self.svc.db.execute('INSERT INTO reminders(key,fired_at) VALUES(?,?)',
                            ('weather:slot:' + str(int(self.AT.timestamp())), store.iso(self.AT)))
        self.add_news('one')
        self.add_news('two')
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': 1000, 'duration': 180}
        self.svc.next_track = '/tmp/selected.wav'
        self.command(action='insert', kind='weather', at=store.iso(self.AT + timedelta(minutes=12)))
        with patch('radio.daemon.time.time', return_value=1000):
            snap = self.svc._broadcast_plan()
            news = next(r for r in snap['rows'] if r['kind'] == 'news')
            music = next(r for r in snap['rows'] if r.get('placeholder') and r['kind'] == 'music')
            weather = next(r for r in snap['rows'] if r['kind'] == 'weather')
            when = self.AT + timedelta(minutes=10)
            saved = self.save_plan(snap, [{'id': news['id'], 'at': news['at'], 'rank': 0},
                                         {'id': weather['id'], 'at': store.iso(when), 'rank': 2}])
        self.assertEqual([r['kind'] for r in saved['broadcast_plan']['rows'][:4]], ['news', 'music', 'weather', 'music'])
        self.assertEqual(len([r for r in saved['broadcast_plan']['rows'] if r['kind'] == 'news' and not r.get('placeholder')]), 1)

    def test_plan_save_is_atomic_and_rejects_changed_or_locked_snapshots(self):
        pid = self.add_news('one')
        self.svc.db.execute("UPDATE packages SET status='QUEUED' WHERE id=?", (pid,))
        self.svc.queued_break = ('90', [pid])
        snap = self.svc._broadcast_plan()
        news = next(r for r in snap['rows'] if r['kind'] == 'weather')
        locked = next(r for r in snap['rows'] if r['locked'])
        due = store.iso(self.AT + timedelta(minutes=10))
        with self.assertRaises(ValueError):
            self.save_plan(snap, [{'id': news['id'], 'at': due}, {'id': locked['id'], 'at': due}])
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM air_plan').fetchone()[0], 0)
        self.svc.queued_break = None
        self.svc.db.execute("UPDATE packages SET status='READY'")
        with self.assertRaisesRegex(ValueError, 'schedule changed'):
            self.save_plan(snap, [{'id': news['id'], 'at': due}])
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM air_plan').fetchone()[0], 0)

    def test_plan_defers_skips_and_restores_a_real_bulletin_as_one_block(self):
        ids = [self.add_news(x) for x in ('one', 'two')]
        due = store.iso(self.AT + timedelta(minutes=15))
        snap = self.svc._broadcast_plan()
        news = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': news['id'], 'at': due, 'rank': 1}])
        self.assertEqual(self.svc._pick_break(), [])
        self.clock.return_value = self.AT + timedelta(minutes=15)
        self.assertEqual({r['id'] for r in self.svc._pick_break()}, set(ids))
        snap = self.svc._broadcast_plan()
        self.save_plan(snap, [{'id': news['id'], 'at': due, 'skipped': True}])
        self.assertEqual(self.svc._pick_break(), [])
        snap = self.svc._broadcast_plan()
        skipped = next(r for r in snap['rows'] if r.get('skipped'))
        self.save_plan(snap, [{'id': skipped['id'], 'at': due, 'skipped': False}])
        self.assertEqual({r['id'] for r in self.svc._pick_break()}, set(ids))

    def test_placeholder_news_waits_for_preparation_and_obeys_edited_section_time(self):
        from unittest.mock import AsyncMock
        snap = self.svc._broadcast_plan()
        placeholder = next(r for r in snap['rows'] if r['kind'] == 'news')
        due = self.AT + timedelta(minutes=10)
        self.save_plan(snap, [{'id': placeholder['id'], 'at': store.iso(due)}])
        self.assertFalse(self.svc._break_due(0))
        pid = self.add_news('waiting')
        self.svc.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (pid,))
        self.svc.engine = Mock(push_news=AsyncMock(return_value='80'))
        self.clock.return_value = due
        self.assertFalse(asyncio.run(self.svc._direction_tick()))
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (placeholder['id'],)).fetchone()[0], 'PENDING')
        self.svc.engine.push_news.assert_not_awaited()
        self.svc.db.execute("UPDATE packages SET status='READY' WHERE id=?", (pid,))
        import time
        plugins.set_plugin('host_links', False)
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': time.time(), 'duration': 2}
        asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_awaited_once()
        self.svc.on_event({'kind': 'news', 'ref': pid})
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (placeholder['id'],)).fetchone()[0], 'PLAYED')

    def test_edited_unknown_section_survives_restart_and_resolves_without_duplicate_bulletin(self):
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        due = store.iso(self.AT + timedelta(minutes=20))
        self.save_plan(snap, [{'id': row['id'], 'at': due}])
        self.add_news('Now known')
        restarted = Service(self.cfg, tts=MockTTS(), output='x')
        try:
            self.assertFalse(restarted._break_due(0))
            news = [r for r in restarted._broadcast_plan()['rows'] if r['kind'] == 'news' and not r.get('placeholder')]
            self.assertEqual(len(news), 1)
            self.assertEqual(news[0]['id'], row['id'])
            self.assertEqual(news[0]['title'], 'Host edition')
            self.assertEqual(news[0]['segments'][0]['items'], ['Now known'])
            self.assertEqual(news[0]['at'], due)
        finally:
            restarted.db.close()

    def test_skipping_unknown_news_does_not_suppress_later_breaks_forever(self):
        self.svc.songs_since_break = 1
        snap = self.svc._broadcast_plan()
        placeholder = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': placeholder['id'], 'at': store.iso(self.AT), 'skipped': True}])
        self.assertFalse(self.svc._break_due(0))
        self.clock.return_value = self.AT + timedelta(minutes=self.cfg['broadcast']['music_set_minutes'] + 1)
        self.assertTrue(self.svc._break_due(0))

    def test_existing_reminder_can_be_deferred_without_losing_original_details(self):
        from unittest.mock import AsyncMock
        plugins.set_plugin('reminders_todoist', True)
        r = {'key': 'todoist:planned', 'kind': 'task', 'title': 'Standup', 'description': 'Original text',
             'start': self.AT + timedelta(minutes=20)}
        self.svc.reminders_due[r['key']] = r
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'reminder')
        self.save_plan(snap, [{'id': row['id'], 'at': store.iso(self.AT + timedelta(minutes=5))}])
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='81'))
        self.assertFalse(asyncio.run(self.svc._remind(20)))
        self.svc.engine.push_replay.assert_not_awaited()
        self.clock.return_value = self.AT + timedelta(minutes=5)
        with patch.object(self.svc, '_render_notice', return_value=Path('/tmp/notice.wav')):
            self.assertTrue(asyncio.run(self.svc._remind(20)))
        self.assertIn('Original text', self.svc.reminder_text[r['key']])

    def test_news_skip_covers_new_topics_but_not_the_next_music_set(self):
        first = self.add_news('one')
        self.svc.songs_since_break = 1
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        second = self.add_news('two')
        self.assertFalse(self.svc._break_due(0))
        self.assertEqual(self.svc._pick_break(), [])
        self.assertEqual({r['status'] for r in self.svc.db.execute('SELECT status FROM packages')}, {'READY'})
        self.clock.return_value = self.AT + timedelta(minutes=self.cfg['broadcast']['music_set_minutes'] + 1)
        self.assertTrue(self.svc._break_due(0))
        self.assertEqual({r['id'] for r in self.svc._pick_break()}, {first, second})

    def test_future_section_skip_does_not_skip_the_nearer_news(self):
        self.add_news('one')
        snap = self.svc._broadcast_plan()
        future = next(r for r in snap['rows'] if r['id'].startswith('section:news:future:'))
        self.save_plan(snap, [{'id': future['id'], 'at': future['at'], 'skipped': True}])
        self.assertTrue(self.svc._pick_break())
        self.clock.return_value = content.parse_time(future['at'])
        self.assertEqual(self.svc._pick_break(), [])

    def test_reminder_skip_requires_confirmation_and_does_not_modify_the_task(self):
        from unittest.mock import AsyncMock
        plugins.set_plugin('reminders_todoist', True)
        r = {'key': 'todoist:confirm', 'kind': 'task', 'title': 'Standup', 'description': 'Original',
             'start': self.AT + timedelta(minutes=20)}
        self.svc.reminders_due[r['key']] = r
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'reminder')
        change = {'id': row['id'], 'at': row['at'], 'skipped': True}
        with self.assertRaisesRegex(ValueError, 'Confirm'):
            self.save_plan(snap, [change])
        change['skip_confirmed'] = True
        self.save_plan(snap, [change])
        self.svc.engine = Mock(push_replay=AsyncMock())
        self.assertFalse(asyncio.run(self.svc._remind(20)))
        self.svc.engine.push_replay.assert_not_awaited()
        self.assertEqual(self.svc.reminders_due[r['key']], r)

    def test_weather_skip_discards_waiting_report_and_allows_the_next_slot(self):
        from unittest.mock import AsyncMock
        self.svc.engine = Mock(push_replay=AsyncMock())
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'weather')
        self.svc.reminders_due[row['id']] = {'key': row['id'], 'kind': 'weather', 'start': self.AT,
                                          'expires': self.AT + timedelta(minutes=30), 'text': 'Weather'}
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        self.assertFalse(asyncio.run(self.svc._remind(5)))
        self.assertNotIn(row['id'], self.svc.reminders_due)
        self.svc.engine.push_replay.assert_not_awaited()
        self.assertTrue(any(r['kind'] == 'weather' and not r['skipped'] for r in self.svc._broadcast_plan()['rows']))

    def test_skip_queued_bulletin_cancels_the_whole_break_without_consuming_topics(self):
        from unittest.mock import AsyncMock
        ids = [self.add_news(x) for x in ('one', 'two')]
        self.svc.db.execute("UPDATE packages SET status='QUEUED'")
        self.svc.queued_break = ('99', ids)
        self.svc.engine = Mock(remove=AsyncMock())
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True, 'skip_only': True}])
        self.svc.engine.remove.assert_awaited_once_with('99')
        self.assertIsNone(self.svc.queued_break)
        self.assertEqual(self.svc._pick_break(), [])
        self.assertEqual({r[0] for r in self.svc.db.execute('SELECT status FROM packages')}, {'READY'})

    def test_reminder_skip_during_synthesis_prevents_queueing(self):
        from unittest.mock import AsyncMock
        import time
        plugins.set_plugin('reminders_todoist', True)
        r = {'key': 'todoist:rendering', 'kind': 'task', 'title': 'Standup', 'start': self.AT + timedelta(minutes=20)}
        self.svc.reminders_due[r['key']] = r
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': time.time(), 'duration': 180}
        self.svc.engine = Mock(push_replay=AsyncMock())
        def skip_while_rendering(text):
            self.svc.db.execute("INSERT INTO air_plan(id,kind,skipped) VALUES(?,'reminder',1)", ('reminder:' + r['key'],))
            return Path('/tmp/notice.wav')
        with patch.object(self.svc, '_render_notice', side_effect=skip_while_rendering):
            self.assertFalse(asyncio.run(self.svc._remind(120)))
        self.svc.engine.push_replay.assert_not_awaited()

    def test_skipping_directed_reminder_also_skips_its_original_occurrence(self):
        from unittest.mock import AsyncMock
        plugins.set_plugin('reminders_todoist', True)
        r = {'key': 'todoist:queued', 'kind': 'task', 'title': 'Standup', 'start': self.AT + timedelta(minutes=20)}
        self.svc.queued_reminders[r['key']] = ('88', r)
        job = self.command(action='insert', kind='reminder', at=store.iso(self.AT))['id']
        self.svc.db.execute("UPDATE air_plan SET status='QUEUED',rid='88',ref=? WHERE id=?", (r['key'], job))
        self.svc.engine = Mock(remove=AsyncMock(), push_replay=AsyncMock())
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['id'] == job)
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True, 'skip_only': True, 'skip_confirmed': True}])
        self.assertFalse(asyncio.run(self.svc._remind(20)))
        self.svc.engine.push_replay.assert_not_awaited()

    def test_music_gap_cannot_change_the_section_schedule(self):
        self.add_news('one')
        snap = self.svc._broadcast_plan()
        gap = next(r for r in snap['rows'] if r['kind'] == 'music')
        with self.assertRaisesRegex(ValueError, 'Music fills the gaps'):
            self.save_plan(snap, [{'id': gap['id'], 'at': store.iso(self.AT), 'rank': 0}])
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM air_plan').fetchone()[0], 0)

    def test_track_selection_does_not_change_section_identity_or_draft_version(self):
        self.add_news('one')
        self.svc.next_track = '/tmp/First.wav'
        before = self.svc._broadcast_plan()
        self.svc.next_track = '/tmp/Second.wav'
        after = self.svc._broadcast_plan()
        self.assertEqual(before['version'], after['version'])
        self.assertEqual([r['id'] for r in before['rows']], [r['id'] for r in after['rows']])
        self.assertIn('Second', next(r['content_title'] for r in after['rows'] if r['kind'] == 'music'))

    def test_skipped_marker_belongs_only_to_the_selected_news_section(self):
        self.add_news('one')
        self.command(action='insert', kind='news', at=store.iso(self.AT + timedelta(minutes=5)))
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news' and r.get('members') == ['one#1'])
        saved = self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        self.assertEqual([r['id'] for r in saved['broadcast_plan']['rows'] if r['kind'] == 'news' and r['skipped']], [row['id']])

    def test_postponed_section_is_not_replaced_with_other_ready_topics(self):
        plugins.set_plugin('host_links', False)
        self.cfg['broadcast']['max_break_speech_sec'] = 5
        self.add_news('one')
        self.add_news('two')
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        due = store.iso(self.AT + timedelta(minutes=10))
        saved = self.save_plan(snap, [{'id': row['id'], 'at': due, 'skipped': False}])
        self.assertFalse(self.svc._break_due(0))
        self.assertEqual([r['id'] for r in saved['broadcast_plan']['rows'] if r['kind'] == 'news' and r['at'] < due and not r['skipped']], [])

    def test_skip_then_drag_save_preserves_section_count_and_a_single_skip(self):
        self.add_news('one')
        self.add_news('two')
        snap = self.svc._broadcast_plan()
        first = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': first['id'], 'at': first['at'], 'skipped': True}])
        snap = self.svc._broadcast_plan()
        rows = snap['rows']
        count = sum(r['kind'] == 'news' for r in rows)
        source = next(i for i, r in enumerate(rows) if r['kind'] == 'news' and not r['skipped'])
        moved = rows.pop(source)
        rows.insert(source + 2, moved)
        cursor, changes = content.parse_time(snap['starts_at']), []
        for rank, row in enumerate(rows):
            if row['locked']:
                cursor = max(cursor, content.parse_time(row['at']) + timedelta(seconds=row['duration']))
                continue
            if row['kind'] != 'music':
                changes.append({'id': row['id'], 'at': store.iso(cursor), 'skipped': row['skipped'], 'rank': rank})
            cursor += timedelta(seconds=0 if row['skipped'] else row['duration'])
        saved = self.save_plan(snap, changes)['broadcast_plan']['rows']
        self.assertEqual([r['id'] for r in saved if r['kind'] == 'news' and r['skipped']], [first['id']])
        self.assertEqual(sum(r['kind'] == 'news' for r in saved), count)
        self.assertEqual(sum(r['id'] == moved['id'] for r in saved), 1)

    def test_explicitly_moved_next_section_can_air_after_skipping_the_previous_one(self):
        self.add_news('one')
        snap = self.svc._broadcast_plan()
        rows = [r for r in snap['rows'] if r['kind'] == 'news']
        first, second = rows[:2]
        due = self.AT + timedelta(minutes=5)
        self.save_plan(snap, [{'id': first['id'], 'at': first['at'], 'skipped': True},
                              {'id': second['id'], 'at': store.iso(due), 'skipped': False}])
        self.assertFalse(self.svc._break_due(0))
        self.clock.return_value = due
        self.assertTrue(self.svc._break_due(0))
        self.assertTrue(self.svc._pick_break())

    def test_editing_a_previously_saved_topic_schedule_converts_it_to_one_section(self):
        pid = self.add_news('one')
        self.command(action='time', id=pid, at=store.iso(self.AT + timedelta(minutes=5)))
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if pid in r.get('legacy_members', []))
        due = store.iso(self.AT + timedelta(minutes=10))
        saved = self.save_plan(snap, [{'id': row['id'], 'at': due, 'skipped': False}])
        self.assertIsNone(self.svc.db.execute('SELECT 1 FROM air_plan WHERE id=?', (pid,)).fetchone())
        sections = [r for r in saved['broadcast_plan']['rows'] if r['kind'] == 'news' and not r.get('placeholder')]
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['at'], due)
        self.assertFalse(self.svc._break_due(0))

    def test_moving_the_skipped_section_keeps_only_that_section_skipped(self):
        self.add_news('one')
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': row['id'], 'at': row['at'], 'skipped': True}])
        snap = self.svc._broadcast_plan()
        due = store.iso(self.AT + timedelta(minutes=10))
        saved = self.save_plan(snap, [{'id': row['id'], 'at': due, 'skipped': True}])
        news = [r for r in saved['broadcast_plan']['rows'] if r['kind'] == 'news']
        self.assertEqual([r['id'] for r in news if r['skipped']], [row['id']])
        self.assertEqual(next(r['at'] for r in news if r['id'] == row['id']), due)
        self.assertFalse(any(r['at'] < due and not r['skipped'] for r in news))
        self.assertFalse(self.svc._break_due(0))

    def test_queued_tracks_are_contents_of_one_music_section(self):
        self.add_news('one')
        self.svc.next_track = '/tmp/Selected.wav'
        self.svc.replays = {'replay': {'kind': 'music', 'ref': '/tmp/Replay.wav', 'rid': '81'}}
        self.svc.fresh_queued = {'/tmp/Fresh.wav': '82'}
        rows = self.svc._broadcast_plan()['rows']
        music = [r for r in rows if r['kind'] == 'music' and r['content_title']]
        self.assertEqual(len(music), 1)
        for title in ('Selected', 'Replay', 'Fresh'):
            self.assertIn(title, music[0]['content_title'])
        self.assertEqual(music[0]['members'], [])
        self.assertFalse(any(r['id'].startswith(('next-music:', 'replay:', 'fresh:', 'placeholder:music:')) for r in rows))
        self.assertEqual(self.svc.replays['replay']['rid'], '81')

    def test_directed_and_source_reminder_are_one_section(self):
        plugins.set_plugin('reminders_todoist', True)
        reminder = {'key': 'todoist:same', 'kind': 'task', 'title': 'Standup', 'start': self.AT + timedelta(minutes=20)}
        self.svc.reminders_due[reminder['key']] = reminder
        job = self.command(action='insert', kind='reminder', at=store.iso(self.AT + timedelta(minutes=5)))['id']
        self.svc.db.execute('UPDATE air_plan SET ref=? WHERE id=?', (reminder['key'], job))
        rows = [r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'reminder']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], job)

    def test_music_cannot_be_added_as_an_editor_section(self):
        self.svc.library = {'/tmp/song.wav'}
        self.add_news('one')
        snap = self.svc._broadcast_plan()
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            self.save_plan(snap, [{'id': 'new:music', 'kind': 'music', 'at': store.iso(self.AT)}])
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM air_plan').fetchone()[0], 0)

    def test_old_music_placeholders_do_not_become_individual_track_insertions(self):
        from unittest.mock import AsyncMock
        key = 'section:music:future:old'
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,'music',?)", (key, store.iso(self.AT)))
        self.svc.engine = Mock(push_replay=AsyncMock())
        self.assertFalse(asyncio.run(self.svc._direction_tick()))
        self.svc.engine.push_replay.assert_not_awaited()
        self.assertFalse(any(r['id'] == key for r in self.svc._broadcast_plan()['rows']))

    def test_overdue_news_slots_are_one_section_and_postpone_together(self):
        from unittest.mock import AsyncMock
        import time
        pid = self.add_news('one')
        keys = ['section:news:future:old' + str(n) for n in range(3)]
        for n, key in enumerate(keys):
            self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,'news',?)",
                                (key, store.iso(self.AT - timedelta(minutes=30 - n * 10))))
        snap = self.svc._broadcast_plan()
        section = next(r for r in snap['rows'] if r['kind'] == 'news' and set(keys) <= set(r['members']))
        self.assertTrue(section['id'].startswith('section:news:current:'))
        self.assertTrue(all(content.parse_time(r['at']) >= self.AT for r in snap['rows']))
        due = self.AT + timedelta(minutes=10)
        saved = self.save_plan(snap, [{'id': section['id'], 'at': store.iso(due)}])
        self.assertEqual(sum(r['kind'] == 'news' and r['at'] == store.iso(due) for r in saved['broadcast_plan']['rows']), 1)
        self.assertFalse(self.svc._break_due(0))
        self.assertEqual(self.svc._pick_break(), [])
        self.clock.return_value = due
        plugins.set_plugin('host_links', False)
        self.svc.current = {'kind': 'music', 'ref': '/tmp/song.wav', 'started': time.time(), 'duration': 2}
        self.svc.engine = Mock(push_news=AsyncMock(return_value='92'))
        asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_awaited_once()
        self.svc.on_event({'kind': 'news', 'ref': pid})
        for key in [section['id'], *keys]:
            self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PLAYED')

    def test_overdue_news_section_skip_and_restore_cover_all_its_slots(self):
        self.add_news('one')
        for n in range(3):
            self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,'news',?)",
                                ('section:news:future:old' + str(n), store.iso(self.AT - timedelta(minutes=n + 1))))
        snap = self.svc._broadcast_plan()
        section = next(r for r in snap['rows'] if r['id'].startswith('section:news:current:'))
        self.save_plan(snap, [{'id': section['id'], 'at': section['at'], 'skipped': True}])
        snap = self.svc._broadcast_plan()
        skipped = [r for r in snap['rows'] if r['kind'] == 'news' and r['skipped']]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(self.svc._pick_break(), [])
        self.save_plan(snap, [{'id': skipped[0]['id'], 'at': skipped[0]['at'], 'skipped': False}])
        self.assertTrue(self.svc._pick_break())
        self.assertFalse(any(r['kind'] == 'news' and r['skipped'] for r in self.svc._broadcast_plan()['rows']))

    def test_grouped_section_skip_still_cancels_the_actual_queued_break(self):
        from unittest.mock import AsyncMock
        pid = self.add_news('one')
        self.svc.db.execute("UPDATE packages SET status='QUEUED' WHERE id=?", (pid,))
        self.svc.queued_break = ('93', [pid])
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before,status,ref,rid) VALUES('section:news:future:old','news',?,'QUEUED',?,'93')",
                            (store.iso(self.AT - timedelta(minutes=10)), pid))
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before,status) VALUES('section:news:future:other','news',?,'PENDING')",
                            (store.iso(self.AT - timedelta(minutes=5)),))
        self.svc.engine = Mock(remove=AsyncMock())
        snap = self.svc._broadcast_plan()
        section = next(r for r in snap['rows'] if r['kind'] == 'news' and r['locked'])
        self.assertEqual(section['status'], 'QUEUED')
        self.save_plan(snap, [{'id': section['id'], 'at': section['at'], 'skipped': True, 'skip_only': True}])
        self.svc.engine.remove.assert_awaited_once_with('93')
        self.assertIsNone(self.svc.queued_break)

    def test_news_readiness_distinguishes_missing_text_text_and_rendered_voice(self):
        snap = self.svc._broadcast_plan()
        empty = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.assertFalse(empty['text_ready'])
        self.assertFalse(empty['voice_ready'])
        pid = self.add_news('one')
        self.svc.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (pid,))
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'news')
        self.assertTrue(row['text_ready'])
        self.assertFalse(row['voice_ready'])
        self.svc.db.execute("UPDATE packages SET status='READY' WHERE id=?", (pid,))
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'news')
        self.assertTrue(row['text_ready'])
        self.assertTrue(row['voice_ready'])

    def test_readiness_of_combined_news_requires_every_selected_voice(self):
        ids = [self.add_news(name) for name in ('one', 'two')]
        due = store.iso(self.AT + timedelta(minutes=5))
        for pid in ids:
            self.command(action='time', id=pid, at=due)
        self.svc.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (ids[1],))
        row = next(r for r in self.svc._broadcast_plan()['rows'] if set(ids) <= set(r['members']))
        self.assertTrue(row['text_ready'])
        self.assertFalse(row['voice_ready'])

    def test_reminder_material_is_ready_before_voice_and_queued_voice_is_ready(self):
        plugins.set_plugin('reminders_todoist', True)
        reminder = {'key': 'todoist:readiness', 'kind': 'task', 'title': 'Standup', 'start': self.AT + timedelta(minutes=20)}
        self.svc.reminders_due[reminder['key']] = reminder
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'reminder')
        self.assertTrue(row['text_ready'])
        self.assertFalse(row['voice_ready'])
        self.svc.reminders_due.clear()
        self.svc.queued_reminders[reminder['key']] = ('94', reminder)
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'reminder')
        self.assertTrue(row['text_ready'])
        self.assertTrue(row['voice_ready'])

    def test_catalog_has_only_types_regardless_of_library_and_history_size(self):
        self.svc.library = {f'/tmp/track-{i}.wav' for i in range(1000)}
        self.add_news('old bulletin')
        blocks = self.command(action='catalog')['blocks']
        kinds = [b['kind'] for b in blocks]
        self.assertEqual(len(kinds), len(set(kinds)))
        self.assertLessEqual(len(blocks), 6)
        self.assertTrue(all(b['ref'] == '' for b in blocks))
        self.assertIn('news', kinds)
        self.assertNotIn('music', kinds)

    def test_news_type_selects_full_edition_when_due_without_replaying_history(self):
        key = self.command(action='insert', kind='news', at=store.iso(self.AT))['id']
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM packages').fetchone()[0], 0)
        self.svc.current = {'kind': 'music', 'ref': '/tmp/current.wav', 'started': 1000, 'duration': 20}
        self.svc.engine = Mock(next_music=AsyncMock(return_value=''), push_news=AsyncMock(return_value='77'), remove=AsyncMock())
        with patch('radio.daemon.time.time', return_value=1000), patch.object(self.svc, '_assemble_break', return_value=(Path('/tmp/edition.wav'), 0)):
            asyncio.run(self.svc.schedule())
            self.svc.engine.push_news.assert_not_called()
            ids = [self.add_news(name) for name in ('fresh bulletin', 'another source')]
            asyncio.run(self.svc.schedule())
            self.assertEqual(self.svc.engine.push_news.call_args.args[1], '|'.join(ids))
            self.command(action='skip', id=key)
            self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (ids[0],)).fetchone()[0], 'READY')
            self.command(action='skip', id=key)
            asyncio.run(self.svc.schedule())
            self.svc.on_event({'kind': 'news', 'ref': '|'.join(ids)})
            self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PLAYED')
            self.assertEqual(self.svc.editor.calls, 0)

    def test_skipped_sections_keep_their_time_when_another_section_is_selected(self):
        due = self.AT + timedelta(minutes=5)
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before,skipped) VALUES('section:news:saved:skip','news',?,1)", (store.iso(due),))
        self.svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES('section:news:saved:other','news',?)", (store.iso(self.AT),))
        self.clock.return_value = self.AT + timedelta(minutes=10)
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['id'] == 'section:news:saved:skip')
        self.assertTrue(row['skipped'])
        self.assertEqual(row['at'], store.iso(due))

    def test_reminder_type_reuses_current_timed_reminder_and_cancellation(self):
        from unittest.mock import AsyncMock
        import time
        plugins.set_plugin('reminders_todoist', True)
        key = self.command(action='insert', kind='reminder', at=store.iso(self.AT))['id']
        self.svc._reminders_tick = AsyncMock()
        self.svc.current = {'kind': 'music', 'ref': '/tmp/song.wav', 'started': time.time(), 'duration': 100}
        reminder = {'key': 'todoist:active:today', 'kind': 'task', 'title': 'Standup',
                    'description': 'Original description', 'start': self.AT + timedelta(minutes=10)}
        self.svc.reminders_due[reminder['key']] = reminder
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='79'), remove=AsyncMock())
        with patch.object(self.svc, '_render_notice', return_value=Path('/tmp/notice.wav')):
            self.assertTrue(asyncio.run(self.svc._direction_tick()))
        self.assertEqual(self.svc.engine.push_replay.call_args.args[2], reminder['key'])
        self.assertIn('Original description', self.svc.reminder_text[reminder['key']])
        self.command(action='skip', id=key)
        self.assertFalse(self.svc.queued_reminders)
        self.assertFalse(self.svc.id_pending)
        self.assertIn(reminder['key'], self.svc.reminders_due)

    def next_fixture(self):
        pid = self.add_news('next-ready')
        self.svc.current = {'kind': 'music', 'ref': '/tmp/song.wav', 'started': 1000, 'duration': 180}
        self.svc.set_started = 1000
        self.svc.engine = Mock(proc=None, next_music=AsyncMock(return_value='/tmp/next.wav'),
                               push_news=AsyncMock(return_value='42'), command=AsyncMock(return_value='OK'),
                               wait_insert=AsyncMock(), wait_music=AsyncMock())
        return pid

    def test_next_previews_ready_edition_before_late_assembly(self):
        self.next_fixture()
        with patch('radio.daemon.time.time', return_value=1000):
            self.assertTrue(any(r['stage'] == 'next' and r['title'] == 'Host edition' for r in self.svc.status()['upcoming']))
            asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_not_awaited()

    def test_next_queues_ready_edition_before_skipping_song(self):
        pid = self.next_fixture()
        order = []
        async def push(*args):
            order.append('queue')
            return '42'
        async def loaded(*args):
            order.append('loaded')
        async def skip(cmd):
            order.append(cmd)
        self.svc.engine.push_news.side_effect = push
        self.svc.engine.wait_insert.side_effect = loaded
        self.svc.engine.command.side_effect = skip
        with patch('radio.daemon.time.time', return_value=1000), patch.object(self.svc, '_assemble_break', side_effect=AssertionError('must not synthesize on Next')):
            self.assertTrue(asyncio.run(self.svc.dispatch('next'))['ok'])
        self.assertEqual(order, ['queue', 'loaded', 'skip'])
        self.assertEqual(self.svc.queued_break, ('42', [pid]))

    def test_next_does_not_wait_for_host_links_already_being_synthesized(self):
        import threading
        self.next_fixture()
        self.svc.current['duration'] = 30
        release = threading.Event()
        def assemble(*args):
            release.wait(2)
            return Path('/tmp/with-links.wav'), 0
        async def scenario():
            normal = asyncio.create_task(self.svc.schedule())
            try:
                while not self.svc.assembling:
                    await asyncio.sleep(0)
                await asyncio.wait_for(self.svc.dispatch('next'), .5)
                self.svc.engine.command.assert_awaited_once_with('skip')
            finally:
                release.set()
                await normal
        with patch('radio.daemon.time.time', return_value=1000), patch.object(self.svc, '_assemble_break', side_effect=assemble):
            asyncio.run(scenario())
        self.svc.engine.push_news.assert_awaited_once()
        self.assertEqual(self.svc.engine.push_news.call_args.args[0], Path('/tmp/test.wav'))

    def test_manual_transition_speed_is_used_only_for_the_next_track(self):
        self.next_fixture()
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        asyncio.run(self.svc.dispatch('next'))
        with patch('radio.daemon.audio.duration', return_value=180):
            self.svc.on_event({'kind': 'music', 'ref': '/tmp/next.wav'})
            self.assertEqual(self.svc.status()['transition_sec'], .1)
            self.svc.on_event({'kind': 'music', 'ref': '/tmp/later.wav'})
            self.assertEqual(self.svc.status()['transition_sec'], self.cfg['music']['crossfade_sec'])

    def test_next_keeps_current_song_until_an_alternative_is_ready(self):
        self.next_fixture()
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        async def loading():
            self.svc.engine.command.assert_not_awaited()
            self.assertEqual(self.svc.current['ref'], '/tmp/song.wav')
        self.svc.engine.wait_music.side_effect = loading
        asyncio.run(self.svc.dispatch('next'))
        self.svc.engine.command.assert_awaited_once_with('skip')

    def test_failed_loading_does_not_interrupt_current_audio(self):
        self.next_fixture()
        self.svc.db.execute("UPDATE packages SET status='PENDING'")
        self.svc.engine.wait_music.side_effect = TimeoutError('still loading')
        with self.assertRaises(TimeoutError):
            asyncio.run(self.svc.dispatch('next'))
        self.svc.engine.command.assert_not_awaited()
        self.assertEqual(self.svc.current['ref'], '/tmp/song.wav')

    def test_next_does_not_advance_a_future_section(self):
        self.next_fixture()
        snap = self.svc._broadcast_plan()
        row = next(r for r in snap['rows'] if r['kind'] == 'news')
        self.save_plan(snap, [{'id': row['id'], 'at': store.iso(self.AT + timedelta(hours=1))}])
        with patch('radio.daemon.time.time', return_value=1000):
            self.assertFalse(any(r['stage'] == 'next' for r in self.svc.status()['upcoming']))
            asyncio.run(self.svc.dispatch('next'))
        self.svc.engine.push_news.assert_not_awaited()
        self.svc.engine.command.assert_awaited_once_with('skip')

    def test_next_does_not_skip_a_section_that_started_during_assembly(self):
        self.next_fixture()
        def load(*args):
            self.svc.current = {'kind': 'news', 'ref': 'already-started', 'started': 1001}
            return Path('/tmp/edition.wav'), 0
        with patch('radio.daemon.time.time', return_value=1000), patch('radio.daemon.audio.join_packages', side_effect=load):
            asyncio.run(self.svc.dispatch('next'))
        self.svc.engine.command.assert_not_awaited()
        self.svc.engine.push_news.assert_not_awaited()

    def test_upcoming_obeys_edits_and_remains_available_without_experiment(self):
        ids = [self.add_news(x) for x in ('one', 'two', 'three')]
        self.command(action='skip', id=ids[0])
        due = store.iso(self.AT + timedelta(minutes=20))
        self.command(action='time', id=ids[1], at=due)
        key = self.command(action='insert', kind='weather', ref='', at=due)['id']
        upcoming = self.svc.status()['upcoming']
        self.assertEqual([r['title'] for r in upcoming if r['stage'] == 'rotation'], ['three'])
        self.assertEqual(next(r['at'] for r in upcoming if r['title'] == 'two'), due)
        self.assertEqual(len([r for r in upcoming if r['kind'] == 'weather' and r['at'] == due]), 1)
        self.svc.db.execute("UPDATE air_plan SET status='ERROR' WHERE id=?", (key,))
        self.assertFalse(any(r['kind'] == 'weather' and r['at'] == due for r in self.svc.status()['upcoming']))
        plugins.set_plugin('air_direction', False)
        status = self.svc.status()
        self.assertEqual(status['direction'], [])
        self.assertEqual([r['title'] for r in status['upcoming'] if r['stage'] == 'rotation'], ['one', 'two', 'three'])
        self.assertTrue(any(r['kind'] == 'weather' for r in status['upcoming']))

    def test_upcoming_includes_real_queue_and_next_song_without_playback(self):
        from unittest.mock import AsyncMock
        pid = self.add_news('queued bulletin')
        self.svc.db.execute("UPDATE packages SET status='QUEUED' WHERE id=?", (pid,))
        self.svc.replays['repeat'] = {'kind': 'music', 'ref': '/tmp/Artist - Repeat.mp3', 'rid': '42'}
        self.svc.engine = Mock(proc=Mock(returncode=None), next_music=AsyncMock(return_value='/tmp/Artist - Next.mp3'))
        status = asyncio.run(self.svc.dispatch('status'))['status']
        self.assertEqual([r['title'] for r in status['upcoming'] if r['stage'] == 'queued'], ['Artist - Repeat', 'queued bulletin'])
        self.assertEqual([r['title'] for r in status['upcoming'] if r['stage'] == 'music'], ['Artist - Next'])
        self.svc.engine.next_music = AsyncMock(side_effect=OSError('not connected'))
        self.assertTrue(asyncio.run(self.svc.dispatch('status'))['ok'])
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'QUEUED')

    def test_news_skip_order_time_reset_and_disabled_path(self):
        ids = [self.add_news(x) for x in ('one', 'two', 'three')]
        self.command(action='up', id=ids[1])
        self.assertEqual([p['id'] for p in self.svc._pick_break()], [ids[1], ids[0], ids[2]])
        self.command(action='skip', id=ids[0])
        self.command(action='time', id=ids[1], at=store.iso(self.AT + timedelta(minutes=20)))
        self.assertEqual([p['id'] for p in self.svc._pick_break()], [ids[2]])
        restarted = Service(self.cfg, tts=MockTTS(), output='x')
        try:
            self.assertEqual([p['id'] for p in restarted._pick_break()], [ids[2]])
        finally:
            restarted.db.close()
        self.command(action='reset', id=ids[0])
        self.assertEqual({p['id'] for p in self.svc._pick_break()}, {ids[0], ids[2]})
        plugins.set_plugin('air_direction', False)
        self.assertEqual([p['id'] for p in self.svc._pick_break()], ids)
        self.assertFalse(self.command(action='catalog')['ok'])

    def test_custom_text_and_reused_news_keep_timing_and_no_llm(self):
        due = store.iso(self.AT + timedelta(minutes=5))
        result = self.command(action='insert', kind='text', ref='', text='Короткая своя реплика.', at=due)
        self.assertTrue(result['ok'])
        row = self.svc.db.execute("SELECT p.*,i.source FROM packages p JOIN items i ON i.id=p.item_id").fetchone()
        self.assertEqual(row['source'], 'direction')
        self.assertEqual(direction.edits(self.svc.db)[row['id']]['not_before'], due)
        self.assertEqual(Path(row['script_path']).read_text(), 'Короткая своя реплика.')
        self.assertEqual(self.svc.editor.calls, 0)
        original = self.add_news('original')
        result = self.command(action='insert', kind='news', ref=original, at=due)
        self.assertTrue(result['ok'])
        with self.assertRaises(ValueError):
            self.command(action='insert', kind='music', ref='/etc/passwd', at=due)
        with self.assertRaises(ValueError):
            self.command(action='insert', kind='text', ref='', text='x', at=store.iso(self.AT - timedelta(days=2)))

    def test_generic_weather_job_does_not_shift_clock_slots(self):
        from unittest.mock import AsyncMock
        due = store.iso(self.AT)
        key = self.command(action='insert', kind='weather', ref='', at=due)['id']
        report = {'text': 'Далат: плюс 17 градусов, туман.', 'summary': 'Da Lat · 17° · fog',
                  'kind': 'weather', 'start': self.AT, 'expires': self.AT + timedelta(minutes=30)}
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='17'))
        with patch.object(weather, 'fetch', return_value=report), patch.object(self.svc, '_render_weather', return_value=Path('/tmp/w.wav')), patch('radio.audio.duration', return_value=5):
            self.assertFalse(asyncio.run(self.svc._direction_tick()))
        self.svc.engine.push_replay.assert_not_called()
        pid = 'system:' + key + '#1'
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'READY')
        self.svc.on_event({'kind': 'news', 'ref': pid})
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PLAYED')
        self.assertIsNone(self.svc.db.execute("SELECT 1 FROM reminders WHERE key LIKE 'weather:slot:%'").fetchone())
        slots = direction.weather_rows(self.svc.db, self.cfg, self.AT, direction.edits(self.svc.db))
        self.assertEqual(slots[0]['id'], 'weather:slot:' + str(int(self.AT.timestamp())))

    def test_wall_clock_slots_skip_missed_windows_and_persist(self):
        self.assertEqual(weather.slot(self.AT - timedelta(minutes=1), 'Asia/Ho_Chi_Minh').hour, 18)
        with patch.object(weather, 'fetch') as fetch:
            self.clock.return_value = self.AT - timedelta(minutes=1)
            asyncio.run(self.svc._weather_tick())
            fetch.assert_not_called()
        report = {'kind': 'weather', 'key': 'ignored', 'start': self.AT,
                  'expires': self.AT + timedelta(minutes=30), 'text': 'x', 'summary': 'x'}
        self.clock.return_value = self.AT
        with patch.object(weather, 'fetch', return_value=report) as fetch:
            asyncio.run(self.svc._weather_tick())
            asyncio.run(self.svc._weather_tick())
            fetch.assert_called_once()
        key = 'weather:slot:' + str(int(self.AT.timestamp()))
        self.assertIn(key, self.svc.reminders_due)
        self.svc.db.execute('INSERT INTO reminders(key,fired_at) VALUES(?,?)', (key, store.iso(self.AT)))
        self.svc.reminders_due.clear()
        with patch.object(weather, 'fetch') as fetch:
            asyncio.run(self.svc._weather_tick())
            fetch.assert_not_called()
        next_key = 'weather:slot:' + str(int((self.AT + timedelta(hours=3)).timestamp()))
        self.command(action='time', id=next_key, at=store.iso(self.AT + timedelta(minutes=10)))
        self.clock.return_value = self.AT + timedelta(minutes=10)
        with patch.object(weather, 'fetch', return_value=dict(report)) as fetch:
            asyncio.run(self.svc._weather_tick())
            fetch.assert_called_once()
        self.assertIn(next_key, self.svc.reminders_due)

    def test_edit_queued_bulletin_cancels_the_whole_unstarted_break(self):
        from unittest.mock import AsyncMock
        first, second = self.add_news('one'), self.add_news('two')
        self.svc.db.execute("UPDATE packages SET status='QUEUED'")
        self.svc.queued_break = ('42', [first, second])
        self.svc.engine = Mock(remove=AsyncMock())
        self.command(action='skip', id=first)
        self.svc.engine.remove.assert_awaited_once_with('42')
        self.assertEqual([p['id'] for p in self.svc._pick_break()], [second])
        with self.assertRaises(ValueError):
            self.command(action='time', id=second, at='not a time')
        self.assertNotIn(second, direction.edits(self.svc.db))

    def test_future_insert_survives_news_ttl_and_audio_cleanup(self):
        due = store.iso(self.AT + timedelta(days=7))
        self.command(action='insert', kind='text', ref='', text='Будущая реплика.', at=due)
        row = self.svc.db.execute('SELECT * FROM packages').fetchone()
        import os, time
        os.utime(row['script_path'], (time.time() - 10 * 86400,) * 2)
        self.svc._cleanup_cache()
        self.assertTrue(Path(row['script_path']).exists())
        with patch('radio.content.now', return_value=self.AT + timedelta(days=4)):
            content.expire_stale(self.svc.db, self.cfg)
        self.assertEqual(self.svc.db.execute('SELECT state FROM items').fetchone()[0], 'NEW')

    def test_disabled_experiment_cancels_unstarted_insert_and_keeps_plan(self):
        from unittest.mock import AsyncMock
        key = self.command(action='insert', kind='weather', ref='', at=store.iso(self.AT))['id']
        self.svc.db.execute("UPDATE air_plan SET status='QUEUED',rid='71' WHERE id=?", (key,))
        self.svc.engine = Mock(remove=AsyncMock())
        self.svc.id_pending = True
        asyncio.run(self.svc.dispatch('plugin', {'name': 'air_direction', 'on': False}))
        self.svc.engine.remove.assert_awaited_once_with('71')
        self.assertFalse(self.svc.id_pending)
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PENDING')

    def test_explicit_news_time_bypasses_normal_music_set_wait(self):
        from unittest.mock import AsyncMock
        import time
        result = self.command(action='insert', kind='text', ref='', text='Вставка сейчас.', at=store.iso(self.AT))
        self.svc.db.execute("UPDATE packages SET status='READY',audio_path='/tmp/x.wav',speech_sec=5")
        self.svc.current = {'kind': 'music', 'ref': '/tmp/song.wav', 'duration': 300, 'started': time.time()}
        self.svc.engine = Mock(push_news=AsyncMock(return_value='83'))
        self.svc._break_due = Mock(return_value=False)
        asyncio.run(self.svc.schedule())
        self.svc.engine.push_news.assert_awaited_once()
        self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE item_id=?', (result['id'],)).fetchone()[0], 'QUEUED')

    def test_song_uses_the_same_scheduled_insert_path(self):
        from unittest.mock import AsyncMock
        self.svc.library = {'/tmp/song.wav'}
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='91'))
        key = self.command(action='insert', kind='music', ref='/tmp/song.wav', at=store.iso(self.AT))['id']
        self.assertTrue(asyncio.run(self.svc._direction_tick()))
        self.assertEqual(self.svc.engine.push_replay.call_args.args[1:3], ('music', '/tmp/song.wav'))
        with patch('radio.audio.duration', return_value=90):
            self.svc.on_event({'kind': 'music', 'ref': '/tmp/song.wav', 'replay_id': key})
        self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PLAYED')

    def test_jingle_and_saved_reminder_are_available_as_scheduled_blocks(self):
        import json
        from unittest.mock import AsyncMock
        self.svc.engine = Mock(push_replay=AsyncMock(return_value='95'))
        for kind, ref in [('jingle', 'stinger_in'), ('reminder', None)]:
            with self.subTest(kind=kind):
                if kind == 'reminder':
                    store.log_event(self.svc.db, self.svc.session, 'REMINDER', 'original',
                                    json.dumps({'text': 'Проверка напоминания.', 'description': 'Original description'}))
                    ref = str(self.svc.db.execute("SELECT id FROM events WHERE type='REMINDER' ORDER BY id DESC").fetchone()[0])
                key = self.command(action='insert', kind=kind, ref=ref, at=store.iso(self.AT))['id']
                with patch('radio.audio.lufs', return_value=-16), patch.object(self.svc, '_render_notice', return_value=Path('/tmp/notice.wav')) as render:
                    self.assertTrue(asyncio.run(self.svc._direction_tick()))
                    if kind == 'reminder':
                        render.assert_called_once_with('Проверка напоминания.')
                self.svc.on_event({'kind': 'id' if kind == 'jingle' else kind, 'ref': key, 'replay_id': key})
                self.assertEqual(self.svc.db.execute('SELECT status FROM air_plan WHERE id=?', (key,)).fetchone()[0], 'PLAYED')

    def test_manual_weather_history_retains_the_requested_report(self):
        from radio import timeline
        store.log_event(self.svc.db, self.svc.session, 'WEATHER_REQUESTED', 'manual-one', 'Da Lat · 17° · fog')
        store.log_event(self.svc.db, self.svc.session, 'WEATHER', 'manual-one', '')
        row = next(r for r in timeline._build(self.svc.db, 12, 20) if r['kind'] == 'weather')
        self.assertEqual(row['title'], 'Da Lat · 17° · fog')
