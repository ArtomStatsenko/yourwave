import asyncio
import os
import sqlite3
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

from radio import config, content, editor, plugins, settings, store
from tests import test_direction


class SyncTests(unittest.TestCase):
    AT = test_direction.DirectionTests.AT
    setUp = test_direction.DirectionTests.setUp
    add_news = test_direction.DirectionTests.add_news

    def test_worker_rollback_cannot_erase_another_workers_item(self):
        started, release = threading.Event(), threading.Event()
        original = content.scripts_for
        def scripts(item, *args):
            if item['id'] == 'failing':
                started.set()
                release.wait(3)
                raise OSError('injected failure')
            return original(item, *args)
        def add(name):
            return content.add_item(self.svc.db, self.cfg, {'id': name, 'source': 'inbox', 'topic': name,
                'url': '', 'published_at': self.AT, 'body': 'Проверка новости.'}, 'scheduled')
        with patch('radio.content.scripts_for', side_effect=scripts), ThreadPoolExecutor(2) as pool:
            failed = pool.submit(add, 'failing')
            self.assertTrue(started.wait(3))
            other = pool.submit(add, 'successful')
            release.set()
            with self.assertRaises(OSError):
                failed.result(5)
            self.assertEqual(other.result(5), 'NEW')
        self.assertIsNotNone(self.svc.db.execute("SELECT 1 FROM items WHERE id='successful'").fetchone())

    def test_failed_plan_save_rolls_back_every_change(self):
        snap = self.svc._broadcast_plan()
        rows = [r for r in snap['rows'] if r['kind'] == 'news'][:2]
        self.svc.db.execute("CREATE TEMP TRIGGER fail_plan BEFORE INSERT ON air_plan WHEN NEW.id='" +
            rows[1]['id'] + "' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            asyncio.run(self.svc._save_broadcast_plan({'version': snap['version'], 'changes': [
                {'id': r['id'], 'at': r['at'], 'skipped': True, 'rank': i} for i, r in enumerate(rows)]}))
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM air_plan').fetchone()[0], 0)

    def test_parallel_settings_keep_both_values(self):
        original = settings._saved
        barrier = threading.Barrier(2)
        def slow_read():
            saved = original()
            time.sleep(.05)
            return saved
        def save(key, value):
            barrier.wait(3)
            settings.set_setting(key, value)
        with patch('radio.settings._saved', side_effect=slow_read), ThreadPoolExecutor(2) as pool:
            jobs = [pool.submit(save, *pair) for pair in [('host_name', 'Аудит'), ('station_name', 'Test')]]
            for job in jobs:
                job.result(5)
        self.assertEqual(original(), {'host_name': 'Аудит', 'station_name': 'Test'})

    def test_removed_task_is_cancelled_in_memory_and_engine_queue(self):
        plugins.set_plugin('reminders_calendar', False)
        plugins.set_plugin('reminders_todoist', True)
        self.cfg['reminders']['todoist_token'] = 'test'
        task = {'id': 'one', 'content': 'Old title', 'due': {'date': store.iso(self.AT + timedelta(minutes=5))}}
        self.svc.engine = Mock(remove=AsyncMock())
        with patch('radio.reminders.fetch_todoist', side_effect=[[task], []]):
            asyncio.run(self.svc._reminders_tick())
            key, notice = next(iter(self.svc.reminders_due.items()))
            self.svc.queued_reminders[key] = ('77', notice)
            self.svc.reminders_due.clear()
            self.svc.next_read['todoist'] = 0
            asyncio.run(self.svc._reminders_tick())
        self.assertFalse(self.svc.reminders_due)
        self.assertFalse(self.svc.queued_reminders)
        self.svc.engine.remove.assert_awaited_once_with('77')

    def test_task_changes_update_pending_notice(self):
        plugins.set_plugin('reminders_calendar', False)
        plugins.set_plugin('reminders_todoist', True)
        self.cfg['reminders']['todoist_token'] = 'test'
        task = {'id': 'one', 'content': 'Old title', 'due': {'date': store.iso(self.AT + timedelta(minutes=5))}}
        with patch('radio.reminders.fetch_todoist', side_effect=[[task], [{**task, 'content': 'New title'}]]):
            asyncio.run(self.svc._reminders_tick())
            self.svc.next_read['todoist'] = 0
            asyncio.run(self.svc._reminders_tick())
        self.assertEqual(next(iter(self.svc.reminders_due.values()))['title'], 'New title')

    def test_source_outage_keeps_the_last_valid_reminder(self):
        plugins.set_plugin('reminders_calendar', False)
        plugins.set_plugin('reminders_todoist', True)
        self.cfg['reminders']['todoist_token'] = 'test'
        task = {'id': 'one', 'content': 'Keep this task', 'due': {'date': store.iso(self.AT + timedelta(minutes=5))}}
        with patch('radio.reminders.fetch_todoist', side_effect=[[task], OSError('offline')]):
            asyncio.run(self.svc._reminders_tick())
            self.svc.next_read['todoist'] = 0
            asyncio.run(self.svc._reminders_tick())
        self.assertEqual(next(iter(self.svc.reminders_due.values()))['title'], 'Keep this task')

    def test_engine_reset_discards_old_request_ids_and_unlocks_plan(self):
        self.svc.db.execute("INSERT INTO air_plan(id,kind,status,rid) VALUES('direction:one','news','QUEUED','77')")
        self.svc.queued_break = ('77', ['one'])
        self.svc.queued_clock = {'id': 'old'}
        self.svc.fresh_queued = {'song': '78'}
        self.svc.replays = {'r': {'rid': '79'}}
        self.svc.gap_rid = '80'
        self.svc._reset_inflight()
        self.assertIsNone(self.svc.queued_break)
        self.assertIsNone(self.svc.queued_clock)
        self.assertIsNone(self.svc.gap_rid)
        self.assertFalse(self.svc.fresh_queued or self.svc.replays)
        self.assertEqual(tuple(self.svc.db.execute('SELECT status,rid FROM air_plan').fetchone()), ('PENDING', None))

    def test_pronunciations_leave_budget_for_news(self):
        self.cfg['voice']['engine'] = 'qwen3'
        ed = editor.Editor(self.cfg)
        ed.max_calls = 2
        replies = [{'choices': [{'message': {'content': '{"ZyxBand": "Зиксбэнд"}'}}]},
                   {'choices': [{'message': {'content': 'Компания выпустила новый продукт.'}}]}]
        with patch.object(ed, '_post', side_effect=replies) as post:
            self.assertEqual(ed.speech('ZyxBand'), 'Зиксбэнд')
            self.assertEqual(ed.speech('AnotherBand'), 'AnotherBand')
            self.assertEqual(ed.retell_news('Новость', 'Исходный текст'), 'Компания выпустила новый продукт.')
            self.assertEqual(post.call_count, 2)

    def test_running_station_cleans_expired_cache_without_a_restart(self):
        stale = config.DATA_DIR / 'cache/tts/expired.wav'
        stale.write_bytes(b'old audio')
        os.utime(stale, (0, 0))
        self.svc.next_cache_cleanup = 1000
        async def tick():
            self.svc.stopping = True
            self.svc.wake.set()
        with patch('radio.daemon.time.time', return_value=200000), patch.object(self.svc, 'content_tick', side_effect=tick):
            asyncio.run(self.svc.content_loop())
        self.assertFalse(stale.exists())
        self.assertEqual(self.svc.next_cache_cleanup, 203600)
