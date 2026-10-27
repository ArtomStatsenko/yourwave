import asyncio
import unittest
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from radio import plugins, reminders, store
from tests import test_direction


class HolidayPreviewTests(unittest.TestCase):
    AT = test_direction.DirectionTests.AT
    setUp = test_direction.DirectionTests.setUp

    def events(self, title='День рождения друга', extra='RRULE:FREQ=YEARLY\r\n', date='20251024'):
        return reminders.parse_events('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:holiday\r\n'
            f'DTSTART;VALUE=DATE:{date}\r\nSUMMARY:{title}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR', timezone.utc)

    def test_default_off_and_experiment_list(self):
        self.assertFalse(plugins.on(self.cfg, 'holiday_previews'))
        row = next(r for r in plugins.listing(self.cfg) if r['name'] == 'holiday_previews')
        self.assertEqual(row['section'], 'experiments')
        self.cfg['reminders']['calendar_url'] = 'test'
        plugins.set_plugin('reminders_calendar', True)
        self.svc.cal_events = self.events()
        self.assertIsNone(self.svc._day_due(self.AT))

    def test_two_windows_no_repeat_and_one_preview_per_day(self):
        events = self.events()
        row = reminders.holiday_due(events, self.AT, [])
        self.assertTrue(row['holiday_preview'])
        self.assertIn('Через три дня', row['text'])
        self.assertIn('поздравление', row['text'])
        history = [(row['key'], store.iso(self.AT))]
        self.assertIsNone(reminders.holiday_due(events, self.AT, history))
        self.assertIsNone(reminders.holiday_due(events, self.AT+timedelta(days=1), history))
        eve = reminders.holiday_due(events, self.AT+timedelta(days=2), history)
        self.assertIn('Завтра', eve['text'])
        history.append((eve['key'], store.iso(self.AT+timedelta(days=2))))
        self.assertIsNone(reminders.holiday_due(events, self.AT+timedelta(days=2), history))
        self.assertIsNotNone(reminders.day_due(events, self.AT+timedelta(days=3), []))
        other = self.events(title='Другой праздник', extra='CATEGORIES:Holiday\r\n', date='20261025')
        self.assertIsNone(reminders.holiday_due(other, self.AT, history))

    def test_early_window_only_once_even_after_restart(self):
        events = self.events(date='20251026')
        first = reminders.holiday_due(events, self.AT, [])
        history = [(first['key'], store.iso(self.AT))]
        self.assertIsNone(reminders.holiday_due(events, self.AT+timedelta(days=1), history))
        self.assertIsNone(reminders.holiday_due(events, self.AT+timedelta(days=2), history))

    def test_selection_cancellation_and_eve_without_useful_greeting(self):
        self.assertIsNone(reminders.holiday_due(self.events(extra='', date='20261024'), self.AT, []))
        tagged = self.events(title='День книги', extra='CATEGORIES:Holiday\r\n', date='20261024')
        self.assertIsNotNone(reminders.holiday_due(tagged, self.AT, []))
        self.assertIsNone(reminders.holiday_due(tagged, self.AT+timedelta(days=2), []))
        cancelled = self.events(extra='RRULE:FREQ=YEARLY\r\nSTATUS:CANCELLED\r\n')
        self.assertIsNone(reminders.holiday_due(cancelled, self.AT, []))

    def test_alarm_summaries_do_not_replace_event_or_holiday_metadata(self):
        events = self.events(extra='RRULE:FREQ=YEARLY\r\nBEGIN:VALARM\r\nTRIGGER:-PT15M\r\nSUMMARY:Alarm\r\nDESCRIPTION:Alarm details\r\nEND:VALARM\r\n')
        row = reminders.holiday_due(events, self.AT, [])
        self.assertEqual(row['title'], 'День рождения друга')

    def test_host_edition_path_and_on_air_history_prevent_repetition(self):
        self.cfg['reminders']['calendar_url'] = 'test'
        plugins.set_plugin('reminders_calendar', True)
        plugins.set_plugin('holiday_previews', True)
        self.svc.cal_events = self.events()
        notice = self.svc._day_due(self.AT)
        self.svc.reminders_due = {notice['key']: notice}
        self.svc._break_due = Mock(return_value=True)
        with patch.object(self.svc, '_render_day', return_value=(notice['text'], Path('/tmp/holiday.wav'))), \
             patch('radio.audio.lufs', return_value=-16), patch('radio.audio.duration', return_value=5):
            self.assertFalse(asyncio.run(self.svc._remind(20)))
        packages = self.svc._pick_break()
        self.assertEqual(len(packages), 1)
        self.assertEqual(self.svc.db.execute('SELECT source FROM items WHERE id=?', (packages[0]['item_id'],)).fetchone()[0], 'calendar')
        self.assertTrue(packages[0]['id'].startswith('system:holiday:'))
        rows = self.svc._broadcast_plan()['rows']
        self.assertTrue(any(segment['title'] == 'Upcoming holidays' for row in rows for segment in row.get('segments', [])))
        self.assertIsNotNone(self.svc._day_due(self.AT), 'Preparation alone must not consume the preview')
        self.svc.on_event({'kind': 'news', 'ref': packages[0]['id']})
        self.assertIsNone(self.svc._day_due(self.AT))
        self.assertIsNotNone(self.svc.db.execute('SELECT 1 FROM reminders WHERE key=?', (notice['key'],)).fetchone())

    def test_switch_off_excludes_prepared_preview(self):
        self.cfg['reminders']['calendar_url'] = 'test'
        plugins.set_plugin('reminders_calendar', True)
        plugins.set_plugin('holiday_previews', True)
        self.svc.cal_events = self.events()
        notice = self.svc._day_due(self.AT)
        self.svc.reminders_due = {notice['key']: notice}
        with patch('radio.audio.duration', return_value=5):
            self.svc._store_host_segment(notice['key'], notice, notice['text'], Path('/tmp/holiday.wav'))
        plugins.set_plugin('holiday_previews', False)
        self.assertEqual(self.svc._pick_break(), [])
