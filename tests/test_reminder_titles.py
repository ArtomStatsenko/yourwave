import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, editor, reminders, store, timeline
from radio.daemon import Service
from radio.tts import MockTTS


class ReminderTitleTests(unittest.TestCase):
    """A reminder's row: a few words from the editor in its own language (asked once), else its cleaned title cut
    short, else "Reminder"; unfolded, the original title and description word for word."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-reminder-titles-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for name, value in [('DATA_DIR', root), ('RUN_DIR', root / 'run'), ('SOCK', root / 'run/radio.sock')]:
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config.load()

    def test_the_editor_names_a_reminder_once_and_a_long_answer_is_no_name(self):
        e = editor.Editor(self.cfg)
        with patch.object(e, '_complete', return_value='«Позвонить стоматологу».') as ask:
            self.assertEqual(e.short_title('Позвонить в клинику насчёт записи к стоматологу', ''), 'Позвонить стоматологу')
            self.assertEqual(e.short_title('Позвонить в клинику насчёт записи к стоматологу', ''), 'Позвонить стоматологу')
        self.assertEqual(ask.call_count, 1, 'cached: one ask per reminder')
        with patch.object(e, '_complete', return_value='Sure! Here is a short name for this reminder about the team meeting'):
            self.assertIsNone(e.short_title('Team sync', 'Agenda'))
        self.cfg['editor']['llm_enabled'] = False
        self.assertIsNone(e.short_title('Other', ''))

    def test_without_a_name_the_title_or_description_cut_short_then_reminder(self):
        self.assertEqual(reminders.label({'title': 'Stand-up 🚀 https://meet.example/x'}, None), 'Stand-up')
        self.assertEqual(reminders.label({'title': '', 'description': 'Bring the signed lease to the landlord before noon today'}, None),
                         'Bring the signed lease to the landlord…')
        self.assertEqual(reminders.label({'title': '', 'description': ''}, None), 'Reminder')
        self.assertEqual(reminders.label({'title': 'x'}, 'Short'), 'Short')
        self.assertEqual(reminders.original({'title': 'Stand-up 🚀', 'description': 'Room 4\nhttps://meet.example/x'}),
                         'Stand-up 🚀\n\nRoom 4\nhttps://meet.example/x')

    def test_plan_row_and_history_carry_the_name_and_the_original(self):
        svc = Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)
        r = {'key': 'cal:e1:1', 'kind': 'event', 'title': '', 'description': 'Забрать документы у нотариуса', 'start': store.now()}
        with patch.object(svc, '_reminder_candidates', return_value=[r]), \
             patch.object(svc.editor, 'short_title', return_value='Документы у нотариуса') as ask:
            asyncio.run(svc._name_reminders())
            asyncio.run(svc._name_reminders())
        ask.assert_called_once_with('', 'Забрать документы у нотариуса')
        self.assertEqual(svc.reminder_titles, {'cal:e1:1': 'Документы у нотариуса'})
        detail = json.dumps({'text': 'Через пять минут — забрать документы.', 'title': reminders.label(r, svc.reminder_titles['cal:e1:1']),
                             'original': reminders.original(r)}, ensure_ascii=False)
        store.log_event(svc.db, svc.session, 'REMINDER', r['key'], detail)
        entry = next(x for x in timeline.timeline() if x['kind'] == 'reminder')
        self.assertEqual((entry['title'], entry['text'], entry['original']),
                         ('Документы у нотариуса', 'Через пять минут — забрать документы.', 'Забрать документы у нотариуса'))
        with patch.object(svc, '_reminder_candidates', return_value=[{**r, 'key': 'cal:e2:1'}]), \
             patch.object(svc.editor, 'short_title', side_effect=OSError('offline')):
            asyncio.run(svc._name_reminders())
        self.assertNotIn('cal:e2:1', svc.reminder_titles, 'the row falls back; asked again after a pause')
        self.assertGreater(svc.reminder_titles_retry, 0)


if __name__ == '__main__':
    unittest.main()
