import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave
import zipfile

from tools import install_base_audio as installer


class BaseAudioTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.archive = self.root / "base.zip"
        audio = io.BytesIO()
        with wave.open(audio, "wb") as wav:
            wav.setparams((2, 2, 44100, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0" * 400)
        self.content = audio.getvalue()
        self.files = {name: self.content for name in
                      ("bed.wav", "stinger_in.wav", "stinger_out.wav", "separator.wav")}
        self.spec = {"version": "test", "url": "https://example.invalid/base.zip", "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in self.files.items()}}
        self.pack(self.files)
        for name, value in (("ASSETS", self.assets), ("MANIFEST", self.root / "manifest.json")):
            p = patch.object(installer, name, value)
            p.start()
            self.addCleanup(p.stop)

    def pack(self, files):
        with zipfile.ZipFile(self.archive, "w") as z:
            for name, data in {**files, "RIGHTS.md": b"credit", "LICENSE-CC-BY-4.0.txt": b"license"}.items():
                z.writestr(name, data)
        data = self.archive.read_bytes()
        self.spec.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        (self.root / "manifest.json").write_text(json.dumps(self.spec))

    def test_offline_install_is_idempotent_and_preserves_personal_files(self):
        personal = self.assets / "own.wav"
        personal.write_bytes(b"personal")
        self.assertEqual(installer.install(self.archive), "test")
        self.assertEqual(installer.install(self.archive), "test")
        for name in self.files:
            self.assertEqual((self.assets / name).read_bytes(), self.content)
        self.assertEqual(personal.read_bytes(), b"personal")

    def test_download_uses_pinned_url_and_timeout(self):
        with patch.object(installer.urllib.request, "urlopen", return_value=io.BytesIO(self.archive.read_bytes())) as get:
            installer.install()
        get.assert_called_once_with(self.spec["url"], timeout=30)

    def test_corrupted_download_writes_nothing(self):
        self.archive.write_bytes(self.archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            installer.install(self.archive)
        self.assertEqual(list(self.assets.iterdir()), [])

    def test_traversal_and_wrong_audio_hash_write_nothing(self):
        for files in ({**self.files, "../escape.wav": self.content},
                      {**self.files, "bed.wav": b"tamper"}):
            with self.subTest(files=list(files)):
                self.pack(files)
                with self.assertRaises(ValueError):
                    installer.install(self.archive)
                self.assertEqual(list(self.assets.iterdir()), [])
                self.assertFalse((self.root / "escape.wav").exists())

    def test_custom_audio_and_symlinks_are_not_replaced(self):
        dest = self.assets / "bed.wav"
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    dest.symlink_to(self.root / "outside.wav")
                else:
                    dest.write_bytes(b"custom")
                with self.assertRaisesRegex(ValueError, "Custom sound"):
                    installer.install(self.archive)
                self.assertEqual(len(list(self.assets.iterdir())), 1)
                dest.unlink()

    def test_failed_install_rolls_back_new_files(self):
        original = self.assets / "stinger_out.wav"
        original.write_bytes(self.content)
        replace = installer.os.replace
        calls = 0

        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk error")
            return replace(*args)

        with patch.object(installer.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "disk error"):
                installer.install(self.archive)
        self.assertEqual(list(self.assets.iterdir()), [original])
        self.assertEqual(original.read_bytes(), self.content)
