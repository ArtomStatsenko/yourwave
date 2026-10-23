"""Liquidsoap child process: start/stop, telnet-style commands over its Unix socket, track events from stdout."""
import asyncio
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from . import config, ytmusic


class Liquidsoap:
    def __init__(self, m3u: Path, crossfade_sec: float, output: str = "device", record: str = "",
                 sink: dict | None = None, fallback_m3u: Path | None = None):
        self.m3u, self.xfade, self.output = m3u, crossfade_sec, output
        self.record = record
        self.fallback_m3u = fallback_m3u
        self.sink = sink or {"mode": "speakers"}  # cfg["output"]: where the device sink sends the sound
        self.sock = config.RUN_DIR / "liq.sock"
        self.proc: asyncio.subprocess.Process | None = None
        self.stopped = False
        self.pidfile = config.RUN_DIR / "liquidsoap.pid"

    async def start(self, on_event, first: list[str] | tuple = ()) -> None:
        """first: request lines to air before the shuffle (a host opener, a resumed song with liq_cue_in): the
        output starts paused until the first one is loaded, so the shuffle cannot take the first slot; the
        others load meanwhile. The first not loaded in 30 s: they are dropped and the shuffle starts."""
        await self._start(on_event, bool(first))
        if not first:
            return
        try:
            rids = [(await self.command(f"news.push {line}")).strip() for line in first]
            for _ in range(300):
                if await self.command("news.ready") == "true":
                    break
                await asyncio.sleep(.1)
            else:
                for rid in rids:
                    await self.remove(rid)
            await self.command("resume")
        except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            if not self.stopped:
                raise  # else: stopped while starting

    async def _start(self, on_event, paused: str) -> None:
        self.sock.unlink(missing_ok=True)
        events = config.RUN_DIR / "events.jsonl"
        events.write_text("")
        env = dict(os.environ, RADIO_EVENTS=str(events), RADIO_M3U=str(self.m3u), RADIO_LIQ_SOCK=str(self.sock), RADIO_XFADE=str(self.xfade),
                   RADIO_LIQ_LOG=str(config.DATA_DIR / "logs/liquidsoap.log"), RADIO_OUTPUT=self.output,
                   RADIO_SINK_CMD=self.sink_command(), RADIO_DATA_DIR=str(config.DATA_DIR),
                   RADIO_FALLBACK_M3U=str(self.fallback_m3u or ""), RADIO_START_PAUSED="1" if paused else "",
                   RADIO_YTMUSIC_COUNT=str(len(ytmusic.library()["tracks"])) if self.m3u.name == "ytmusic.m3u" else "0",
                   RADIO_YTMUSIC_CMD=shlex.join([sys.executable, "-m", "radio.ytmusic"]))
        self._end_orphan()
        self.proc = await asyncio.create_subprocess_exec(
            "liquidsoap", str(config.PKG_DIR / "radio.liq"), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        self.pidfile.write_text(str(self.proc.pid))
        asyncio.create_task(self._tail_events(self.proc, events, on_event))
        for _ in range(100):  # socket appears once the script is compiled and running
            if self.sock.exists():
                return
            if self.proc.returncode is not None:
                break
            await asyncio.sleep(0.1)
        raise RuntimeError("liquidsoap did not start, see logs/liquidsoap.log")

    def sink_command(self) -> str:
        """ffmpeg reading Liquidsoap's raw PCM: true-peak-safe limiter (-1.5 dB) -> speakers and/or the browser
        stream (+ recording). The stream is mp3 over local UDP to the UI server, which fans it out to its pages:
        UDP never blocks or fails the sink when no page is listening."""
        lim = "alimiter=limit=0.84:attack=5:release=50:level=false"
        mode = self.sink["mode"]
        outs = []
        if mode in ("speakers", "both"):
            outs.append(["-f", "audiotoolbox", "-"])
        if mode in ("browser", "both"):
            outs.append(["-c:a", "libmp3lame", "-b:a", self.sink["stream_bitrate"], "-f", "mp3",
                         f"udp://127.0.0.1:{self.sink['stream_port']}?pkt_size=1316"])
        if self.record:
            outs.append(["-c:a", "pcm_s16le", "-y", self.record])
        if not outs:
            raise ValueError(f"output.mode must be speakers, browser or both, not {mode!r}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "f32le", "-ar", "44100", "-ac", "2", "-i", "pipe:0"]
        labels = "".join(f"[o{i}]" for i in range(len(outs)))
        cmd += ["-filter_complex", f"[0:a]{lim},asplit={len(outs)}{labels}" if len(outs) > 1 else f"[0:a]{lim}[o0]"]
        for i, o in enumerate(outs):
            cmd += ["-map", f"[o{i}]", *o]
        return shlex.join(cmd)

    async def _tail_events(self, proc, path: Path, on_event):
        with open(path, encoding="utf-8") as f:
            buf = ""
            while proc.returncode is None:
                chunk = f.read()
                if not chunk:
                    await asyncio.sleep(0.1)
                    continue
                buf += chunk
                *lines, buf = buf.split("\n")
                for line in lines:
                    try:
                        on_event(json.loads(line))
                    except Exception as e:  # never let a bad event kill the reader
                        print("event error:", e, line, flush=True)

    async def command(self, cmd: str, timeout: float = 5) -> str:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.sock)), 3)
        try:
            writer.write((cmd + "\n").encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.readuntil(b"END\r\n"), timeout)
            writer.write(b"quit\n")
            return data.decode().removesuffix("END\r\n").strip()
        finally:
            writer.close()

    async def wait_music(self):
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            if await self.command('fresh.ready') == 'true' or await self.next_music():
                return
            await asyncio.sleep(.1)
        raise TimeoutError('Next track is still loading; current playback continues')

    async def wait_insert(self, rid=None):
        for _ in range(60):
            if (not rid or rid in (await self.command('news.pending')).split()) and await self.command('news.ready') == 'true':
                return
            await asyncio.sleep(.05)
        raise TimeoutError('The next section is not loaded yet; try again shortly')

    async def push_news(self, wav: Path, package_id: str, talkover_sec: float = 0.0) -> str:
        safe_id = package_id.replace('"', "")
        # Short fades around the package so the stinger is heard; songs keep the full crossfade.
        gain = 0.0  # packages are mastered to -16 LUFS like the music
        # Talkover: the next song starts talkover_sec before the end, under the last phrase. The voice is never
        # faded (a host just closes the mic): the crossfade fades the outgoing part from its very start, so the fade
        # is delayed into the last 0.1 s, which is the silence after the last word (audio.TALKOVER_END_SEC).
        ending = (f'liq_cross_end_duration="{talkover_sec:.2f}",liq_fade_out="0.1",'
                  f'liq_fade_out_delay="{max(0.0, talkover_sec - 0.1):.2f}"' if talkover_sec
                  else 'liq_cross_end_duration="1.5"')
        # Start window longer than any song's end window: radio.liq then places the break by liq_talk_up.
        ending += ',liq_cross_start_duration="12"'
        uri = f'annotate:kind="news",ref="{safe_id}",liq_amplify="{gain:+.1f} dB",{ending},liq_fade_in="0.1":{wav}'
        return (await self.command(f"news.push {uri}")).strip()

    async def push_station_id(self, wav: Path, gain_db: float, tail_sec: float = 0.0) -> None:
        # Goes through the same queue as news, so it too waits for the song boundary. The next song starts
        # over the call sign's decaying tail instead of after it.
        uri = (f'annotate:kind="id",ref="{wav.name}",liq_amplify="{gain_db:+.2f} dB",'
               f'liq_cross_duration="{max(1.0, tail_sec + 0.3):.2f}",liq_fade_in="0.1":{wav}')
        await self.command(f"news.push {uri}")

    async def push_replay(self, wav: Path, kind: str, ref: str, gain_db: float, replay_id: str, tags: str = "",
                          queue: str = "news") -> str:
        """A song into a request queue (news: next song boundary, before everything; fresh: after the news
        queue). tags: more annotations starting with a comma. Returns the request id, for remove()."""
        safe_ref = ref.replace('"', "")
        uri = (f'annotate:kind="{kind}",ref="{safe_ref}",replay_id="{replay_id}",liq_amplify="{gain_db:+.2f} dB",'
               f'liq_fade_in="0.1"{tags}:{wav}')
        return (await self.command(f"{queue}.push {uri}")).strip()

    async def next_music(self) -> str | None:
        """The playlist's next song (the one after the current song and anything queued), for the set opener."""
        return (await self.command('music.peek')).strip() or None

    async def upcoming_music(self) -> list[str]:
        """The next songs in air order (queued, then the rest of the shuffle round); only the first is certain."""
        return [r for r in (await self.command('music.upcoming')).splitlines() if r.strip()]

    def music_choices(self, expected: str) -> dict[str, str]:
        paths = [self.m3u, self.fallback_m3u]
        for path in paths:
            if path is None:
                continue
            choices = {}
            for line in path.read_text().splitlines():
                match = re.search(r'ref="([^"\r\n]*)"', line)
                if match:
                    choices[match.group(1)] = line
            if expected in choices:
                return choices
        return {}

    async def replace_music(self, expected: str, uri: str, queue: str = 'music', rid: str = '') -> str:
        if queue not in ('music', 'local_music', 'news', 'fresh'):
            raise ValueError('Invalid music queue')
        result = await self.command(queue + '.replace ' + json.dumps({
            'expected': expected, 'uri': uri, 'rid': int(rid) if rid else -1, 'head': not rid}), timeout=35)
        if not result.isdigit():
            raise ValueError('The next track changed. Try again.' if result == 'STALE' else 'Could not load another track. Try again.')
        return result

    async def remove(self, rid: str, queue: str = "news") -> None:
        # Also works on a request already prefetched for the next break, but misses one the queue is resolving at
        # that moment (right after a transition: off the queue, not yet prefetched, invisible to news.pending).
        # Local files resolve in milliseconds, so it is gone once two checks 50 ms apart do not see it.
        gone = 0
        for _ in range(40):
            await self.command(f"{queue}.remove {rid}")
            if queue != "news":
                return
            gone = gone + 1 if rid not in (await self.command('news.pending')).split() else 0
            if gone == 2:
                return
            await asyncio.sleep(.05)

    def _end_orphan(self) -> None:
        """A Liquidsoap left by a daemon that died without stopping it still plays into the same output and the
        same stream: two engines interleave as stutter. End it before starting this one."""
        try:
            pid = int(self.pidfile.read_text())
            command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
        except (OSError, ValueError):
            return
        if "liquidsoap" in command and str(config.PKG_DIR / "radio.liq") in command:
            os.kill(pid, signal.SIGTERM)
            print("ended a Liquidsoap left by a previous daemon:", pid, flush=True)

    async def stop(self) -> None:
        self.stopped = True
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        self.sock.unlink(missing_ok=True)
