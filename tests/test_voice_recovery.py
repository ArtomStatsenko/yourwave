import asyncio
import time
import unittest
from unittest.mock import Mock

from radio.daemon import Service


class VoiceRecoveryTests(unittest.TestCase):
    def service(self):
        svc = Service.__new__(Service)
        svc._live_settings = Mock()
        svc.cfg = {"voice": {}}
        svc.voice_engine = "voicestudio"
        svc.voice_proc = None
        svc.tts = Mock(health=Mock(return_value=True))
        svc.tts_retry_at = time.time() + 900
        svc.tts_backoff = 900
        svc.wake = asyncio.Event()
        return svc

    def test_recovery_clears_outage_and_retries_pending_speech_now(self):
        svc = self.service()
        svc.last_error = "VoiceStudio unavailable, will retry later"
        asyncio.run(svc._voice_tick())
        self.assertEqual(svc.last_error, "")
        self.assertEqual(svc.tts_retry_at, 0)
        self.assertEqual(svc.tts_backoff, 60)
        self.assertTrue(svc.wake.is_set())

    def test_healthy_voice_preserves_unrelated_error_and_retry(self):
        svc = self.service()
        svc.last_error = "content: unavailable"
        retry_at = svc.tts_retry_at
        asyncio.run(svc._voice_tick())
        self.assertEqual(svc.last_error, "content: unavailable")
        self.assertEqual(svc.tts_retry_at, retry_at)
        self.assertFalse(svc.wake.is_set())
