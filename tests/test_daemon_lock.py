"""One daemon at a time, and a stopped one does not hold the next start back while it finishes a thread."""
import fcntl
import unittest
from unittest.mock import AsyncMock, patch

from tests import test_radio  # noqa: F401  (an isolated data folder)
from radio import config, daemon
from radio.tts import MockTTS


class DaemonLockTests(unittest.TestCase):
    def test_the_lock_is_free_once_the_session_has_ended(self):
        config.RUN_DIR.mkdir(parents=True, exist_ok=True)
        held = open(config.RUN_DIR / 'daemon.lock', 'w')
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        svc = daemon.Service(config.load(), tts=MockTTS(), output='x')
        svc.lock = held
        other = open(config.RUN_DIR / 'daemon.lock', 'w')
        self.addCleanup(other.close)
        with self.assertRaises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        svc._release_lock()
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # a new daemon starts, though this process lives on

    def test_main_hands_the_daemon_its_lock(self):
        with patch.object(daemon, 'Service') as service:
            service.return_value.run = AsyncMock()
            daemon.main('x')
        self.assertEqual(service.return_value.lock.name, str(config.RUN_DIR / 'daemon.lock'))
        service.return_value.lock.close()


if __name__ == '__main__':
    unittest.main()
