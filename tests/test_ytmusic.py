import asyncio
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from radio import config, music, store, ytmusic


class YouTubeMusicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.location = patch.object(config, "DATA_DIR", Path(self.tmp.name))
        self.location.start()
        self.addCleanup(self.location.stop)
        config.load()
        self.data = {"entries": [{"id": "dQw4w9WgXcQ", "title": 'Song "one"\n',
                                  "artist": "Artist", "duration": 213}]}
        self.cookie = "__Secure-3PAPISID=private-test-value; SID=test"

    def connect(self):
        with patch.object(ytmusic, "run", return_value=json.dumps(self.data)):
            return ytmusic.connect(self.cookie)

    def test_feature_can_be_enabled_before_connecting_in_settings(self):
        from radio import plugins
        self.assertFalse(ytmusic.status()['connected'])
        plugins.set_plugin('ytmusic', True)
        self.assertTrue(plugins.on(config.load(), 'ytmusic'))
        self.assertFalse(ytmusic.status()['connected'])
        self.assertFalse((ytmusic.directory() / 'cookies.txt').exists())
        plugins.set_plugin('ytmusic', False)
        self.assertFalse(plugins.on(config.load(), 'ytmusic'))

    def test_auth_validation_and_private_storage(self):
        for cookie in ("", "SID=only", self.cookie + "\nX-Header: injected", "SAPISID=x\ty"):
            with self.assertRaises(ValueError):
                ytmusic.cookie_jar(cookie)
        result = self.connect()
        self.assertEqual(result["tracks"], 1)
        jar = ytmusic.directory() / "cookies.txt"
        self.assertEqual(jar.stat().st_mode & 0o777, 0o600)
        self.assertEqual(jar.parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(".youtube.com\tTRUE", jar.read_text())
        with patch.object(ytmusic, "run", side_effect=ValueError("offline")):
            with self.assertRaises(ValueError):
                ytmusic.connect("SAPISID=new-value")
        self.assertIn("private-test-value", jar.read_text())
        self.assertEqual(ytmusic.library()["tracks"][0]["id"], "dQw4w9WgXcQ")

    def test_untrusted_playlist_and_entries(self):
        for data in ([], {}, {"entries": None}):
            with self.assertRaises(ValueError):
                ytmusic.parse_tracks(data)
        for url in ("file:///tmp/secret", "https://localhost/playlist?list=LM",
                    "https://music.youtube.com.evil/playlist?list=LM", "https://music.youtube.com/watch?v=x",
                    "https://music.youtube.com/playlist?list=LM&list=other"):
            with self.assertRaises(ValueError):
                ytmusic.playlist_url(url)
        self.data["entries"] += [None, {"id": "../../x", "title": "x"},
                                 {"id": "abcdefghijk", "title": "[Private video]"},
                                 {"id": "abcdefghijk", "title": "live", "duration": float("inf")},
                                 self.data["entries"][0]]
        self.assertEqual(len(ytmusic.parse_tracks(self.data)), 1)

    def test_corrupt_manifest_does_not_break_local_radio(self):
        self.connect()
        for data in ([], {}, {"playlist": "x", "tracks": None}):
            (ytmusic.directory() / "library.json").write_text(json.dumps(data))
            self.assertFalse(ytmusic.status()["connected"])

    def test_playlist_metadata_and_resolver_boundary(self):
        self.connect()
        db = store.connect()
        self.addCleanup(db.close)
        out = config.DATA_DIR / "run/music.m3u"
        refs = ytmusic.write_m3u(db, out)
        ref = "ytmusic:dQw4w9WgXcQ"
        self.assertEqual(refs, {ref: None})
        self.assertIn('ref="ytmusic:dQw4w9WgXcQ"', out.read_text())
        self.assertEqual(music.label(db, ref), 'Artist - Song "one"')
        self.assertEqual(ytmusic.duration(ref), 213)
        def download(args, *a, **kw):
            Path(args[args.index("-o") + 1]).write_bytes(b"test audio")
        with patch.object(ytmusic, "run", side_effect=download) as run:
            out = Path(ytmusic.resolve("dQw4w9WgXcQ"))
            self.assertEqual(out.read_bytes(), b"test audio")
            self.assertIn("--http-chunk-size", run.call_args.args[0])
            out.unlink()
        with patch.object(ytmusic, "run") as run:
            for vid in ("; touch /tmp/x", "abcdefghijk"):
                with self.assertRaises(ValueError):
                    ytmusic.resolve(vid)
            run.assert_not_called()
        with patch.object(ytmusic, "run", return_value=""):
            with self.assertRaises(ValueError):
                ytmusic.resolve("dQw4w9WgXcQ")
        self.assertEqual(list((ytmusic.directory() / "audio").iterdir()), [])

    def test_yt_dlp_failure_does_not_expose_credentials(self):
        self.connect()
        import subprocess
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "SAPISID=secret HTTP 429")):
            with self.assertRaises(ValueError) as error:
                ytmusic.resolve("dQw4w9WgXcQ")
        self.assertNotIn("secret", str(error.exception))
        self.assertIn("rate-limiting", str(error.exception))

    def test_unavailable_song_is_distinct_from_network_failure(self):
        import subprocess
        self.connect()
        for message in ("Video unavailable", "Private video", "Not available in your country"):
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", message)):
                with self.assertRaises(ytmusic.Unavailable):
                    ytmusic.resolve("dQw4w9WgXcQ")
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired('yt-dlp', 20)):
            with self.assertRaises(ValueError) as error:
                ytmusic.resolve("dQw4w9WgXcQ")
            self.assertNotIsInstance(error.exception, ytmusic.Unavailable)
        self.assertEqual(list((ytmusic.directory() / "audio").iterdir()), [])

    def test_downloaded_artist_is_displayed_and_survives_playlist_reload(self):
        self.data['entries'][0]['artist'] = ''  # flat playlists omit musical credits
        self.connect()
        db = store.connect()
        self.addCleanup(db.close)
        playlist = config.DATA_DIR / 'run/ytmusic.m3u'
        ytmusic.write_m3u(db, playlist)
        metadata = {'artists': ['Kina', 'Adriana Proenza'], 'track': 'Can We Kiss Forever?',
                    'title': 'Music video title', 'channel': 'Uploader name'}
        def download(args, *a, **kw):
            Path(args[args.index('-o') + 1]).write_bytes(b'audio')
            return json.dumps(metadata)
        with patch.object(ytmusic, 'run', side_effect=download):
            Path(ytmusic.resolve('dQw4w9WgXcQ')).unlink()
        expected = 'Kina, Adriana Proenza - Can We Kiss Forever?'
        self.assertEqual(music.label(db, 'ytmusic:dQw4w9WgXcQ'), expected)
        ytmusic.write_m3u(db, playlist)
        self.assertEqual(music.label(db, 'ytmusic:dQw4w9WgXcQ'), expected)
        metadata = {'channel': 'Another uploader', 'title': 'Another video title'}
        with patch.object(ytmusic, 'run', side_effect=download):
            Path(ytmusic.resolve('dQw4w9WgXcQ')).unlink()
        self.assertEqual(music.label(db, 'ytmusic:dQw4w9WgXcQ'), expected)

    def test_remote_track_duration_timeline_and_replay(self):
        from radio import timeline
        from radio.daemon import Service
        from radio.tts import MockTTS
        self.connect()
        svc = Service(config.load(), tts=MockTTS())
        self.addCleanup(svc.db.close)
        refs = ytmusic.write_m3u(svc.db, config.DATA_DIR / "run/ytmusic.m3u")
        svc.library = set(refs)
        svc.ytmusic_active = True
        ref = next(iter(refs))
        with patch("radio.audio.duration", side_effect=AssertionError("remote ref is not a file")):
            svc.on_event({"kind": "music", "ref": ref})
        self.assertEqual(svc.current["duration"], 213)
        self.assertFalse(svc._new_set_due())
        entry = timeline._build(svc.db, 12, 20)[0]
        self.assertTrue(entry["replayable"])
        self.assertEqual(entry["title"], 'Artist - Song "one"')
        svc.engine = AsyncMock()
        svc.engine.push_replay.return_value = "1"
        self.assertTrue(asyncio.run(svc.replay("music", ref))["ok"])
        self.assertEqual(str(svc.engine.push_replay.call_args.args[0]), ref)
        self.assertFalse(asyncio.run(svc.replay("music", "ytmusic:abcdefghijk"))["ok"])


