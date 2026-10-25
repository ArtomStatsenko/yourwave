import unittest
from datetime import timedelta

import test_direction
from radio import store


class PlanInsertTests(unittest.TestCase):
    def setUp(self):
        self.editor = test_direction.DirectionTests()
        self.addCleanup(self.editor.doCleanups)
        self.editor.setUp()

    def change(self, kind, at):
        return {'id': 'new:editor-insert', 'kind': kind, 'text': 'Hello station',
                'at': at, 'skipped': False, 'rank': 0, 'skip_only': False, 'skip_confirmed': False}

    def test_editor_payload_adds_text_with_undo(self):
        due = store.iso(self.editor.AT + timedelta(minutes=10))
        snapshot = self.editor.svc._broadcast_plan()
        saved = self.editor.command(action='save', version=snapshot['version'], remember_undo=True,
                                    changes=[self.change('text', due)])
        self.assertTrue(saved['ok'])
        added = next(r for r in saved['broadcast_plan']['rows'] if r.get('source') == 'direction')
        self.assertEqual(added['at'], due)
        self.assertEqual(self.editor.svc.db.execute("SELECT COUNT(*) FROM items WHERE source='direction'").fetchone()[0], 1)
        self.assertTrue(self.editor.command(action='undo', token=saved['undo_token'])['ok'])
        self.assertFalse(any(r.get('source') == 'direction' and not r['skipped']
                             for r in self.editor.svc._broadcast_plan()['rows']))

    def test_editor_payload_adds_host_edition(self):
        due = store.iso(self.editor.AT + timedelta(minutes=10))
        snapshot = self.editor.svc._broadcast_plan()
        saved = self.editor.save_plan(snapshot, [self.change('news', due)])
        self.assertTrue(saved['ok'])
        row = self.editor.svc.db.execute("SELECT * FROM air_plan WHERE id LIKE 'direction:%' AND kind='news' AND not_before=?", (due,)).fetchone()
        self.assertIsNotNone(row)

    def test_new_block_still_requires_current_or_future_time(self):
        for due in (None, store.iso(self.editor.AT - timedelta(hours=1))):
            with self.subTest(due=due):
                snapshot = self.editor.svc._broadcast_plan()
                with self.assertRaisesRegex(ValueError, 'current or future time'):
                    self.editor.save_plan(snapshot, [self.change('text', due)])
                self.assertEqual(self.editor.svc.db.execute("SELECT COUNT(*) FROM items WHERE source='direction'").fetchone()[0], 0)
