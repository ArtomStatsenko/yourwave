import unittest
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from radio import plugins, reminders, store
from tests import test_direction


class CalendarReminderTimesTests(unittest.TestCase):
    AT = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    LEAD = timedelta(minutes=15)

    def events(self, alarms='', extra=''):
        return reminders.parse_events('BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:meeting\r\n'
            'DTSTART:20261027T120000Z\r\nDTEND:20261027T130000Z\r\nSUMMARY:Meeting\r\n'
            'DESCRIPTION:Original details\r\n' + extra + alarms + 'END:VEVENT\r\nEND:VCALENDAR', timezone.utc)

    def test_own_alarm_and_fallback_match_todoist(self):
        for trigger, minutes in [('TRIGGER:-PT30M', 30), ('TRIGGER:PT0S', 0),
                                 ('TRIGGER;VALUE=DATE-TIME:20261027T110000Z', 60),
                                 ('TRIGGER;RELATED=END:-PT90M', 30),
                                 ('TRIGGER:bad', 15), ('', 15)]:
            with self.subTest(trigger=trigger):
                alarms = 'BEGIN:VALARM\r\nACTION:DISPLAY\r\n' + trigger + '\r\nDESCRIPTION:Alarm message\r\nSUMMARY:Alarm title\r\nEND:VALARM\r\n'
                events = self.events(alarms)
                when = self.AT - timedelta(minutes=minutes)
                self.assertEqual(reminders.calendar_due(events, when-timedelta(seconds=1), self.LEAD), [])
                row, = reminders.calendar_due(events, when, self.LEAD)
                task = {'id': 'meeting', 'content': 'Meeting', 'due': {'date': self.AT.isoformat()},
                        'reminders': [{'type': 'relative', 'minute_offset': minutes}]}
                other, = reminders.todoist_due([task], when, self.LEAD)
                self.assertEqual(row['remind_at'], other['remind_at'])
                self.assertEqual(row['start'], other['start'])
                self.assertEqual(row['title'], 'Meeting')
                self.assertEqual(row['description'], 'Original details')
                future, = reminders.calendar_due(events, when-timedelta(hours=2), self.LEAD, horizon=timedelta(hours=2))
                self.assertEqual(future, row)

    def test_recurring_alarm_and_earliest_of_multiple(self):
        alarms = ''.join('BEGIN:VALARM\r\nTRIGGER:' + trigger + '\r\nEND:VALARM\r\n'
                         for trigger in ('-PT10M', '-P1D'))
        events = self.events(alarms, 'RRULE:FREQ=DAILY;COUNT=3\r\n')
        when = self.AT + timedelta(hours=1)
        rows = reminders.calendar_due(events, when, self.LEAD)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['start'], self.AT + timedelta(days=1))
        self.assertEqual(rows[0]['remind_at'], self.AT)
        self.assertEqual(rows[0]['key'], 'cal:meeting:20261028T1200')

    def test_duration_parser(self):
        for value, expected in [('-P1DT2H3M4S', -timedelta(days=1,hours=2,minutes=3,seconds=4)),
                                ('P2W', timedelta(weeks=2)), ('P', None), ('PT', None),
                                ('P9999999999999W', None), ('no', None)]:
            self.assertEqual(reminders._duration(value), expected)


class CalendarReminderIntegrationTests(unittest.TestCase):
    AT = CalendarReminderTimesTests.AT - timedelta(hours=1)
    setUp = test_direction.DirectionTests.setUp

    def test_source_alarm_reaches_plan_and_becomes_due_at_its_own_time(self):
        plugins.set_plugin('reminders_calendar', True)
        self.cfg['reminders']['calendar_url'] = 'test'
        events = CalendarReminderTimesTests().events('BEGIN:VALARM\r\nTRIGGER:-PT30M\r\nEND:VALARM\r\n')
        self.svc.cal_events = events
        with patch.object(reminders, 'fetch_ics', return_value='test'), patch.object(reminders, 'parse_events', return_value=events):
            asyncio.run(self.svc._reminders_tick())
        self.assertFalse(self.svc.reminders_due)
        row = next(r for r in self.svc._broadcast_plan()['rows'] if r['kind'] == 'reminder')
        self.assertEqual(row['at'], store.iso(self.AT + timedelta(minutes=30)))
        self.assertFalse(row['estimated'])
        self.clock.return_value = self.AT + timedelta(minutes=30)
        asyncio.run(self.svc._reminders_tick())
        notice, = self.svc.reminders_due.values()
        self.assertEqual(notice['start'], self.AT + timedelta(hours=1))
        self.assertEqual(notice['remind_at'], self.AT + timedelta(minutes=30))
