"""One process per session: owns Liquidsoap, prepares news in the background, serves the CLI socket."""
import asyncio
import fcntl
import hashlib
import os
import json
import re
import random
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from . import audio, config, content, direction, github_new, github_trending, horizon, host, music, music_facts, plugins, reminders, settings, store, timeline, weather, ytmusic
from .editor import Editor
from .engine import Liquidsoap
from .store import iso, now
from . import tts as tts_mod
from .tts import VoiceStudio, cached_synthesize

READY_AHEAD = 2
# shortcut: fixed lead times; use measured runtimes if preparation regularly exceeds this reserve.
TEXT_LEAD_SEC = 15 * 60
VOICE_LEAD_SEC = 5 * 60
LATE_ASSEMBLY_SEC = 60  # a news break (time check, next song) is assembled when the song has this much left
REMIND_LATE_SEC = 40    # optional host inserts wait; timed reminders need enough time for synthesis
BREAK_SEP = "|"         # a break airs several packages as one request: ref = their ids joined
WEATHER_JOINS_SEC = 300  # a news section this soon after a weather slot airs in the same edition
GAP_LEAD_SEC = 12       # gap filler: the next song must be loaded this long before the current one ends
GAP_EVERY_SEC = 600     # gap filler: at most one line this often
GAP_LINES = ("YouTube Music не успел загрузить следующую песню, так что пока — из моей фонотеки.",
             "Связь с YouTube Music сегодня неторопливая: пока она догружает трек, ставлю песню из своей коллекции.",
             "Следующий трек из YouTube Music ещё в пути — а пока послушаем кое-что из местной фонотеки.")
PLAN_HOURS = 12         # how far ahead the plan shows (and lets you edit) sections, reminders and weather
RESUME_WITHIN_SEC = 300  # a restart this soon continues the session: its id, the music set and the song
RESUME_TAIL_SEC = 15     # a song this close to its end is not resumed: the next one starts instead


