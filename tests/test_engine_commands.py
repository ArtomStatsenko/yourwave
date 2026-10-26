import asyncio
import resource
import tempfile
import unittest
from pathlib import Path

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