class LiquidsoapSkipTests(unittest.TestCase):
    def test_local_songs_cover_loading_and_a_bad_song_does_not_switch_the_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tone = root / "tone.wav"
            with wave.open(str(tone), "wb") as w:
                w.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
                w.writeframes(b"\xe8\x03\xe8\x03" * (44100 * 6))
            remote = root / "remote.m3u"
            remote.write_text('annotate:kind="music",ref="ytmusic:abcdefghijk":ytmusic:abcdefghijk\n'
                              'annotate:kind="music",ref="ytmusic:dQw4w9WgXcQ":ytmusic:dQw4w9WgXcQ\n')
            local = root / "local.m3u"
            local.write_text(f'annotate:kind="music",ref="local-test":{tone}\n')
            helper = root / "resolve.py"
            helper.write_text('import os, sys, pathlib, tempfile, shutil, time\n'
                              'time.sleep(.3)\n'
                              'root=pathlib.Path(sys.argv[1]); case=os.environ["TEST_YT_CASE"]\n'
                              'marker=root/(case+".attempted")\n'
                              'failed=not marker.exists(); marker.touch()\n'
                              'if case=="offline": print("!offline")\n'
                              'elif case=="all-bad" or failed: print("!unavailable")\n'
                              'else:\n'
                              ' f=tempfile.NamedTemporaryFile(dir=root,suffix=".wav",delete=False); f.close()\n'
                              ' shutil.copyfile(root/"tone.wav",f.name); print(f.name)\n')
            # A song not loaded by the boundary is covered by a local one (never silence); YouTube Music comes back
            # after one bad song, and stays off only when all fail or it is offline.
            for case, expected in (("one-bad", "ytmusic:"), ("all-bad", "local-test"), ("offline", "local-test")):
                with self.subTest(case=case):
                    events = root / (case + ".jsonl")
                    events.touch()
                    env = dict(os.environ, TEST_YT_CASE=case, RADIO_M3U=str(remote), RADIO_FALLBACK_M3U=str(local),
                               RADIO_YTMUSIC_COUNT="2", RADIO_YTMUSIC_CMD=shlex.join([sys.executable, str(helper), str(root)]),
                               RADIO_EVENTS=str(events), RADIO_LIQ_SOCK=str(root / (case + ".sock")),
                               RADIO_LIQ_LOG=str(root / (case + ".log")), RADIO_OUTPUT=str(root / (case + ".wav")),
                               RADIO_FILE_SYNC="realtime", RADIO_XFADE="1")
                    with (root / (case + ".stderr")).open("w") as log:
                        proc = subprocess.Popen(["liquidsoap", str(config.PKG_DIR / "radio.liq")], env=env, stdout=log, stderr=log)
                        try:
                            started, deadline = time.monotonic(), time.monotonic() + 20
                            while not events.stat().st_size and proc.poll() is None and time.monotonic() < deadline:
                                time.sleep(.1)
                            self.assertLess(time.monotonic() - started, 8, "Silence at the start")
                            def rows():
                                return [json.loads(line) for line in events.read_text().splitlines()]
                            while not (root / (case + ".attempted")).exists() or case == "one-bad" and not any(
                                    r["ref"].startswith("ytmusic:") for r in rows()):
                                if time.monotonic() > deadline or proc.poll() is not None:
                                    break
                                time.sleep(.1)
                            self.assertTrue((root / (case + ".attempted")).exists())
                            self.assertTrue(any(r["ref"].startswith(expected) for r in rows()), rows())
                            if case != "one-bad":
                                self.assertFalse(any(r["ref"].startswith("ytmusic:") for r in rows()), rows())
                        finally:
                            proc.terminate()
                            try:
                                proc.wait(5)
                            except subprocess.TimeoutExpired:
                                proc.kill()
                                proc.wait()


if __name__ == "__main__":
    unittest.main()