NUMBER_WORD = re.compile(r"(ноль|один|одна|одно|одну|одного|двух|трёх|трех|четыре|пять|шесть|семь|восемь|девять|десять|\w+надцать|"
                         r"\w+дцать|сорок|\w+десят|девяносто|двести|триста|\w+сот|тысяч\w*|миллион\w*)$")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Service:
    def __init__(self, cfg: dict, tts=None, output: str = "device"):
        self.cfg, self.output = cfg, output
        self.defaults = None  # the config.toml values, taken at the first decision; settings.json (the UI) overrides them
        v = cfg["voice"]
        self.tts = tts or VoiceStudio(v["base_url"], v["voice_id"], v["language"], v["speed"], v["instruct"], v["seed"])
        # plugin "Two hosts": the same server, a second designed voice (a mock in tests: the same mock)
        self.tts2 = self.tts if tts else VoiceStudio(v["base_url"], v["voice_id"], v["language"], v["speed"],
                                                     v["cohost_instruct"], v["cohost_seed"])
        self.db = store.connect()
        self.session = uuid.uuid4().hex[:12]
        self.state = "STARTING"
        self.auto_paused, self.paused_at, self.heard_at = False, 0.0, time.time()
        self.replays = {}  # replay_id -> {kind, ref, rid}: replays waiting in Liquidsoap's queue
        self.current: dict | None = None
        self.skip_flag = False
        self.next_transition_sec = None
        self.last_error = ""
        self.stopping = False
        self.done = asyncio.Event()
        self.wake = asyncio.Event()
        self.rendering = False
        self.assembling = self.rerolling = False
        self.schedule_lock = asyncio.Lock()
        self.schedule_generation = 0
        self.tts_retry_at, self.tts_backoff = 0.0, 60.0
        self.stop_measure = threading.Event()
        self.sources: dict[str, str] = {}
        self.editor = Editor(cfg)
        self.recent_jingles: dict = {}  # per role, for no-repeat rotation within the session
        self.songs_since_break = 0      # songs since the last news break or station ID
        self.set_started: float | None = None  # music set: started with the first song after a news break
        self.set_songs: list[tuple[str, str]] = []  # (artist, title) of the set so far, for the back-sell
        self.intro_rooms: dict = {}  # path -> seconds of intro the host may talk over (from the playlist)
        self.files: list[Path] = []
        self.ytmusic_active = False
        self.voice_proc = None         # the VoiceStudio server this daemon started (and so stops)
        self.voice_started = 0.0
        self.cal_events: list = []                 # calendar events (read every poll_minutes)
        self.todoist_tasks: list = []
        self.next_read = {"calendar": 0.0, "todoist": 0.0}
        self.next_weather_read = 0.0
        self.reminders_due: dict[str, dict] = {}   # key -> reminder waiting for a song boundary
        self.reminder_text: dict[str, str] = {}    # key -> what the host says, for the timeline
        self.queued_reminders: dict[str, tuple] = {}  # timed reminders can expire before the song ends
        self.sets_since_fresh = 0     # news breaks since the last "new tracks" set
        self.fresh_queued: dict[str, str] = {}  # path -> request id of new songs waiting in Liquidsoap's fresh queue
        self.pkg_jingles: dict = {}     # package id -> jingles chosen at render, reused at final assembly
        self.id_pending = False
        self.queued_break = None
        self.all_sources_checked = False  # set only after a *successful* check of every enabled source
        self.resume_offset: dict[str, float] = {}
        self.weather_hold: dict[str, float] = {}
        self.gap_voice: list[tuple[str, Path]] = []  # gap filler lines, rendered ahead: a gap leaves seconds
        self.gap_song, self.gap_said = 0.0, 0.0      # the song last checked, when the host last covered a gap
        self.opener_retry = self.gap_retry = 0.0     # next try to pre-render the startup opener, the gap lines  # weather slot -> start of the song its joining break first waited at  # ref -> seconds already played, for the song resumed at start

    # ---- lifecycle ----
    async def run(self) -> None:
        self._cleanup_cache()
        self._reset_inflight()
        self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE status IN ('QUEUED','PREPARING')")
        self._refresh_audio_cache()
        # Unplayed remarks about a previous session's music are stale.
        self.db.execute("UPDATE packages SET status='SKIPPED' WHERE (item_id LIKE 'system:no-news:%' OR item_id LIKE 'system:music-link:%') "
                        "AND status IN ('PENDING','READY')")
        self.db.execute("UPDATE items SET state='SKIPPED' WHERE (id LIKE 'system:no-news:%' OR id LIKE 'system:music-link:%') AND state='NEW'")
        files = self.files = music.scan(self.cfg["music"]["path"])
        self.library = {str(f) for f in files}  # the only music paths a replay request may name
        m3u = config.RUN_DIR / "music.m3u"
        m = self.cfg["music"]
        self.intro_rooms = music.write_m3u(self.db, files, m3u, m["target_lufs"], m["max_boost_db"], m["crossfade_sec"],
                                                  True) if files else {}
        fallback_m3u = None
        self.ytmusic_active = plugins.on(self.cfg, "ytmusic")
        if self.ytmusic_active:
            for stale in (ytmusic.directory() / "audio").glob("*.webm"):
                stale.unlink(missing_ok=True)  # files left by an interrupted previous session
            remote_m3u = config.RUN_DIR / "ytmusic.m3u"
            try:
                remote = ytmusic.write_m3u(self.db, remote_m3u)
            except ValueError:
                self.ytmusic_active = False
                log("YouTube Music library unavailable; using local music")
            else:
                fallback_m3u = m3u if self.intro_rooms else None
                m3u = remote_m3u
                self.library.update(remote)
                self.intro_rooms.update(remote)
        if not self.intro_rooms:
            raise SystemExit(f"NO_MUSIC: no playable files in {m['path']}")
        self.engine = Liquidsoap(m3u, m["crossfade_sec"], self.output, os.environ.get("RADIO_RECORD", ""),
                                 self.cfg["output"], fallback_m3u=fallback_m3u)
        restored = self._restore_session()
        resumed = self._resume_line()
        # Answer on the socket while the engine starts: the page then sees a radio starting, not a stopped one it
        # would start a second time.
        config.SOCK.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle_client, str(config.SOCK))
        config.SOCK.chmod(0o600)
        # YouTube Music needs seconds for its first song: an opener goes first instead of silence or a local song.
        opens = self.ytmusic_active and (not resumed or "ytmusic:" in resumed)
        await self.engine.start(self.on_event, [line for line in (self._opener_line() if opens else "", resumed) if line])
        if self.stopping:  # stopped while starting
            await self.done.wait()
            server.close()
            return
        self.state = "PLAYING"
        store.log_event(self.db, self.session, "SESSION_STARTED")
        log("session", self.session, "resumed," if restored else "started,", len(files), "tracks")
        threading.Thread(target=music.measure_missing, args=(store.connect(), files, self.stop_measure),
                         daemon=True).start()
        asyncio.create_task(self.content_loop())
        asyncio.create_task(self.watch_engine())
        asyncio.create_task(self.trending_loop())
        asyncio.create_task(self.horizon_loop())
        asyncio.create_task(self.reminders_loop())
        asyncio.create_task(self.gap_loop())
        if isinstance(self.tts, VoiceStudio):
            asyncio.create_task(self.voice_loop())
        if self.output == "device" and self.cfg["output"]["mode"] == "browser":
            asyncio.create_task(self.listeners_loop())
        await self.done.wait()
        server.close()
        config.SOCK.unlink(missing_ok=True)

    async def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        self._save_session()
        horizon.stop()
        await self._stop_voice()
        self.stop_measure.set()
        await self.engine.stop()
        self._reset_inflight()  # a package cut off by stop is not PLAYED; it may air next session if still fresh
        self.state = "STOPPED"
        store.log_event(self.db, self.session, "SESSION_STOPPED")
        log("session stopped")
        self.done.set()

    def _save_session(self) -> None:
        at = self.paused_at if self.state == "PAUSED" else time.time()
        cur, songs, count = self.current, self.set_songs, self.songs_since_break
        song = None
        if cur and cur["kind"] == "music" and cur.get("duration") and at - cur["started"] < cur["duration"] - RESUME_TAIL_SEC:
            song = {"ref": cur["ref"], "position": at - cur["started"]}
            count = max(0, count - 1)  # the resumed song counts again when it airs
            if songs and songs[-1] == music.song(self.db, cur["ref"]):
                songs = songs[:-1]
        (config.RUN_DIR / "session.json").write_text(json.dumps({
            "session": self.session, "stopped_at": time.time(), "song": song, "songs_since_break": count,
            "set_songs": songs, "set_elapsed": at - self.set_started if self.set_started else None}))

    def _restore_session(self) -> bool:
        path = config.RUN_DIR / "session.json"
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            return False
        finally:
            path.unlink(missing_ok=True)
        if time.time() - saved["stopped_at"] > RESUME_WITHIN_SEC:
            return False
        self.session, self.songs_since_break = saved["session"], saved["songs_since_break"]
        self.set_songs = [tuple(s) for s in saved["set_songs"]]
        if saved["set_elapsed"] is not None:
            self.set_started = time.time() - saved["set_elapsed"]
        if saved["song"]:
            self.resume_offset = {saved["song"]["ref"]: saved["song"]["position"]}
        return True

    def _resume_line(self) -> str:
        """The resumed song's playlist line (its gain and windows), starting at its position."""
        for ref, position in self.resume_offset.items():
            line = self.engine.music_choices(ref).get(ref, "")
            return line.replace("annotate:", f'annotate:liq_cue_in="{position:.2f}",', 1) if line else ""
        return ""

    async def _pause(self) -> None:
        await self.engine.command("pause")
        self.state, self.paused_at = "PAUSED", time.time()
        await self._expire_reminders()

    async def _resume(self) -> None:
        await self._expire_reminders()
        await self.engine.command("resume")
        if self.current:  # the paused song stays in place: its elapsed time must not count the pause
            self.current["started"] += time.time() - self.paused_at
        if self.set_started:
            self.set_started += time.time() - self.paused_at
        self.state, self.auto_paused, self.heard_at = "PLAYING", False, time.time()
        self.wake.set()

    async def listeners_loop(self) -> None:
        """Browser output: nobody listening for idle_pause_sec -> pause (no news airs, no speech budget is spent);
        a page starts listening -> resume. A pause by command is never undone here."""
        while not self.stopping:
            await asyncio.sleep(1)
            if listeners():
                self.heard_at = time.time()
                if self.auto_paused and self.state == "PAUSED":
                    await self._resume()
                    log("listener back: resumed")
            elif self.state == "PLAYING" and time.time() - self.heard_at >= self.cfg["output"]["idle_pause_sec"]:
                await self._pause()
                self.auto_paused = True
                log("no listeners: paused")

    async def gap_loop(self) -> None:
        while not self.stopping:
            try:
                await self._gap_tick()
            except Exception as e:
                log("gap filler:", type(e).__name__)
            await asyncio.sleep(1)

    def _opener_text(self) -> str:
        return host.startup(self.cfg["broadcast"]["station_name"])

    def _opener_file(self) -> Path:
        name = hashlib.sha256((self._opener_text() + str(self.cfg["jingles"]["stinger_in"])).encode()).hexdigest()[:16]
        return config.DATA_DIR / "cache/packages" / f"opener-{name}.wav"

    def _opener_line(self) -> str:
        """The rendered opener (jingle and voice), or the intro jingle alone until one is rendered."""
        path = self._opener_file()
        if path.exists():
            return f'annotate:kind="gap",ref="{self._opener_text().replace(chr(34), "")}":{path}'
        return f'annotate:kind="id",ref="startup":{self.cfg["jingles"]["stinger_in"][0]}'

    def _prepare_opener(self) -> None:
        voice = self._say(self._opener_text())
        audio.render_notice(voice, Path(self.cfg["jingles"]["stinger_in"][0]), self._opener_file())

    async def _gap_tick(self) -> None:
        if self.ytmusic_active and time.time() >= self.opener_retry and not self._opener_file().exists():
            self.opener_retry = time.time() + 60  # the voice server may still be starting: try again later
            try:
                await asyncio.to_thread(self._prepare_opener)
            except Exception as e:
                log("startup opener:", type(e).__name__)
        await self._gap_line()

    async def _gap_line(self) -> None:
        """Experiment: YouTube Music has not loaded the next song by the end of this one -> a ready edition (news,
        weather, a music story) airs now; with nothing ready, one line about it before the local song the engine
        plays instead (radio.liq: local songs cover a song not loaded). Never silence."""
        if not plugins.on(self.cfg, "gap_filler") or not self.ytmusic_active or self.state != "PLAYING":
            return
        if not self.gap_voice:
            if time.time() >= self.gap_retry:
                self.gap_retry = time.time() + 60  # the voice server may still be starting: not every second
                self.gap_voice = [(text, await asyncio.to_thread(self._say, text))
                                  for text in GAP_LINES]
            return
        cur = self.current
        if not cur or cur["kind"] != "music" or not cur.get("duration") or cur["started"] == self.gap_song:
            return
        if cur["duration"] - (time.time() - cur["started"]) > GAP_LEAD_SEC:
            return
        self.gap_song = cur["started"]  # one look per song
        if self.queued_break or self.replays or self.fresh_queued or self.queued_reminders or self.id_pending:
            return  # something airs at this boundary anyway
        if await self.engine.next_music():
            return
        if self._pick_break():
            await self.schedule(immediate=True, gap=True)
            if self.queued_break:
                log("gap filler: a ready edition airs while YouTube Music loads")
                return
        if time.time() - self.gap_said < GAP_EVERY_SEC:
            return
        text, wav = self.gap_voice[int(time.time() // GAP_EVERY_SEC) % len(self.gap_voice)]
        b = self.cfg["broadcast"]
        speech = await asyncio.to_thread(audio.duration, wav)
        if store.spoken_last_hour(self.db) + speech > b["max_spoken_seconds_per_rolling_hour"]:
            return
        gain = -16 - (await asyncio.to_thread(audio.lufs, wav) or -16)
        await self.engine.push_replay(wav, "gap", text, gain, "", ',liq_cross_end_duration="1.5"')
        self.gap_said, self.id_pending = time.time(), True
        log("gap filler queued: the next YouTube Music song is not loaded, a local one follows")

    async def watch_engine(self) -> None:
        restarts = 0
        while not self.stopping:
            await self.engine.proc.wait()
            if self.stopping:
                return
            self.last_error = f"liquidsoap exited with code {self.engine.proc.returncode}"
            log(self.last_error)
            if restarts >= 1:
                log("liquidsoap failed twice, stopping")
                await self.stop()
                return
            restarts += 1
            self._reset_inflight()
            self.current, self.id_pending = None, False
            await self.engine.start(self.on_event)
            self.state = "PLAYING"

    def _reset_inflight(self) -> None:
        for key, (_, r) in self.queued_reminders.items():
            self.reminders_due[key] = r
            self.reminder_text.pop(key, None)
        self.queued_reminders.clear()
        self.db.execute("UPDATE packages SET status='READY' WHERE status IN ('QUEUED','PLAYING')")
        for p in self.db.execute("SELECT id, audio_path FROM packages WHERE status='READY'").fetchall():
            if not p["audio_path"] or not Path(p["audio_path"]).exists():
                self.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (p["id"],))

    def _refresh_audio_cache(self) -> None:
        signature = json.dumps(self.cfg["jingles"], default=str, sort_keys=True)
        path = config.DATA_DIR / "cache/audio-config.json"
        if not path.exists() or path.read_text() != signature:
            # Prepared audio contains the old jingles; keep its text and rebuild with the selected pack.
            self.db.execute("UPDATE packages SET status='PENDING' WHERE status='READY'")
            path.write_text(signature)

    def _cleanup_cache(self) -> None:
        cutoff = time.time() - self.cfg["broadcast"]["package_cache_ttl_hours"] * 3600
        protected = {str(Path(value)) for row in self.db.execute(
            "SELECT script_path,audio_path FROM packages WHERE status IN ('PENDING','READY','QUEUED','PLAYING')")
                     for value in row if value}
        for d in ("cache/tts", "cache/packages", "cache/scripts"):
            for f in (config.DATA_DIR / d).iterdir():
                if f.stat().st_mtime < cutoff and str(f) not in protected:
                    f.unlink(missing_ok=True)

    # ---- playback events from Liquidsoap ----
    def on_event(self, m: dict) -> None:
        prev = self.current
        if prev and prev["kind"] == "news":
            status = "SKIPPED" if self.skip_flag else "PLAYED"
            for pid in prev["ref"].split(BREAK_SEP):
                cur = self.db.execute("UPDATE packages SET status=?, played_at=? WHERE id=? AND status='PLAYING'",
                                      (status, iso(now()), pid))
                if cur.rowcount:
                    store.log_event(self.db, self.session,
                                    "PACKAGE_COMPLETED" if status == "PLAYED" else "PACKAGE_SKIPPED", pid)
                    item = self.db.execute("SELECT item_id FROM packages WHERE id=?", (pid,)).fetchone()
                    content.finish_item_if_done(self.db, item["item_id"])
        self.skip_flag = False
        self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (m.get("replay_id") or "",))
        self.replays.pop(m.get("replay_id") or "", None)  # on air now: no longer queued
        self.fresh_queued.pop(m.get("ref") or "", None)
        self.next_track = ""
        played = self.resume_offset.pop(m.get("ref") or "", 0.0)
        self.resume_offset.clear()  # only the first track can be the resumed song
        self.current = {"kind": m.get("kind") or "music", "ref": m.get("ref") or m.get("filename", ""),
                        "started": time.time() - played, "transition_sec": self.next_transition_sec
                        if self.next_transition_sec is not None else self.cfg['music']['crossfade_sec']}
        self.next_transition_sec = None
        if self.current["kind"] == "news":
            self.queued_break = None
            self.db.executemany("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE kind='news' AND ref=? AND status='QUEUED'", [(pid,) for pid in self.current['ref'].split(BREAK_SEP)])
            self.songs_since_break, self.set_started, self.set_songs = 0, None, []
            for pid in self.current["ref"].split(BREAK_SEP):
                self.db.execute("UPDATE packages SET status='PLAYING' WHERE id=?", (pid,))
                store.log_event(self.db, self.session, "PACKAGE_STARTED", pid)
                item = self.db.execute('SELECT item_id FROM packages WHERE id=?', (pid,)).fetchone()
                if item and self._host_segment({'item_id': item['item_id']}):
                    key = item['item_id'].removeprefix('system:')
                    self.reminders_due.pop(key, None)
                    self.db.execute('INSERT OR REPLACE INTO reminders(key,fired_at) VALUES(?,?)', (key, iso(now())))
                    self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (key,))
            log("news on air:", self.current["ref"])
        elif self.current["kind"] in ("reminder", "weather"):
            self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (self.current['ref'],))
            self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE kind='reminder' AND ref=? AND status='QUEUED'", (self.current['ref'],))
            self.id_pending = False
            if self.queued_reminders.pop(self.current["ref"], None) or self.current["kind"] == "weather":
                self.db.execute("INSERT OR REPLACE INTO reminders(key, fired_at) VALUES(?,?)",
                                (self.current["ref"], iso(now())))
            store.log_event(self.db, self.session, self.current["kind"].upper(), self.current["ref"],
                            self.reminder_text.pop(self.current["ref"], ""))
            log(self.current["kind"], "on air")
        elif self.current["kind"] == "gap":
            self.id_pending = False
            store.log_event(self.db, self.session, "GAP", "", self.current["ref"])
            log("gap filler on air")
        elif self.current["kind"] == "id":
            self.songs_since_break, self.id_pending = 0, False
            store.log_event(self.db, self.session, "STATION_ID", self.current["ref"])
            log("station id:", self.current["ref"])
        else:
            self.songs_since_break += 1
            self.set_started = self.set_started or self.current["started"]
            if s := music.song(self.db, self.current["ref"]):
                self.set_songs.append(s)
            self.current["duration"] = self._song_seconds(self.current["ref"])
            store.log_event(self.db, self.session, "MUSIC_STARTED", self.current["ref"])
            log("music:", Path(self.current["ref"]).name)
        self.wake.set()

    @staticmethod
    def _song_seconds(ref: str) -> float:
        return ytmusic.duration(ref) if ref.startswith("ytmusic:") else audio.duration(Path(ref))

    # ---- content pipeline ----
    async def content_loop(self) -> None:
        while not self.stopping:
            try:
                await self.content_tick()
            except Exception as e:
                self.last_error = f"content: {e}"
                log("content error:", e)
            # A package waiting for its song boundary needs a fine-grained tick for the late assembly.
            waiting = self.reminders_due or self.db.execute("SELECT 1 FROM packages WHERE status='READY'").fetchone()
            try:
                await asyncio.wait_for(self.wake.wait(), 5 if waiting else self.cfg["sources"]["inbox_poll_sec"])
            except asyncio.TimeoutError:
                pass
            self.wake.clear()

    async def trending_loop(self) -> None:
        """Check enabled sources near the next news section, respecting refresh and retry delays."""
        gt, gn = self.cfg["sources"]["github_trending"], self.cfg["sources"]["github_new"]
        next_fetch, was_on = 0.0, set()
        while not self.stopping:
            on = {n for n in ("github_trending", "github_new") if plugins.on(self.cfg, n)}
            for n in ("github_trending", "github_new"):
                if n not in on:
                    self.sources[n] = "off"
            if not on:
                self.all_sources_checked = True
            elif self._news_preparation_due(TEXT_LEAD_SEC) and (time.time() >= next_fetch or on - was_on):
                next_fetch = time.time() + await self._fetch_github("github_trending" in on, "github_new" in on)
            was_on = on
            try:
                await asyncio.wait_for(self.done.wait(), 60)
            except asyncio.TimeoutError:
                pass

    async def _fetch_github(self, trending_on: bool, new_on: bool) -> float:
        """One fetch of the enabled GitHub sources. Returns the delay until the next one."""
        gt, gn = self.cfg["sources"]["github_trending"], self.cfg["sources"]["github_new"]
        delay = gt["refresh_minutes"] * 60
        key = "github_trending" if trending_on else "github_new"
        try:
            main, langs = [], []
            if trending_on:
                main = github_trending.parse_list(await asyncio.to_thread(github_trending.fetch_html), 25)
                for lang in gt["indie_languages"]:  # extra pools for small projects; one failing page is skipped
                    try:
                        langs += github_trending.parse_list(
                            await asyncio.to_thread(github_trending.fetch_html, 20, lang), 25)
                    except Exception as e:
                        log(f"github_trending/{lang}: {e}")
            new, new_ok = [], True
            if new_on:
                try:
                    new = await asyncio.to_thread(github_new.fetch_new, gn["created_within_days"])
                    self.sources["github_new"] = f"ok {time.strftime('%H:%M')}"
                except Exception as e:
                    new_ok = False
                    self.sources["github_new"] = f"error: {e}"
            result = await asyncio.to_thread(self._take_github, main, langs, new)
            # "No news" may only be said after every enabled source answered.
            self.all_sources_checked = self.all_sources_checked or new_ok
            self.sources[key] = f"ok {time.strftime('%H:%M')}: {result}"
            store.log_event(self.db, self.session, "FETCH_SUCCEEDED", "github")
        except github_trending.RateLimited as e:
            delay = max(delay, e.retry_after)
            self.sources[key] = f"rate limited, next try in {delay / 60:.0f} min"
        except github_trending.ParseError as e:
            self.sources[key] = f"degraded (page markup changed?): {e}"
        except Exception as e:  # network down, 5xx, timeout: music goes on, one short retry
            delay = 300 if not self.sources.get(key, "").startswith("error") else delay
            self.sources[key] = f"error: {e}"
        if not self.sources[key].startswith("ok"):
            store.log_event(self.db, self.session, "FETCH_FAILED", "github", self.sources[key])
            log(f"{key}:", self.sources[key])
        self.wake.set()
        return delay

    async def horizon_loop(self) -> None:
        """Run Horizon near the next news section, respecting refresh and retry delays."""
        hz = self.cfg["sources"]["horizon"]
        next_run, was_on = 0.0, False
        while not self.stopping:
            on = plugins.on(self.cfg, "horizon")
            if not on:
                self.sources["horizon"] = "off"
            elif self._news_preparation_due(TEXT_LEAD_SEC) and (time.time() >= next_run or not was_on):
                self.sources["horizon"] = f"running since {time.strftime('%H:%M')}"
                try:
                    briefing = await asyncio.to_thread(horizon.run, self.cfg)
                    result = await asyncio.to_thread(self._take_horizon, horizon.parse(briefing.read_text(encoding="utf-8")))
                    self.sources["horizon"] = f"ok {time.strftime('%H:%M')}: {result}"
                    next_run = time.time() + hz["refresh_minutes"] * 60
                except Exception as e:
                    self.sources["horizon"] = f"error: {str(e)[:160]}"
                    log("horizon:", e)
                    next_run = time.time() + 600
                self.wake.set()
            was_on = on
            try:
                await asyncio.wait_for(self.done.wait(), 60)
            except asyncio.TimeoutError:
                pass

    def _take_horizon(self, items: list[dict]) -> str:
        """Best-ranked new items of a Horizon briefing, each its own bulletin, retold in Russian by the local model;
        a failed retelling passes its slot to the next item."""
        hz = self.cfg["sources"]["horizon"]
        added, tries = [], 0
        for it in items:
            if len(added) >= hz["picks"] or tries >= hz["picks"] + 3:
                break
            iid = horizon.item_id(it)
            if self.db.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone():
                continue
            tries += 1
            text = self.editor.retell_news(it["title"], it["summary"])
            if not text:
                continue
            origin = [{"repo": it["title"], "url": it["url"], "description": it["summary"], "score": it["score"]}]
            if it["discussion"]:
                origin.append({"repo": "обсуждение", "url": it["discussion"], "description": ""})
            content.add_item(self.db, self.cfg, {
                "id": iid, "topic": "Новости технологий", "source": "horizon", "url": it["url"],
                "speak_topic": not self.cfg["jingles"]["horizon_intro"],  # the section intro names it
                "published_at": now(), "body": text, "origin": origin}, kind="raw_post")
            store.log_event(self.db, self.session, "ITEM_COLLECTED", iid)
            added.append(it["title"][:50])
        if added:
            log("horizon:", "; ".join(added))
        return f"{len(added)} new" if added else "nothing new"

    def _take_github(self, main: list[dict], langs: list[dict], new: list[dict]) -> str:
        """Hourly picks, each its own short bulletin: top trend(s), indie finds from trending pages, new of the week.
        Dedup and selection happen before the LLM, so repeats cost nothing."""
        gt, gn = self.cfg["sources"]["github_trending"], self.cfg["sources"]["github_new"]
        everything = main + langs + new
        ids = list({github_trending.repo_id(t) for t in everything})
        seen = {r[0] for r in self.db.execute(
            f"SELECT id FROM items WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
        # Candidates per role in GitHub's order; a candidate whose retelling fails gives its slot to the next one.
        pool, pool_ids = [], set()
        for t in main + langs:  # overall page first, then languages in config order; one entry per repo
            if (rid := github_trending.repo_id(t)) not in pool_ids:
                pool.append(t)
                pool_ids.add(rid)
        blocked = tuple(self.cfg["sources"]["blocked_words"])
        roles = [("top", "github_trending", gt["top_picks"],
                  [t for t, _ in github_trending.select_mix(main, seen, len(main), 0, 0, blocked)]),
                 ("indie", "github_trending", gt["indie_picks"],
                  [t for t, _ in github_trending.select_mix(pool, seen, 0, len(pool), gt["indie_max_total_stars"],
                                                            blocked)]),
                 ("new", "github_new", gn["picks"],
                  [t for t in new if github_trending.repo_id(t) not in seen and github_trending.airable(t, blocked)])]
        added, taken = [], set(seen)
        for role, source, quota, candidates in roles:
            got = tries = 0
            for t in candidates:
                if got >= quota or tries >= quota + 3:
                    break
                if github_trending.repo_id(t) in taken:
                    continue
                tries += 1
                # Not stored on failure: the next hourly fetch may try it again while it is still listed.
                # The README opening gives the retelling something to say beyond the one-line description.
                readme = github_trending.fetch_readme(t["owner"], t["repo"])
                item = github_trending.to_item(t, self.editor.retell(t["repo"], t["description"], readme), role,
                                               gt["daily_min"], source)
                if item is None:
                    continue
                # Freshness = first time we saw it in today's ranking or this week's new list, not repo.created_at.
                item["published_at"] = now()
                item["speak_topic"] = not self.cfg["jingles"]["github_intro"]  # the section intro names it
                content.add_item(self.db, self.cfg, item, kind="raw_post")
                store.log_event(self.db, self.session, "ITEM_COLLECTED", item["id"])
                taken.add(item["id"])
                added.append(f"{role} {t['owner']}/{t['repo']}")
                got += 1
        if added:
            log("github:", ", ".join(added))
        return ", ".join(added) + " new" if added else "nothing new"

    async def _maybe_music_link(self) -> None:
        self._live_settings()
        if (not self.all_sources_checked or not plugins.on(self.cfg, "music_facts") or self.state != "PLAYING"
                or not self.current or self.current["kind"] != "music" or not self._break_due(0)):
            return
        pending = ("SELECT i.source FROM packages p JOIN items i ON i.id=p.item_id "
                   "WHERE p.status IN ('PENDING','READY','QUEUED','PLAYING') AND i.state='NEW'")
        off = self._sources_off()
        if any(r[0] not in off for r in self.db.execute(pending)):
            return
        started = self.set_started
        song = self.set_songs[-1] if self.set_songs else music.song(self.db, self.current["ref"])
        facts = await asyncio.to_thread(music_facts.fetch, song[0], host._title(song[1])) if song else None
        if (self.set_started != started or self.state != "PLAYING" or not plugins.on(self.cfg, "music_facts")
                or any(r[0] not in self._sources_off() for r in self.db.execute(pending))):
            return
        seed = f"system:music-link:{self.session}:{uuid.uuid4().hex[:8]}"
        self.sources["music_facts"] = "verified facts" if facts else "music only: no verified facts"
        content.add_item(self.db, self.cfg, {
            "id": seed, "topic": "Музыкальная пауза", "speak_topic": False, "source": "music_facts",
            "url": " ".join(facts["urls"]) if facts else "", "published_at": now(),
            "body": host.music_link(song, facts["text"] if facts else "", self.recent_jingles, seed)}, kind="announcement")
        log("music link prepared")

    async def content_tick(self) -> None:
        for item_id in content.scan_inbox(self.db, self.cfg):
            store.log_event(self.db, self.session, "ITEM_COLLECTED", item_id)
            log("new digest:", item_id)
        content.expire_stale(self.db, self.cfg)
        await self._maybe_music_link()
        await self.schedule()
        if self.rendering or time.time() < self.tts_retry_at:
            return
        changes = direction.edits(self.db) if plugins.on(self.cfg, 'air_direction') else {}
        deadline = now() + timedelta(seconds=VOICE_LEAD_SEC)
        news_due = self._news_preparation_due(VOICE_LEAD_SEC)
        off = self._sources_off()
        candidates = [r for r in direction.news(self.db) if r['source'] not in off
                      and direction.allowed(r, changes, deadline)
                      and (news_due or self._host_segment(r) and r['item_id'].removeprefix('system:') in self.reminders_due)]
        ready = sum(r['status'] == 'READY' for r in candidates)
        pending = [r for r in candidates if r['status'] == 'PENDING']
        p = next((r for r in pending if self._host_segment(r) or r['item_id'].startswith('system:direction:')), None)
        if p is None and ready < READY_AHEAD:
            p = next(iter(pending), None)
        if p:
            self.rendering = True
            asyncio.create_task(self._render_task(p))

    def _news_preparation_due(self, lead: float) -> bool:
        if self.state != 'PLAYING':
            return False
        deadline = now() + timedelta(seconds=lead)
        # Use the editor's real section forecast so moving or skipping a section also moves preparation.
        return any((r['kind'] == 'news' or r.get('edition_news_ids')) and not r['skipped'] and r['status'] not in ('QUEUED', 'PREPARING')
                   and content.parse_time(r['at']) <= deadline for r in self._broadcast_plan()['rows'])

    def _live_settings(self) -> None:
        if self.defaults is None:
            self.defaults = settings.base(self.cfg)
        settings.apply(self.cfg, self.defaults)

    def _waiting_break(self, remaining):
        pkgs = self._pick_break()
        due = self._break_due(remaining) or any(self._host_segment(p) == 'weather' for p in pkgs)
        if not due:
            changes = direction.edits(self.db) if plugins.on(self.cfg, 'air_direction') else {}
            if not any(changes.get(p['id'], {}).get('not_before') and changes[p['id']]['not_before'] <= iso(now()) for p in pkgs):
                return []
        if pkgs and not any(self._host_segment(p) == 'weather' for p in pkgs) and self._weather_pending(remaining):
            return []
        return pkgs

    def _weather_pending(self, remaining) -> bool:
        """The break at the end of this song joins a weather slot (as the plan shows it) whose segment is not
        ready: it waits for it one song, the one it first waited at, then airs without it."""
        if not self._weather_enabled() or not self.current:
            return False
        air = now() + timedelta(seconds=max(0.0, remaining))
        changes = direction.edits(self.db) if plugins.on(self.cfg, 'air_direction') else {}
        for r in direction.weather_rows(self.db, self.cfg, now(), changes):
            slot = content.parse_time(r['not_before'])
            if changes.get(r['id'], {}).get('skipped') or not slot <= air < slot + timedelta(seconds=WEATHER_JOINS_SEC):
                continue
            self.weather_hold = {r['id']: self.weather_hold.get(r['id'], self.current['started'])}
            return self.weather_hold[r['id']] == self.current['started']
        return False

    async def schedule(self, immediate=False, gap=False) -> None:
        """immediate: queue what is ready now, joined as rendered. gap: also when no break is due yet."""
        if immediate:
            self.schedule_generation += 1
            await self._schedule(True, gap)
        else:
            async with self.schedule_lock:
                await self._schedule()

    async def _schedule(self, immediate=False, gap=False) -> None:
        generation = self.schedule_generation
        self._live_settings()
        if self.current is None:
            return  # Play starts with music: nothing is queued before the first song is on air
        if plugins.on(self.cfg, 'air_direction') and self.db.execute("SELECT 1 FROM air_plan WHERE status='QUEUED'").fetchone():
            return
        if self.state != "PLAYING" or self.id_pending or self.rerolling or self.db.execute(
                "SELECT 1 FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone():
            return  # one insert (package or station ID) in the engine at a time
        remaining = 10.0
        if self.current["kind"] == "music" and self.current.get("duration"):
            remaining = self.current["duration"] - (time.time() - self.current["started"])
        if not immediate and plugins.on(self.cfg, "air_direction") and await self._direction_tick():
            return
        if not immediate and self.reminders_due and await self._remind(remaining):
            return
        pkgs = self._pick_break() if gap else self._waiting_break(remaining)
        current = self.current
        if immediate:
            remaining = 0.0
        if pkgs:
            new = []
            air = now() + timedelta(seconds=max(0.0, remaining))
            links = plugins.on(self.cfg, "host_links") and (not pkgs[0]["id"].startswith("system:") or self._host_segment(pkgs[0]))
            wav, talkover = Path(pkgs[0]["audio_path"]), pkgs[0]["talkover_sec"] or 0
            if immediate:
                wav = await asyncio.to_thread(audio.join_packages, [Path(p['audio_path']) for p in pkgs],
                                              config.DATA_DIR / 'cache/packages/immediate.wav')
                talkover = pkgs[-1]['talkover_sec'] or 0
            elif links or len(pkgs) > 1:
                if remaining > LATE_ASSEMBLY_SEC:
                    return  # assembled later, so the time check and the next song are right when it airs
                await self._clear_fresh()  # songs of the last new-tracks set that did not get to air
                self.sets_since_fresh += 1
                new = await asyncio.to_thread(self._pick_new) if links and self._new_set_due() else []
                nxt = str(new[0]) if new else (await self.engine.next_music() or "") if links else ""
                first = music.song(self.db, nxt)
                # shortcut: a replay pushed after this point would play first and its intro is not planned for;
                # with replays already waiting the next song is not known, so no intro talk then.
                room = self.intro_rooms.get(nxt) if not self.replays else None
                air = now() + timedelta(seconds=max(0.0, remaining))
                self.assembling = True
                try:
                    done = await asyncio.to_thread(self._assemble_break, [dict(p) for p in pkgs], air, links, first, room,
                                                   bool(new))
                finally:
                    self.assembling = False
                if done:
                    wav, talkover = done
                else:
                    pkgs, new = pkgs[:1], []  # fallback: the first bulletin, pre-rendered
            if generation != self.schedule_generation or immediate and self.current is not current:
                return
            if self._news_skipped(now() + timedelta(seconds=max(0, remaining))) and any(not p['id'].startswith('system:') for p in pkgs):
                return
            changes = direction.edits(self.db) if plugins.on(self.cfg, 'air_direction') else {}
            off = self._sources_off()
            for p in pkgs:
                source = self.db.execute('SELECT source FROM items WHERE id=?', (p['item_id'],)).fetchone()[0]
                key = p['item_id'].removeprefix('system:')
                notice = self.reminders_due.get(key)
                if source in off or not direction.allowed(p, changes, now()) or self._host_segment(p) and (
                        not notice or not direction.allowed({'id': key}, changes, now()) or
                        notice['kind'] == 'weather' and (not self._weather_enabled() or air >= notice['expires'])):
                    return
            ids = [p["id"] for p in pkgs]
            rid = await self.engine.push_news(wav, BREAK_SEP.join(ids), talkover)
            self.queued_break = (rid, ids)
            for p in pkgs:
                if self._host_segment(p):
                    self.db.execute("INSERT INTO air_plan(id,kind,ref,status,rid) VALUES(?,? ,?,'QUEUED',?) ON CONFLICT(id) DO UPDATE SET status='QUEUED',rid=excluded.rid,ref=excluded.ref",
                                    (p['item_id'].removeprefix('system:'), 'weather' if self._host_segment(p) == 'weather' else 'news', p['id'], rid))
            self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=? WHERE id LIKE 'section:news:%' AND skipped=0 AND status IN ('PENDING','ERROR') AND not_before<=?",
                            (rid, ids[0], iso(now() + timedelta(seconds=max(0, remaining)))))
            self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=? WHERE id LIKE 'direction:%' AND kind='news' AND skipped=0 AND status='PENDING' AND not_before<=?", (rid, ids[0], iso(now())))
            self.db.executemany("UPDATE packages SET status='QUEUED' WHERE id=?", [(i,) for i in ids])
            log("break queued for next song boundary:", ", ".join(ids))
            if new:
                await self._queue_fresh(new)
            return
        every = self.cfg["broadcast"]["station_id_every_songs"]  # 0 = no call signs
        every = every if plugins.on(self.cfg, "station_ids") else 0
        if every and self.current["kind"] == "music" and self.songs_since_break >= every:
            # shortcut: the ~2 s of speech in a call sign is not counted in the rolling-hour budget.
            f = audio.choose_jingles({"station_id": self.cfg["jingles"]["station_id"]}, f"id{time.time()}",
                                     self.recent_jingles)["station_id"]
            gain = -16 - (await asyncio.to_thread(audio.lufs, f) or -16)
            _, loud_end = await asyncio.to_thread(audio.active_span, f)
            await self.engine.push_station_id(f, gain, audio.duration(f) - loud_end)
            self.id_pending = True
            log("station id queued:", f.name)

    async def _render_task(self, p: dict) -> None:
        try:
            await asyncio.to_thread(self._render, p)
        except Exception as e:
            self.last_error = f"render {p['id']}: {e}"
            log(self.last_error)
        finally:
            self.rendering = False
            self.wake.set()

    def _render(self, p: dict) -> None:
        if not self.tts.health():
            self.tts_retry_at = time.time() + self.tts_backoff
            self.tts_backoff = min(self.tts_backoff * 2, 900)
            self.last_error = "VoiceStudio unavailable, will retry later"
            return
        self.tts_backoff = 60.0
        t0 = time.time()
        try:
            voices, sounds = [], []
            for segment, sound in self._body_segments(p, None):
                sounds.append(sound)
                voices.append(self._segment_voice(p["id"], segment))
            speech = sum(audio.duration(v) for v in voices)
            out = config.DATA_DIR / "cache/packages" / (hashlib.sha256(p["id"].encode()).hexdigest()[:16] + ".wav")
            jingles = self._jingles_for(p["id"])
            self.pkg_jingles[p["id"]] = jingles
            talkover = audio.render_package(voices, out, separators=sounds[1:], jingles=jingles,
                                            bed_gain_db=self.cfg["broadcast"]["voice_bed_gain_db"],
                                            talkover=self._talkover(p["id"]))
        except Exception as e:
            attempts = p["attempts"] + 1
            self.db.execute("UPDATE packages SET attempts=?, error=?, status=? WHERE id=?",
                            (attempts, str(e)[:500], "FAILED" if attempts >= 2 else "PENDING", p["id"]))
            raise
        # Only PENDING -> READY: a package skipped while it was being rendered stays skipped.
        self.db.execute("UPDATE packages SET status='READY', audio_path=?, speech_sec=?, duration_sec=?, talkover_sec=? "
                        "WHERE id=? AND status='PENDING'", (str(out), speech, audio.duration(out), talkover, p["id"]))
        store.log_event(self.db, self.session, "PACKAGE_READY", p["id"], f"tts+render {time.time() - t0:.0f}s")
        log(f"package ready: {p['id']} speech={speech:.1f}s in {time.time() - t0:.0f}s")

    def _jingles_for(self, package_id: str) -> dict:
        """One file per role for a package; GitHub bulletins open with their own section intro if configured."""
        roles = {k: v for k, v in self.cfg["jingles"].items() if k not in ("station_id", *(s[0] for s in host.SECTIONS.values()))}
        jingles = audio.choose_jingles(roles, package_id, self.recent_jingles)
        if sec := host.SECTIONS.get(package_id.split(":")[0]):
            if self.cfg["jingles"][sec[0]]:
                jingles["stinger_in"] = audio.pick(self.cfg["jingles"][sec[0]], package_id)
        return jingles

    def _body_segments(self, p: dict, prev: str | None) -> list[tuple[str, bool]]:
        """Voice segments of a package. A section's name is not in its script but said here: by its intro jingle
        when it opens the break (prev is None), else as a short phrase when it follows another section."""
        segs = content.voice_segments(Path(p["script_path"]).read_text(encoding="utf-8"))
        cat = p["id"].split(":")[0]
        sec = host.SECTIONS.get(cat)
        if not sec or not self.cfg["jingles"][sec[0]]:
            return segs  # no intro configured: the topic is spoken in the script
        if segs and segs[0][0].rstrip(".") == sec[1].rstrip("."):
            segs = segs[1:]  # scripts made before the intro existed start with the phrase
        return segs if prev is None or prev == cat else [(sec[1], False)] + segs

    @staticmethod
    def _speech_check(path: Path) -> str | None:
        return audio.validate(path, 0.5, 900) or audio.speech_problem(path)

    def _talkover(self, package_id: str) -> bool:
        """Whether this bulletin ends over the next song instead of an outro: a stable choice per package."""
        share = self.cfg["broadcast"]["talkover_share"] or 0.5
        h = int(hashlib.sha256(f"talkover|{package_id}".encode()).hexdigest(), 16) % 1000
        return not package_id.startswith("system:") and h < share * 1000

    async def voice_loop(self) -> None:
        """The voice is a separate server (VoiceStudio's backend). If nothing answers and nothing is starting on its
        port, start it ourselves: no app window. Own child only: it stops with the radio, a VoiceStudio that was
        already running is never touched. A crashed child is started again, at most once a minute."""
        while not self.stopping:
            try:
                await self._voice_tick()
            except Exception as e:
                log("voice loop:", type(e).__name__)
            await asyncio.sleep(20)

    async def _voice_tick(self) -> None:
        v = self.cfg["voice"]
        if await asyncio.to_thread(self.tts.health):
            return
        port = int(v["base_url"].rsplit(":", 1)[-1])
        if (self.voice_proc and self.voice_proc.poll() is None) or await asyncio.to_thread(tts_mod.port_open, port) \
                or time.time() - self.voice_started < 60:
            return  # still loading, or somebody else is starting it
        self.voice_started = time.time()
        log_file = open(config.DATA_DIR / "logs/voicestudio.log", "a")
        self.voice_proc = await asyncio.to_thread(tts_mod.start_backend, v["server_dir"], port, log_file)
        log("VoiceStudio is not answering: started its server (pid", self.voice_proc.pid, ")")

    async def _stop_voice(self) -> None:
        proc, self.voice_proc = self.voice_proc, None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), 15)
            except asyncio.TimeoutError:
                proc.kill()
            log("VoiceStudio server stopped")

    # ---- reminders: a short insert before an event or a task with a time ----
    async def reminders_loop(self) -> None:
        while not self.stopping:
            try:
                await self._reminders_tick()
                await self._weather_tick()
            except Exception as e:
                self.last_error = f"reminders: {e}"
                log("reminders error:", e)
            await asyncio.sleep(30)

    async def _expire_reminders(self) -> None:
        for key, (rid, r) in list(self.queued_reminders.items()):
            plugin = "reminders_todoist" if r["kind"] == "task" else "reminders_calendar"
            enabled = plugins.on(self.cfg, plugin)
            # The text counts minutes to its airtime: a later airing would say them wrong, so it is said anew.
            stale = now() >= r.get("air", r["start"]) + timedelta(minutes=1)
            if self.state == "PAUSED" or stale or now() >= reminders.until(r) or not enabled:
                await self.engine.remove(rid)
                self.queued_reminders.pop(key, None)
                self.db.execute("UPDATE air_plan SET status='ERROR',rid=NULL,error='Reminder expired or paused' WHERE kind='reminder' AND ref=? AND status='QUEUED'", (key,))
                self.reminder_text.pop(key, None)
                self.id_pending = False
                if enabled and now() < reminders.until(r):
                    self.reminders_due[key] = r

    async def _reminders_tick(self) -> None:
        self._live_settings()
        await self._expire_reminders()
        rc, t = self.cfg["reminders"], time.time()
        lead = timedelta(minutes=rc["lead_min"])
        due = []
        for name, plugin, secret, read, parse in (
                ("calendar", "reminders_calendar", rc["calendar_url"], reminders.fetch_ics, reminders.parse_events),
                ("todoist", "reminders_todoist", rc["todoist_token"], reminders.fetch_todoist, lambda x: x)):
            if not plugins.on(self.cfg, plugin):
                self.sources.pop(name, None)
                continue
            if not secret:
                self.sources[name] = "not set up"
                continue
            if t >= self.next_read[name]:
                try:
                    data = await asyncio.to_thread(lambda: parse(read(secret)))
                    if name == "calendar":
                        self.cal_events = data
                        bad = reminders.unsupported(data)
                        self.sources[name] = "ok" + (f", unsupported recurrence rule: {', '.join(bad[:3])}" if bad else "")
                    else:
                        self.todoist_tasks = data
                        self.sources[name] = "ok"
                    self.next_read[name] = t + rc["poll_minutes"] * 60
                except Exception as e:  # never log the link or the token
                    self.sources[name] = f"error: {type(e).__name__}"
                    log(f"reminders {name}:", type(e).__name__)
                    self.next_read[name] = t + 60
            due += (reminders.calendar_due(self.cal_events, now(), lead) if name == "calendar"
                    else reminders.todoist_due(self.todoist_tasks, now(), lead))
        changes = direction.edits(self.db) if plugins.on(self.cfg, "air_direction") else {}
        due += [r for r in self._reminder_candidates(now(), timedelta(hours=PLAN_HOURS))  # moved earlier in the plan
                if (moved := changes.get("reminder:" + r["key"], {}).get("not_before")) and moved <= iso(now())]
        for r in due:
            if r["key"] not in self.reminders_due and r["key"] not in self.queued_reminders and not self.db.execute(
                    "SELECT 1 FROM reminders WHERE key=?", (r["key"],)).fetchone():
                self.reminders_due[r["key"]] = r
        for key in [k for k, r in self.reminders_due.items()
                    if r["kind"] == "day" or (r.get("expires") or reminders.until(r)) < now()]:
            self.reminders_due.pop(key)  # expire timed reminders; refresh today's topics from the calendar
        if day := self._day_due(now().astimezone()):
            self.reminders_due[day["key"]] = day
        if self.reminders_due:
            self.wake.set()

    def _reminder_candidates(self, at, span):
        """Timed reminders from the last read of the calendar and Todoist: starting within `span` from `at`, or
        started and still making sense (reminders.until); not aired yet."""
        back = timedelta(hours=12)
        found = []
        if plugins.on(self.cfg, "reminders_calendar"):
            found += reminders.calendar_due(self.cal_events, at - back, back + span)
        if plugins.on(self.cfg, "reminders_todoist"):
            found += reminders.todoist_due(self.todoist_tasks, at - back, back + span)
        fired = {row[0] for row in self.db.execute("SELECT key FROM reminders WHERE key LIKE 'cal:%' OR key LIKE 'todoist:%'")}
        return [r for r in found if reminders.until(r) > at and r["key"] not in fired]

    def _day_due(self, at):
        if not plugins.on(self.cfg, "reminders_calendar") or not self.cfg["reminders"]["calendar_url"] \
                or not plugins.on(self.cfg, "host_links"):
            return None
        history = self.db.execute("SELECT key, fired_at FROM reminders WHERE key LIKE ?",
                                  (f"day:{at.date()}:%",)).fetchall()
        return reminders.day_due(self.cal_events, at, history)

    def _weather_enabled(self) -> bool:
        return bool(self.cfg["weather"]["city"]) and plugins.on(self.cfg, "weather") and plugins.on(self.cfg, "host_links")

    async def _weather_tick(self) -> str | None:
        if not self._weather_enabled():
            self.sources.pop("weather", None)
            for key in [k for k, r in self.reminders_due.items() if r["kind"] == "weather"]:
                self.reminders_due.pop(key)
            return
        waiting = next((r for r in self.reminders_due.values() if r["kind"] == "weather"), None)
        if waiting:
            return None
        if self.state != "PLAYING":
            return None
        at = now()
        slot = weather.slot(at, self.cfg["weather"].get("timezone", "Asia/Ho_Chi_Minh"))
        key = f"weather:slot:{int(slot.timestamp())}"
        if plugins.on(self.cfg, "air_direction"):
            changes = direction.edits(self.db)
            slots = [r for r in direction.weather_rows(self.db, self.cfg, at, changes)
                     if direction.allowed(r, changes, at) and r['not_before'] <= iso(at)]
            if not slots:
                return None
            key = slots[0]['id']
        elif (at - slot >= timedelta(minutes=30) or self.db.execute(
                "SELECT 1 FROM reminders WHERE key=?", (key,)).fetchone()):
            return None
        if self.db.execute("SELECT 1 FROM air_plan WHERE id=? AND status='QUEUED'", (key,)).fetchone():
            return None
        if time.time() < self.next_weather_read:
            return None
        try:
            r = await asyncio.to_thread(weather.fetch, self.cfg["weather"]["city"], at)
            r["key"] = key
            self.reminders_due[r["key"]] = r
            self.sources["weather"] = "ok"
            self.wake.set()
            return r["key"]
        except Exception as e:
            self.sources["weather"] = f"error: {type(e).__name__}"
            log("weather:", type(e).__name__)
            self.next_weather_read = time.time() + 15 * 60

    async def _remind(self, remaining: float, timed_only=False) -> bool:
        """Prepare timed notices early; optional host inserts wait for the song's ending."""
        for key, r in sorted(self.reminders_due.items(), key=lambda kv: (kv[1]["kind"] in ("day", "weather"), kv[1]["start"])):
            if plugins.on(self.cfg, 'air_direction') and not direction.allowed({'id': key if r['kind'] == 'weather' else 'reminder:' + key}, direction.edits(self.db), now()):
                if r['kind'] == 'weather' and direction.edits(self.db).get(key, {}).get('skipped'):
                    self.reminders_due.pop(key, None)
                continue
            timed = r["kind"] not in ("day", "weather")
            if timed_only and not timed:
                continue
            if r["kind"] == "day" and remaining > REMIND_LATE_SEC:
                continue  # weather is rendered at once: the break that joins it waits for it (_weather_pending)
            if r.get("retry", 0) > time.time():
                continue
            track, state = self.current, self.state
            air = now() + timedelta(seconds=max(0.0, remaining))
            if r["kind"] == "weather":
                if not self._weather_enabled() or air >= r["expires"]:
                    self.reminders_due.pop(key)
                    continue
                text = r["text"]
            elif r["kind"] == "day":
                day = self._day_due(air.astimezone())
                if day is None or day["key"] != key:
                    self.reminders_due.pop(key)
                    continue
                text = ("Сегодня — {title}.", "К слову, сегодня — {title}.",
                        "Ещё одна примета сегодняшнего дня — {title}.")[r["count"]].format(title=r["title"])
            else:
                text = reminders.say(r, air, host.span_words)
            if text is None:
                self.reminders_due.pop(key)
                continue
            if not timed and self.db.execute("SELECT 1 FROM packages WHERE item_id=? AND status IN ('PENDING','READY','QUEUED','PLAYING')", ('system:' + key,)).fetchone():
                continue
            if r['kind'] == 'day' and not self._break_due(remaining) and not any(n['kind'] == 'weather' for n in self.reminders_due.values()):
                continue
            try:
                gain = 0.0
                if r["kind"] in ("day", "weather"):
                    if r["kind"] == "weather":
                        wav = await asyncio.to_thread(self._render_weather, text)
                        if key not in self.reminders_due or not self._weather_enabled() or now() >= r["expires"]:
                            self.reminders_due.pop(key, None)
                            continue
                    else:
                        text, wav = await asyncio.to_thread(self._render_day, r, text)
                    gain = -16 - (await asyncio.to_thread(audio.lufs, wav) or -16)
                    if r["kind"] == "day":
                        current = self._day_due(now().astimezone())
                        if current is None or current["key"] != key:
                            self.reminders_due.pop(key, None)
                            continue  # rendering crossed midnight or the calendar feature was switched off
                else:
                    wav = await asyncio.to_thread(self._render_notice, text, reminders.plain(r, air, host.span_words))
            except Exception as e:
                self.last_error = f"reminder: {e}"
                r["retry"] = time.time() + 30
                return False
            if plugins.on(self.cfg, 'air_direction') and not direction.allowed({'id': key if r['kind'] == 'weather' else 'reminder:' + key}, direction.edits(self.db), now()):
                continue
            if timed and (self.current is not track or self.state != state or now() + timedelta(seconds=10) >= air):
                return False  # the voice missed this boundary; recalculate for the next song
            if not timed:
                self._store_host_segment(key, r, text, wav)
                return False
            rid = await self.engine.push_replay(wav, "weather" if r["kind"] == "weather" else "reminder", key, gain, key if r["kind"] == "weather" else "",
                                                ',liq_cross_end_duration="1.5",liq_cross_start_duration="12"')
            if timed:
                r["air"] = air
                self.queued_reminders[key] = (rid, r)
                self.db.execute("UPDATE air_plan SET status='QUEUED',rid=? WHERE id=?", (rid, 'reminder:' + key))
            elif r["kind"] == "weather":
                self.db.execute("INSERT INTO air_plan(id,kind,ref,status,rid) VALUES(?,?,?,'QUEUED',?) "
                                "ON CONFLICT(id) DO UPDATE SET status='QUEUED',rid=excluded.rid", (key, 'weather', key, rid))
            else:
                self.db.execute("INSERT OR REPLACE INTO reminders(key, fired_at) VALUES(?,?)", (key, iso(now())))
            self.reminders_due.pop(key, None)
            self.reminder_text[key] = r.get("summary", text)
            if r["kind"] == "task" and r.get("description"):
                self.reminder_text[key] = json.dumps({"text": text, "description": r["description"]}, ensure_ascii=False)
            self.id_pending = True  # one insert at a time, like a station ID
            log(r["kind"], "queued:", text)
            return True
        return False

    def _host_segment(self, package):
        item = self.db.execute('SELECT kind FROM items WHERE id=?', (package['item_id'],)).fetchone()
        return item['kind'] if item and item['kind'] in ('weather', 'day') else None

    def _store_host_segment(self, key, notice, text, wav):
        item_id = 'system:' + key
        if not self.db.execute('SELECT 1 FROM items WHERE id=?', (item_id,)).fetchone():
            cfg = {**self.cfg, 'broadcast': {**self.cfg['broadcast'], 'max_package_speech_sec': max(self.cfg['broadcast']['max_package_speech_sec'], len(text) / self.cfg['voice']['chars_per_sec'] + 10)}}
            content.add_item(self.db, cfg, {
                'id': item_id, 'topic': notice.get('summary') or notice['title'], 'speak_topic': False,
                'source': 'weather' if notice['kind'] == 'weather' else 'calendar', 'url': '',
                'published_at': now(), 'body': text}, kind=notice['kind'])
        self.db.execute("UPDATE packages SET status='READY',audio_path=?,speech_sec=?,duration_sec=? WHERE item_id=? AND status='PENDING'",
                        (str(wav), audio.duration(wav), audio.duration(wav), item_id))

    def _segment_voice(self, package_id, text, voice=None):
        item = self.db.execute('SELECT i.kind FROM packages p JOIN items i ON i.id=p.item_id WHERE p.id=?', (package_id,)).fetchone()
        return self._render_weather(text) if item and item['kind'] == 'weather' else self._say(text, voice)

    def _say(self, text: str, voice=None) -> Path:
        """Voice a text, checked twice: the spectrum (no noise) and, for a fresh take, a local Whisper transcript that
        must contain most of the text's words (no noise, no swallowed or garbled words)."""
        # 5-letter stems (endings may differ); number words left out: Whisper writes "пятнадцать" as "15"
        words = {w[:5] for w in re.findall(r"[а-яё]{4,}", text.casefold()) if not NUMBER_WORD.match(w)}

        def check(path):
            if error := self._speech_check(path):
                return error
            # shortcut: a cached clip is trusted (it passed when made); only new takes are transcribed (~4 s each)
            if len(words) < 3 or not path.name.endswith(".take.wav") or not hasattr(self.tts, "transcribe"):
                return None
            try:
                heard = {w[:5] for w in re.findall(r"[а-яё]{4,}", self.tts.transcribe(path).casefold())}
            except Exception as e:
                log("speech verification unavailable:", type(e).__name__)  # the spectrum check stands
                return None
            return None if len(words & heard) >= len(words) * 0.6 else "speech does not match the text"

        return cached_synthesize(voice or self.tts, text, check)

    def _render_weather(self, text: str) -> Path:
        expected = set(re.findall(r"[а-яё]{4,}", text.casefold()))

        def check(path):
            if error := self._speech_check(path):
                return error
            heard = set(re.findall(r"[а-яё]{4,}", self.tts.transcribe(path).casefold()))
            if len(expected & heard) < len(expected) * 0.6:
                return "weather speech does not match forecast"
            return None

        return cached_synthesize(self.tts, text, check)

    def _render_day(self, r: dict, text: str):
        short = text
        if not r["count"] and r["description"]:
            try:
                text = self.editor.retell_day(r["title"], r["description"]) or text
            except Exception as e:
                log("calendar story:", type(e).__name__)  # a failed story still gets a short mention
        try:
            return text, self._say(text)
        except RuntimeError:
            if text == short:
                raise
            return short, self._say(short)

    def _render_notice(self, text: str, fallback: str = "") -> Path:
        try:
            voice = self._say(text)
        except RuntimeError:  # the voice cannot read this title in a longer phrase: the shortest form
            voice = self._say(fallback or text.removeprefix("Напоминание. "))
        out = config.DATA_DIR / "cache/packages" / ("reminder-" + hashlib.sha256(text.encode()).hexdigest()[:16] + ".wav")
        audio.render_notice(voice, self.cfg["jingles"]["reminder"][0], out)
        return out

    def _new_set_due(self) -> bool:
        b = self.cfg["broadcast"]
        return not self.ytmusic_active and plugins.on(self.cfg, "new_tracks") and 0 < b["new_every_sets"] <= self.sets_since_fresh

    def _pick_new(self) -> list[Path]:
        """Songs for a new-tracks set: unaired songs added in the last new_days days, newest first, enough for
        a normal set (music_set_minutes); none if there are fewer than new_min_tracks."""
        b = self.cfg["broadcast"]
        found = [f for f in music.unaired_new(self.db, self.files, b["new_days"]) if str(f) in self.intro_rooms]
        if len(found) < b["new_min_tracks"]:
            return []
        picked, total = [], 0.0
        for f in found:
            picked.append(f)
            total += audio.duration(f)
            if total >= b["music_set_minutes"] * 60:
                break
        return picked

    async def _queue_fresh(self, files: list[Path]) -> None:
        """The new songs go to Liquidsoap's fresh queue: after the break just queued, but before the shuffle,
        and the next break or call sign still comes first at a song boundary."""
        m = self.cfg["music"]
        for f in files:
            row = self.db.execute("SELECT * FROM tracks WHERE path=?", (str(f),)).fetchone()
            gain = min(m["max_boost_db"], m["target_lufs"] - row["lufs"]) if row["lufs"] is not None else 0.0
            tags, _ = music.song_tags(row, m["crossfade_sec"], True)
            self.fresh_queued[str(f)] = await self.engine.push_replay(f, "music", str(f), gain, "", tags, "fresh")
        self.sets_since_fresh = 0
        log("new-tracks set queued:", len(files), "songs")

    async def _clear_fresh(self) -> None:
        for rid in self.fresh_queued.values():
            try:
                await self.engine.remove(rid, "fresh")
            except Exception:
                pass  # already on air or gone
        self.fresh_queued = {}

    def _news_skipped(self, at=None):
        if not plugins.on(self.cfg, 'air_direction'):
            return False
        at = at or now()
        start = at - timedelta(minutes=self.cfg['broadcast']['music_set_minutes'])
        skip = self.db.execute("SELECT not_before FROM air_plan WHERE id LIKE 'news-skip:%' AND skipped=1 "
                               "AND not_before<=? AND not_before>? ORDER BY not_before DESC LIMIT 1", (iso(at), iso(start))).fetchone()
        if not skip:
            return False
        return not self.db.execute("SELECT 1 FROM air_plan WHERE (id LIKE 'section:news:%' OR id LIKE 'direction:%' AND kind='news') "
                                   "AND skipped=0 AND status IN ('PENDING','PREPARING','QUEUED') AND not_before>? AND not_before<=?",
                                   (skip['not_before'], iso(at))).fetchone()

    def _requested_edition(self):
        if not plugins.on(self.cfg, 'air_direction'):
            return None
        return self.db.execute("SELECT id FROM air_plan WHERE id LIKE 'direction:%' AND kind='news' AND skipped=0 AND status='PENDING' AND not_before<=? ORDER BY not_before LIMIT 1", (iso(now()),)).fetchone()

    def _break_due(self, remaining: float, directed=True) -> bool:
        """A news break ends a music set: the first of a session after one song, then when the set reaches
        music_set_minutes by the end of the current song."""
        if directed and self._news_skipped(now() + timedelta(seconds=max(0, remaining))):
            return False
        if directed and self._requested_edition():
            return True
        if directed and plugins.on(self.cfg, 'air_direction'):
            section = self.db.execute('SELECT * FROM air_plan WHERE id=?', (self._news_section_id(),)).fetchone()
            if section and section['status'] not in ('PLAYED', 'ERROR') and not (section['skipped'] and section['not_before'] and now() >= content.parse_time(section['not_before']) + timedelta(seconds=self.cfg['broadcast']['music_set_minutes'] * 60)):
                return not section['skipped'] and (not section['not_before'] or section['not_before'] <= iso(now()))
        if not self.db.execute("SELECT 1 FROM events WHERE session_id=? AND type='PACKAGE_STARTED'",
                               (self.session,)).fetchone():
            return self.songs_since_break >= 1 or self.set_started is not None
        return bool(self.set_started) and (time.time() - self.set_started + max(0.0, remaining)
                                           >= self.cfg["broadcast"]["music_set_minutes"] * 60)

    def _pick_break(self) -> list:
        """Ready bulletins for one break: in rotation order (the section aired longest ago opens the break), a
        section's bulletins together; up to max_break_speech_sec and what the rolling-hour limit still allows."""
        b = self.cfg["broadcast"]
        budget = min(b["max_break_speech_sec"], b["max_spoken_seconds_per_rolling_hour"] - store.spoken_last_hour(self.db))
        budget -= 12 if plugins.on(self.cfg, "host_links") else 0  # back-sell, time check, set opener (~10 s)
        ready = content.ready_to_air(self.db, self._sources_off())
        changes = direction.edits(self.db) if plugins.on(self.cfg, "air_direction") else {}
        if changes:
            candidates = direction.news(self.db)
            ready = direction.ordered([r for r in candidates if r['status'] == 'READY' and r['source'] not in self._sources_off()
                     and direction.allowed(r, changes, now()) and not any(
                         p['item_id'] == r['item_id'] and p['part'] < r['part'] and not changes.get(p['id'], {}).get('skipped')
                         for p in candidates)], changes)
        section = self.db.execute('SELECT * FROM air_plan WHERE id=?', (self._news_section_id(),)).fetchone() if plugins.on(self.cfg, 'air_direction') else None
        if self._news_skipped() or section and not self._requested_edition() and not section['skipped'] and section['not_before'] and section['not_before'] > iso(now()):
            ready = [p for p in ready if p['id'].startswith('system:')]
        ready = [p for p in ready if not self._host_segment(p) or
                 ((notice := self.reminders_due.get(p['item_id'].removeprefix('system:'))) and
                  (notice['kind'] != 'weather' or self._weather_enabled() and now() < notice['expires']) and
                  (notice['kind'] != 'day' or self._day_due(now().astimezone())) and
                  direction.allowed({'id': p['item_id'].removeprefix('system:')}, changes, now()))]
        ready.sort(key=lambda p: self._host_segment(p) != 'weather')
        news = [p for p in ready if not p["id"].startswith("system:music-link:")]
        ready = news or ready
        if ready and ready[0]["id"].startswith("system:") and not self._host_segment(ready[0]):
            return [ready[0]] if ready[0]["speech_sec"] <= budget + 12 else []  # announcements air alone
        picked, total = [], 0.0
        for p in ready:
            if total + p["speech_sec"] <= budget:
                picked.append(p)
                total += p["speech_sec"]
        order = {}
        for p in picked:
            order.setdefault(p["id"].split(":")[0], len(order))
        return picked if any(changes.get(p["id"], {}).get("rank") is not None for p in picked) else sorted(picked, key=lambda p: order[p["id"].split(":")[0]])

    def _assemble_break(self, pkgs: list[dict], air_time, links: bool, first: tuple[str, str] | None,
                        intro_room: float | None = None, novelty: bool = False):
        """One news break: [back-sell, time check] + every bulletin's voice segments (a separator between
        bulletins) + [set opener]. Voice segments come from the TTS cache; only host lines are new.
        intro_room: the set opener goes over the next song's intro, ending at most this long into it.
        Returns (wav, talkover_sec), or None to air the first bulletin pre-rendered."""
        try:
            t0 = time.time()
            b, seed = self.cfg["broadcast"], pkgs[0]["id"]
            segs, owners, lead = [], [], None
            if links:
                # The back-sell comes right after the song, before the news sounder; the time check after it.
                if back := host.back_sell(self.set_songs, self.recent_jingles, seed):
                    lead = self._say(back)
                segs.append((host.opener(air_time.astimezone(), b["station_name"], self.recent_jingles, seed), False))
                owners.append(seed)
            prev = None
            for k, p in enumerate(pkgs):
                for j, (text, sound) in enumerate(self._body_segments(p, prev)):
                    segs.append((text, sound or (j == 0 and k > 0)))
                    owners.append(p["id"])
                prev = p["id"].split(":")[0]
            if links:
                segs.append((host.set_opener(b["music_set_minutes"], first, self.recent_jingles, seed, novelty), False))
                owners.append(seed)
            speakers = [None] * len(segs)
            if plugins.on(self.cfg, "duo_hosts") and len(pkgs) > 1:
                segs, owners, speakers = self._duo(segs, owners, pkgs, seed)
            voices = [self._segment_voice(owner, text, voice) for (text, _), owner, voice in zip(segs, owners, speakers)]
            jingles = self.pkg_jingles.get(seed) or self._jingles_for(seed)
            key = hashlib.sha256(BREAK_SEP.join(p["id"] for p in pkgs).encode()).hexdigest()[:16]
            out = config.DATA_DIR / "cache/packages" / f"break-{key}.wav"
            over_intro = bool(links and intro_room)
            talkover = audio.render_package(voices, out, separators=[s for _, s in segs][1:], jingles=jingles,
                                            bed_gain_db=b["voice_bed_gain_db"], lead=lead,
                                            talkover=over_intro or self._talkover(seed),
                                            talkover_max=intro_room if over_intro else None)
            for p in pkgs:  # host lines count on the first bulletin, so the rolling-hour budget sees all speech
                speech = sum(audio.duration(v) for v, o in zip(voices, owners) if o == p["id"])
                speech += audio.duration(lead) if lead and p["id"] == seed else 0
                self.db.execute("UPDATE packages SET speech_sec=? WHERE id=?", (speech, p["id"]))
            if links:
                store.log_event(self.db, self.session, "HOST_LINKS", seed)
            log(f"break assembled for {air_time.astimezone():%H:%M}: {len(pkgs)} bulletins in {time.time() - t0:.1f}s")
            return out, talkover
        except Exception as e:
            self.last_error = f"assembly {pkgs[0]['id']}: {e}"
            log(self.last_error, "- airing the first bulletin pre-rendered")
            return None

    def _duo(self, segs, owners, pkgs, seed):
        """Two hosts: bulletins alternate between the voices (host lines stay with the main one); when the voice
        changes, the one finishing hands over by name and the next starts with a short thanks."""
        b = self.cfg["broadcast"]
        names = {None: b["host_name"], self.tts2: b["cohost_name"]}
        turn = {p["id"]: (self.tts2 if k % 2 else None) for k, p in enumerate(pkgs)}
        out_s, out_o, out_v, cur = [], [], [], None
        for (text, sound), owner in zip(segs, owners):
            voice = turn.get(owner)  # host lines (owner = the break seed) are not bulletins: main voice
            if voice is not cur and out_s:
                out_s.append((host.handoff(names[voice], self.recent_jingles, seed + owner), False))
                out_o.append(out_o[-1])
                out_v.append(cur)
                text = host.ack(names[cur], self.recent_jingles, seed + owner) + text
            out_s.append((text, sound))
            out_o.append(owner)
            out_v.append(voice)
            cur = voice
        return out_s, out_o, out_v

    # ---- CLI ----
    async def handle_client(self, reader, writer) -> None:
        try:
            req = json.loads(await reader.readline())
            resp = await self.dispatch(req.get("cmd", ""), req)
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
        await writer.drain()
        writer.close()

    async def replay(self, kind: str, ref: str) -> dict:
        """Puts a song into the queue again (UI timeline). Only library files are accepted."""
        if kind == "music" and ref in self.library:
            row = self.db.execute("SELECT lufs FROM tracks WHERE path=?", (ref,)).fetchone()
            m = self.cfg["music"]
            gain = min(m["max_boost_db"], m["target_lufs"] - row["lufs"]) if row and row["lufs"] is not None else 0.0
        else:
            return {"ok": False, "error": "nothing to replay"}
        replay_id = f"r{time.time_ns()}"
        duration = await asyncio.to_thread(self._song_seconds, ref)  # before the push: it may air at once
        rid = await self.engine.push_replay(Path(ref), "music", ref, gain, replay_id)
        self.replays[replay_id] = {"kind": kind, "ref": ref, "rid": rid, "duration": duration}
        log("replay queued:", kind, ref)
        return {"ok": True, "status": self.status()}

    async def unqueue(self, kind: str, ref: str) -> dict:
        """Takes a replay off the queue. One that is already on air is left alone (it is no longer queued)."""
        for replay_id, r in list(self.replays.items()):
            if (r["kind"], r["ref"]) == (kind, ref):
                del self.replays[replay_id]
                await self.engine.remove(r["rid"])
                log("replay unqueued:", kind, ref)
                break
        return {"ok": True, "status": self.status()}

    def _next_music_slot(self):
        if not plugins.on(self.cfg, 'reroll_next'):
            return None
        for key, row in self.replays.items():
            if row['kind'] == 'music':
                return {'ref': row['ref'], 'queue': 'news', 'rid': row['rid'], 'replay_id': key}
        if self.fresh_queued:
            ref, rid = next(iter(self.fresh_queued.items()))
            return {'ref': ref, 'queue': 'fresh', 'rid': rid}
        ref = getattr(self, 'next_track', '')
        if ref:
            queue = 'local_music' if self.ytmusic_active and not ref.startswith('ytmusic:') else 'music'
            return {'ref': ref, 'queue': queue, 'rid': ''}
        return None

    def _reroll_state(self):
        slot = self._next_music_slot()
        if not slot:
            return None
        remaining = ((self.current or {}).get('duration') or 0) - ((self.paused_at if self.state == 'PAUSED' else time.time()) - (self.current or {}).get('started', 0))
        reason = ''
        if not self.current or self.current['kind'] != 'music' or self.state not in ('PLAYING', 'PAUSED'):
            reason = 'Wait for the next music track'
        elif self.assembling or self.queued_break:
            reason = 'The host transition is already prepared'
        elif remaining <= max(10, self.cfg['music']['crossfade_sec'] + 3):
            reason = 'The next track is already starting'
        elif self.rerolling:
            reason = 'Choosing another track'
        return {'ref': slot['ref'], 'title': music.label(self.db, slot['ref']), 'available': not reason, 'reason': reason}

    async def _reroll_next(self, expected):
        state = self._reroll_state()
        if not state or not state['available'] or expected != state['ref']:
            return {'ok': False, 'error': (state or {}).get('reason') or 'The next track changed. Try again.'}
        slot = self._next_music_slot()
        choices = self.engine.music_choices(expected)
        excluded = {expected, self.current['ref'], *self.fresh_queued, *(r['ref'] for r in self.replays.values())}
        choices = {ref: uri for ref, uri in choices.items() if ref in self.library and ref not in excluded}
        if not choices:
            return {'ok': False, 'error': 'No other track is available'}
        replacement = random.choice(list(choices))
        uri = choices[replacement]
        if slot.get('replay_id'):
            uri = uri.replace('annotate:', 'annotate:replay_id="' + slot['replay_id'] + '",', 1)
        self.rerolling = True
        downloaded = None
        try:
            if replacement.startswith('ytmusic:'):
                # Download here: inside Liquidsoap's replace command the download blocks every engine command.
                try:
                    downloaded = await asyncio.to_thread(ytmusic.resolve, replacement.removeprefix('ytmusic:'))
                except Exception:
                    return {'ok': False, 'error': 'That song could not be loaded from YouTube Music. Try again.'}
                uri = uri.removesuffix(replacement) + 'tmp:' + downloaded  # Liquidsoap removes it after airing
            rid = await self.engine.replace_music(expected, uri, slot['queue'], slot['rid'])
            downloaded = None
            if slot.get('replay_id') in self.replays:
                self.replays[slot['replay_id']].update(ref=replacement, rid=rid,
                                                       duration=await asyncio.to_thread(self._song_seconds, replacement))
            elif slot['queue'] == 'fresh':
                self.fresh_queued = {replacement if ref == expected else ref: rid if ref == expected else old_rid for ref, old_rid in self.fresh_queued.items()}
            elif self.current['ref'] != replacement:
                self.next_track = replacement
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            return {'ok': False, 'error': 'Could not replace the next track. Try again.'}
        finally:
            self.rerolling = False
            if downloaded:  # not handed to Liquidsoap
                Path(downloaded).unlink(missing_ok=True)
        return await self.dispatch('status')

    def _direction_catalog(self):
        blocks = [{'kind': 'news', 'ref': '', 'title': 'Host edition'},
                  {'kind': 'text', 'ref': '', 'title': 'Spoken text'}]
        if self._weather_enabled():
            blocks.append({'kind': 'weather', 'ref': '', 'title': 'Host edition with weather'})
        if any(isinstance(files, list) and files for files in self.cfg['jingles'].values()):
            blocks.append({'kind': 'jingle', 'ref': '', 'title': 'Jingle'})
        if any(plugins.on(self.cfg, p) for p in ('reminders_calendar', 'reminders_todoist')):
            blocks.append({'kind': 'reminder', 'ref': '', 'title': 'Reminder'})
        return blocks

    async def _direction_tick(self):
        job = self.db.execute("SELECT * FROM air_plan WHERE (id LIKE 'direction:%' OR id LIKE 'section:%') AND id NOT LIKE 'section:news:%' AND id NOT LIKE 'section:music:%' AND kind!='news' AND status='PENDING' "
                              "AND skipped=0 AND not_before<=? ORDER BY not_before,id LIMIT 1", (iso(now()),)).fetchone()
        if not job:
            return False
        key, kind, ref = job['id'], job['kind'], job['ref']
        if kind == 'weather' and ref.startswith('system:') and self.db.execute("SELECT 1 FROM packages WHERE id=? AND status='READY'", (ref,)).fetchone():
            return False
        if kind == 'news' and self._news_skipped():
            return False
        self.db.execute("UPDATE air_plan SET status='PREPARING' WHERE id=?", (key,))
        try:
            gain, text = 0.0, job['text']
            if kind == 'reminder' and (not ref or ref.startswith(('cal:', 'todoist:'))):
                await self._reminders_tick()
                before = set(self.queued_reminders)
                remaining = max(0, self.current.get('duration', 0) - (time.time() - self.current['started'])) if self.current else 0
                if not await self._remind(remaining, timed_only=True):
                    raise ValueError('No upcoming timed reminder is available')
                ref = next(iter(set(self.queued_reminders) - before))
                rid, reminder = self.queued_reminders[ref]
                current = self.db.execute('SELECT * FROM air_plan WHERE id=?', (key,)).fetchone()
                if not plugins.on(self.cfg, 'air_direction') or not direction.allowed(current, {key: dict(current)}, now()):
                    await self._cancel_direction_job({'rid': rid, 'kind': kind, 'id': key, 'ref': ref})
                    self.db.execute("UPDATE air_plan SET status='PENDING' WHERE id=?", (key,))
                    return False
                self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=?,text=?,error=NULL WHERE id=?", (rid, ref, reminder['title'], key))
                return True
            elif kind == 'weather':
                if not self._weather_enabled():
                    raise ValueError('Weather is disabled')
                report = await asyncio.to_thread(weather.fetch, self.cfg['weather']['city'], now())
                wav = await asyncio.to_thread(self._render_weather, report['text'])
                if now() >= report['expires']:
                    raise ValueError('Weather expired during preparation')
                report['key'] = key
                self.reminders_due[key] = report
                self._store_host_segment(key, {**report, 'kind': 'weather'}, report['text'], wav)
                self.db.execute("UPDATE air_plan SET status='PENDING',ref=? WHERE id=?", ('system:' + key + '#1', key))
                return False
            elif kind == 'music':
                if not ref:
                    ref = await self.engine.next_music() or next(iter(sorted(self.library)), '')
                if ref not in self.library:
                    raise ValueError('Song is no longer in the library')
                wav = Path(ref)
                row = self.db.execute('SELECT lufs FROM tracks WHERE path=?', (ref,)).fetchone()
                m = self.cfg['music']
                gain = min(m['max_boost_db'], m['target_lufs'] - row['lufs']) if row and row['lufs'] is not None else 0.0
            elif kind == 'jingle':
                if not ref:
                    ref = next((r for r in ('station_id', *self.cfg['jingles']) if isinstance(self.cfg['jingles'].get(r), list) and self.cfg['jingles'][r]), '')
                if ref not in self.cfg['jingles'] or not self.cfg['jingles'][ref]:
                    raise ValueError('Jingle is unavailable')
                wav = audio.choose_jingles({ref: self.cfg['jingles'][ref]}, key, self.recent_jingles)[ref]
                gain = -16 - (await asyncio.to_thread(audio.lufs, wav) or -16)
            elif kind == 'reminder':
                row = self.db.execute("SELECT detail FROM events WHERE type='REMINDER' AND id=?", (ref,)).fetchone()
                if not row:
                    raise ValueError('Reminder is unavailable')
                text = row['detail']
                try:
                    speech = json.loads(text)['text']
                except (ValueError, TypeError, KeyError):
                    speech = text
                wav = await asyncio.to_thread(self._render_notice, speech)
            else:
                raise ValueError('Unknown block type')
            current = self.db.execute('SELECT * FROM air_plan WHERE id=?', (key,)).fetchone()
            if not plugins.on(self.cfg, 'air_direction') or not direction.allowed(current, {key: dict(current)}, now()) or kind == 'news' and self._news_skipped():
                self.db.execute("UPDATE air_plan SET status='PENDING' WHERE id=?", (key,))
                return False
            air_kind = 'id' if kind == 'jingle' else kind
            if kind == 'news':
                rid = await self.engine.push_news(wav, ref, p['talkover_sec'] or 0)
                self.db.execute("UPDATE packages SET status='QUEUED' WHERE id=?", (ref,))
                self.queued_break = (rid, [ref])
            else:
                rid = await self.engine.push_replay(wav, air_kind, ref if kind == 'music' else key, gain, key)
            self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=?,text=?,error=NULL WHERE id=?", (rid, ref, text, key))
            if kind in ('weather', 'reminder'):
                self.reminder_text[key] = text
            log('directed block queued:', kind, key)
            return True
        except Exception as error:
            self.db.execute("UPDATE air_plan SET status='ERROR',error=? WHERE id=?", (str(error)[:240], key))
            log('directed block:', type(error).__name__)
            return False

    async def _cancel_direction_job(self, row):
        if self.queued_break and row['rid'] == self.queued_break[0]:
            rid, ids = self.queued_break
            await self.engine.remove(rid)
            self.db.executemany("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", [(pid,) for pid in ids])
            self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE rid=? AND status='QUEUED'", (rid,))
            self.queued_break = None
            return
        await self.engine.remove(row['rid'])
        if row['kind'] == 'news':
            self.db.execute("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", (row['ref'],))
            self.queued_break = None
        if row['kind'] in ('weather', 'reminder'):
            self.id_pending = False
            self.reminder_text.pop(row['id'], None)
            self.reminder_text.pop(row['ref'], None)
            waiting = self.queued_reminders.pop(row['ref'], None)
            if waiting:
                self.reminders_due[row['ref']] = waiting[1]

    async def _direction_command(self, req):
        if not plugins.on(self.cfg, 'air_direction'):
            return {'ok': False, 'error': 'Enable Air direction in Experiments first'}
        action = req.get('action')
        if action == 'save':
            return await self._save_broadcast_plan(req)
        if action not in ('catalog', 'insert', 'up', 'down', 'skip', 'time', 'reset'):
            raise ValueError('Unknown running-order action')
        if action == 'catalog':
            return {'ok': True, 'blocks': self._direction_catalog()}
        if action == 'insert':
            kind, ref = str(req.get('kind', '')), str(req.get('ref', ''))
            catalog = self._direction_catalog()
            selected = next((b for b in catalog if (b['kind'], b['ref']) == (kind, ref)), None)
            if not selected and ref:
                if kind == 'music' and ref in self.library or kind == 'jingle' and self.cfg['jingles'].get(ref):
                    selected = {'title': kind.title()}
                elif kind == 'news' and self.db.execute('SELECT 1 FROM packages WHERE id=?', (ref,)).fetchone():
                    selected = {'title': 'News'}
                elif kind == 'reminder' and self.db.execute("SELECT 1 FROM events WHERE id=? AND type='REMINDER'", (ref,)).fetchone():
                    selected = {'title': 'Reminder'}
            if not selected:
                raise ValueError('Choose an available block')
            key = direction.insert(self.db, self.cfg, kind, ref, str(req.get('text') or '') if kind == 'text' else selected['title'], now(), req.get('at'))
        else:
            if action == 'time':
                direction.requested_time(req.get('at'), now())
            key = str(req.get('id', ''))
            package = self.db.execute('SELECT status FROM packages WHERE id=?', (key,)).fetchone()
            if package and package['status'] == 'QUEUED' and self.queued_break:
                rid, ids = self.queued_break
                if not rid:
                    raise ValueError('This block is already entering playback')
                await self.engine.remove(rid)
                self.db.executemany("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", [(pid,) for pid in ids])
                self.queued_break = None
            job = self.db.execute('SELECT * FROM air_plan WHERE id=?', (key,)).fetchone()
            if job and job['status'] == 'QUEUED' and job['rid']:
                await self._cancel_direction_job(job)
                self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE id=? AND status='QUEUED'", (key,))
            direction.edit(self.db, self.cfg, key, action, now(), req.get('at'))
            self.reminders_due.pop(key, None)
        self.wake.set()
        return {'ok': True, 'id': key, 'direction': direction.rows(self.db, self.cfg, now())}

    def _sources_off(self) -> list[str]:
        """Sources whose plugin is switched off: their ready bulletins wait instead of airing."""
        return [src for src, name in plugins.SOURCE_PLUGIN.items() if not plugins.on(self.cfg, name)]

    async def dispatch(self, cmd: str, req: dict | None = None) -> dict:
        if cmd == "plugins":
            return {"ok": True, "plugins": plugins.listing(self.cfg)}
        if cmd == "plugin":
            name, enabled = str((req or {}).get('name', '')), bool((req or {}).get('on'))
            if name == 'air_direction' and not enabled:
                for row in self.db.execute("SELECT * FROM air_plan WHERE status='QUEUED' AND rid IS NOT NULL").fetchall():
                    await self._cancel_direction_job(row)
                    self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE id=? AND status='QUEUED'", (row['id'],))
                if self.queued_break:
                    rid, ids = self.queued_break
                    if rid:
                        await self.engine.remove(rid)
                        self.db.executemany("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", [(pid,) for pid in ids])
                        self.queued_break = None
                for key in list(self.reminders_due):
                    if key.startswith('weather:slot:'):
                        self.reminders_due.pop(key)
            plugins.set_plugin(name, enabled)
            self.wake.set()
            return {"ok": True, "plugins": plugins.listing(self.cfg)}
        if cmd == "direction":
            return await self._direction_command(req or {})
        if cmd == "replay":
            return await self.replay(str((req or {}).get("kind", "")), str((req or {}).get("ref", "")))
        if cmd == "unqueue":
            return await self.unqueue(str((req or {}).get("kind", "")), str((req or {}).get("ref", "")))
        if cmd == 'reroll-next':
            return await self._reroll_next(str((req or {}).get('ref', '')))
        if cmd in ("status", "play"):
            if cmd == "play" and self.state == "PAUSED":
                return await self.dispatch("resume")
            status = self.status()
            if getattr(self, 'engine', None) and self.engine.proc and self.engine.proc.returncode is None:
                try:
                    ref = await asyncio.wait_for(self.engine.next_music(), .5)
                    if ref:
                        self.next_track = ref
                        status['upcoming'].append({'kind': 'music', 'ref': ref, 'title': music.label(self.db, ref), 'stage': 'music'})
                        status['next_reroll'] = self._reroll_state()
                        if plugins.on(self.cfg, 'air_direction'):
                            status['broadcast_plan'] = self._broadcast_plan()
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                    pass
            return {"ok": True, "status": status}
        if cmd == "pause" and self.state == "PLAYING":
            await self._pause()
        elif cmd == "resume" and self.state == "PAUSED":
            await self._resume()
        elif cmd == "next":
            current = self.current
            if current and current['kind'] == 'music':
                remaining = max(0, current.get('duration', 0) - (time.time() - current['started']))
                if self.queued_break or self._waiting_break(remaining):
                    await self.schedule(immediate=True)
                if self.queued_break or self.queued_reminders or self.replays or self.id_pending:
                    await self.engine.wait_insert(self.queued_break[0] if self.queued_break else None)
                else:
                    await self.engine.wait_music()
            if self.current is current:
                self.skip_flag = bool(current and current['kind'] == 'news')
                self.next_transition_sec = .1
                try:
                    await self.engine.command("skip")
                except Exception:
                    self.next_transition_sec = None
                    raise
        elif cmd == "skip-news":
            if not (self.current and self.current["kind"] == "news"):
                return {"ok": False, "error": "no bulletin is on air"}
            self.skip_flag = True
            self.next_transition_sec = .1
            try:
                await self.engine.command("skip")
            except Exception:
                self.next_transition_sec = None
                raise
        elif cmd == "stop":
            asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.stop()))
            return {"ok": True, "status": {"state": "STOPPING"}}
        elif cmd not in ("pause", "resume"):
            return {"ok": False, "error": f"unknown command {cmd}"}
        return {"ok": True, "status": self.status()}

    def _title(self) -> str:
        """What the page shows as now playing: the song, or the topics of the break (no part numbers or lengths)."""
        kind, ref = self.current["kind"], self.current["ref"]
        if kind == "music":
            return music.label(self.db, ref)
        if kind == "news":
            return 'Host edition'
        return {"id": "Station ID", "reminder": "Reminder", "weather": "Weather"}.get(kind, kind)

    def _news_section_id(self):
        active = self.db.execute("SELECT id FROM air_plan WHERE id LIKE 'section:news:%' "
                                 "AND status NOT IN ('PLAYED','ERROR') AND (id NOT LIKE 'section:news:future:%' OR not_before<=?) AND (skipped=0 OR not_before>?) "
                                 "ORDER BY CASE WHEN skipped=0 AND not_before<=? THEN 0 ELSE 1 END,not_before LIMIT 1", (iso(now()), iso(now() - timedelta(minutes=self.cfg['broadcast']['music_set_minutes'])), iso(now()))).fetchone()
        return active['id'] if active else f"section:news:{self.session}:{int(self.set_started or 0)}"

    def _broadcast_plan(self):
        at = now()
        cur = self.current
        remaining = max(0, (cur.get('duration') or 0) - ((self.paused_at if self.state == 'PAUSED' else time.time()) - cur['started'])) if cur else 0
        boundary = at + timedelta(seconds=remaining)
        # Replays wait in the news queue: they air before a break or insert that is not queued ahead of them.
        replays = [r for r in self.replays.values() if not self.queued_break or int(r['rid'] or 0) < int(self.queued_break[0] or 0)]
        first = boundary + timedelta(seconds=sum(r.get('duration') or 0 for r in replays))
        interval = self.cfg['broadcast']['music_set_minutes'] * 60
        news_at = first
        if not self._break_due(remaining, directed=False) and self.set_started:
            news_at = max(first, at + timedelta(seconds=max(0, interval - (time.time() - self.set_started))))
        horizon = at + timedelta(hours=PLAN_HOURS)
        plan = direction.rows(self.db, self.cfg, at)
        result, seen_rows = [], set()
        for r in plan:
            if r['id'] in seen_rows:
                continue
            seen_rows.add(r['id'])
            if r['kind'] == 'music':
                continue
            if r['id'].startswith('section:news:') and not r['id'].startswith('section:news:future:') and r['id'] != self._news_section_id() and not r['skipped']:
                continue
            if r['kind'] == 'news' and not r['id'].startswith(('direction:', 'section:')) and not r['not_before']:
                continue
            due = content.parse_time(r['not_before'])
            if due and due > horizon and not r.get('edited'):
                continue
            result.append({**r, 'title': self.cfg['weather']['city'] if r['kind'] == 'weather' and r['title'] == 'Weather' else r['title'], 'at': iso(due if r['skipped'] else max(first, due)) if due else iso(first), 'duration': r.get('duration_sec') or r.get('est_speech_sec') or 30,
                           'estimated': not due or due < boundary, 'locked': r['status'] in ('QUEUED', 'PREPARING'),
                           'members': [r['id']]})
        grouped, timed = [], {}
        for r in result:
            if r['kind'] != 'news' or r['id'].startswith(('direction:', 'section:')):
                grouped.append(r)
                continue
            key = (r['not_before'], r['skipped'])
            if key not in timed:
                r['title'] = timeline.topic_name(r['title'])
                timed[key] = r
                grouped.append(r)
            else:
                block = timed[key]
                block['members'] += r['members']
                block['title'] = ' · '.join(dict.fromkeys([block['title'], timeline.topic_name(r['title'])]))
                block['duration'] += r['duration']
                block['locked'] = block['locked'] or r['locked']
                if r['status'] in ('QUEUED', 'PREPARING', 'PENDING'):
                    block['status'] = r['status']
        result = grouped
        candidates = [r for r in plan if r['kind'] == 'news' and not r['id'].startswith(('direction:', 'section:')) and not r['skipped'] and not r['not_before'] and not self._host_segment(r)]
        picked = self._pick_break()
        if self.queued_break:
            picked = [{'id': key} for key in self.queued_break[1]]
        selected = [r for r in candidates if r['id'] in {p['id'] for p in picked}] or candidates[:3]
        section_id = self._news_section_id()
        section = next((r for r in result if r['id'] == section_id), None)
        requested = next((r for r in sorted(result, key=lambda r: r['at']) if r['kind'] == 'news' and r['id'].startswith('direction:') and not r['skipped'] and content.parse_time(r['at']) <= (content.parse_time(section['at']) if section else news_at)), None)
        section = requested or section
        if selected and section:
            section['content_members'] = [r['id'] for r in selected]
            section['title'] = ' · '.join(dict.fromkeys(timeline.topic_name(r['title']) for r in selected))
            section['duration'] = sum(r.get('duration_sec') or r.get('est_speech_sec') or 30 for r in selected)
            if section['status'] == 'PENDING' and all(r['status'] == 'READY' for r in selected):
                section['status'] = 'READY'
        elif selected:
            due = max([news_at] + [content.parse_time(r['not_before']) for r in selected if r['not_before']])
            titles = list(dict.fromkeys(timeline.topic_name(r['title']) for r in selected))
            result.append({'id': section_id, 'kind': 'news', 'title': ' · '.join(titles),
                           'members': [r['id'] for r in selected], 'at': iso(due), 'not_before': selected[0]['not_before'],
                           'estimated': True, 'duration': sum(r.get('duration_sec') or r.get('est_speech_sec') or 30 for r in selected),
                           'status': 'QUEUED' if any(r['status'] == 'QUEUED' for r in selected) else 'READY' if all(r['status'] == 'READY' for r in selected) else 'PENDING',
                           'locked': any(r['status'] == 'QUEUED' for r in selected), 'skipped': False, 'rid': self.queued_break[0] if self.queued_break else None})
        elif not section and not any(r['kind'] == 'news' and not r['skipped'] for r in result) and any(plugins.on(self.cfg, p) for p in ('horizon', 'github_trending', 'github_new')):
            result.append({'id': section_id, 'kind': 'news', 'title': 'News', 'members': [], 'placeholder': True,
                           'at': iso(news_at), 'not_before': None, 'estimated': True, 'duration': 60, 'status': 'PENDING', 'locked': False, 'skipped': False})
        skipped = [r for r in plan if r['kind'] == 'news' and r['skipped'] and not r['id'].startswith(('direction:', 'section:'))]
        if skipped:
            result.append({'id': skipped[0]['id'], 'kind': 'news', 'title': 'Skipped bulletin', 'members': [r['id'] for r in skipped],
                           'at': iso(news_at), 'not_before': skipped[0]['not_before'], 'estimated': True,
                           'duration': 0, 'status': 'READY', 'locked': False, 'skipped': True})
        changes = direction.edits(self.db)
        early_titles = [music.label(self.db, r['ref']) for r in replays if r['kind'] == 'music']
        music_titles = [music.label(self.db, r['ref']) for r in self.replays.values() if r['kind'] == 'music' and r not in replays]
        music_titles += [music.label(self.db, ref) for ref in self.fresh_queued]
        if getattr(self, 'next_track', ''):
            music_titles.append(music.label(self.db, self.next_track))
        lead = timedelta(minutes=self.cfg['reminders']['lead_min'])
        waiting = {**self.reminders_due, **{key: pair[1] for key, pair in self.queued_reminders.items()}}
        upcoming = {r['key']: r for r in self._reminder_candidates(at, horizon - at)}
        for key, r in {**upcoming, **waiting}.items():
            if r['kind'] not in ('task', 'event') or at >= reminders.until(r) or not plugins.on(self.cfg, 'reminders_todoist' if r['kind'] == 'task' else 'reminders_calendar'):
                continue
            if any(row['kind'] == 'reminder' and row.get('ref') == key for row in result):
                continue
            edit_id = 'reminder:' + key
            edit = changes.get(edit_id, {})
            result.append({'id': edit_id, 'kind': 'reminder', 'title': reminders.clean_title(r['title']) or 'Reminder', 'ref': key, 'members': [edit_id],
                           'ends': iso(reminders.until(r)),
                           'at': edit.get('not_before') or iso(first if key in waiting else max(first, r['start'] - lead)), 'not_before': edit.get('not_before'),
                           'duration': 30, 'estimated': not bool(edit.get('not_before')), 'locked': key in self.queued_reminders,
                           'status': 'QUEUED' if key in self.queued_reminders else 'PENDING', 'skipped': bool(edit.get('skipped')),
                           'rid': self.queued_reminders[key][0] if key in self.queued_reminders else None})
        # shortcut: future sets show the configured interval, not songs or speech that have not been prepared.
        future = news_at + timedelta(seconds=interval)
        while future <= horizon:
            anchor = int(future.timestamp() // interval)
            key = f'section:news:future:{anchor}'
            if not any(r['id'] == key or r['kind'] == 'news' and not r['skipped'] and abs((content.parse_time(r['at']) - future).total_seconds()) <= max(60, r['duration']) for r in result) and any(plugins.on(self.cfg, p) for p in ('horizon', 'github_trending', 'github_new')):
                result.append({'id': key, 'kind': 'news', 'title': 'News', 'members': [], 'placeholder': True,
                               'at': iso(future), 'not_before': None, 'estimated': True, 'duration': 60,
                               'status': 'PENDING', 'locked': False, 'skipped': False})
            future += timedelta(seconds=interval)
        for row in result:
            if row['kind'] == 'news':
                skip_id = 'news-skip:' + row['id']
                row['skip_id'] = skip_id
                row['skipped'] = row['skipped'] or bool(changes.get(skip_id, {}).get('skipped'))
        # A skip lasts one music set from its time (as in _news_skipped); after that the block would only hang.
        result = [row for row in result if not (row['skipped'] and (skipped_at := content.parse_time(
            changes.get(row.get('skip_id'), {}).get('not_before') or row.get('not_before'))) and
            skipped_at + timedelta(seconds=interval) <= at)]
        for row in result:
            if row['kind'] == 'news' and not row['id'].startswith(('section:', 'direction:', 'system:')):
                row['legacy_members'] = row['members']
                row['id'] = 'section:news:saved:' + row['id']
            row['content_title'] = row['title']
            row['title'] = {'news': 'News', 'weather': 'Weather', 'reminder': 'Reminder', 'jingle': 'Jingle'}.get(row['kind'], row['kind'].title())
        news_sections = []
        stages = {'ERROR': 0, 'PENDING': 1, 'READY': 2, 'PREPARING': 3, 'QUEUED': 4}
        sections = []
        for row in result:
            if row['kind'] != 'news' or not row['id'].startswith('section:news:'):
                sections.append(row)
                continue
            # Overlapping blocks are one edition, e.g. a saved slot and the real one; back-to-back ones stay apart.
            start = content.parse_time(row['at'])
            block = next((b for b in news_sections if b['skipped'] == row['skipped'] and
                          content.parse_time(b['at']) < start + timedelta(seconds=row['duration']) and
                          start < content.parse_time(b['at']) + timedelta(seconds=b['duration'])), None)
            if not block:
                news_sections.append(row)
                sections.append(row)
                continue
            block['members'] = list(dict.fromkeys([*block['members'], row['id'], *row['members']]))
            if block['id'].startswith('section:news:future:') and not row['id'].startswith('section:news:future:'):
                block['members'].append(block['id'])
                block['id'], block['skip_id'], block['at'] = row['id'], row['skip_id'], row['at']
            block['content_members'] = list(dict.fromkeys([*block.get('content_members', []), *row.get('content_members', [])]))
            block['duration'] = max(block['duration'], row['duration'])
            block['legacy_members'] = list(dict.fromkeys([*block.get('legacy_members', []), *row.get('legacy_members', [])]))
            block['content_title'] = ' · '.join(dict.fromkeys(t for t in (block['content_title'], row['content_title']) if t and t != 'News'))
            block['locked'] = block['locked'] or row['locked']
            block['status'] = max((block['status'], row['status']), key=lambda stage: stages.get(stage, 0))
        for block in news_sections:
            if block['id'].startswith('section:news:future:') and content.parse_time(block['at']) <= boundary:
                block['members'] = list(dict.fromkeys([block['id'], *block['members']]))
                block['id'] = block['id'].replace('section:news:future:', 'section:news:current:', 1)
        result = sections
        # Weather airs at the first song boundary after its time and takes the ready news along (_waiting_break),
        # so a news section starting by then is the same edition: one block to skip or move.
        # shortcut: "by then" is WEATHER_JOINS_SEC (about a song); use the real next boundaries if songs get longer.
        for clock in [r for r in result if r['kind'] == 'weather']:
            start = content.parse_time(clock['at'])
            merged = [r for r in result if r['kind'] == 'news' and r['skipped'] == clock['skipped'] and
                      (start - timedelta(seconds=r['duration']) < content.parse_time(r['at']) < start + timedelta(seconds=WEATHER_JOINS_SEC)
                       or self.queued_break and clock.get('rid') == self.queued_break[0] and
                       any(key in self.queued_break[1] for key in [*r['members'], *r.get('content_members', [])]))]
            if merged:
                clock['edition_news_ids'] = [r['id'] for r in merged]
                clock['content_members'] = list(dict.fromkeys([key for r in merged for key in [*r['members'], *r.get('content_members', [])]]))
                clock['locked'] = clock['locked'] or any(r['locked'] for r in merged)
                clock['duration'] += sum(r['duration'] for r in merged)
                result = [r for r in result if r not in merged]
        result.sort(key=lambda r: (r['at'], not r['locked']))
        cursor, previous = boundary, 'opening:' + self.session
        music_sections = []
        for row in [r for r in result if not r['skipped']] + [None]:
            end = max(cursor, content.parse_time(row['at'])) if row else max(cursor, horizon)
            if end > cursor:
                # Before a block that waits for the replays only they play; the shuffle's song comes after it.
                waits = row and content.parse_time(row['at']) <= first
                titles, early_titles = early_titles + ([] if waits else music_titles), []
                if not waits:
                    music_titles = []
                music_sections.append({'id': 'section:music:' + previous, 'kind': 'music', 'title': 'Music',
                                       'content_title': ' · '.join(dict.fromkeys(titles)),
                                       'at': iso(cursor), 'not_before': None, 'duration': (end - cursor).total_seconds(),
                                       'next_section': row['id'] if row else None, 'members': [], 'placeholder': True,
                                       'estimated': True, 'status': 'PENDING', 'locked': False, 'skipped': False})
            if row:
                cursor = max(cursor, content.parse_time(row['at']) + timedelta(seconds=row['duration']))
                previous = row['id']
        result += music_sections
        packages = {r['id']: r for r in plan if r['kind'] == 'news' and r.get('script_path')}
        for row in result:
            if row['kind'] not in ('news', 'weather', 'reminder'):
                continue
            ids = [*row['members'], *row.get('content_members', []), row.get('ref')]
            parts = [packages[key] for key in dict.fromkeys(ids) if key in packages]
            notice = self.reminders_due.get(row.get('ref')) or self.reminders_due.get(row['id'])
            row['text_ready'] = row['status'] == 'QUEUED' or bool(parts) and all(Path(p['script_path']).is_file() for p in parts) or bool(notice and (notice.get('title') or notice.get('text')))
            row['voice_ready'] = row['status'] == 'QUEUED' or bool(parts) and all(p['status'] in ('READY', 'QUEUED') for p in parts)
        self._edition_details(result, packages)
        result.sort(key=lambda r: (r['at'], not r['locked']))
        version = hashlib.sha256(json.dumps([{k: r.get(k) for k in ('id', 'status', 'not_before', 'skipped', 'locked')} for r in sorted(result, key=lambda r: r['id'])], sort_keys=True).encode()).hexdigest()[:20]
        return {'version': version, 'rows': result, 'starts_at': iso(boundary)}

    def _edition_details(self, rows, packages):
        labels = {'horizon': 'Tech news', 'github_trending': 'GitHub trends', 'github_new': 'New GitHub projects',
                  'weather': 'Weather', 'calendar': 'Today', 'music_facts': 'Music stories', 'inbox': 'News', 'direction': 'Announcement'}
        for row in rows:
            if row['kind'] not in ('news', 'weather'):
                continue
            ids = [*row['members'], *row.get('content_members', []), row.get('ref')]
            if row['kind'] == 'weather':
                ids += [key for key, p in packages.items() if p['item_id'] == 'system:' + row['id']]
            parts = [packages[key] for key in dict.fromkeys(ids) if key in packages]
            segments = {}
            for p in parts:
                source = p['source']
                seg = segments.setdefault(source, {'title': labels.get(source, source), 'items': [], 'text_ready': True, 'voice_ready': True})
                seg['items'].append(timeline.topic_name(p['title']))
                seg['voice_ready'] = seg['voice_ready'] and p['status'] in ('READY', 'QUEUED')
            if not segments:
                sources = ['weather'] if row['kind'] == 'weather' else [name for name in ('horizon', 'github_trending', 'github_new') if plugins.on(self.cfg, name)]
                segments = {name: {'title': labels[name], 'items': [], 'text_ready': bool(row.get('text_ready')) if name == 'weather' else False, 'voice_ready': False} for name in sources}
                if row['kind'] != 'weather' and plugins.on(self.cfg, 'music_facts'):
                    segments['music_facts'] = {'title': labels['music_facts'], 'items': [], 'optional': True, 'text_ready': False, 'voice_ready': False}
            title = timeline.edition_title(['weather'] if row['kind'] == 'weather' else [p['source'] for p in parts])
            row.update(edition=True, title=title, content_title='', segments=list(segments.values()))
            if parts:
                row['text_ready'] = all(seg['text_ready'] for seg in row['segments'])
                row['voice_ready'] = all(seg['voice_ready'] for seg in row['segments'])
            row['preparation_started'] = bool(row.get('voice_ready') or row['kind'] == 'weather' and row.get('text_ready') or row['status'] in ('PREPARING', 'QUEUED') or
                                              row['kind'] == 'news' and self.state == 'PLAYING' and content.parse_time(row['at']) <= now() + timedelta(seconds=TEXT_LEAD_SEC))
            for segment in row['segments']:
                segment['preparation_started'] = row['preparation_started']

    async def _save_broadcast_plan(self, req):
        snapshot = self._broadcast_plan()
        changes = req.get('changes')
        if not isinstance(changes, list) or len(changes) > 100:
            raise ValueError('Invalid schedule changes')
        if not changes:
            return {'ok': True, 'broadcast_plan': snapshot}
        if req.get('version') != snapshot['version']:
            raise ValueError('The schedule changed while editing. Cancel and reopen Edit to use the latest plan.')
        current = {r['id']: r for r in snapshot['rows']}
        validated, seen = [], set()
        available = {b['kind'] for b in self._direction_catalog()}
        for change in changes:
            if not isinstance(change, dict) or not isinstance(change.get('id'), str) or change['id'] in seen:
                raise ValueError('Invalid or repeated block')
            seen.add(change['id'])
            row = current.get(change['id'])
            if change['id'].startswith('new:'):
                if change.get('kind') not in available:
                    raise ValueError('This block type is no longer available')
                row = {'id': change['id'], 'kind': change['kind'], 'members': [], 'new': True}
                if row['kind'] == 'text' and (not isinstance(change.get('text'), str) or not 1 <= len(change['text'].strip()) <= 4000):
                    raise ValueError('Enter 1–4000 characters')
            if 'skipped' in change and type(change['skipped']) is not bool:
                raise ValueError('Invalid skipped state')
            rank = change.get('rank', 0)
            if type(rank) is not int or not 0 <= rank <= 100:
                raise ValueError('Invalid block order')
            if row and row['kind'] == 'music':
                raise ValueError('Music fills the gaps automatically; edit a host section instead')
            if not row or row.get('locked') and not (row['kind'] in ('news', 'weather', 'reminder') and change.get('skip_only') is True and change.get('skipped') is True):
                raise ValueError('A block is already selected or preparing. Cancel and reopen Edit.')
            if row['kind'] == 'reminder' and change.get('skipped') and not row.get('skipped') and change.get('skip_confirmed') is not True:
                raise ValueError('Confirm skipping this reminder')
            due = direction.requested_time(row['at'] if row.get('locked') else change.get('at'), now())
            if row['kind'] == 'reminder' and row.get('ends') and due >= content.parse_time(row['ends']):
                raise ValueError('The event is over by then; move the reminder earlier or skip it')
            validated.append((row, change, due, rank))
        for row, change, due, rank in validated:
            if not row.get('locked'):
                continue
            if self.current and self.current['kind'] == row['kind'] and self.current['ref'] in (row.get('ref'), row['id'], BREAK_SEP.join(row['members'])):
                raise ValueError('This block is already on air')
            if row['status'] == 'QUEUED':
                if row['kind'] == 'news' and self.queued_break:
                    await self.engine.remove(self.queued_break[0])
                    self.db.executemany("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", [(key,) for key in self.queued_break[1]])
                    self.db.executemany("UPDATE air_plan SET status='PENDING',rid=NULL WHERE id=? AND status='QUEUED'", [(key,) for key in row['members']])
                    self.queued_break = None
                else:
                    await self._cancel_direction_job(row)
                self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE id=?", (row['id'],))
        with self.db:
            for row, change, due, rank in validated:
                skipped = int(bool(change.get('skipped')))
                for key in row.get('edition_news_ids', []):
                    if key.startswith('section:news:'):
                        self.db.execute("INSERT INTO air_plan(id,kind,text,not_before,rank,skipped) VALUES(?,'news','Host edition',?,?,?) ON CONFLICT(id) DO UPDATE SET not_before=excluded.not_before,rank=excluded.rank,skipped=excluded.skipped,status='PENDING',rid=NULL", (key, iso(due), rank, skipped))
                    self.db.execute("INSERT INTO air_plan(id,kind,not_before,skipped) VALUES(?,'news',?,?) ON CONFLICT(id) DO UPDATE SET not_before=excluded.not_before,skipped=excluded.skipped", ('news-skip:' + key, iso(due), skipped))
                if row.get('new') or row.get('placeholder') and row['kind'] != 'news' and not row['id'].startswith('section:'):
                    if not skipped:
                        direction.insert(self.db, self.cfg, row['kind'], '', change.get('text') or row.get('title') or row['kind'].title(), now(), iso(due))
                    continue
                if row.get('legacy_members'):
                    self.db.executemany('DELETE FROM air_plan WHERE id=?', [(key,) for key in row['legacy_members']])
                if row['kind'] == 'news':
                    if not row['id'].startswith(('section:', 'direction:', 'system:')):
                        self.db.executemany('DELETE FROM air_plan WHERE id=?', [(key,) for key in row['members']])
                        key = 'section:news:saved:' + row['id']
                        row = {**row, 'id': key, 'members': [], 'skip_id': 'news-skip:' + key}
                    self.db.execute("INSERT INTO air_plan(id,kind,not_before,skipped) VALUES(?,'news',?,?) "
                                    "ON CONFLICT(id) DO UPDATE SET not_before=excluded.not_before,skipped=excluded.skipped",
                                    (row.get('skip_id') or 'news-skip:' + row['id'], iso(due), skipped))
                    if row['id'].startswith('section:news:'):
                        keys = list(dict.fromkeys([row['id'], *(key for key in row['members'] if key.startswith('section:news:'))]))
                        self.db.executemany('UPDATE air_plan SET skipped=?,not_before=? WHERE id=?',
                                            [(skipped, iso(due), 'news-skip:' + key) for key in [*keys, *row.get('legacy_members', [])]])
                        for key in keys:
                            self.db.execute("INSERT INTO air_plan(id,kind,text,not_before,rank,skipped) VALUES(?,'news',?,?,?,?) "
                                            "ON CONFLICT(id) DO UPDATE SET text=excluded.text,not_before=excluded.not_before,rank=excluded.rank,skipped=excluded.skipped,error=NULL,status='PENDING'",
                                            (key, row.get('title') or 'News', iso(due), rank, skipped))
                        continue
                    if skipped:
                        continue
                if row.get('placeholder') and row['id'].startswith('section:'):
                    self.db.execute("INSERT OR IGNORE INTO air_plan(id,kind,text) VALUES(?,?,?)", (row['id'], row['kind'], row['title']))
                keys = row['members'] or [row['id']]
                if row['kind'] == 'reminder' and str(row.get('ref', '')).startswith(('cal:', 'todoist:')):
                    keys = list(dict.fromkeys([*keys, 'reminder:' + row['ref']]))
                for key in keys:
                    self.db.execute("INSERT INTO air_plan(id,kind,ref,not_before,rank,skipped) VALUES(?,?,?,?,?,?) "
                                    "ON CONFLICT(id) DO UPDATE SET not_before=excluded.not_before,rank=excluded.rank,skipped=excluded.skipped,error=NULL,status=CASE WHEN air_plan.status='ERROR' THEN 'PENDING' ELSE air_plan.status END",
                                    (key, row['kind'], '' if row.get('placeholder') else row.get('ref') or key, iso(due), rank, skipped))
        self.wake.set()
        return {'ok': True, 'broadcast_plan': self._broadcast_plan()}

    def _upcoming(self, plan):
        result = []
        queued_refs = {(r['kind'], r['ref']) for r in self.replays.values()}
        for r in self.replays.values():
            title = music.label(self.db, r['ref']) if r['kind'] == 'music' else self.reminder_text.get(r['ref'], r['kind'].title())
            result.append({'kind': r['kind'], 'ref': r['ref'], 'title': title, 'stage': 'queued'})
        for key, (_, r) in self.queued_reminders.items():
            if ('reminder', key) not in queued_refs:
                result.append({'kind': 'reminder', 'title': r['title'], 'stage': 'queued'})
        result += [{'kind': 'music', 'ref': ref, 'title': music.label(self.db, ref), 'stage': 'music'} for ref in self.fresh_queued]
        scheduled, rotation, seen = [], [], set()
        for key, r in self.reminders_due.items():
            if r['kind'] in ('task', 'event') and r['start'] > now() and plugins.on(self.cfg, 'reminders_todoist' if r['kind'] == 'task' else 'reminders_calendar'):
                scheduled.append({'kind': 'reminder', 'title': r['title'], 'stage': 'scheduled',
                                  'at': iso(r['start'] - timedelta(minutes=self.cfg['reminders']['lead_min']))})
        for r in plan:
            if r['skipped'] or r['status'] == 'ERROR' or (r['kind'], r['id']) in queued_refs:
                continue
            title = timeline.topic_name(r['title'])
            if r['kind'] == 'music':
                title = music.label(self.db, r['ref'])
            title = title or ('Weather · ' + self.cfg['weather']['city'] if r['kind'] == 'weather' else r['kind'].title())
            entry = {'kind': r['kind'], 'title': title, 'status': r['status'], 'at': r['not_before']}
            if r['status'] == 'QUEUED':
                result.append({**entry, 'stage': 'queued'})
            elif r['not_before']:
                scheduled.append({**entry, 'stage': 'scheduled'})
            elif r.get('item_id') not in seen:
                rotation.append({**entry, 'stage': 'rotation'})
                seen.add(r.get('item_id'))
        if self.current and self.current['kind'] == 'music' and not self.id_pending and not self.queued_break:
            remaining = max(0, self.current.get('duration', 0) - ((self.paused_at if self.state == 'PAUSED' else time.time()) - self.current['started']))
            if self._waiting_break(remaining):
                result.append({'kind': 'news', 'title': 'Host edition', 'stage': 'next'})
        result += rotation[:3]
        result += sorted(scheduled, key=lambda r: r['at'])[:3]
        return result

    def status(self) -> dict:
        self._live_settings()
        cur = None
        if self.current:
            ref = self.current["ref"]
            if self.current["kind"] == "id":
                cur = f"ПОЗЫВНОЙ: {ref}"
            elif self.current["kind"] == "reminder":
                cur = "НАПОМИНАНИЕ"
            elif self.current["kind"] == "weather":
                cur = "ПОГОДА"
            elif self.current["kind"] == "news":
                row = self.db.execute("SELECT p.part, p.parts, p.duration_sec, i.topic FROM packages p "
                                      "JOIN items i ON i.id=p.item_id WHERE p.id=?", (ref.split(BREAK_SEP)[0],)).fetchone()
                cur = (f"НОВОСТИ: {row['topic'] or 'объявление'} ({row['part']}/{row['parts']}, "
                       f"{row['duration_sec']:.0f}s)") if row else ref
            if self.current["kind"] == "music":
                cur = music.label(self.db, ref)
            cur += f"  [{time.time() - self.current['started']:.0f}s]"
        counts = dict(self.db.execute("SELECT p.status, COUNT(*) FROM packages p JOIN items i ON i.id=p.item_id "
                                      "WHERE p.status IN ('PENDING','READY','QUEUED') AND i.state='NEW' "
                                      "GROUP BY p.status").fetchall())
        title = None
        if self.current:
            title = self._title()
        plan = direction.rows(self.db, self.cfg, now())
        return {
            "state": self.state, "session": self.session, "current": cur,
            "current_kind": self.current["kind"] if self.current else None, "current_title": title,
            "elapsed_sec": round((self.paused_at if self.state == "PAUSED" else time.time()) - self.current["started"])
            if self.current else 0,
            "transition_sec": self.current.get('transition_sec', self.cfg['music']['crossfade_sec']) if self.current else 0,
            "duration_sec": round(self.current.get("duration") or 0) if self.current else 0,
            "news_ready": counts.get("READY", 0) + counts.get("QUEUED", 0), "news_preparing": counts.get("PENDING", 0),
            "spoken_last_hour_sec": round(store.spoken_last_hour(self.db)),
            "speech_limit_sec": self.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"],
            "voicestudio": "ok" if self.tts.health() else "unavailable",
            "llm_calls": self.editor.calls, "sources": self.sources, "last_error": self.last_error,
            "output": self.cfg["output"]["mode"], "auto_paused": self.auto_paused,
            "ytmusic_active": self.ytmusic_active,
            "music_source": 'ytmusic' if (self.current and self.current['kind'] == 'music' and self.current['ref'].startswith('ytmusic:') or
                                           self.ytmusic_active and (not self.current or self.current['kind'] != 'music')) else 'local',
            "broadcast_plan": self._broadcast_plan() if plugins.on(self.cfg, 'air_direction') else None,
            "direction": plan if plugins.on(self.cfg, 'air_direction') else [],
            "upcoming": self._upcoming(plan),
            "next_reroll": self._reroll_state(),
            "direction_timezone": self.cfg['weather'].get('timezone', 'Asia/Ho_Chi_Minh'),
            "replay_queue": [{"kind": r["kind"], "ref": r["ref"]} for r in self.replays.values()],
        }


def listeners() -> int:
    """Pages listening to the stream, from the UI server's heartbeat file; a stale file (UI gone) counts as 0."""
    try:
        d = json.loads((config.RUN_DIR / "listeners.json").read_text())
        return int(d["n"]) if time.time() - d["t"] < 15 else 0
    except (OSError, ValueError, KeyError):
        return 0


def main(output: str = "device") -> None:
    cfg = config.load()
    # One daemon: a second one (a start while this one is still starting) would run a second engine beside it.
    lock = open(config.RUN_DIR / "daemon.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another radio daemon is running: not starting a second one")
        return
    svc = Service(cfg, output=output)

    async def runner():
        loop = asyncio.get_running_loop()
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(svc.stop()))
        await svc.run()

    asyncio.run(runner())
