"""Isolated source-to-Liquidsoap integration; source HTTP and voice are deterministic fixtures."""
import asyncio
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Set before importing radio modules, including when this file is run alone.
os.environ.setdefault('RADIO_DATA_DIR', tempfile.mkdtemp(prefix='public-news-import-'))
from radio import config, content, daemon, plugins, public_news, reddit, store, telegram, timeline
from radio.tts import MockTTS


def atom(date, title='Compiler release', key='t3_one'):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>{key}</id>
    <title>{title}</title><link href="https://www.reddit.com/r/python/comments/{key}/release/"/>
    <published>{date}</published><content type="html">Never read feed chrome</content></entry></feed>'''


def preview(date, text='Вышел новый компилятор.', number=42):
    return f'''<html><div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="example/{number}">
    <div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div>
    <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/example/{number}"><time datetime="{date}">10:00</time></a></div>
    </div></div></html>'''


class PublicNewsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='public-news-test-')
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for key, value in [('DATA_DIR', self.data), ('RUN_DIR', self.data / 'run'), ('SOCK', self.data / 'run/radio.sock')]:
            p = patch.object(config, key, value)
            p.start()
            self.addCleanup(p.stop)
        self.cfg = config.load()
        self.cfg['sources']['reddit']['communities'] = ['python']
        self.cfg['sources']['telegram']['channels'] = ['example']
        for name in plugins.PLUGINS:
            plugins.set_plugin(name, False)
        self.db = store.connect()
        self.addCleanup(self.db.close)
        self.date = store.iso(store.now())

    def test_normalization_dates_and_durable_dedup(self):
        posts = telegram.parse(preview(self.date, 'Привет <b>мир</b>!<br>Новая &amp; версия.'), 'example')
        self.assertEqual(posts[0]['body'], 'Привет мир! Новая & версия.')
        self.assertEqual(posts[0]['url'], 'https://t.me/example/42')
        self.assertEqual(public_news.take(self.db, self.cfg, posts), ['telegram:example/42'])
        db2 = store.connect()
        self.addCleanup(db2.close)
        self.assertEqual(public_news.take(db2, self.cfg, posts), [])
        other = dict(posts[0], id='reddit:t3_copy', source='reddit', url='https://www.reddit.com/r/python/copy')
        self.assertEqual(public_news.take(db2, self.cfg, [other]), [])
        self.assertEqual(len(reddit.parse(atom(self.date), 'python')), 1)
        for date in ('', '2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z', '2026-10-23T10:00:00'):
            self.assertEqual(public_news.take(db2, self.cfg, telegram.parse(preview(date, number=43), 'example')), [])
        self.assertEqual(public_news.take(db2, self.cfg, telegram.parse(preview(self.date, '', 44), 'example')), [])
        self.assertEqual(public_news.take(db2, self.cfg, telegram.parse(preview(self.date, 'nsfw', 45), 'example')), [])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM packages').fetchone()[0], 1)

    def test_access_validation_and_experiment_listing(self):
        for source in ('reddit', 'telegram'):
            for name in ('https://evil.test/', '../private', '@example', 'a?b', 'private+invite'):
                with self.assertRaises(ValueError):
                    public_news.names([name], source)
        for url in ('http://t.me/s/example', 'https://evil.test/', 'https://t.me:999/s/example', 'https://x@t.me/s/example'):
            with self.assertRaises(ValueError):
                public_news.check_url(url)
        with self.assertRaisesRegex(ValueError, 'preview unavailable'):
            telegram.parse('<html>Login or channel unavailable</html>', 'example')
        with self.assertRaises(ValueError):
            reddit.parse('<html/>', 'python')
        listing = {r['name']: r for r in plugins.listing(self.cfg)}
        for source in ('reddit', 'telegram'):
            self.assertEqual(listing[source]['section'], 'experiments')
            self.assertFalse(listing[source]['on'])

    def test_poll_gates_error_isolation_and_throttle(self):
        svc = daemon.Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)
        svc._news_preparation_due = lambda lead: True
        def get(url):
            if 'reddit.com' in url:
                raise OSError('403 public feed unavailable')
            return preview(self.date)
        with patch.object(public_news, 'fetch', side_effect=get) as get_mock:
            asyncio.run(svc.public_news_tick())
            get_mock.assert_not_called()
            plugins.set_plugin('reddit', True)
            plugins.set_plugin('telegram', True)
            asyncio.run(svc.public_news_tick())
            self.assertIn('error:', svc.sources['reddit'])
            self.assertEqual(svc.sources['telegram'], '1 new')
            self.assertEqual(get_mock.call_count, 2)
            asyncio.run(svc.public_news_tick())
            self.assertEqual(get_mock.call_count, 2)
            plugins.set_plugin('telegram', False)
            asyncio.run(svc.public_news_tick())
            self.assertIn('telegram', svc._sources_off())
            plugins.set_plugin('telegram', True)
            asyncio.run(svc.public_news_tick())
            self.assertEqual(get_mock.call_count, 3)
            self.assertEqual(svc.sources['telegram'], '0 new')

    def test_digest_import_unchanged(self):
        path = self.data / 'inbox/digest.md'
        path.write_text(f'---\ntopic: Digest\npublished_at: {self.date}\n---\nИсходный текст дайджеста.')
        content.scan_inbox(self.db, self.cfg)
        row = self.db.execute('SELECT * FROM items').fetchone()
        self.assertEqual((row['source'], row['kind'], row['state']), ('inbox', 'ready_digest', 'NEW'))
        script = Path(self.db.execute('SELECT script_path FROM packages').fetchone()[0]).read_text()
        self.assertIn('Исходный текст дайджеста.', script)

    def test_sources_reach_actual_broadcast(self):
        music = self.data / 'music'
        music.mkdir()
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=220:d=20',
                        '-ac', '2', str(music / 'track.mp3')], check=True)
        self.cfg['music']['path'] = music
        self.cfg['sources']['inbox_poll_sec'] = 1
        self.cfg['broadcast']['music_set_minutes'] = 5
        self.cfg['broadcast']['talkover_share'] = 0
        self.cfg['editor']['llm_enabled'] = False
        for source in ('reddit', 'telegram'):
            plugins.set_plugin(source, True)
        out = self.data / 'broadcast.wav'
        svc = daemon.Service(self.cfg, tts=MockTTS(), output=str(out))
        self.addCleanup(svc.db.close)
        def get(url):
            return atom(self.date) if 'reddit.com' in url else preview(self.date)

        from radio import broadcast_clock
        def edition_now():
            at = store.now()
            svc.db.execute("INSERT OR IGNORE INTO air_plan(id,kind,not_before) VALUES(?,'news',?)",
                           (broadcast_clock.PREFIX + str(int(at.timestamp())), store.iso(at)))
        edition_now()

        async def run():
            task = asyncio.create_task(svc.run())
            try:
                deadline = time.monotonic() + 75
                while time.monotonic() < deadline:
                    await asyncio.sleep(.25)
                    if task.done():
                        await task
                    # Prepare a second real-time occurrence after the first has aired.
                    if (svc.current and svc.current['kind'] == 'music' and not svc.queued_break
                            and svc.db.execute("SELECT COUNT(*) FROM packages WHERE status='PLAYED'").fetchone()[0] == 1
                            and not svc._clock_slot()):
                        edition_now()
                    if svc.db.execute("SELECT COUNT(*) FROM packages WHERE status='PLAYED'").fetchone()[0] >= 2:
                        break
            finally:
                await svc.stop()
                await task
        with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}), patch.object(public_news, 'fetch', side_effect=get), patch.object(
                svc.editor, 'retell_news', side_effect=AssertionError('news must not call LLM')):
            asyncio.run(run())
        rows = svc.db.execute('SELECT i.source,p.status FROM packages p JOIN items i ON i.id=p.item_id').fetchall()
        self.assertEqual({(r['source'], r['status']) for r in rows}, {('reddit', 'PLAYED'), ('telegram', 'PLAYED')})
        events = [r[0] for r in svc.db.execute('SELECT type FROM events ORDER BY id')]
        self.assertEqual(events.count('PACKAGE_COMPLETED'), 2)
        news = [r for r in timeline._build(svc.db, 12, 50) if r['kind'] == 'news']
        self.assertTrue(news)
        self.assertEqual({p['source'] for row in news for p in row['parts']}, {'reddit', 'telegram'})
        self.assertGreater(out.stat().st_size, 1000)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(out), '-f', 'null', '-'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())


if __name__ == '__main__':
    unittest.main()
