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
