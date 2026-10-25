"""Every news source crosses the same editorial gate before it can be voiced."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, content, daemon, editor, store, timeline
from radio.tts import MockTTS


class EditorialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-editorial-')
        self.addCleanup(tmp.cleanup)
        for name, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.cfg = config.load()
        self.svc = daemon.Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)

    def package(self, source, kind='raw_post', **extra):
        item = {'id': source, 'source': source, 'topic': 'Новости', 'url': '',
                'published_at': store.now(), 'body': 'A company released a new editor.', **extra}
        content.add_item(self.svc.db, self.cfg, item, kind)
        return dict(self.svc.db.execute('SELECT * FROM packages WHERE item_id=?', (source,)).fetchone())

    def test_every_news_source_uses_russian_preparation_and_keeps_original(self):
        russian = 'Компания выпустила новый редактор кода.'
        for source, kind in [('reddit', 'raw_post'), ('telegram', 'raw_post'), ('github_trending', 'raw_post'),
                             ('github_new', 'raw_post'), ('horizon', 'raw_post'), ('inbox', 'ready_digest'),
                             ('future_source', 'raw_post')]:
            with self.subTest(source=source), patch.object(self.svc.editor, '_ask', return_value=russian) as ask:
                package = self.package(source, kind)
                original = Path(package['script_path']).read_text()
                self.svc._prepare_package(package)
                self.assertEqual(self.svc._body_segments(package, None), [(russian, False)])
                self.svc._prepare_package(package)  # stale caller snapshot, retry or a restart
                ask.assert_called_once()
                self.assertEqual(Path(package['script_path']).read_text(), original)
                self.assertIn('A company', ask.call_args.args[0])

    def test_existing_source_retelling_is_not_rewritten(self):
        package = self.package('github', text_prepared=True, body='Компания выпустила новый редактор кода.')
        with patch.object(self.svc.editor, '_ask', side_effect=AssertionError('second retelling')):
            prepared = self.svc._prepare_package(package)
        self.assertIn('новый редактор', prepared['prepared_text'])

    def test_ready_audio_cannot_bypass_editorial_gate_even_with_a_plan_override(self):
        package = self.package('telegram')
        self.svc.db.execute("UPDATE packages SET status='READY',speech_sec=5,audio_path='/tmp/unreviewed.wav'")
        self.assertEqual(content.ready_to_air(self.svc.db), [])
        with self.assertRaisesRegex(ValueError, 'editorial preparation'):
            self.svc._body_segments(package, None)
        from radio import plugins
        plugins.set_plugin('air_direction', True)
        plugins.set_plugin('telegram', True)
        self.svc.db.execute("INSERT INTO air_plan(id,kind,rank) VALUES(?,'news',0)", (package['id'],))
        self.assertEqual(self.svc._pick_break(), [])

    def test_failed_or_rejected_editorial_text_never_reaches_tts(self):
        for result in (None, OSError('provider unavailable')):
            with self.subTest(result=result):
                self.svc.db.execute('DELETE FROM packages')
                self.svc.db.execute('DELETE FROM items')
                package = self.package('telegram')
                with patch.object(self.svc.editor, '_ask', side_effect=result if isinstance(result, Exception) else None,
                                  return_value=result), patch.object(self.svc.tts, 'health', return_value=True), \
                        patch.object(self.svc.tts, 'synthesize') as synth:
                    if isinstance(result, Exception):
                        with self.assertRaises(OSError):
                            self.svc._render(package)
                    else:
                        self.svc._render(package)
                synth.assert_not_called()
                saved = self.svc.db.execute('SELECT * FROM packages WHERE id=?', (package['id'],)).fetchone()
                self.assertEqual(saved['status'], 'PENDING' if isinstance(result, Exception) else 'SKIPPED')
                self.assertEqual(saved['attempts'], 0)
                self.assertIsNone(saved['prepared_text'])

    def test_disabled_editor_and_hourly_limit_keep_news_retryable(self):
        package = self.package('inbox', 'ready_digest')
        with patch.object(self.svc.tts, 'health', return_value=True), patch.object(self.svc.tts, 'synthesize') as synth:
            self.svc.cfg['editor']['llm_enabled'] = False
            with self.assertRaisesRegex(RuntimeError, 'editor disabled'):
                self.svc._render(package)
            self.svc.cfg['editor']['llm_enabled'] = True
            with patch.object(self.svc.editor, '_ask', side_effect=ValueError('editor hourly request limit reached')):
                for _ in range(3):
                    with self.assertRaisesRegex(ValueError, 'hourly request limit'):
                        self.svc._render(package)
            synth.assert_not_called()
        saved = self.svc.db.execute('SELECT status,attempts FROM packages').fetchone()
        self.assertEqual(tuple(saved), ('PENDING', 0))

    def test_notices_keep_dates_numbers_names_and_instructions_exactly(self):
        text = 'Через пять минут — Созвон с John в 14:35, комната 204. Не опаздывай.'
        with patch.object(self.svc.editor, '_ask', side_effect=AssertionError('facts must stay exact')):
            for policy in ('reminder', 'weather', 'announcement'):
                self.assertEqual(self.svc.editor.prepare(text, policy), text)
        with self.assertRaises(ValueError):
            self.svc.editor.prepare(text, 'unknown')

    def test_history_and_editor_show_the_spoken_text(self):
        package = self.package('telegram')
        russian = 'Компания выпустила новый редактор кода.'
        with patch.object(self.svc.editor, '_ask', return_value=russian):
            self.svc._prepare_package(package)
        store.log_event(self.svc.db, self.svc.session, 'PACKAGE_STARTED', package['id'])
        shown = timeline._build(self.svc.db, 12, 20)
        self.assertEqual(shown[0]['parts'][0]['text'], russian)
        from radio import direction
        with patch('radio.direction.plugins.on', return_value=True):
            self.assertEqual(direction.rows(self.svc.db, self.cfg, store.now())[0]['preview'], russian)

    def test_legacy_ready_news_returns_to_preparation_once_without_changing_history(self):
        self.svc.db.close()
        path = config.DATA_DIR / 'db/radio.sqlite3'
        import sqlite3
        db = sqlite3.connect(path)
        db.execute('ALTER TABLE packages DROP COLUMN prepared_text')
        for status in ('READY', 'PLAYED', 'PENDING'):
            db.execute("INSERT INTO items(id,kind,state) VALUES(?,'raw_post','NEW')", (status,))
            db.execute('INSERT INTO packages(id,item_id,status) VALUES(?,?,?)', (status, status, status))
        db.commit()
        db.close()
        db = store.connect()
        self.addCleanup(db.close)
        self.assertEqual(dict(db.execute('SELECT id,status FROM packages')), {'READY': 'PENDING', 'PLAYED': 'PLAYED', 'PENDING': 'PENDING'})
        db.execute("UPDATE packages SET status='READY',prepared_text='Готовый текст.' WHERE id='READY'")
        reopened = store.connect()
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.execute("SELECT status FROM packages WHERE id='READY'").fetchone()[0], 'READY')
