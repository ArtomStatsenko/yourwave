import asyncio
import resource
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from radio import config
from radio.engine import Liquidsoap


class EngineCommandTests(unittest.TestCase):
    def test_repeated_commands_do_not_exhaust_engine_sockets(self):
        async def scenario():
            with tempfile.TemporaryDirectory(prefix='radio-command-') as directory:
                root = Path(directory)
                engine = Liquidsoap(root / 'music.m3u', .1)
                engine.sock = root / 'liq.sock'
                script = root / 'control.liq'
                script.write_text(
                    'settings.log.stdout := false\nsettings.log.file := false\n'
                    'settings.server.socket := true\n'
                    f'settings.server.socket.path := "{engine.sock}"\n'
                    'output.dummy(blank())\n')

                def limit_files():
                    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))

                proc = await asyncio.create_subprocess_exec(
                    'liquidsoap', str(script), preexec_fn=limit_files,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    for _ in range(100):
                        if engine.sock.exists():
                            break
                        await asyncio.sleep(.1)
                    for _ in range(160):
                        self.assertIn('Liquidsoap', await engine.command('version'))
                finally:
                    proc.terminate()
                    await proc.wait()
        asyncio.run(scenario())

    def test_a_left_liquidsoap_is_gone_before_the_new_one_makes_its_socket(self):
        """Liquidsoap deletes its socket file on its way out: an old one still shutting down deleted the new one's."""
        with tempfile.TemporaryDirectory(prefix='radio-orphan-') as directory:
            root = Path(directory)
            engine = Liquidsoap(root / 'music.m3u', .1)
            engine.sock, engine.pidfile = root / 'liq.sock', root / 'liquidsoap.pid'
            # Stands in for the old engine (its command line names liquidsoap and radio.liq): slow to stop, then
            # deletes the socket path. Started by a shell that exits, so it is nobody's child, like a dead daemon's.
            old = ('import os,signal,sys,time\n'
                   'def end(*_): time.sleep(.5); os.unlink(sys.argv[1]) if os.path.exists(sys.argv[1]) else 0; os._exit(0)\n'
                   'signal.signal(signal.SIGTERM, end)\nopen(sys.argv[4], "w").write(str(os.getpid()))\ntime.sleep(30)\n')
            ready = root / 'old.pid'
            subprocess.run(['sh', '-c', '"$0" -c "$1" "$2" liquidsoap "$3" "$4" >/dev/null 2>&1 &',
                            sys.executable, old, str(engine.sock), str(config.PKG_DIR / 'radio.liq'), str(ready)], check=True)
            for _ in range(50):
                if ready.exists() and ready.read_text():
                    break
                time.sleep(.1)
            engine.pidfile.write_text(ready.read_text())
            asyncio.run(engine._end_orphan())
            engine.sock.write_text('')  # the new engine's socket
            time.sleep(.8)
            self.assertTrue(engine.sock.exists())
