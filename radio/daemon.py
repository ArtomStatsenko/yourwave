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
import sys
import signal
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import broadcast_clock, audio, config, content, direction, github_new, github_trending, horizon, host, music, music_facts, plugins, reminders, settings, store, timeline, weather, ytmusic
from . import editor, public_news, reddit, telegram
from . import catchup, energy, hooks, previews
from .editor import Editor
from .engine import Liquidsoap
from .store import iso, now
from . import tts as tts_mod
from .tts import cached_synthesize

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


UNAVAILABLE = "unavailable"  # a reroll pick YouTube does not serve: the next spare is tried
HEAR_EVERY_SEC = 180  # one song every 3 minutes: the library (~270 songs) is done in about a day, never at once
ENERGY_PREFETCH = 2  # queue reserve, independent of the energy curve


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Service:
    def __init__(self, cfg: dict, tts=None, output: str = "device"):
        self.cfg, self.output = cfg, output
        # config.toml gives the defaults, settings.json (the Settings tab) overrides them: applied before anything runs,
        # else the first jobs (the GitHub fetch right after start) used the default AI provider, not the chosen one
        self.defaults = settings.base(cfg)
        settings.apply(cfg, self.defaults)
        self.mock_voice = tts is not None
        self.voice_engine = cfg["voice"]["engine"]
        self.tts = tts or tts_mod.make_voice(cfg)
        self.tts2 = tts or tts_mod.make_voice(cfg, "cohost")  # plugin "Two hosts" (a mock in tests: the same mock)
        self._db_local = threading.local()
        self.session = uuid.uuid4().hex[:12]
        self.state = "STARTING"
        self.auto_paused, self.paused_at, self.heard_at = False, 0.0, time.time()
        self.replays = {}  # replay_id -> {kind, ref, rid}: replays waiting in Liquidsoap's queue
        self.replays_checked = 0.0  # when Liquidsoap's queue was last compared with them
        self.reroll_pending, self.reroll_error = None, None  # a reroll loading in the background; its failure
        self.reroll_task = None
        self.reroll_gen = 0  # each dice click; an older download that finishes after a newer click is dropped
        self.reroll_download = asyncio.Lock()  # one yt-dlp at a time: fast clicks must not start parallel downloads
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
        self.handoff_lock = asyncio.Lock()
        self.plan_undo = None
        self.schedule_generation = 0
        self.preview_job = None
        self.announced_tracks = []
        self.plan_durations = {}
        self.plan_metadata_task = None
        self.plan_metadata_missing = False
        self.preview_enabled = False
        self.preview_retry = 0.0
        self.catchup_task = None
        self.catchup_error = ''
        self.hook_proc = None
        self.hook_retry = {}
        self.hook_snapshot = []
        self.next_cache_cleanup = 0.0
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
        self.music_source_lock = asyncio.Lock()
        self.music_source_error = ""
        self.music_source_ready = False
        self.source_overlap = None
        self.voice_proc = None         # the VoiceStudio server this daemon started (and so stops)
        self.voice_started = 0.0
        self.voice_proc_engine = ""
        self.cal_events: list = []                 # calendar events (read every poll_minutes)
        self.todoist_tasks: list = []
        self.next_read = {"calendar": 0.0, "todoist": 0.0}
        self.public_news_due = {}
        self.public_news_selection = {}
        self.next_weather_read = 0.0
        self.reminders_due: dict[str, dict] = {}   # key -> reminder waiting for a song boundary
        self.reminder_text: dict[str, str] = {}    # key -> what the host says, for the timeline
        self.queued_reminders: dict[str, tuple] = {}  # timed reminders can expire before the song ends
        self.fresh_missing: set[str] = set()
        self.fresh_queued: dict[str, str] = {}  # path -> request id of new songs waiting in Liquidsoap's fresh queue
        self.energy_queued = set()
        self.energy_played = set()
        self.energy_seconds = 0.0
        self.energy_previous = ''
        self.pkg_jingles: dict = {}     # package id -> jingles chosen at render, reused at final assembly
        self.id_pending = False
        self.gap_rid = None
        self.queued_break = None
        self.queued_clock = None
        self.manual_switch = False
        self.manual_skip_pending = False
        self.resume_offset: dict[str, float] = {}  # ref -> seconds already played, for the song resumed at start
        self.weather_hold: dict[str, float] = {}   # weather slot -> start of the song its joining break first waited at
        self.gap_voice: list[tuple[str, Path]] = []  # gap filler lines, rendered ahead: a gap leaves seconds
        self.gap_song, self.gap_said = 0.0, 0.0      # the song last checked, when the host last covered a gap
        self.opener_retry = self.gap_retry = 0.0     # next try to pre-render the startup opener, the gap lines

    # ---- lifecycle ----
    @property
    def db(self):
        if not hasattr(self._db_local, 'connection'):
            self._db_local.connection = store.connect()
        return self._db_local.connection

    async def run(self) -> None:
        self._cleanup_cache()
        self._reset_inflight()
        github_trending.retire_undated(self.db)
        self.db.execute("UPDATE air_plan SET status='EXPIRED',rid=NULL WHERE status IN ('PENDING','QUEUED','PREPARING') AND "
                        "((kind='news' AND not_before<=?) OR (kind NOT IN ('news','reminder') AND not_before<=?))",
                        (iso(now() - broadcast_clock.WINDOW),iso(now() - broadcast_clock.INSERT_WINDOW)))
        self.db.execute("UPDATE air_plan SET status='EXPIRED',rid=NULL WHERE id LIKE ? AND not_before<=? AND status IN ('PENDING','QUEUED','PREPARING')", (broadcast_clock.PREFIX + '%', iso(now() - broadcast_clock.WINDOW)))
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
        if not files:
            m3u.write_text("#EXTM3U\n")
        remote_m3u = config.RUN_DIR / "ytmusic.m3u"
        for stale in (ytmusic.directory() / "audio").glob("*.webm"):
            stale.unlink(missing_ok=True)
        try:
            remote = ytmusic.write_m3u(self.db, remote_m3u) if settings.music_source(self.cfg) == "ytmusic" else {}
        except ValueError:
            remote = {}
            remote_m3u.write_text("#EXTM3U\n")
        self.library.update(remote)
        self.intro_rooms.update(remote)
        self.ytmusic_active = settings.music_source(self.cfg) == "ytmusic" and bool(remote)
        if not self.ytmusic_active:
            remote_m3u.write_text("#EXTM3U\n")
        if not files and not self.ytmusic_active:
            raise SystemExit(f"NO_MUSIC: no playable files in {m['path']}")
        self.engine = Liquidsoap(remote_m3u, m["crossfade_sec"], self.output, os.environ.get("RADIO_RECORD", ""),
                                 self.cfg["output"], fallback_m3u=m3u)
        self.engine.music_source = 'ytmusic' if self.ytmusic_active else 'local'
        self.engine.music_shuffle = bool(m['shuffle'])
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
        asyncio.create_task(self.public_news_loop())
        asyncio.create_task(self.reminders_loop())
        asyncio.create_task(self.gap_loop())
        asyncio.create_task(self.preview_loop())
        asyncio.create_task(self.hook_loop())
        if not self.mock_voice:
            asyncio.create_task(self.hearing_loop())
        if not self.mock_voice:
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
        tts_mod.stop_whisper()  # a song being heard would keep the process alive after the socket is gone
        self._stop_hook_worker()
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
            "set_songs": songs, "set_elapsed": at - self.set_started if self.set_started else None,
            "energy_seconds": self.energy_seconds if song else self._energy_elapsed(),
            "energy_previous": self.energy_previous}))

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
        self.energy_seconds = saved.get('energy_seconds', 0.0)
        self.energy_previous = saved.get('energy_previous', '')
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

    async def _cancel_automatic(self, status='PENDING'):
        self.schedule_generation += 1
        await self._cancel_preview()
        async with self.handoff_lock:
            if self.gap_rid:
                await self.engine.remove(self.gap_rid)
                self.gap_rid, self.id_pending = None, False
            if self.queued_break:
                rid, ids = self.queued_break
                await self.engine.remove(rid)
                self.db.executemany("UPDATE packages SET status='READY' WHERE id=? AND status='QUEUED'", [(key,) for key in ids])
                self.db.execute("UPDATE air_plan SET status=?,rid=NULL WHERE rid=? AND status='QUEUED'", (status, rid))
                self.queued_break = self.queued_clock = None
                self.id_pending = False

    async def _pause(self) -> None:
        await self.engine.command("pause")
        await self._cancel_automatic()
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

    async def hearing_loop(self) -> None:
        """Vocal timing for songs LRCLIB does not know, by the local Whisper: one song every HEAR_EVERY_SEC, only when
        the radio is not voicing or assembling, at background priority. Spread over the day, never a batch; new
        tracks first. Found timings apply from the next start (the playlist is written then)."""
        while not self.stopping:
            await asyncio.sleep(HEAR_EVERY_SEC)
            if self.stopping or self.rendering or self.assembling:
                continue
            try:  # an error here must not end the loop for the rest of the session
                if not (f := await asyncio.to_thread(music.to_hear, self.db, self._local_files())):
                    continue
                await asyncio.to_thread(music.hear, self.db, f)
                log("vocals heard:", f.name)
            except Exception as e:
                log("vocal timing:", type(e).__name__)

    async def gap_loop(self) -> None:
        while not self.stopping:
            try:
                await self._sync_shuffle()
                await self._sync_music_source()
                self.music_source_ready = await self.engine.command("music.source_ready") == "true"
                await self._forget_dropped_fresh()
                if self.energy_queued and (self.ytmusic_active or not plugins.on(self.cfg, 'energy_wave')):
                    await self._clear_energy()
                await self._gap_tick()
            except Exception as e:
                log("gap filler:", type(e).__name__)
            await asyncio.sleep(1)

    def _stop_hook_worker(self):
        if self.hook_proc and self.hook_proc.returncode is None:
            try:
                os.killpg(self.hook_proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    async def hook_loop(self):
        while not self.stopping:
            try:
                if plugins.on(self.cfg, 'track_previews'):
                    paths = [(x['ref'], x['path']) for x in self.hook_snapshot]
                    paths += [(str(p), str(p)) for p in self._local_files()]
                    for ref, path in paths:
                        if not Path(path).is_file() or self.hook_retry.get((ref,path), 0) > time.time():
                            continue
                        row = self.db.execute('SELECT * FROM track_hooks WHERE ref=? AND source_path=? ORDER BY id DESC LIMIT 1', (ref,path)).fetchone()
                        if row and row['source_sig'] == hooks.signature(path) and row['retry_after'] > time.time() and not hooks.missing_clip(row):
                            continue
                        self.hook_proc = await asyncio.create_subprocess_exec('taskpolicy','-b','nice','-n','20',sys.executable, '-m', 'radio.hooks', '--ref', ref, '--path', path,
                            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
                        try:
                            await asyncio.wait_for(self.hook_proc.wait(), 1500)
                        except asyncio.TimeoutError:
                            self._stop_hook_worker()
                            await self.hook_proc.wait()
                        if self.hook_proc.returncode:
                            self.hook_retry[(ref,path)] = time.time() + 1800
                            store.log_event(self.db, self.session, 'HOOK_ANALYSIS_ERROR', ref)
                        self.hook_proc = None
                        break
            except Exception as error:
                log('hooks:', type(error).__name__)
            await asyncio.sleep(30)

    async def _cancel_preview(self):
        if not self.preview_job:
            return
        job = self.preview_job
        await self.engine.command('previews.cancel ' + job['id'])
        try:
            if job.get('rid'):
                await self.engine.remove(job['rid'])
        finally:
            self.preview_job = None
            self.db.execute("UPDATE music_previews SET status='CANCELLED',rid=NULL WHERE id=? AND status='QUEUED'", (job['id'],))
            self.id_pending = False

    async def preview_loop(self):
        while not self.stopping:
            try:
                await self._preview_tick()
            except Exception as error:
                self.preview_retry = time.time() + 60
                log('coming up:', type(error).__name__)
            await asyncio.sleep(2)

    async def _preview_tick(self):
        enabled = plugins.on(self.cfg, 'track_previews')
        if enabled != self.preview_enabled:
            if await self.engine.command('previews.enable ' + ('1' if enabled else '0')) != 'OK':
                return
            self.preview_enabled = enabled
        if not enabled:
            await self._cancel_preview()
            self._stop_hook_worker()
            return
        if self.preview_job:
            snapshot = json.loads(await self.engine.command('previews.next'))
            if (self.state != 'PLAYING' or not self.current or self.current['kind'] != 'news'
                    or self.current['ref'] != self.preview_job['after_news'] or self.skip_flag
                    or snapshot != json.loads(self.preview_job['snapshot'])
                    or self.preview_job.get('failed') or self.reminders_due or self.replays or time.time() > self.preview_job['expires']):
                await self._cancel_preview()
            return
        current = self.current
        if (self.state != 'PLAYING' or not current or current['kind'] != 'news' or self.manual_switch
                or self.manual_skip_pending or self.id_pending or self.queued_break or self.rerolling
                or self.skip_flag or current['ref'].startswith(('system:music-link:', 'system:no-news:'))
                or self.rendering or self.assembling or self.reminders_due or self.replays or self.stopping):
            return
        remaining = current.get('duration', 0) - (time.time() - current['started'])
        if remaining < 15 or time.time() < self.preview_retry:
            return
        snapshot = json.loads(await self.engine.command('previews.prepare'))
        if len(snapshot) != 2:
            return
        self.hook_snapshot = snapshot
        refs = [x['ref'] for x in snapshot]
        titles = [music.label(self.db, ref) for ref in refs]
        generation = self.schedule_generation
        rendered = await asyncio.to_thread(previews.render, self.db, snapshot, titles, self._say)
        self.preview_retry = time.time() + 60
        if not rendered:
            return
        async with self.handoff_lock:
            if (generation != self.schedule_generation or current is not self.current or self.state != 'PLAYING'
                    or self.stopping or self.id_pending or self.queued_break or self.reminders_due or self.replays
                    or self.skip_flag or self.manual_switch or self.manual_skip_pending
                    or not plugins.on(self.cfg, 'track_previews') or self.rerolling
                    or store.spoken_last_hour(self.db) + rendered['speech_sec'] > self.cfg['broadcast']['max_spoken_seconds_per_rolling_hour']):
                return
            job = {**rendered, 'id': uuid.uuid4().hex, 'refs': json.dumps(rendered['announced_refs']),
                   'titles': json.dumps(rendered['announced_titles'], ensure_ascii=False), 'after_news': current['ref'],
                   'snapshot':json.dumps(snapshot,ensure_ascii=False),
                   'expires': time.time() + max(0, current['duration'] - (time.time()-current['started'])) + 15}
            if json.loads(await self.engine.command('previews.next')) != snapshot:
                return
            self.db.execute("INSERT INTO music_previews(id,refs,titles,audio_path,speech_sec,duration_sec,status) VALUES(?,?,?,?,?,?,'QUEUED')",
                tuple(job[k] for k in ('id','refs','titles','audio_path','speech_sec','duration_sec')))
            try:
                rid = await self.engine.command('previews.push ' + json.dumps({'refs': refs, 'files':snapshot, 'uri': previews.uri(job)}, ensure_ascii=False))
            except (OSError,asyncio.TimeoutError,asyncio.IncompleteReadError):
                self.preview_job, self.id_pending = {**job,'failed':True}, True
                raise
            if not rid.isdigit():
                self.db.execute("UPDATE music_previews SET status='CANCELLED' WHERE id=?", (job['id'],))
                return
            job['rid'] = rid
            self.db.execute('UPDATE music_previews SET rid=? WHERE id=?', (rid,job['id']))
            if not self.current or self.current.get('ref') != job['id']:
                self.preview_job, self.id_pending = job, True

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
        self.gap_rid = await self.engine.push_replay(wav, "gap", text, gain, "", ',liq_cross_end_duration="1.5"')
        self.gap_said, self.id_pending = time.time(), True
        log("gap filler queued: the next YouTube Music song is not loaded, a local one follows")

    async def watch_engine(self) -> None:
        restarts, started = 0, time.time()
        while not self.stopping:
            await self.engine.proc.wait()
            if time.time() - started > 600:
                restarts = 0  # only a crash soon after a restart means the engine cannot run; one days later does not
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
            self.energy_seconds = self._energy_elapsed()
            self.current, self.id_pending = None, False
            await self.engine.start(self.on_event)
            started = time.time()
            self.state = "PLAYING"

    def _reset_inflight(self) -> None:
        self.queued_break = self.queued_clock = self.gap_rid = None
        self.id_pending = False
        self.fresh_queued.clear()
        self.energy_queued.clear()
        self.fresh_missing.clear()
        self.replays.clear()
        self.next_track = ''
        self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE status IN ('QUEUED','PREPARING')")
        self.announced_tracks = []
        self.db.execute("UPDATE music_previews SET status='CANCELLED',rid=NULL WHERE status='QUEUED'")
        self.db.execute("UPDATE music_previews SET status='PLAYED',rid=NULL WHERE status='PLAYING'")
        self.preview_job, self.preview_enabled = None, False
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
        self.next_cache_cleanup = time.time() + 3600

    # ---- playback events from Liquidsoap ----
    def on_event(self, m: dict) -> None:
        if m.get('kind') == 'health':
            if m.get('ref') == 'silence':
                self.last_error = 'Audio output has been silent for 20 seconds'
                store.log_event(self.db, self.session, 'AUDIO_SILENCE')
            elif self.last_error == 'Audio output has been silent for 20 seconds':
                self.last_error = ''
            return
        self.manual_skip_pending = False
        prev = self.current
        self.energy_seconds = self._energy_elapsed()
        if prev and prev["kind"] == "news":
            status = "SKIPPED" if self.skip_flag else "PLAYED"
            if prev.get('clock_id'):
                store.log_event(self.db, self.session, 'CLOCK_INTERRUPTED' if self.skip_flag else 'CLOCK_COMPLETED', prev['clock_id'])
                if self.skip_flag:
                    self.db.execute("UPDATE air_plan SET status='INTERRUPTED' WHERE id=?", (prev['clock_id'],))
            for pid in prev["ref"].split(BREAK_SEP):
                cur = self.db.execute("UPDATE packages SET status=?, played_at=? WHERE id=? AND status='PLAYING'",
                                      (status, iso(now()), pid))
                if cur.rowcount:
                    if status == 'PLAYED':
                        catchup.complete(self.db,pid,self.session)
                    store.log_event(self.db, self.session,
                                    "PACKAGE_COMPLETED" if status == "PLAYED" else "PACKAGE_SKIPPED", pid)
                    item = self.db.execute("SELECT item_id FROM packages WHERE id=?", (pid,)).fetchone()
                    content.finish_item_if_done(self.db, item["item_id"])
        self.skip_flag = False
        if prev and prev['kind'] == 'teaser':
            self.db.execute("UPDATE music_previews SET status='PLAYED',played_at=? WHERE id=? AND status='PLAYING'", (iso(now()),prev['ref']))
        self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (m.get("replay_id") or "",))
        self.replays.pop(m.get("replay_id") or "", None)  # on air now: no longer queued
        self.fresh_queued.pop(m.get("ref") or "", None)
        self.energy_queued.discard(m.get('ref') or '')
        self.next_track = ""
        played = self.resume_offset.pop(m.get("ref") or "", 0.0)
        self.resume_offset.clear()  # only the first track can be the resumed song
        self.current = {"kind": m.get("kind") or "music", "ref": m.get("ref") or m.get("filename", ""),
                        "started": time.time() - played, "transition_sec": self.next_transition_sec
                        if self.next_transition_sec is not None else self.cfg['music']['crossfade_sec']}
        if prev and prev['kind'] == 'music' and self.current['kind'] == 'music':
            old_source = 'ytmusic' if prev['ref'].startswith('ytmusic:') else 'local'
            new_source = 'ytmusic' if self.current['ref'].startswith('ytmusic:') else 'local'
            self.source_overlap = {'from': old_source, 'until': time.time() + self.current['transition_sec']} if old_source != new_source else None
        else:
            self.source_overlap = None
        self.next_transition_sec = None
        if self.current["kind"] == "news":
            self.current['duration'] = audio.duration(Path(m['filename'])) if m.get('filename') else 0
            if self.queued_clock:
                self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (self.queued_clock['id'],))
                store.log_event(self.db, self.session, 'CLOCK_STARTED', self.queued_clock['id'])
                self.current['clock_id'] = self.queued_clock['id']
                self.queued_clock = None
            if plugins.on(self.cfg, 'host_links'):
                store.log_event(self.db, self.session, 'STATION_BRANDING', self.current['ref'])
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
        elif self.current['kind'] == 'teaser':
            job = self.db.execute('SELECT * FROM music_previews WHERE id=?', (self.current['ref'],)).fetchone()
            self.preview_job, self.id_pending = None, False
            if job:
                self.announced_tracks = json.loads(job['refs'])
                self.current['duration'] = job['duration_sec']
                self.db.execute("UPDATE music_previews SET status='PLAYING',played_at=? WHERE id=?", (iso(now()),job['id']))
                store.log_event(self.db, self.session, 'MUSIC_TEASER', job['id'], ' · '.join(json.loads(job['titles'])))
        elif self.current["kind"] == "gap":
            self.gap_rid = None
            self.id_pending = False
            store.log_event(self.db, self.session, "GAP", "", self.current["ref"])
            log("gap filler on air")
        elif self.current["kind"] == "id":
            if self.queued_clock:
                self.db.execute("UPDATE air_plan SET status='PLAYED',rid=NULL WHERE id=?", (self.queued_clock['id'],))
                self.queued_clock = self.queued_break = None
            self.songs_since_break, self.id_pending = 0, False
            store.log_event(self.db, self.session, "STATION_ID", self.current["ref"])
            log("station id:", self.current["ref"])
        else:
            if self.current['ref'] in self.announced_tracks:
                self.announced_tracks.remove(self.current['ref'])
            self.songs_since_break += 1
            self.set_started = self.set_started or self.current["started"]
            if s := music.song(self.db, self.current["ref"]):
                self.set_songs.append(s)
            self.current["duration"] = self._song_seconds(self.current["ref"])
            store.log_event(self.db, self.session, "MUSIC_STARTED", self.current["ref"])
            if self.current['kind'] == 'music':
                if not self.current['ref'].startswith('ytmusic:'):
                    self.energy_played.add(self.current['ref'])
                self.energy_previous = self.current['ref']
            log("music:", Path(self.current["ref"]).name)
        self.wake.set()

    @staticmethod
    def _song_seconds(ref: str) -> float:
        return ytmusic.duration(ref) if ref.startswith("ytmusic:") else audio.duration(Path(ref))

    # ---- content pipeline ----
    async def content_loop(self) -> None:
        while not self.stopping:
            try:
                if time.time() >= self.next_cache_cleanup and not self.rendering and not self.assembling:
                    await asyncio.to_thread(self._cleanup_cache)
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
        next_fetch = {}
        while not self.stopping:
            on = {n for n in ("github_trending", "github_new") if plugins.on(self.cfg, n)}
            prepare = self._news_preparation_due(TEXT_LEAD_SEC)
            for n in ("github_trending", "github_new"):
                if n not in on:
                    self.sources[n] = "off"
            for name in ('github_trending', 'github_new'):
                if name not in on:
                    next_fetch.pop(name, None)
                elif prepare and self.state == 'PLAYING' and time.time() >= next_fetch.get(name, 0):
                    next_fetch[name] = time.time() + await self._fetch_github(name == 'github_trending', name == 'github_new')
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
                    new = await asyncio.to_thread(github_new.fetch_new, min(gn["created_within_days"],self.cfg['sources']['max_age_hours']/24))
                    self.sources["github_new"] = f"ok {time.strftime('%H:%M')}"
                except Exception as e:
                    new_ok = False
                    self.sources["github_new"] = f"error: {e}"
            result = await asyncio.to_thread(self._take_github, main, langs, new)
            healthy = new_ok and not result.startswith('degraded')
            if not new_ok:
                result = f"degraded: new repositories fetch failed; {result}"
            self.sources[key] = f"ok {time.strftime('%H:%M')}: {result}" if healthy else result
            if healthy:
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
                    self.sources["horizon"] = result if result.startswith('degraded') else f"ok {time.strftime('%H:%M')}: {result}"
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

    async def public_news_loop(self) -> None:
        while not self.stopping:
            await self.public_news_tick()
            try:
                await asyncio.wait_for(self.done.wait(), 15)
            except asyncio.TimeoutError:
                pass

    async def public_news_tick(self) -> None:
        self._live_settings()
        for source, key, adapter in (("reddit", "communities", reddit), ("telegram", "channels", telegram)):
            if not plugins.on(self.cfg, source):
                self.sources[source] = "off"
                self.public_news_due.pop(source, None)
                continue
            selected = (tuple(self.cfg["sources"][source][key]), tuple(self.cfg["sources"][source]["posts"]))
            if self.public_news_selection.get(source) != selected:
                self.public_news_due.pop(source, None)
                self.public_news_selection[source] = selected
            if time.time() < self.public_news_due.get(source, 0) or not self._news_preparation_due(TEXT_LEAD_SEC):
                continue
            # Fixed gentle cadence, including on errors; no rapid retries or fallback scraping.
            self.public_news_due[source] = time.time() + 1800
            added, errors = [], []
            targets = [(name, adapter.fetch) for name in public_news.names(list(selected[0]), source)]
            targets += [(url, adapter.fetch_post) for url in public_news.references(list(selected[1]), source, "posts")]
            for name, fetch in targets:
                if self.stopping or not plugins.on(self.cfg, source):
                    break
                try:
                    items = await asyncio.to_thread(fetch, name)
                    self._live_settings()
                    if self.stopping or not plugins.on(self.cfg, source):
                        break
                    if selected != (tuple(self.cfg["sources"][source][key]), tuple(self.cfg["sources"][source]["posts"])):
                        self.public_news_due.pop(source, None)
                        break
                    ids = public_news.take(self.db, self.cfg, items)
                    for iid in ids:
                        store.log_event(self.db, self.session, "ITEM_COLLECTED", iid)
                    added.extend(ids)
                except Exception as e:
                    errors.append(f"{name}: {type(e).__name__}: {str(e)[:100]}")
            self.sources[source] = f"{len(added)} new" + ("; error: " + "; ".join(errors) if errors else "")
            self.wake.set()

    def _take_horizon(self, items: list[dict]) -> str:
        """Best-ranked new items of a Horizon briefing, each its own bulletin, retold in Russian by the local model;
        a failed retelling passes its slot to the next item."""
        hz = self.cfg["sources"]["horizon"]
        added, tries, failures = [], 0, 0
        for it in items:
            if len(added) >= hz["picks"] or tries >= hz["picks"] + 3:
                break
            iid = horizon.item_id(it)
            if self.db.execute("SELECT 1 FROM items WHERE id=?", (iid,)).fetchone():
                continue
            tries += 1
            try:
                text = self.editor.retell_news(it["title"], it["summary"])
            except Exception as e:  # one failed retelling (timeout, 429) must not lose the rest of the briefing
                failures += 1
                log("horizon retelling:", type(e).__name__)
                continue
            if not text:
                continue
            origin = [{"repo": it["title"], "url": it["url"], "description": it["summary"], "score": it["score"]}]
            if it["discussion"]:
                origin.append({"repo": "обсуждение", "url": it["discussion"], "description": ""})
            content.add_item(self.db, self.cfg, {
                "id": iid, "topic": "Новости технологий", "source": "horizon", "url": it["url"],
                "speak_topic": not self.cfg["jingles"]["horizon_intro"],  # the section intro names it
                "published_at": now(), "body": text, "origin": origin, "text_prepared": True}, kind="raw_post")
            store.log_event(self.db, self.session, "ITEM_COLLECTED", iid)
            added.append(it["title"][:50])
        if added:
            log("horizon:", "; ".join(added))
        result = f"{len(added)} new" if added else "nothing new"
        return f"degraded: {failures} retellings failed; {result}" if failures else result

    def _take_github(self, main: list[dict], langs: list[dict], new: list[dict]) -> str:
        """Hourly release candidates from trending pages, plus newly created projects.
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
                  [t for t, _ in github_trending.select_mix(main, set(), len(main), 0, 0, blocked)]),
                 ("indie", "github_trending", gt["indie_picks"],
                  [t for t, _ in github_trending.select_mix(pool, set(), 0, len(pool), gt["indie_max_total_stars"],
                                                            blocked)]),
                 ("new", "github_new", gn["picks"],
                  [t for t in new if github_trending.repo_id(t) not in seen and github_trending.airable(t, blocked)])]
        added, taken, failures = [], set(), 0
        releases = {}
        for role, source, quota, candidates in roles:
            got = tries = 0
            for t in candidates:
                if got >= quota or tries >= quota + 3:
                    break
                tries += 1
                rid = github_trending.repo_id(t)
                release = None
                if role != 'new':
                    if rid not in releases:
                        releases[rid] = github_trending.fetch_release(t,now(),self.cfg['sources']['max_age_hours'])
                    release = releases[rid]
                    if not release:
                        continue
                    iid, published = f"{rid}:release:{release['id']}", content.parse_time(release['published_at'])
                else:
                    iid, published = rid, content.parse_time(t.get('created_at'))
                    if not content.is_fresh(published,self.cfg['sources']['max_age_hours']):
                        continue
                if iid in taken or self.db.execute('SELECT 1 FROM items WHERE id=?',(iid,)).fetchone():
                    continue
                # Not stored on failure: the next hourly fetch may try it again while it is still listed.
                # The README opening gives the retelling something to say beyond the one-line description.
                readme = github_trending.fetch_readme(t["owner"], t["repo"]) if not release else ''
                try:
                    told = (self.editor.retell_news(t['repo']+' — '+(release.get('name') or release.get('tag_name') or ''),
                                                   release['body'][:6000]) if release else
                            self.editor.retell(t["repo"], t["description"], readme))
                except Exception as e:  # one failed retelling must not lose the other picks of this hour
                    failures += 1
                    log("github retelling:", type(e).__name__)
                    continue
                item = github_trending.to_item(t, told, 'release' if release else role, gt["daily_min"], source)
                if item is None:
                    continue
                item['id'], item["published_at"] = iid, published
                if release:
                    item['url'] = f"https://github.com/{t['owner']}/{t['repo']}/releases"
                    item['origin'][0].update(url=item['url'],description=release['body'][:6000],release=release.get('tag_name'))
                item['origin'][0].update(published_at=iso(published),event_type='release' if release else 'created')
                item["text_prepared"] = True
                item["speak_topic"] = not self.cfg["jingles"]["github_intro"]  # the section intro names it
                content.add_item(self.db, self.cfg, item, kind="raw_post")
                store.log_event(self.db, self.session, "ITEM_COLLECTED", item["id"])
                taken.add(item["id"])
                added.append(f"{role} {t['owner']}/{t['repo']}")
                got += 1
        if added:
            log("github:", ", ".join(added))
        result = ", ".join(added) if added else "nothing new"
        return f"degraded: {failures} retellings failed; {result}" if failures else result

    async def _maybe_music_link(self) -> None:
        self._live_settings()
        if (not plugins.on(self.cfg, "music_facts") or self.state != "PLAYING"
                or not self.current or self.current["kind"] != "music" or not self._news_preparation_due(TEXT_LEAD_SEC)):
            return
        pending = ("SELECT i.source FROM packages p JOIN items i ON i.id=p.item_id "
                   "WHERE p.status IN ('PENDING','READY','QUEUED','PLAYING') AND i.state='NEW' AND i.source='music_facts'")
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
        try:
            for item_id in content.scan_inbox(self.db, self.cfg):
                store.log_event(self.db, self.session, "ITEM_COLLECTED", item_id)
                log("new digest:", item_id)
            self.sources.pop('inbox', None)
        except Exception as e:
            self.sources['inbox'] = f"error: {type(e).__name__}"
            log('inbox:', type(e).__name__)
        content.expire_stale(self.db, self.cfg)
        for row in self.db.execute("SELECT DISTINCT i.id FROM items i JOIN packages p ON p.item_id=i.id WHERE i.source='catchup' "
                                   "AND p.status IN ('PENDING','READY') AND EXISTS(SELECT 1 FROM packages bad WHERE bad.item_id=i.id AND bad.status IN ('FAILED','SKIPPED'))").fetchall():
            self.db.execute("UPDATE packages SET status='SKIPPED' WHERE item_id=? AND status IN ('PENDING','READY')",(row['id'],))
            self.catchup_error = 'Catch-up was cancelled or audio failed; prepare it again.'
        try:
            await self._maybe_music_link()
        except Exception as e:
            self.sources['music_facts'] = f"error: {type(e).__name__}"
            log('music stories:', type(e).__name__)
        await self.schedule()
        if self.rendering or time.time() < self.tts_retry_at:
            return
        changes = direction.edits(self.db) if plugins.on(self.cfg, 'air_direction') else {}
        voice_lead = max(VOICE_LEAD_SEC, VOICE_LEAD_SEC * self.cfg['broadcast']['max_break_speech_sec'] / 120)
        deadline = now() + timedelta(seconds=voice_lead)
        news_due = self._news_preparation_due(voice_lead)
        off = self._sources_off()
        reserved = catchup.reserved(self.db)
        slot = next((r for r in self._clock_rows() if not r['skipped']), None)
        air = max(now(), content.parse_time(slot['at'])) if slot else now()
        cutoff = iso(air - timedelta(hours=self.cfg['sources']['max_age_hours']))
        candidates = [r for r in direction.news(self.db) if r['source'] not in off and r['item_id'] not in reserved
                      and direction.allowed(r, changes, deadline)
                      and (r['source'] == 'direction' or changes.get(r['id'], {}).get('not_before')
                           or (r['published_at'] or '') >= cutoff)
                      and (news_due or r['source']=='catchup' or self._host_segment(r) and r['item_id'].removeprefix('system:') in self.reminders_due)]
        ready_speech = sum(r['speech_sec'] or r['est_speech_sec'] or 0 for r in candidates if r['status'] == 'READY')
        pending = [r for r in candidates if r['status'] == 'PENDING']
        p = next((r for r in pending if r['source']=='catchup' or self._host_segment(r) or r['item_id'].startswith('system:direction:')), None)
        if p is None and ready_speech < self._edition_speech_budget():
            p = next(iter(pending), None)
        if p:
            self.rendering = True
            asyncio.create_task(self._render_task(p))

    def _news_preparation_due(self, lead: float) -> bool:
        if self.state != 'PLAYING':
            return False
        deadline = now() + timedelta(seconds=lead)
        return any(not r['skipped'] and r['status'] not in ('QUEUED', 'PREPARING') and
                   content.parse_time(r['at']) <= deadline for r in self._clock_rows())

    def _live_settings(self) -> None:
        settings.apply(self.cfg, self.defaults)
        if not self.mock_voice and self.cfg["voice"]["engine"] != self.voice_engine:
            # The voice engine switched on the Settings tab: new phrases use it; voice_loop starts its server
            self.voice_engine = self.cfg["voice"]["engine"]
            self.tts, self.tts2 = tts_mod.make_voice(self.cfg), tts_mod.make_voice(self.cfg, "cohost")
            self.voice_started = 0.0
            log("voice engine:", self.voice_engine)

    def _waiting_break(self, remaining):
        pkgs = self._pick_break()
        due = self._break_due(remaining)
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
        overdue = self.db.execute("SELECT * FROM air_plan WHERE status IN ('PENDING','QUEUED','PREPARING') AND "
                                  "((kind='news' AND not_before<=?) OR (kind NOT IN ('news','reminder') AND not_before<=?))",
                                  (iso(now() - broadcast_clock.WINDOW),iso(now() - broadcast_clock.INSERT_WINDOW))).fetchall()
        for row in overdue:
            if row['status'] == 'QUEUED' and row['rid']:
                await self._cancel_direction_job(row)
            self.db.execute("UPDATE air_plan SET status='EXPIRED',rid=NULL WHERE id=?", (row['id'],))
        if self.queued_clock and now() >= content.parse_time(self.queued_clock['ends']):
            await self._cancel_automatic('EXPIRED')
        if self.manual_switch or self.manual_skip_pending:
            return
        if self.current is None:
            return  # Play starts with music: nothing is queued before the first song is on air
        if plugins.on(self.cfg, 'air_direction') and self.db.execute("SELECT 1 FROM air_plan WHERE status='QUEUED'").fetchone():
            return
        if self.state != "PLAYING" or self.id_pending or self.rerolling or self.db.execute(
                "SELECT 1 FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone():
            return  # one insert (package or station ID) in the engine at a time
        if self.current['kind'] != 'music':
            return
        remaining = 10.0
        if self.current["kind"] == "music" and self.current.get("duration"):
            remaining = self.current["duration"] - (time.time() - self.current["started"])
        if not immediate and plugins.on(self.cfg, "air_direction") and await self._direction_tick():
            return
        if not immediate and self.reminders_due and await self._remind(remaining):
            return
        slot = self._clock_slot(now() + timedelta(seconds=max(0, remaining)))
        current = self.current
        if not immediate and not self.fresh_queued and self._new_set_due():
            new = await asyncio.to_thread(self._pick_new)
            async with self.handoff_lock:
                if (generation != self.schedule_generation or self.current is not current
                        or self.state != 'PLAYING' or self.manual_switch or self.manual_skip_pending):
                    return
                if new:
                    await self._queue_fresh(new)
        if (not immediate and not self.fresh_queued and not self.ytmusic_active
                and plugins.on(self.cfg, 'energy_wave')):
            wave = await asyncio.to_thread(self._pick_wave)
            async with self.handoff_lock:
                if (generation != self.schedule_generation or self.current is not current
                        or self.state != 'PLAYING' or self.manual_switch or self.manual_skip_pending):
                    return
                if wave and plugins.on(self.cfg, 'energy_wave'):
                    await self._queue_fresh(wave, wave=True)
        pkgs = self._waiting_break(remaining)
        if not slot and not self._requested_edition():
            pkgs = []
        if immediate:
            remaining = 0.0
        if pkgs:
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
                nxt = next(iter(self.fresh_queued), '') or (await self.engine.next_music() or "") if links else ""
                first = music.song(self.db, nxt)
                # shortcut: a replay pushed after this point would play first and its intro is not planned for;
                # with replays already waiting the next song is not known, so no intro talk then.
                room = self.intro_rooms.get(nxt) if not self.replays else None
                air = now() + timedelta(seconds=max(0.0, remaining))
                self.assembling = True
                try:
                    done = await asyncio.to_thread(self._assemble_break, [dict(p) for p in pkgs], air, links, first, room,
                                                   bool(set(self.fresh_queued) - self.energy_queued))
                finally:
                    self.assembling = False
                if done:
                    wav, talkover = done
                else:
                    pkgs = pkgs[:1]  # fallback: the first bulletin, pre-rendered
            if generation != self.schedule_generation or self.current is not current:
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
                        notice['kind'] == 'weather' and (not self._weather_enabled() or air >= notice['expires']) or
                        notice['kind'] == 'day' and (not (day := self._day_due(air.astimezone())) or day['key'] != key)):
                    return
            if slot and not (content.parse_time(slot['at']) <= now() + timedelta(seconds=max(0, remaining)) < content.parse_time(slot['ends'])):
                return
            ids = [p["id"] for p in pkgs]
            async with self.handoff_lock:
                if generation != self.schedule_generation or self.manual_switch or self.manual_skip_pending:
                    return
                rid = await self.engine.push_news(wav, BREAK_SEP.join(ids), talkover, expires=content.parse_time(slot['ends']).timestamp() if slot else 0)
                if generation != self.schedule_generation or self.current is not current or self.state != 'PLAYING':
                    await self.engine.remove(rid)
                    return
                self.queued_break = (rid, ids)
                if slot:
                    self.queued_clock = slot
                    self.db.execute("INSERT INTO air_plan(id,kind,not_before,status,rid) VALUES(?,'news',?,'QUEUED',?) ON CONFLICT(id) DO UPDATE SET status='QUEUED',rid=excluded.rid", (slot['id'], slot['at'], rid))
                for p in pkgs:
                    if self._host_segment(p):
                        self.db.execute("INSERT INTO air_plan(id,kind,ref,status,rid) VALUES(?,? ,?,'QUEUED',?) ON CONFLICT(id) DO UPDATE SET status='QUEUED',rid=excluded.rid,ref=excluded.ref",
                                        (p['item_id'].removeprefix('system:'), 'weather' if self._host_segment(p) == 'weather' else 'news', p['id'], rid))
                self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=? WHERE id LIKE 'section:news:%' AND skipped=0 AND status IN ('PENDING','ERROR') AND not_before<=?",
                                (rid, ids[0], iso(now() + timedelta(seconds=max(0, remaining)))))
                self.db.execute("UPDATE air_plan SET status='QUEUED',rid=?,ref=? WHERE id LIKE 'direction:%' AND kind='news' AND skipped=0 AND status='PENDING' AND not_before<=?", (rid, ids[0], iso(now())))
                self.db.executemany("UPDATE packages SET status='QUEUED' WHERE id=?", [(i,) for i in ids])
                log("break queued for next song boundary:", ", ".join(ids))
                return

        if slot and remaining <= 10 and plugins.on(self.cfg, 'station_ids'):
            branded = self.db.execute("SELECT MAX(at) FROM events WHERE type IN ('STATION_ID','STATION_BRANDING')").fetchone()[0]
            if branded and now() - content.parse_time(branded) < timedelta(minutes=30):
                return
            f = audio.choose_jingles({'station_id': self.cfg['jingles']['station_id']}, slot['id'], self.recent_jingles)['station_id']
            gain = -16 - (await asyncio.to_thread(audio.lufs, f) or -16)
            _, end = await asyncio.to_thread(audio.active_span, f)
            if generation != self.schedule_generation or self.current is not current or now() >= content.parse_time(slot['ends']):
                return
            async with self.handoff_lock:
                if generation != self.schedule_generation or self.manual_switch or self.manual_skip_pending:
                    return
                rid = await self.engine.push_station_id(f, gain, audio.duration(f) - end, expires=content.parse_time(slot['ends']).timestamp())
                self.queued_break, self.queued_clock, self.id_pending = (rid, []), slot, True
                self.db.execute("INSERT INTO air_plan(id,kind,not_before,status,rid) VALUES(?,'news',?,'QUEUED',?) ON CONFLICT(id) DO UPDATE SET status='QUEUED',rid=excluded.rid", (slot['id'], slot['at'], rid))

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
            p = self._prepare_package(p)
        except Exception as e:
            # Provider outages and the shared hourly limit must not spend the TTS retry budget.
            self.db.execute("UPDATE packages SET error=? WHERE id=? AND status='PENDING'", (str(e)[:500], p['id']))
            self.tts_retry_at = time.time() + 60
            raise
        if p is None:
            return
        try:
            voices, sounds = [], []
            for segment, sound in self._body_segments(p, None):
                sounds.append(sound)
                voices.append(self._segment_voice(p["id"], segment))
            speech = sum(audio.duration(v) for v in voices)
            out = config.DATA_DIR / "cache/packages" / (hashlib.sha256(p["id"].encode()).hexdigest()[:16] + ".wav")
            jingles = self._jingles_for(p["id"])
            self.pkg_jingles[p["id"]] = jingles
            if p["id"].startswith("system:music-link:"):
                # Said over the end of the song that just played; its jingle only once the host has finished
                audio.render_link(voices, out, jingles["stinger_out"], jingles["stinger_out_max_sec"])
                talkover = 0.0
            else:
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

    def _prepare_package(self, p):
        row = self.db.execute('SELECT p.prepared_text,i.kind,i.topic FROM packages p JOIN items i ON i.id=p.item_id '
                              'WHERE p.id=?', (p['id'],)).fetchone()
        if row is None:
            raise ValueError('Unknown speech package')
        text = row['prepared_text']
        if text is None:
            original = Path(p['script_path']).read_text(encoding='utf-8')
            policy = 'news' if row['kind'] in ('raw_post', 'ready_digest') else 'announcement'
            if policy == 'news' and not self.editor.enabled:
                raise RuntimeError('News editor disabled; source withheld')
            text = self.editor.prepare(original, policy, row['topic'] or '')
            if not text:
                self.db.execute("UPDATE packages SET status='SKIPPED',error='Editorial rejection' WHERE id=? AND status='PENDING'", (p['id'],))
                content.finish_item_if_done(self.db, p['item_id'])
                return None
            self.db.execute("UPDATE packages SET prepared_text=?,est_speech_sec=?,error=NULL WHERE id=? AND status='PENDING'",
                            (text, len(text) / self.cfg['voice']['chars_per_sec'], p['id']))
        return {**dict(p), 'prepared_text': text}

    def _body_segments(self, p: dict, prev: str | None) -> list[tuple[str, bool]]:
        """Voice segments of a package. A section's name is not in its script but said here: by its intro jingle
        when it opens the break (prev is None), else as a short phrase when it follows another section."""
        prepared = self.db.execute('SELECT p.prepared_text,i.kind FROM packages p JOIN items i ON i.id=p.item_id WHERE p.id=?', (p['id'],)).fetchone()
        if prepared and prepared['kind'] in ('raw_post', 'ready_digest') and prepared['prepared_text'] is None:
            raise ValueError('News text has not passed editorial preparation')
        script = prepared['prepared_text'] if prepared and prepared['prepared_text'] is not None else Path(p['script_path']).read_text(encoding='utf-8')
        segs = content.voice_segments(script)
        if prepared and prepared['kind'] in ('raw_post', 'ready_digest'):
            allow_reaction = editor.reactions_enabled(self.cfg)
            checked = []
            for text, sound in segs:
                text = editor.news_reaction(text, allow_reaction)
                allow_reaction = allow_reaction and not text.startswith(('[surprise-ah]', '[laughter]'))
                if text:
                    checked.append((text, sound))
            segs = checked
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
        self._live_settings()
        v, engine = self.cfg["voice"], self.voice_engine
        if self.voice_proc and self.voice_proc_engine != engine:
            await self._stop_voice()  # the other engine's server: free the GPU memory for this one
        if await asyncio.to_thread(self.tts.health):
            if self.last_error == "VoiceStudio unavailable, will retry later":
                self.last_error = ""
                self.tts_retry_at, self.tts_backoff = 0.0, 60.0
                self.wake.set()
            return
        port = int((v["mlx_audio_url"] if engine == "qwen3" else v["base_url"]).rsplit(":", 1)[-1])
        if (self.voice_proc and self.voice_proc.poll() is None) or await asyncio.to_thread(tts_mod.port_open, port) \
                or time.time() - self.voice_started < 60:
            return  # still loading, or somebody else is starting it
        self.voice_started = time.time()
        log_file = open(config.DATA_DIR / ("logs/mlx-audio.log" if engine == "qwen3" else "logs/voicestudio.log"), "a")
        if engine == "qwen3":
            self.voice_proc = await asyncio.to_thread(tts_mod.start_mlx_server, v["mlx_audio_dir"], port, log_file)
        else:
            self.voice_proc = await asyncio.to_thread(tts_mod.start_backend, v["server_dir"], port, log_file)
        self.voice_proc_engine = engine
        log(f"voice server for {engine} is not answering: started it (pid", self.voice_proc.pid, ")")

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
                    await self._sync_reminders('event' if name == 'calendar' else 'task')
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

    async def _sync_reminders(self, kind):
        fresh = {r['key']: r for r in self._reminder_candidates(now(), timedelta(hours=PLAN_HOURS)) if r['kind'] == kind}
        waiting = {**self.reminders_due, **{key: pair[1] for key, pair in self.queued_reminders.items()}}
        for key, old in waiting.items():
            if old['kind'] != kind:
                continue
            updated = fresh.get(key)
            if updated and all(old.get(field) == updated.get(field) for field in ('title', 'start', 'end', 'description', 'remind_at')):
                continue
            queued = self.queued_reminders.get(key)
            if queued:
                await self.engine.remove(queued[0])
                if self.queued_reminders.get(key) is not queued:
                    continue
                self.queued_reminders.pop(key)
                self.reminder_text.pop(key, None)
                self.db.execute("UPDATE air_plan SET status=?,rid=NULL WHERE id=? AND status='QUEUED'",
                                ('PENDING' if updated else 'EXPIRED', 'reminder:' + key))
                self.id_pending = bool(self.queued_reminders or self.queued_clock or self.gap_rid or self.preview_job)
            self.reminders_due.pop(key, None)
            if updated:
                self.reminders_due[key] = updated

    def _reminder_candidates(self, at, span):
        """Timed reminders from the last read of the calendar and Todoist: starting within `span` from `at`, or
        started and still making sense (reminders.until); not aired yet."""
        back = timedelta(hours=12)
        found = []
        if plugins.on(self.cfg, "reminders_calendar"):
            found += reminders.calendar_due(self.cal_events, at - back, timedelta(minutes=self.cfg["reminders"]["lead_min"]), horizon=back + span)
        if plugins.on(self.cfg, "reminders_todoist"):
            found += reminders.todoist_due(self.todoist_tasks, at - back, timedelta(minutes=self.cfg["reminders"]["lead_min"]), horizon=back + span)
        fired = {row[0] for row in self.db.execute("SELECT key FROM reminders WHERE key LIKE 'cal:%' OR key LIKE 'todoist:%'")}
        return [r for r in found if reminders.until(r) > at and r["key"] not in fired]

    def _day_due(self, at):
        if not plugins.on(self.cfg, "reminders_calendar") or not self.cfg["reminders"]["calendar_url"]:
            return None
        history = self.db.execute("SELECT key, fired_at FROM reminders WHERE key LIKE ?",
                                  (f"day:{at.date()}:%",)).fetchall()
        today = reminders.day_due(self.cal_events, at, history)
        if today or not plugins.on(self.cfg, 'holiday_previews') or not plugins.on(self.cfg, 'host_links'):
            return today
        previews = self.db.execute("SELECT key, fired_at FROM reminders WHERE key LIKE 'holiday:%'").fetchall()
        return reminders.holiday_due(self.cal_events, at, previews)

    def _weather_enabled(self) -> bool:
        return bool(self.cfg["weather"]["city"]) and plugins.on(self.cfg, "weather")

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
        elif (at - slot >= broadcast_clock.INSERT_WINDOW or self.db.execute(
                "SELECT 1 FROM reminders WHERE key=?", (key,)).fetchone()):
            return None
        if self.db.execute("SELECT 1 FROM air_plan WHERE id=? AND status='QUEUED'", (key,)).fetchone():
            return None
        if time.time() < self.next_weather_read:
            return None
        try:
            r = await asyncio.to_thread(weather.fetch, self.cfg["weather"]["city"], at)
            r["key"] = key
            due = content.parse_time(changes.get(key, {}).get('not_before')) if plugins.on(self.cfg, 'air_direction') else slot
            r['expires'] = min(r['expires'], (due or slot) + broadcast_clock.INSERT_WINDOW)
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
            if timed and r.get('remind_at') and now() < r['remind_at']:
                edit = direction.edits(self.db).get('reminder:' + key, {}) if plugins.on(self.cfg, 'air_direction') else {}
                if not edit.get('not_before') or content.parse_time(edit['not_before']) > now():
                    continue
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
                text = r.get("text") if r.get("holiday_preview") else ("Сегодня — {title}.", "К слову, сегодня — {title}.",
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
            if timed and self.reminders_due.get(key) is not r:
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
        text = self.editor.prepare(text, 'announcement')
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

        return cached_synthesize(voice or self.tts, self.editor.speech(text), check)

    def _render_weather(self, text: str) -> Path:
        text = self.editor.prepare(text, 'weather')
        expected = set(re.findall(r"[а-яё]{4,}", text.casefold()))

        def check(path):
            if error := self._speech_check(path):
                return error
            heard = set(re.findall(r"[а-яё]{4,}", self.tts.transcribe(path).casefold()))
            if len(expected & heard) < len(expected) * 0.6:
                return "weather speech does not match forecast"
            return None

        return cached_synthesize(self.tts, self.editor.speech(text), check)

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
        text = self.editor.prepare(text, 'reminder')
        try:
            voice = self._say(text)
        except RuntimeError:  # the voice cannot read this title in a longer phrase: the shortest form
            voice = self._say(fallback or text.removeprefix("Напоминание. "))
        out = config.DATA_DIR / "cache/packages" / ("reminder-" + hashlib.sha256(text.encode()).hexdigest()[:16] + ".wav")
        audio.render_notice(voice, self.cfg["jingles"]["reminder"][0], out)
        return out

    def _new_set_due(self) -> bool:
        b = self.cfg["broadcast"]
        return plugins.on(self.cfg, "new_tracks") and b["new_every_sets"] > 0

    def _local_files(self) -> list[Path]:
        self.files = [f for f in self.files if f.is_file()]
        return self.files

    def _pick_new(self) -> list[Path | str]:
        """Unplayed tracks of the active source, ahead of the shuffle; local files are newest first."""
        b = self.cfg["broadcast"]
        if self.ytmusic_active:
            aired = {r[0] for r in self.db.execute("SELECT DISTINCT ref FROM events WHERE type='MUSIC_STARTED'")}
            found = [f"ytmusic:{t['id']}" for t in ytmusic.library()['tracks'] if f"ytmusic:{t['id']}" not in aired]
        else:
            found = music.unaired_new(self.db, self._local_files())
        excluded = {(self.current or {}).get('ref', '')}
        excluded.update(r['ref'] for r in self.replays.values() if r['kind'] == 'music')
        found = [f for f in found if str(f) in self.intro_rooms and str(f) not in excluded]
        wave = not self.ytmusic_active and plugins.on(self.cfg, 'energy_wave')
        if wave:
            known = energy.profiles(found)
            wave = len(known) >= 2
            if wave:
                found = self._energy_order(found)
        picked, total = [], 0.0
        for f in found:
            picked.append(f)
            total += self._song_seconds(str(f))
            if (wave and len(picked) >= ENERGY_PREFETCH) or total >= b["music_set_minutes"] * 60:
                break
        return picked

    def _energy_elapsed(self):
        current = self.current or {}
        at = self.paused_at if self.state == 'PAUSED' else time.time()
        played = min(current.get('duration', 0), max(0, at - current.get('started', at))) if current.get('kind') == 'music' else 0
        return self.energy_seconds + played

    def _energy_order(self, files):
        known = energy.profiles(self.files)
        at = self.paused_at if self.state == 'PAUSED' else time.time()
        current = self.current or {}
        remaining = max(0, current.get('duration', 0) - max(0, at-current.get('started', at))) if current.get('kind') == 'music' else 0
        boundary = at + remaining
        voice = [content.parse_time(r['at']).timestamp() for r in self._clock_rows()
                 if not r['skipped'] and content.parse_time(r['ends']).timestamp() > boundary]
        voice += [content.parse_time(r[0]).timestamp() for r in self.db.execute(
            "SELECT not_before FROM air_plan WHERE kind IN ('news','weather','reminder','text') "
            "AND skipped=0 AND status IN ('PENDING','PREPARING','QUEUED') AND not_before>=?", (iso(now()),))]
        moment = datetime.fromtimestamp(at, timezone.utc)
        lead = timedelta(minutes=self.cfg['reminders']['lead_min'])
        voice += [due.timestamp() for r in self._reminder_candidates(moment, timedelta(hours=1))
                  if (due := r.get('remind_at', r['start']-lead)) >= moment]
        recent = {r[0]: 1/(i+1) for i, r in enumerate(self.db.execute(
            "SELECT ref,MAX(id) AS last FROM events WHERE type='MUSIC_STARTED' GROUP BY ref ORDER BY last DESC"))}
        return energy.order(files, known, self._energy_elapsed()+remaining,
                            known.get(self.energy_previous), recent,
                            min(voice)-boundary if voice else None)

    def _pick_wave(self):
        files = [f for f in self._local_files() if str(f) in self.intro_rooms]
        known = energy.profiles(files)
        if len(known) < 2:
            return []  # no usable analysis: keep ordinary playback
        current = (self.current or {}).get('ref')
        excluded = {current, *(r['ref'] for r in self.replays.values())}
        available = [f for f in files if str(f) not in excluded]
        remaining = [f for f in available if str(f) not in self.energy_played]
        if not remaining:
            self.energy_played.intersection_update(excluded)
            remaining = available
        return self._energy_order(remaining)[:ENERGY_PREFETCH]

    async def _queue_fresh(self, files: list[Path | str], wave=False) -> None:
        """Unplayed songs precede shuffle; news and manual inserts still take the next song boundary first."""
        m = self.cfg["music"]
        for f in files:
            row = self.db.execute("SELECT * FROM tracks WHERE path=?", (str(f),)).fetchone()
            gain = min(m["max_boost_db"], m["target_lufs"] - row["lufs"]) if row is not None and row["lufs"] is not None else 0.0
            tags, _ = music.song_tags(row, m["crossfade_sec"], True) if row is not None else ('', None)
            self.fresh_queued[str(f)] = await self.engine.push_replay(f, "music", str(f), gain, "", tags, "fresh")
            if wave:
                self.energy_queued.add(str(f))
        store.log_event(self.db, self.session, 'ENERGY_WAVE_QUEUED' if wave else 'NEW_TRACKS_QUEUED', detail=str(len(files)))
        log("energy-wave set queued:" if wave else "new-tracks set queued:", len(files), "songs")

    async def _forget_dropped_fresh(self) -> None:
        if not self.fresh_queued:
            self.fresh_missing.clear()
            return
        queued = dict(self.fresh_queued)
        pending = await asyncio.wait_for(self.engine.pending('fresh'), .5)
        missing = set(queued.values()) - set(pending)
        # A request can briefly disappear while Liquidsoap hands it to the decoder.
        for ref, rid in queued.items():
            if rid in missing & self.fresh_missing and self.fresh_queued.get(ref) == rid:
                del self.fresh_queued[ref]
                self.energy_queued.discard(ref)
        self.fresh_missing = missing

    async def _clear_fresh(self) -> None:
        for rid in list(self.fresh_queued.values()):
            await self.engine.remove(rid, 'fresh')
        self.fresh_queued = {}
        self.energy_queued.clear()
        self.fresh_missing.clear()

    async def _clear_energy(self):
        for ref in list(self.energy_queued):
            if rid := self.fresh_queued.get(ref):
                await self.engine.remove(rid, 'fresh')
                self.fresh_queued.pop(ref, None)
            self.energy_queued.discard(ref)

    def _news_skipped(self, at=None):
        at = at or now()
        return any(r['skipped'] and content.parse_time(r['at']) <= at < content.parse_time(r['ends']) for r in self._clock_rows())

    def _requested_edition(self):
        manual = self.db.execute("SELECT id FROM air_plan WHERE id LIKE 'section:news:catchup:%' AND status='PENDING' AND skipped=0 AND not_before<=?",(iso(now()),)).fetchone()
        if manual:
            return manual
        if not plugins.on(self.cfg, 'air_direction'):
            return None
        return self.db.execute("SELECT id FROM air_plan WHERE (id LIKE 'direction:%' OR id LIKE 'section:news:%') AND kind='news' AND skipped=0 AND status='PENDING' AND not_before<=? AND not_before>? ORDER BY not_before LIMIT 1", (iso(now()), iso(now() - broadcast_clock.WINDOW))).fetchone()

    def _catchup_status(self):
        active = catchup.active(self.db)
        state = 'preparing' if self.catchup_task else active['status'].lower() if active else 'error' if self.catchup_error else 'idle'
        return {'state':state,'available':len(catchup.candidates(self.db,now(),self._sources_off())),
                'error':self.catchup_error,'hours':48}

    async def _prepare_catchup(self):
        try:
            await asyncio.to_thread(self._build_catchup)
        except Exception as error:
            self.catchup_error = str(error) if isinstance(error,ValueError) else 'Could not prepare catch-up; try again later.'
            log('catch-up:',type(error).__name__)
        finally:
            self.catchup_task = None
            self.wake.set()

    def _build_catchup(self):
        selected = catchup.prepare(self.db,self.cfg,now(),self._sources_off())
        if self.stopping:
            return
        allowed = {r['id'] for r in catchup.candidates(self.db,now(),self._sources_off())}
        selected = [r for r in selected if r['id'] in allowed]
        if not selected:
            raise ValueError('No unplayed stories with usable source text are available for the last 48 hours.')
        stories = [{'title':r['topic'],'source':r['source'],'date':r['date_label'],
                    'date_kind':'selection' if r['source']=='horizon' else 'publication','text':r['source_text']} for r in selected]
        budget = max(200,int((self.cfg['broadcast']['max_break_speech_sec']-30)*self.cfg['voice']['chars_per_sec']*.7))
        zone = ZoneInfo(self.cfg['weather'].get('timezone','Asia/Ho_Chi_Minh'))
        body = self.editor.retell_roundup(stories,now().astimezone(zone).strftime('%Y-%m-%d'),budget)
        if not body:
            raise ValueError('Could not prepare a coherent catch-up from these sources; try again.')
        allowed = {r['id'] for r in catchup.candidates(self.db,now(),self._sources_off())}
        if self.stopping or any(r['id'] not in allowed for r in selected):
            raise ValueError('Catch-up sources changed during preparation; try again.')
        iid = 'catchup:'+uuid.uuid4().hex
        origins = [{'item_id':r['id'],'source':r['source'],'published_at':r['published_at'],
                    'repo':(r['topic'] or 'News')+' · '+r['date_label'],
                    'url':next((u for u in (r['url'] or '').split() if u.startswith(('https://','http://'))),''),
                    'description':'\n'.join(o.get('description','') for o in json.loads(r['origin_json'] or '[]')) or r['prepared'] or ''} for r in selected]
        with store.transaction(self.db):
            content.add_item(self.db,self.cfg,{'id':iid,'source':'catchup','topic':'What I missed · 48h',
                             'published_at':now(),'url':'','body':body,'origin':origins,'text_prepared':True,
                             'speak_topic':False},kind='announcement')
            self.db.execute("INSERT INTO air_plan(id,kind,text,not_before,status) VALUES(?,'news','What I missed · 48h',?,'PENDING')",
                            ('section:news:'+iid,iso(now())))

    def _break_due(self, remaining: float, directed=True) -> bool:
        air = now() + timedelta(seconds=max(0, remaining))
        if directed and self._requested_edition():
            return True
        return self._clock_slot(air) is not None

    def _edition_speech_budget(self):
        slot = self._clock_slot() or next(iter(self._clock_rows()), None)
        maximum = self.cfg['broadcast']['max_break_speech_sec']
        budget = min(maximum, (slot['duration'] if slot else maximum) - 12)
        return budget - (12 if plugins.on(self.cfg, 'host_links') else 0)

    def _pick_break(self, preview=False) -> list:
        """Ready bulletins for one break: in rotation order (the section aired longest ago opens the break), a
        section's bulletins together; up to max_break_speech_sec and what the rolling-hour limit still allows."""
        if not preview and self._news_skipped():
            return []
        b = self.cfg["broadcast"]
        budget = min(self._edition_speech_budget(),
                     b["max_spoken_seconds_per_rolling_hour"] - store.spoken_last_hour(self.db)
                     - (12 if plugins.on(self.cfg, 'host_links') else 0))
        ready = content.ready_to_air(self.db, self._sources_off())
        changes = direction.edits(self.db) if plugins.on(self.cfg, "air_direction") else {}
        if changes:
            candidates = direction.news(self.db)
            ready = direction.ordered([r for r in candidates if r['status'] == 'READY' and r['source'] not in self._sources_off()
                     and (r['item_kind'] not in ('raw_post', 'ready_digest') or r['prepared_text'] is not None)
                     and changes.get(r['id'], {}).get('status') != 'EXPIRED' and direction.allowed(r, changes, now()) and not any(
                         p['item_id'] == r['item_id'] and p['part'] < r['part'] and not changes.get(p['id'], {}).get('skipped')
                         for p in candidates)], changes)
        section = self.db.execute('SELECT * FROM air_plan WHERE id=?', (self._news_section_id(),)).fetchone() if plugins.on(self.cfg, 'air_direction') else None
        reserved = catchup.reserved(self.db)
        ready = [p for p in ready if p['item_id'] not in reserved]
        manual = catchup.active(self.db)
        if manual:
            parts = self.db.execute('SELECT * FROM packages WHERE item_id=? ORDER BY part',(manual['id'],)).fetchall()
            if all(p['status']=='READY' for p in parts):
                limit = min(b['max_break_speech_sec']-24,b['max_spoken_seconds_per_rolling_hour']-store.spoken_last_hour(self.db)-12)
                if sum(p['speech_sec'] or 0 for p in parts)<=limit:
                    return parts
            ready = [p for p in ready if p['item_id']!=manual['id']]
        ready = [p for p in ready if not self._host_segment(p) or
                 ((notice := self.reminders_due.get(p['item_id'].removeprefix('system:'))) and
                  (notice['kind'] != 'weather' or self._weather_enabled() and now() < notice['expires']) and
                  (notice['kind'] != 'day' or (day := self._day_due(now().astimezone())) and day['key'] == notice['key']) and
                  direction.allowed({'id': p['item_id'].removeprefix('system:')}, changes, now()))]
        ready.sort(key=lambda p: self._host_segment(p) != 'weather')
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
                segs.append((host.set_opener(None, None if plugins.on(self.cfg, 'track_previews') else first,
                                             self.recent_jingles, seed, novelty), False))
                owners.append(seed)
            speakers = [None] * len(segs)
            if plugins.on(self.cfg, "duo_hosts") and len(pkgs) > 1:
                segs, owners, speakers = self._duo(segs, owners, pkgs)
            # Host links share the first bulletin's budget, but need the host's number-tolerant speech check.
            voices = [self._say(text, voice) if links and k in (0, len(segs) - 1) else self._segment_voice(owner, text, voice)
                      for k, ((text, _), owner, voice) in enumerate(zip(segs, owners, speakers))]
            jingles = self.pkg_jingles.get(seed) or self._jingles_for(seed)
            key = hashlib.sha256(BREAK_SEP.join(p["id"] for p in pkgs).encode()).hexdigest()[:16]
            out = config.DATA_DIR / "cache/packages" / f"break-{key}.wav"
            over_intro = bool(links and intro_room)
            talkover = audio.render_package(voices, out, separators=[s for _, s in segs][1:], jingles=jingles,
                                            bed_gain_db=b["voice_bed_gain_db"], lead=lead,
                                            talkover=over_intro or self._talkover(seed),
                                            talkover_max=intro_room if over_intro else None,
                                            speaker_changes=[a is not b for a, b in zip(speakers, speakers[1:])])
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

    def _duo(self, segs, owners, pkgs):
        """Alternate bulletin voices without handoff or acknowledgement lines."""
        turn = {p["id"]: (self.tts2 if k % 2 else None) for k, p in enumerate(pkgs)}
        return segs, owners, [turn.get(owner) for owner in owners]

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
        await self._cancel_preview()
        replay_id = f"r{time.time_ns()}"
        duration = await asyncio.to_thread(self._song_seconds, ref)  # before the push: it may air at once
        rid = await self.engine.push_replay(Path(ref), "music", ref, gain, replay_id)
        self.replays[replay_id] = {"kind": kind, "ref": ref, "rid": rid, "duration": duration}
        log("replay queued:", kind, ref)
        return {"ok": True, "status": self.status()}

    async def _forget_dropped_replays(self) -> None:
        """A replay Liquidsoap could not load (a YouTube song it no longer serves, a file deleted meanwhile) is dropped
        from its queue without an event; forgotten here too, else it blocks the gap filler and the intro talk for the
        rest of the session. Two looks in a row, 10 s apart: a request is briefly invisible while the queue resolves it."""
        if not self.replays or time.time() - self.replays_checked < 10:
            return
        self.replays_checked = time.time()
        pending = await asyncio.wait_for(self.engine.pending(), .5)
        for key, r in list(self.replays.items()):
            r['missing'] = 0 if r['rid'] in pending else r.get('missing', 0) + 1
            if r['missing'] >= 2:
                del self.replays[key]
                log('replay dropped by the engine (could not load):', r['ref'])

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
        for key, row in self.replays.items():
            if row['kind'] == 'music':
                return {'ref': row['ref'], 'queue': 'news', 'rid': row['rid'], 'replay_id': key}
        if self.fresh_queued:
            ref, rid = next(iter(self.fresh_queued.items()))
            return {'ref': ref, 'queue': 'fresh', 'rid': rid}
        ref = getattr(self, 'next_track', '')
        if ref:
            queue = 'local_music' if getattr(self.engine, 'fallback_m3u', None) and not ref.startswith('ytmusic:') else 'music'
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
        elif self.rerolling and not self.reroll_pending:  # while a song downloads, another click replaces the pick
            reason = 'Choosing another track'
        state = {'ref': slot['ref'], 'title': music.label(self.db, slot['ref']), 'available': not reason, 'reason': reason}
        if self.reroll_pending:
            state['pending'] = {'title': music.label(self.db, self.reroll_pending)}
        if self.reroll_error and time.time() < self.reroll_error[1]:
            state['error'] = self.reroll_error[0]
        return state

    async def _reroll_next(self, expected):
        """Picks the replacement at once and answers: the page shows its title right away (a YouTube Music song still
        downloads for 5–20 s); the swap happens in the background, a failure is shown under the next title."""
        state = self._reroll_state()
        if not state or not state['available'] or expected != state['ref']:
            return {'ok': False, 'error': (state or {}).get('reason') or 'The next track changed. Try again.'}
        await self._cancel_preview()
        slot = self._next_music_slot()
        choices = self.engine.music_choices(expected)
        excluded = {expected, self.current['ref'], self.reroll_pending, *self.fresh_queued, *(r['ref'] for r in self.replays.values())}
        choices = {ref: uri for ref, uri in choices.items() if ref in self.library and ref not in excluded}
        if not choices:
            return {'ok': False, 'error': 'No other track is available'}
        picks = random.sample(list(choices), min(3, len(choices)))  # a YouTube song may be unavailable: two spares
        def uri_of(ref):
            uri = choices[ref]
            return uri.replace('annotate:', 'annotate:replay_id="' + slot['replay_id'] + '",', 1) if slot.get('replay_id') else uri
        replacement = picks[0]
        self.reroll_gen += 1
        gen = self.reroll_gen
        self.rerolling, self.reroll_pending, self.reroll_error = True, replacement, None
        if replacement.startswith('ytmusic:'):  # a download: answer now with the new title, swap when it is loaded
            async def swap_first_available():
                for ref in picks:
                    self.reroll_pending = ref
                    fail = await self._swap_next(expected, ref, uri_of(ref), slot, gen)
                    if fail != UNAVAILABLE:
                        return fail
                    self.rerolling, self.reroll_error = True, None  # that song is unavailable: the next spare
                self.reroll_error = (self._reroll_failed(picks[0], expected, "YouTube did not serve it or the spares"),
                                     time.time() + 20)
                self.rerolling = False
                return self.reroll_error[0]
            self.reroll_task = asyncio.create_task(swap_first_available())
            return await self.dispatch('status')
        uri = uri_of(replacement)
        if fail := await self._swap_next(expected, replacement, uri, slot, gen):  # a local file swaps at once
            return {'ok': False, 'error': fail}
        return await self.dispatch('status')

    def _reroll_failed(self, new, old, reason):
        return f"Could not load “{music.label(self.db, new)}”: {reason}. Keeping “{music.label(self.db, old)}”."

    async def _swap_next(self, expected, replacement, uri, slot, gen):
        downloaded = None
        fail = None
        try:
            if replacement.startswith('ytmusic:'):
                # Download here: inside Liquidsoap's replace command the download blocks every engine command.
                try:
                    async with self.reroll_download:
                        if gen != self.reroll_gen:
                            return None  # a newer click came while an older download ran: only the latest loads
                        downloaded = await asyncio.to_thread(ytmusic.resolve, replacement.removeprefix('ytmusic:'))
                except ytmusic.Unavailable:
                    fail = UNAVAILABLE
                    return fail
                except Exception:
                    fail = self._reroll_failed(replacement, expected, "YouTube Music did not answer")
                    return fail
                if gen != self.reroll_gen:
                    return None  # a newer click took over: drop this song, the finally deletes the file
                uri = uri.removesuffix(replacement) + 'tmp:' + downloaded  # Liquidsoap removes it after airing
            rid = await self.engine.replace_music(expected, uri, slot['queue'], slot['rid'])
            downloaded = None
            if expected in self.announced_tracks:
                self.announced_tracks.remove(expected)
            if slot.get('replay_id') in self.replays:
                self.replays[slot['replay_id']].update(ref=replacement, rid=rid,
                                                       duration=await asyncio.to_thread(self._song_seconds, replacement))
            elif slot['queue'] == 'fresh':
                self.fresh_queued = {replacement if ref == expected else ref: rid if ref == expected else old_rid for ref, old_rid in self.fresh_queued.items()}
                if expected in self.energy_queued:
                    self.energy_queued.remove(expected)
                    self.energy_queued.add(replacement)
            elif self.current['ref'] != replacement:
                self.next_track = replacement
        except (OSError, ValueError, asyncio.TimeoutError, asyncio.IncompleteReadError) as e:
            fail = self._reroll_failed(replacement, expected, "the next song changed meanwhile" if "changed" in str(e)
                                       else "it did not load in time")
        finally:
            if gen == self.reroll_gen:  # an older download must not clear the newer pick's state
                self.rerolling, self.reroll_pending = False, None
                if fail and fail != UNAVAILABLE:
                    self.reroll_error = (fail, time.time() + 20)  # under the next title until the next reroll or 20 s
            if downloaded:  # not handed to Liquidsoap
                Path(downloaded).unlink(missing_ok=True)
        return fail

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
            self.queued_break = self.queued_clock = None
            self.id_pending = False
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
        if action == 'undo':
            return await self._undo_broadcast_plan(req.get('token'))
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

    async def _sync_shuffle(self):
        settings.apply(self.cfg, self.defaults)
        enabled = bool(self.cfg['music']['shuffle'])
        if getattr(self.engine, 'music_shuffle', None) != enabled:
            result = await self.engine.command('music.shuffle ' + ('1' if enabled else '0'))
            if result == 'OK':
                self.engine.music_shuffle = enabled

    async def _sync_music_source(self):
        async with self.music_source_lock:
            await self._set_music_source()

    def _music_plan_rows(self, rows, probe=True, plan=()):
        current = self.current or {}
        now = time.time()
        at = self.paused_at if self.state == 'PAUSED' else now
        offset = max(0, current.get('duration',0) - (at-current.get('started',at)))
        cursor = at + offset if self.state in ('PLAYING','PAUSED') and current.get('duration') else None
        if not probe:
            self.plan_metadata_missing = False
        blocks = sorted((r for r in plan if r['kind'] != 'music' and not r.get('skipped')
                         and r['status'] != 'QUEUED'), key=lambda r: r.get('expected_at') or r['at'])
        energy_rows = energy.profiles(self.files) if plugins.on(self.cfg, 'energy_wave') else {}
        result = []
        for row in rows:
            if self.stopping or offset >= PLAN_HOURS * 3600:
                break
            ref = row.get('ref','')
            uri = row.get('uri','')
            if not ref:
                match = re.search(r'(?:^annotate:|,)ref=("(?:[^"\\\r\n]|\\.)*")',uri)
                if match:
                    ref = json.loads(match.group(1))
            kind = row.get('kind') or ('music' if ref in self.library else '')
            seconds = row.get('duration',0)
            if kind == 'music' and ref:
                while cursor is not None and blocks and content.parse_time(blocks[0].get('expected_at') or blocks[0]['at']).timestamp() <= cursor:
                    block = blocks.pop(0)
                    if block.get('ends') and cursor >= content.parse_time(block['ends']).timestamp():
                        continue
                    block['expected_at'] = iso(datetime.fromtimestamp(cursor, timezone.utc))
                    cursor += block['duration']
                    offset += block['duration']
                if not seconds:
                    if ref.startswith('ytmusic:'):
                        seconds = ytmusic.duration(ref)
                    else:
                        try:
                            sig = hooks.signature(ref)
                            cached = self.plan_durations.get(ref)
                            if not cached or cached[0] != sig:
                                if probe:
                                    cached = self.plan_durations[ref] = (sig,audio.duration(Path(ref)))
                                else:
                                    cached = None
                                    self.plan_metadata_missing = True
                            seconds = cached[1] if cached else 0
                        except OSError:
                            seconds = 0
                result.append({'ref':ref,'title':music.label(self.db,ref),'set':bool(row.get('set')),
                               'at':iso(datetime.fromtimestamp(cursor,timezone.utc)) if cursor is not None else None})
                profile = energy_rows.get(ref, {})
                if profile:
                    result[-1].update(energy=profile.get('energy'), genre=profile.get('genre') or profile.get('family'),
                                      genre_source=profile.get('genre_source'))
                if not probe and self.plan_metadata_missing:
                    break
            cursor = cursor + seconds if cursor is not None and seconds > 0 else None
            offset += seconds
        return result

    async def _warm_music_plan(self, rows):
        try:
            await asyncio.to_thread(self._music_plan_rows,rows)
        except Exception as error:
            log('music plan metadata:',type(error).__name__)

    async def _set_music_source(self):
        target = settings.music_source(self.cfg) == 'ytmusic'
        if target == self.ytmusic_active:
            self.music_source_error = "" if target or self.files else "No local music available"
            return
        if not target and not self.files and plugins.on(self.cfg, 'ytmusic'):
            self.music_source_error = "No local music available; keeping the current source"
            return
        if target:
            try:
                refs = ytmusic.write_m3u(self.db, config.RUN_DIR / 'ytmusic.m3u')
            except ValueError as e:
                self.music_source_error = str(e)
                return
            self.library.update(refs)
            self.intro_rooms.update(refs)
            result = await self.engine.command('music.reload ' + str(config.RUN_DIR / 'ytmusic.m3u'))
            if 'ERROR' in result or 'Unknown' in result:
                self.music_source_error = 'Restart the radio once to enable live source switching'
                return
        result = await self.engine.command('music.source ' + ('ytmusic' if target else 'local'))
        if result != 'OK':
            self.music_source_error = 'Restart the radio once to enable live source switching'
            return
        await self._cancel_automatic()
        await self._clear_fresh()
        self.ytmusic_active = target
        self.announced_tracks = []
        self.music_source_ready = False
        self.music_source_error = "" if target or self.files else "No local music available"
        self.next_track = ""
        self.reroll_gen += 1
        self.rerolling, self.reroll_pending = False, None

    def _music_source_state(self):
        target = settings.music_source(self.cfg)
        current = self.current or {}
        actual = ('ytmusic' if current['ref'].startswith('ytmusic:') else 'local') if current.get('kind') == 'music' else None
        phase = 'idle'
        if self.state in ('PLAYING', 'PAUSED'):
            if self.music_source_error:
                phase = 'error'
            elif actual != target:
                phase = 'waiting' if self.music_source_ready else 'loading'
            elif self.source_overlap and time.time() < self.source_overlap['until']:
                phase = 'crossfade'
            else:
                phase = 'playing'
        return {'target': target, 'current': actual, 'phase': phase, 'error': self.music_source_error,
                'from': self.source_overlap['from'] if phase == 'crossfade' else None}

    async def dispatch(self, cmd: str, req: dict | None = None) -> dict:
        if cmd == 'catchup':
            if self.state not in ('PLAYING','PAUSED'):
                return {'ok':False,'error':'Start the station before preparing catch-up.'}
            if not self.catchup_task and not catchup.active(self.db):
                if not catchup.candidates(self.db,now(),self._sources_off()):
                    return {'ok':False,'error':'No unplayed stories from the last 48 hours.'}
                self.catchup_error = ''
                self.catchup_task = asyncio.create_task(self._prepare_catchup())
            return {'ok':True,'catchup':self._catchup_status()}
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
            pending_catchup = catchup.active(self.db)
            if not enabled and pending_catchup and pending_catchup['status'] in ('PENDING','READY'):
                if any(plugins.SOURCE_PLUGIN.get(o.get('source'))==name for o in catchup.origins(self.db,pending_catchup['id'])):
                    self.db.execute("UPDATE packages SET status='SKIPPED' WHERE item_id=? AND status IN ('PENDING','READY')",(pending_catchup['id'],))
                    self.catchup_error = 'A selected source was disabled; prepare catch-up again.'
            if name == 'track_previews' and not enabled:
                await self.engine.command('previews.enable 0')
                self.preview_enabled = False
                await self._cancel_preview()
                self._stop_hook_worker()
            if name == 'new_tracks' and not enabled:
                await self._clear_fresh()
            if name == 'energy_wave' and not enabled:
                await self._clear_energy()
            if name == "ytmusic":
                await self._sync_music_source()
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
            if getattr(self, 'engine', None) and self.engine.proc and self.engine.proc.returncode is None:
                try:
                    await self._forget_dropped_fresh()
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                    pass
            status = self.status()
            if getattr(self, 'engine', None) and self.engine.proc and self.engine.proc.returncode is None:
                try:
                    ref = await asyncio.wait_for(self.engine.next_music(), .5)
                    self.next_track = ref or ""
                    status['next_reroll'] = self._reroll_state()
                    self.music_source_ready = bool(ref) and ref.startswith('ytmusic:') == (settings.music_source(self.cfg) == 'ytmusic')
                    status['music_source_state'] = self._music_source_state()
                    await self._forget_dropped_replays()
                    if ref:
                        status['upcoming'].append({'kind': 'music', 'ref': ref, 'title': music.label(self.db, ref), 'stage': 'music'})
                        later = await asyncio.wait_for(self.engine.upcoming_music(), .5)
                        # tentative: a reshuffle, a break or a reroll can change the order; only the next is certain
                        status['upcoming_tracks'] = [{'ref': r, 'title': music.label(self.db, r)} for r in later]
                        if plugins.on(self.cfg, 'air_direction'):
                            status['broadcast_plan'] = self._broadcast_plan()
                    rows = await asyncio.wait_for(self.engine.music_plan(),.5)
                    if isinstance(rows,list):
                        status['upcoming_tracks'] = self._music_plan_rows(rows,probe=False,
                            plan=(status.get('broadcast_plan') or {}).get('rows', []))
                        if not ref and status['upcoming_tracks']:
                            self.next_track = status['upcoming_tracks'][0]['ref']
                            status['upcoming'].append({'kind':'music','ref':self.next_track,
                                                       'title':status['upcoming_tracks'][0]['title'],'stage':'music'})
                            status['next_reroll'] = self._reroll_state()
                        if self.plan_metadata_missing and (self.plan_metadata_task is None or self.plan_metadata_task.done()):
                            self.plan_metadata_task = asyncio.create_task(self._warm_music_plan(rows))
                except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError):
                    pass
            return {"ok": True, "status": status}
        if cmd == "pause" and self.state == "PLAYING":
            await self._pause()
        elif cmd == "resume" and self.state == "PAUSED":
            await self._resume()
        elif cmd == "next":
            if self.state == 'PAUSED':
                return {'ok': False, 'error': 'The radio is paused. Resume playback before skipping a track.'}
            current = self.current
            self.manual_switch = True
            try:
                await self._cancel_automatic()
                if current and current['kind'] == 'music':
                    if self.queued_reminders:
                        for key, (rid, notice) in list(self.queued_reminders.items()):
                            await self.engine.remove(rid)
                            self.reminders_due[key] = notice
                            self.reminder_text.pop(key, None)
                            self.db.execute("UPDATE air_plan SET status='PENDING',rid=NULL WHERE rid=? AND status='QUEUED'", (rid,))
                        self.queued_reminders.clear()
                        self.id_pending = False
                    await self.engine.wait_music()
                if self.current is current:
                    self.skip_flag = bool(current and current['kind'] == 'news')
                    self.next_transition_sec = .1
                    self.manual_skip_pending = True
                    await self.engine.command('skip')
            except Exception:
                self.manual_skip_pending = False
                self.next_transition_sec = None
                raise
            finally:
                self.manual_switch = False
        elif cmd == "skip-news":
            if not (self.current and self.current["kind"] == "news"):
                return {"ok": False, "error": "no bulletin is on air"}
            self.skip_flag = True
            await self._cancel_preview()
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
        return {"id": "Station ID", "reminder": "Reminder", "weather": "Weather", "teaser": "Coming up"}.get(kind, kind)

    def _clock_rows(self, at=None):
        rows = broadcast_clock.rows(self.db, at or now(), PLAN_HOURS, self.cfg['weather'].get('timezone', 'Asia/Ho_Chi_Minh'))
        for row in rows:
            row['duration'] *= self.cfg['broadcast']['max_break_speech_sec'] / 120
        return rows

    def _clock_slot(self, at=None):
        at = at or now()
        return next((r for r in self._clock_rows() if not r['skipped'] and
                     content.parse_time(r['at']) <= at < content.parse_time(r['ends'])), None)

    def _news_section_id(self):
        rows = self._clock_rows()
        return rows[0]['id'] if rows else ''

    def _broadcast_plan(self):
        at = datetime.fromtimestamp(self.paused_at,timezone.utc) if self.state == 'PAUSED' else now()
        cur = self.current
        remaining = max(0, (cur.get('duration') or 0) - ((self.paused_at if self.state == 'PAUSED' else time.time()) - cur['started'])) if cur else 0
        boundary = at + timedelta(seconds=remaining)
        # Replays wait in the news queue: they air before a break or insert that is not queued ahead of them.
        replays = [r for r in self.replays.values() if not self.queued_break or int(r['rid'] or 0) < int(self.queued_break[0] or 0)]
        first = boundary + timedelta(seconds=sum(r.get('duration') or 0 for r in replays))
        interval = 1800
        horizon = at + timedelta(hours=PLAN_HOURS)
        plan = direction.rows(self.db, self.cfg, at)
        result = self._clock_rows(at)
        selected = self._pick_break(preview=True)
        if not selected:
            selected = [r for r in direction.news(self.db) if r['source'] not in self._sources_off()][:2]
        if self.queued_break:
            ids = set(self.queued_break[1])
            selected = [r for r in plan if r['id'] in ids]
        for row in result:
            if row is result[0]:
                row['content_members'] = [r['id'] for r in selected]
                row['members'] += row['content_members']
                if self.queued_break:
                    row['status'], row['locked'], row['rid'] = 'QUEUED', True, self.queued_break[0]
                row['placeholder'] = not bool(selected)
                if not self.queued_break and selected and all(r['status'] == 'READY' for r in selected):
                    row['status'] = 'READY'
        result += [{**r, 'at': r['not_before'] or iso(first), 'duration': 30,
                    'estimated': not bool(r['not_before']), 'locked': r['status'] in ('QUEUED', 'PREPARING'),
                    'members': [r['id']]} for r in plan
                   if r['kind'] != 'music' and not r['id'].startswith(broadcast_clock.PREFIX) and at - (broadcast_clock.WINDOW if r['kind']=='news' else broadcast_clock.INSERT_WINDOW) < content.parse_time(r['not_before'] or iso(at)) <= horizon and
                   (r['kind'] != 'news' or r['id'].startswith(('direction:', 'section:news:')) or r['not_before'])]
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
                           'at': edit.get('not_before') or iso(r.get('remind_at', r['start'] - lead)), 'not_before': edit.get('not_before'),
                           'duration': 30, 'estimated': False, 'locked': key in self.queued_reminders,
                           'status': 'QUEUED' if key in self.queued_reminders else 'PENDING', 'skipped': bool(edit.get('skipped')),
                           'rid': self.queued_reminders[key][0] if key in self.queued_reminders else None})
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
        if early_titles and first > boundary:
            music_sections.append({'id': 'section:music:replays', 'kind': 'music', 'title': 'Music',
                                   'content_title': ' · '.join(early_titles), 'at': iso(boundary), 'not_before': None,
                                   'duration': (first - boundary).total_seconds(), 'members': [], 'placeholder': True,
                                   'estimated': True, 'status': 'PENDING', 'locked': False, 'skipped': False})
            early_titles = []
            cursor = first
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
        for row in result:
            if not row['skipped'] and content.parse_time(row['at']) < first and (not row.get('ends') or first < content.parse_time(row['ends'])):
                row['expected_at'] = iso(first)
        result += music_sections
        packages = {r['id']: r for r in plan if r['kind'] == 'news' and r.get('script_path')}
        for row in result:
            if row['kind'] not in ('news', 'weather', 'reminder'):
                continue
            ids = [*row['members'], *row.get('content_members', []), row.get('ref')]
            parts = [packages[key] for key in dict.fromkeys(ids) if key in packages]
            notice = self.reminders_due.get(row.get('ref')) or self.reminders_due.get(row['id'])
            row['text_ready'] = row['status'] == 'QUEUED' or bool(parts) and all(p.get('prepared_text') is not None or p['item_kind'] not in ('raw_post', 'ready_digest') and Path(p['script_path']).is_file() for p in parts) or bool(notice and (notice.get('title') or notice.get('text')))
            row['voice_ready'] = row['status'] == 'QUEUED' or bool(parts) and all(p['status'] in ('READY', 'QUEUED') for p in parts)
        self._edition_details(result, packages)
        result.sort(key=lambda r: (r['at'], not r['locked']))
        version = hashlib.sha256(json.dumps([{k: r.get(k) for k in ('id', 'status', 'not_before', 'skipped', 'locked')} for r in sorted(result, key=lambda r: r['id'])], sort_keys=True).encode()).hexdigest()[:20]
        return {'version': version, 'rows': result, 'starts_at': iso(boundary)}

    def _edition_details(self, rows, packages):
        labels = {'reddit': 'Reddit news', 'telegram': 'Telegram news', 'horizon': 'Tech news', 'github_trending': 'GitHub releases', 'github_new': 'New GitHub projects', 'catchup':'What I missed · 48h',
                  'weather': 'Weather', 'calendar': 'Today', 'holiday_preview': 'Upcoming holidays', 'music_facts': 'Music stories', 'inbox': 'News', 'direction': 'Announcement'}
        for row in rows:
            if row['kind'] not in ('news', 'weather'):
                continue
            ids = [*row['members'], *row.get('content_members', []), row.get('ref')]
            if row['kind'] == 'weather':
                ids += [key for key, p in packages.items() if p['item_id'] == 'system:' + row['id']]
            parts = [packages[key] for key in dict.fromkeys(ids) if key in packages]
            segments = {}
            for p in parts:
                source = 'holiday_preview' if p['item_id'].startswith('system:holiday:') else p['source']
                seg = segments.setdefault(source, {'title': labels.get(source, source), 'items': [], 'text_ready': True, 'voice_ready': True})
                seg['items'].append(timeline.topic_name(p['title']))
                seg['text_ready'] = seg['text_ready'] and (p.get('prepared_text') is not None or p['item_kind'] not in ('raw_post', 'ready_digest') and Path(p['script_path']).is_file())
                seg['voice_ready'] = seg['voice_ready'] and p['status'] in ('READY', 'QUEUED')
            if not segments:
                sources = ['weather'] if row['kind'] == 'weather' else [name for name in ('horizon', 'github_trending', 'github_new', 'reddit', 'telegram') if plugins.on(self.cfg, name)]
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

    async def _undo_broadcast_plan(self, token):
        undo = self.plan_undo
        if not undo or token != undo['token']:
            raise ValueError('This change can no longer be undone')
        fields = ('kind', 'ref', 'text', 'not_before', 'rank', 'skipped')
        for key, (before, after) in undo['rows'].items():
            current = self.db.execute('SELECT * FROM air_plan WHERE id=?', (key,)).fetchone()
            values = tuple(current[f] for f in fields) if current else None
            if values != (tuple(after[f] for f in fields) if after else None):
                raise ValueError('The program changed again; this action cannot be undone')
            if current and current['status'] in ('QUEUED', 'PREPARING', 'PLAYED', 'EXPIRED', 'INTERRUPTED'):
                raise ValueError('This block is already preparing, queued, aired or expired')
            if key.startswith(broadcast_clock.PREFIX):
                original = content.parse_time(before['not_before']) if before and before['not_before'] else datetime.fromtimestamp(int(key.removeprefix(broadcast_clock.PREFIX)), timezone.utc)
                if now() >= original + broadcast_clock.WINDOW:
                    raise ValueError('The original broadcast window has expired')
            refs = {key, (current['ref'] if current else ''), (before['ref'] if before else '')}
            if self.current and self.current['kind'] != 'music' and refs.intersection(self.current['ref'].split(BREAK_SEP)):
                raise ValueError('This block is already on air')
        with store.transaction(self.db):
            for key, (before, after) in undo['rows'].items():
                if before:
                    self.db.execute("INSERT INTO air_plan(id,kind,ref,text,not_before,rank,skipped) VALUES(?,?,?,?,?,?,?) "
                                    "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,ref=excluded.ref,text=excluded.text,not_before=excluded.not_before,rank=excluded.rank,skipped=excluded.skipped",
                                    (key, *(before[f] for f in fields)))
                elif key.startswith(('direction:', 'system:direction:')):
                    self.db.execute("UPDATE air_plan SET skipped=1,status='PENDING',rid=NULL WHERE id=?", (key,))
                else:
                    self.db.execute('DELETE FROM air_plan WHERE id=?', (key,))
        self.plan_undo = None
        self.wake.set()
        return {'ok': True, 'broadcast_plan': self._broadcast_plan()}

    async def _save_broadcast_plan(self, req):
        snapshot = self._broadcast_plan()
        changes = req.get('changes')
        if not isinstance(changes, list) or len(changes) > 100:
            raise ValueError('Invalid schedule changes')
        if not changes:
            return {'ok': True, 'broadcast_plan': snapshot}
        if req.get('version') != snapshot['version']:
            raise ValueError('The schedule changed. Try the edit again.')
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
                raise ValueError('This block is already selected or preparing. Try another block.')
            if row['kind'] == 'reminder' and change.get('skipped') and not row.get('skipped') and change.get('skip_confirmed') is not True:
                raise ValueError('Confirm skipping this reminder')
            if not row.get('new') and change.get('skipped') is not None and change.get('at') == row['at']:
                due = content.parse_time(row['at'])
            else:
                due = direction.requested_time(change.get('at'), now())
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
        before = {r['id']: dict(r) for r in self.db.execute('SELECT * FROM air_plan')} if req.get('remember_undo') else None
        with store.transaction(self.db):
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
                keys = list(dict.fromkeys([*row['members'], *row.get('edition_news_ids', [])])) or [row['id']]
                if row['kind'] == 'reminder' and str(row.get('ref', '')).startswith(('cal:', 'todoist:')):
                    keys = list(dict.fromkeys([*keys, 'reminder:' + row['ref']]))
                for key in keys:
                    self.db.execute("INSERT INTO air_plan(id,kind,ref,not_before,rank,skipped) VALUES(?,?,?,?,?,?) "
                                    "ON CONFLICT(id) DO UPDATE SET not_before=excluded.not_before,rank=excluded.rank,skipped=excluded.skipped,error=NULL,status=CASE WHEN air_plan.status='ERROR' THEN 'PENDING' ELSE air_plan.status END",
                                    (key, 'news' if key.startswith(broadcast_clock.PREFIX) else row['kind'], '' if row.get('placeholder') else row.get('ref') or key, iso(due), rank, skipped))
        self.wake.set()
        result = {'ok': True, 'broadcast_plan': self._broadcast_plan()}
        if before is not None:
            after = {r['id']: dict(r) for r in self.db.execute('SELECT * FROM air_plan')}
            fields = ('kind', 'ref', 'text', 'not_before', 'rank', 'skipped')
            values = lambda r: tuple(r[f] for f in fields) if r else None
            rows = {key: (before.get(key), after.get(key)) for key in before.keys() | after.keys()
                    if values(before.get(key)) != values(after.get(key))}
            self.plan_undo = {'token': uuid.uuid4().hex, 'rows': rows} if rows else None
            result['undo_token'] = self.plan_undo['token'] if self.plan_undo else None
        return result

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
                                  'at': iso(r.get('remind_at', r['start'] - timedelta(minutes=self.cfg['reminders']['lead_min'])))})
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
            elif self.current['kind'] == 'teaser':
                cur = 'АНОНС ТРЕКОВ'
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
            "paused_at": self.paused_at if self.state == "PAUSED" else None,
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
            'catchup':self._catchup_status(),
            "ytmusic_active": self.ytmusic_active,
            "music_source": self._music_source_state()['current'],
            "music_source_state": self._music_source_state(),
            "broadcast_plan": self._broadcast_plan() if plugins.on(self.cfg, 'air_direction') else None,
            "direction": plan if plugins.on(self.cfg, 'air_direction') else [],
            "upcoming": self._upcoming(plan),
            "announced_tracks": list(self.announced_tracks),
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
