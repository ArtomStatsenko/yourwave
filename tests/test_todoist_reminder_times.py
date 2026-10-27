import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from radio import reminders


class TodoistReminderTimesTests(unittest.TestCase):
    AT = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)

    def task(self, notices):
        return {'id': 'task', 'content': 'Meeting', 'due': {'date': self.AT.isoformat()}, 'reminders': notices}

    def test_relative_zero_absolute_and_fallback(self):
        for notices, minutes in [
            ([{'type': 'relative', 'minute_offset': 30}], 30),
            ([{'type': 'relative', 'minute_offset': 0}], 0),
            ([{'type': 'absolute', 'due': {'date': '2026-10-27T12:00:00', 'timezone': 'Europe/Berlin'}}], 60),
            ([], 15),
            ([{'type': 'location'}, {'type': 'relative', 'minute_offset': 'bad'}], 15),
            ([{'type': 'relative', 'minute_offset': 60, 'is_deleted': True}], 15),
            ([{'type': 'relative', 'minute_offset': 10}, {'type': 'relative', 'minute_offset': 30}], 30),
        ]:
            with self.subTest(notices=notices):
                task = self.task(notices)
                when = self.AT - timedelta(minutes=minutes)
                self.assertEqual(reminders.todoist_due([task], when - timedelta(seconds=1), timedelta(minutes=15)), [])
                row, = reminders.todoist_due([task], when, timedelta(minutes=15))
                self.assertEqual(row['remind_at'], when)
                self.assertEqual(row['start'], self.AT)
                self.assertEqual(row['key'], 'todoist:task:20261027T1200')
                future, = reminders.todoist_due([task], when - timedelta(hours=2), timedelta(minutes=15), horizon=timedelta(hours=2))
                self.assertEqual(future, row)

    def test_fetch_joins_paginated_reminders_without_silently_ignoring_failure(self):
        replies = [
            {'results': [self.task([])], 'next_cursor': None},
            {'results': [{'item_id': 'other', 'type': 'relative', 'minute_offset': 5}], 'next_cursor': 'next'},
            {'results': [{'item_id': 'task', 'type': 'relative', 'minute_offset': 30}], 'next_cursor': None},
        ]
        with patch.object(reminders, '_get', side_effect=[json.dumps(r) for r in replies]) as get:
            task, = reminders.fetch_todoist('test-token')
        self.assertEqual(task['reminders'], replies[2]['results'])
        self.assertIn('/reminders?', get.call_args_list[1].args[0])
        self.assertIn('cursor=next', get.call_args_list[2].args[0])
        with patch.object(reminders, '_get', side_effect=[json.dumps(replies[0]), OSError('offline')]):
            with self.assertRaises(OSError):
                reminders.fetch_todoist('test-token')
