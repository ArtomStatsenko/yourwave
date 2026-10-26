import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from radio import config


class PageServerTests(unittest.TestCase):
    """The page's guards: a per-run token on the API and a local Host header (no DNS rebinding)."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="radio-ui-")
        self.addCleanup(tmp.cleanup)
        for name, value in [("DATA_DIR", Path(tmp.name)), ("RUN_DIR", Path(tmp.name) / "run")]:
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        config.load()
        from radio import ui
        with patch.object(ui, "Stream"), patch("radio.__main__.send", return_value=None):  # the radio is off
            self.server = ui.make_server(0)
        import threading
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def get(self, path, headers=None, data=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_api_needs_the_token_and_a_local_host(self):
        with patch("radio.__main__.send", return_value=None):
            status, page = self.get("/")
            self.assertEqual(status, 200)
            self.assertIn(self.server.token.encode(), page)                  # the page gets the token
            self.assertEqual(self.get("/api/status")[0], 403)                 # no token: nothing
            status, body = self.get("/api/status", {"X-Radio-Token": self.server.token})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"]["state"], "STOPPED")
            self.assertEqual(self.get("/", {"Host": "radio.evil.example:80"})[0], 403)   # DNS rebinding
            self.assertEqual(self.get("/api/cmd", {"X-Radio-Token": self.server.token, "Host": "evil:1"},
                                      data=b'{"cmd":"stop"}')[0], 403)
            self.assertEqual(self.get("/api/cmd", {"X-Radio-Token": "wrong"}, data=b'{"cmd":"stop"}')[0], 403)

    def test_capabilities_reuses_the_shared_registry_and_requires_auth(self):
        from radio import control
        self.assertEqual(self.get('/api/capabilities')[0], 403)
        code, raw = self.get('/api/capabilities', {'X-Radio-Token': self.server.token})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw), control.capabilities())

    def test_news_sources_save_through_the_ui_api(self):
        from radio import plugins, settings
        plugins.set_plugin('reddit', True)
        headers = {'X-Radio-Token': self.server.token, 'Content-Type': 'application/json'}
        with patch('radio.editor.connection', return_value={'state': 'no_key', 'detail': 'No key', 'ids': []}), patch.object(settings, '_voice_state', return_value={}), patch('radio.public_news.fetch', return_value='<feed xmlns="http://www.w3.org/2005/Atom"/>'):
            status, body = self.get('/api/cmd', headers, json.dumps({
                'cmd': 'setting', 'key': 'reddit_posts',
                'value': 'https://www.reddit.com/r/python/comments/abc123/title/'}).encode())
            self.assertEqual(status, 200)
            rows = {r['key']: r for r in json.loads(body)['settings']}
            self.assertNotIn('reddit_posts', rows)
            self.assertEqual(settings._saved()['reddit_posts'], ['https://www.reddit.com/comments/abc123/'])
            before = (config.DATA_DIR / 'settings.json').read_bytes()
            status, body = self.get('/api/cmd', headers, json.dumps({
                'cmd': 'setting', 'key': 'reddit_posts', 'value': 'https://evil.test/comments/abc123'}).encode())
            self.assertEqual(status, 400)
            self.assertFalse(json.loads(body)['ok'])
            self.assertEqual((config.DATA_DIR / 'settings.json').read_bytes(), before)

    def test_music_source_choice_is_independent_of_the_integration_switch(self):
        from radio import plugins, settings
        headers = {'X-Radio-Token': self.server.token, 'Content-Type': 'application/json'}
        def command(body):
            status, raw = self.get('/api/cmd', headers, json.dumps(body).encode())
            return status, json.loads(raw)
        def choose(source):
            return command({'cmd': 'setting', 'key': 'music_source', 'value': source})
        with patch('radio.editor.connection', return_value={'state': 'no_key', 'detail': 'No key', 'ids': []}), patch.object(settings, '_voice_state', return_value={}):
            plugins.set_plugin('ytmusic', False)
            self.assertEqual(choose('ytmusic')[0], 400)
            self.assertEqual(command({'cmd': 'plugin', 'name': 'ytmusic', 'on': True})[0], 200)
            self.assertEqual(settings.music_source(config.load()), 'local')
            self.assertEqual(choose('ytmusic')[0], 200)
            self.assertTrue(plugins.on(config.load(), 'ytmusic'))
            self.assertEqual(settings.music_source(config.load()), 'ytmusic')
            self.assertEqual(choose('local')[0], 200)
            self.assertTrue(plugins.on(config.load(), 'ytmusic'))
            self.assertEqual(choose('ytmusic')[0], 200)
            self.assertEqual(command({'cmd': 'plugin', 'name': 'ytmusic', 'on': False})[0], 200)
            self.assertEqual(settings.music_source(config.load()), 'local')
            self.assertEqual(command({'cmd': 'plugin', 'name': 'ytmusic', 'on': True})[0], 200)
            status, raw = self.get('/api/status', headers)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)['music_source_choice'], 'local')
            self.assertEqual(choose('invalid')[0], 400)

    def test_connecting_youtube_does_not_enable_the_integration(self):
        from radio import plugins
        plugins.set_plugin('ytmusic', False)
        with patch('radio.ytmusic.connect', return_value={'connected': True, 'tracks': 1}):
            status, _ = self.get('/api/cmd', {'X-Radio-Token': self.server.token},
                                 json.dumps({'cmd': 'ytmusic_connect', 'cookie': 'test'}).encode())
        self.assertEqual(status, 200)
        self.assertFalse(plugins.on(config.load(), 'ytmusic'))

    def test_subreddits_are_added_and_removed_individually(self):
        from radio import plugins, settings
        plugins.set_plugin('reddit', True)
        settings.set_setting('reddit_communities', 'python')
        headers = {'X-Radio-Token': self.server.token, 'Content-Type': 'application/json'}
        def change(value, action='add'):
            status, raw = self.get('/api/cmd', headers, json.dumps({
                'cmd': 'reddit_community', 'action': action, 'value': value}).encode())
            return status, json.loads(raw)
        with patch('radio.editor.connection', return_value={'state': 'no_key', 'detail': 'No key', 'ids': []}), patch.object(settings, '_voice_state', return_value={}):
            with patch('radio.public_news.fetch', return_value='<feed xmlns="http://www.w3.org/2005/Atom"/>') as fetch:
                self.assertEqual(change('r/technology')[0], 200)
                fetch.assert_called_once_with('https://www.reddit.com/r/technology/new/.rss?limit=25')
                self.assertEqual(settings._saved()['reddit_communities'], ['python', 'technology'])
                fetch.reset_mock()
                self.assertEqual(change('technology')[0], 200)
                self.assertEqual(change('python', 'remove')[0], 200)
                fetch.assert_not_called()
                self.assertEqual(settings._saved()['reddit_communities'], ['technology'])
            before = (config.DATA_DIR / 'settings.json').read_bytes()
            with patch('radio.public_news.fetch', side_effect=OSError('network')):
                self.assertEqual(change('bestgithubrepos')[0], 400)
                self.assertEqual((config.DATA_DIR / 'settings.json').read_bytes(), before)
                self.assertEqual(change('technology', 'remove')[0], 200)
                self.assertEqual(settings._saved()['reddit_communities'], [])
            with patch('radio.public_news.fetch') as fetch:
                self.assertEqual(change('python,technology')[0], 400)
                settings.set_setting('reddit_communities', ['sub'+str(n) for n in range(20)])
                self.assertEqual(change('another')[0], 400)
                fetch.assert_not_called()

    def test_telegram_channels_are_added_individually_with_public_preview_check(self):
        from radio import plugins, settings
        plugins.set_plugin('telegram', True)
        settings.set_setting('telegram_channels', ['telegram'])
        headers = {'X-Radio-Token': self.server.token, 'Content-Type': 'application/json'}
        def change(value, action='add'):
            status, raw = self.get('/api/cmd', headers, json.dumps({
                'cmd': 'telegram_channel', 'action': action, 'value': value}).encode())
            return status, json.loads(raw)
        with patch('radio.editor.connection', return_value={'state': 'no_key', 'detail': 'No key', 'ids': []}), patch.object(settings, '_voice_state', return_value={}):
            with patch('radio.telegram.fetch', return_value=[]) as fetch:
                self.assertEqual(change('https://t.me/example')[0], 200)
                fetch.assert_called_once_with('example')
                self.assertEqual(settings._saved()['telegram_channels'], ['telegram', 'example'])
                fetch.reset_mock()
                self.assertEqual(change('@Example')[0], 200)
                self.assertEqual(change('telegram', 'remove')[0], 200)
                fetch.assert_not_called()
            before = (config.DATA_DIR / 'settings.json').read_bytes()
            with patch('radio.public_news.fetch', return_value='<html>unavailable</html>'):
                self.assertEqual(change('@unavailable')[0], 400)
                self.assertEqual((config.DATA_DIR / 'settings.json').read_bytes(), before)
            with patch('radio.telegram.fetch', side_effect=OSError('network')) as fetch:
                self.assertEqual(change('@another')[0], 400)
                fetch.reset_mock()
                self.assertEqual(change('https://t.me/c/123/456')[0], 400)
                self.assertEqual(change('@onechan,@twochan')[0], 400)
                self.assertEqual(change('example', 'remove')[0], 200)
                fetch.assert_not_called()
                self.assertEqual(settings._saved()['telegram_channels'], [])

    def test_reddit_rss_check_preserves_saved_lists_on_error(self):
        from radio import plugins, settings
        plugins.set_plugin('reddit', True)
        settings.set_setting('reddit_communities', 'python')
        headers = {'X-Radio-Token': self.server.token, 'Content-Type': 'application/json'}
        def save(value):
            status, raw = self.get('/api/cmd', headers, json.dumps({
                'cmd': 'setting', 'key': 'reddit_communities', 'value': value}).encode())
            return status, json.loads(raw)
        with patch('radio.editor.connection', return_value={'state': 'no_key', 'detail': 'No key', 'ids': []}), patch.object(settings, '_voice_state', return_value={}):
            before = (config.DATA_DIR / 'settings.json').read_bytes()
            for failure in (OSError('network'), ValueError('not a feed')):
                with patch('radio.public_news.fetch', side_effect=failure):
                    status, result = save('junk')
                self.assertEqual(status, 400)
                self.assertIn('junk', result['error'])
                self.assertEqual((config.DATA_DIR / 'settings.json').read_bytes(), before)
            with patch('radio.public_news.fetch', return_value='<feed xmlns="http://www.w3.org/2005/Atom"/>') as fetch:
                self.assertEqual(save('technology')[0], 200)
                fetch.assert_called_once_with('https://www.reddit.com/r/technology/new/.rss?limit=25')
                fetch.reset_mock()
                self.assertEqual(save('')[0], 200)
                fetch.assert_not_called()


class QwenTrimTests(unittest.TestCase):
    def test_only_the_edges_are_trimmed(self):
        import io
        import subprocess
        from radio import audio, tts
        tmp = Path(tempfile.mkdtemp(prefix="radio-trim-"))
        ref = tmp / "host.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=1", str(ref)], check=True)
        (tmp / "host.txt").write_text("текст")
        # 1 s tone, 0.6 s pause, 1 s tone, 0.8 s tail: what the voice server would answer
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc=0.3*sin(2*PI*800*t):s=24000:d=1",
                        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=0.6", "-f", "lavfi", "-i", "aevalsrc=0.3*sin(2*PI*800*t):s=24000:d=1",
                        "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono:d=0.8", "-filter_complex", "[0][1][2][3]concat=n=4:v=0:a=1",
                        "-ac", "1", str(tmp / "answer.wav")], check=True)
        voice = tts.MlxClone("http://127.0.0.1:1", "m", tmp / "host")
        out = tmp / "out.wav"
        with patch("radio.tts.urllib.request.urlopen", return_value=io.BytesIO((tmp / "answer.wav").read_bytes())):
            voice.synthesize("Раз. Два.", out)
        self.assertAlmostEqual(audio.duration(out), 2.6, delta=0.05)  # the pause between the sentences stays
