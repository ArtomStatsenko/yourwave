"""One process per session: owns Liquidsoap, prepares news in the background, serves the CLI socket."""
import asyncio
import hashlib
import os
import json
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

from . import audio, config, content, github_new, github_trending, horizon, host, music, plugins, reminders, settings, store, timeline, weather, ytmusic
from .editor import Editor
from .engine import Liquidsoap
from .store import iso, now
from . import tts as tts_mod
from .tts import VoiceStudio, cached_synthesize

READY_AHEAD = 2
LATE_ASSEMBLY_SEC = 60  # a news break (time check, next song) is assembled when the song has this much left
REMIND_LATE_SEC = 40    # a reminder is rendered when the song has this much left, so "in N minutes" is right when it airs
BREAK_SEP = "|"         # a break airs several packages as one request: ref = their ids joined


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Service:
    def __init__(self, cfg: dict, tts=None, output: str = "device"):
        self.cfg, self.output = cfg, output
        self.defaults = None  # the config.toml values, taken at the first decision; settings.json (the UI) overrides them
        v = cfg["voice"]
        self.tts = tts or VoiceStudio(v["base_url"], v["voice_id"], v["language"], v["speed"], v["instruct"], v["seed"])
        self.db = store.connect()
        self.session = uuid.uuid4().hex[:12]
        self.state = "STARTING"
        self.auto_paused, self.paused_at, self.heard_at = False, 0.0, time.time()
        self.replays = {}  # replay_id -> {kind, ref, rid}: replays waiting in Liquidsoap's queue
        self.current: dict | None = None
        self.skip_flag = False
        self.last_error = ""
        self.stopping = False
        self.done = asyncio.Event()
        self.wake = asyncio.Event()
        self.rendering = False
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
        self.sets_since_fresh = 0     # news breaks since the last "new tracks" set
        self.fresh_queued: dict[str, str] = {}  # path -> request id of new songs waiting in Liquidsoap's fresh queue
        self.pkg_jingles: dict = {}     # package id -> jingles chosen at render, reused at final assembly
        self.id_pending = False
        self.all_sources_checked = False  # set only after a *successful* check of every enabled source
        self.no_news_done = False

    # ---- lifecycle ----
    async def run(self) -> None:
        self._cleanup_cache()
        self._reset_inflight()
        # "No fresh news" is a statement about one session; an unplayed one from an earlier session is stale.
        self.db.execute("UPDATE packages SET status='SKIPPED' WHERE item_id LIKE 'system:no-news:%' "
                        "AND status IN ('PENDING','READY')")
        self.db.execute("UPDATE items SET state='SKIPPED' WHERE id LIKE 'system:no-news:%' AND state='NEW'")
        files = self.files = music.scan(self.cfg["music"]["path"])
        self.library = {str(f) for f in files}  # the only music paths a replay request may name
        m3u = config.RUN_DIR / "music.m3u"
        m = self.cfg["music"]
        self.intro_rooms = music.write_m3u(self.db, files, m3u, m["target_lufs"], m["max_boost_db"], m["crossfade_sec"],
                                                  plugins.on(self.cfg, "voice_over")) if files else {}
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
        await self.engine.start(self.on_event)
        self.state = "PLAYING"
        store.log_event(self.db, self.session, "SESSION_STARTED")
        log("session", self.session, "started,", len(files), "tracks")
        threading.Thread(target=music.measure_missing, args=(store.connect(), files, self.stop_measure,
                                                             lambda: plugins.on(self.cfg, "voice_over")),
                         daemon=True).start()
        config.SOCK.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.handle_client, str(config.SOCK))
        config.SOCK.chmod(0o600)
        asyncio.create_task(self.content_loop())
        asyncio.create_task(self.watch_engine())
        asyncio.create_task(self.trending_loop())
        asyncio.create_task(self.horizon_loop())
        asyncio.create_task(self.reminders_loop())
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
        await self._stop_voice()
        self.stop_measure.set()
        await self.engine.stop()
        self._reset_inflight()  # a package cut off by stop is not PLAYED; it may air next session if still fresh
        self.state = "STOPPED"
        store.log_event(self.db, self.session, "SESSION_STOPPED")
        log("session stopped")
        self.done.set()

    async def _pause(self) -> None:
        await self.engine.command("pause")
        self.state, self.paused_at = "PAUSED", time.time()

    async def _resume(self) -> None:
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
        self.db.execute("UPDATE packages SET status='READY' WHERE status IN ('QUEUED','PLAYING')")
        for p in self.db.execute("SELECT id, audio_path FROM packages WHERE status='READY'").fetchall():
            if not p["audio_path"] or not Path(p["audio_path"]).exists():
                self.db.execute("UPDATE packages SET status='PENDING' WHERE id=?", (p["id"],))

    def _cleanup_cache(self) -> None:
        cutoff = time.time() - self.cfg["broadcast"]["package_cache_ttl_hours"] * 3600
        for d in ("cache/tts", "cache/packages", "cache/scripts"):
            for f in (config.DATA_DIR / d).iterdir():
                if f.stat().st_mtime < cutoff:
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
        self.replays.pop(m.get("replay_id") or "", None)  # on air now: no longer queued
        self.fresh_queued.pop(m.get("ref") or "", None)
        self.current = {"kind": m.get("kind") or "music", "ref": m.get("ref") or m.get("filename", ""),
                        "started": time.time()}
        if self.current["kind"] == "news":
            self.songs_since_break, self.set_started, self.set_songs = 0, None, []
            for pid in self.current["ref"].split(BREAK_SEP):
                self.db.execute("UPDATE packages SET status='PLAYING' WHERE id=?", (pid,))
                store.log_event(self.db, self.session, "PACKAGE_STARTED", pid)
            log("news on air:", self.current["ref"])
        elif self.current["kind"] in ("reminder", "weather"):
            self.id_pending = False
            store.log_event(self.db, self.session, self.current["kind"].upper(), self.current["ref"],
                            self.reminder_text.pop(self.current["ref"], ""))
            log(self.current["kind"], "on air")
        elif self.current["kind"] == "id":
            self.songs_since_break, self.id_pending = 0, False
            store.log_event(self.db, self.session, "STATION_ID", self.current["ref"])
            log("station id:", self.current["ref"])
        else:
            self.songs_since_break += 1
            self.set_started = self.set_started or self.current["started"]
            if s := music.song(self.db, self.current["ref"]):
                self.set_songs.append(s)
            ref = self.current["ref"]
            self.current["duration"] = ytmusic.duration(ref) if ref.startswith("ytmusic:") else audio.duration(Path(ref))
            store.log_event(self.db, self.session, "MUSIC_STARTED", self.current["ref"])
            log("music:", Path(self.current["ref"]).name)
        self.wake.set()

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
        """GitHub sources. Checks the plugin switches every minute: a source switched on is fetched at once,
        otherwise every refresh_minutes. Both off = nothing to check, which counts as checked for "no news"."""
        gt, gn = self.cfg["sources"]["github_trending"], self.cfg["sources"]["github_new"]
        next_fetch, was_on = 0.0, set()
        while not self.stopping:
            on = {n for n in ("github_trending", "github_new") if plugins.on(self.cfg, n)}
            for n in ("github_trending", "github_new"):
                if n not in on:
                    self.sources[n] = "off"
            if not on:
                self.all_sources_checked = True
            elif time.time() >= next_fetch or on - was_on:
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
        """Horizon runs (minutes, in a thread) when its plugin is on: right after it is switched on, then hourly."""
        hz = self.cfg["sources"]["horizon"]
        next_run, was_on = 0.0, False
        while not self.stopping:
            on = plugins.on(self.cfg, "horizon")
            if not on:
                self.sources["horizon"] = "off"
            elif time.time() >= next_run or not was_on:
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

    def _maybe_announce_no_news(self) -> None:
        """Once per session, only after every source answered successfully and gave nothing to air.
        After a network or access error nothing is said: "no news" would be a false claim."""
        if self.no_news_done or not self.all_sources_checked:
            return
        session_played = self.db.execute(
            "SELECT 1 FROM events WHERE session_id=? AND type='PACKAGE_COMPLETED'", (self.session,)).fetchone()
        in_flight = self.db.execute("SELECT 1 FROM packages p JOIN items i ON i.id=p.item_id "
                                    "WHERE p.status IN ('PENDING','READY','QUEUED','PLAYING') AND i.state='NEW'").fetchone()
        self.no_news_done = True  # decided once: either news exist or we announce now
        if session_played or in_flight:
            return
        content.add_item(self.db, self.cfg, {
            "id": f"system:no-news:{self.session}", "topic": "", "source": "system", "url": "",
            "published_at": now(), "body": "Свежих новостей пока нет. Продолжаем музыку."}, kind="announcement")
        log("no fresh news: one announcement queued")

    async def content_tick(self) -> None:
        for item_id in content.scan_inbox(self.db, self.cfg):
            store.log_event(self.db, self.session, "ITEM_COLLECTED", item_id)
            log("new digest:", item_id)
        content.expire_stale(self.db, self.cfg)
        self._maybe_announce_no_news()
        await self.schedule()
        ready = self.db.execute("SELECT COUNT(*) FROM packages WHERE status='READY'").fetchone()[0]
        if not self.rendering and ready < READY_AHEAD and time.time() >= self.tts_retry_at:
            p = content.next_to_render(self.db)
            if p:
                self.rendering = True
                asyncio.create_task(self._render_task(dict(p)))

    def _live_settings(self) -> None:
        if self.defaults is None:
            self.defaults = settings.base(self.cfg)
        settings.apply(self.cfg, self.defaults)

    async def schedule(self) -> None:
        self._live_settings()
        if self.current is None:
            return  # Play starts with music: nothing is queued before the first song is on air
        if self.state != "PLAYING" or self.id_pending or self.db.execute(
                "SELECT 1 FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone():
            return  # one insert (package or station ID) in the engine at a time
        remaining = 10.0
        if self.current["kind"] == "music" and self.current.get("duration"):
            remaining = self.current["duration"] - (time.time() - self.current["started"])
        if self.reminders_due and await self._remind(remaining):
            return
        pkgs = self._pick_break() if self._break_due(remaining) else []
        if pkgs:
            links = plugins.on(self.cfg, "host_links") and not pkgs[0]["id"].startswith("system:")
            wav, talkover = Path(pkgs[0]["audio_path"]), pkgs[0]["talkover_sec"] or 0
            if links or len(pkgs) > 1:
                if remaining > LATE_ASSEMBLY_SEC:
                    return  # assembled later, so the time check and the next song are right when it airs
                await self._clear_fresh()  # songs of the last new-tracks set that did not get to air
                self.sets_since_fresh += 1
                new = await asyncio.to_thread(self._pick_new) if links and self._new_set_due() else []
                nxt = str(new[0]) if new else (await self.engine.next_music() or "") if links else ""
                first = music.song(self.db, nxt)
                # shortcut: a replay pushed after this point would play first and its intro is not planned for;
                # with replays already waiting the next song is not known, so no intro talk then.
                room = self.intro_rooms.get(nxt) if plugins.on(self.cfg, "voice_over") and not self.replays else None
                air = now() + timedelta(seconds=max(0.0, remaining))
                done = await asyncio.to_thread(self._assemble_break, [dict(p) for p in pkgs], air, links, first, room,
                                               bool(new))
                if done:
                    wav, talkover = done
                else:
                    pkgs, new = pkgs[:1], []  # fallback: the first bulletin, pre-rendered
            ids = [p["id"] for p in pkgs]
            await self.engine.push_news(wav, BREAK_SEP.join(ids), talkover)
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
                voices.append(cached_synthesize(self.tts, segment, self._speech_check))
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
        share = self.cfg["broadcast"]["talkover_share"] or 0.5 if plugins.on(self.cfg, "voice_over") else 0.0
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
        if not plugins.on(self.cfg, "voicestudio") or await asyncio.to_thread(self.tts.health):
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

    async def _reminders_tick(self) -> None:
        self._live_settings()
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
        for r in due:
            if r["key"] not in self.reminders_due and not self.db.execute(
                    "SELECT 1 FROM reminders WHERE key=?", (r["key"],)).fetchone():
                self.reminders_due[r["key"]] = r
        for key in [k for k, r in self.reminders_due.items()
                    if r["kind"] == "day" or r.get("expires", r["start"] + timedelta(minutes=1)) < now()]:
            self.reminders_due.pop(key)  # expire timed reminders; refresh today's topics from the calendar
        if day := self._day_due(now().astimezone()):
            self.reminders_due[day["key"]] = day
        if self.reminders_due:
            self.wake.set()

    def _day_due(self, at):
        if not plugins.on(self.cfg, "reminders_calendar") or not self.cfg["reminders"]["calendar_url"] \
                or not plugins.on(self.cfg, "host_links"):
            return None
        history = self.db.execute("SELECT key, fired_at FROM reminders WHERE key LIKE ?",
                                  (f"day:{at.date()}:%",)).fetchall()
        return reminders.day_due(self.cal_events, at, history)

    def _weather_enabled(self) -> bool:
        return bool(self.cfg["weather"]["city"]) and plugins.on(self.cfg, "weather") and plugins.on(self.cfg, "host_links")

    async def _weather_tick(self) -> None:
        if not self._weather_enabled():
            self.sources.pop("weather", None)
            for key in [k for k, r in self.reminders_due.items() if r["kind"] == "weather"]:
                self.reminders_due.pop(key)
            return
        if self.state != "PLAYING" or any(r["kind"] == "weather" for r in self.reminders_due.values()):
            return
        last = self.db.execute("SELECT MAX(fired_at) FROM reminders WHERE key LIKE 'weather:%'").fetchone()[0]
        if last and now() - content.parse_time(last) < weather.INTERVAL:
            return
        if time.time() < self.next_weather_read:
            return
        try:
            r = await asyncio.to_thread(weather.fetch, self.cfg["weather"]["city"], now())
            self.reminders_due[r["key"]] = r
            self.sources["weather"] = "ok"
            self.wake.set()
        except Exception as e:
            self.sources["weather"] = f"error: {type(e).__name__}"
            log("weather:", type(e).__name__)
            self.next_weather_read = time.time() + 15 * 60

    async def _remind(self, remaining: float) -> bool:
        """The most urgent reminder goes out when the current song is about to end. True if an insert was queued."""
        if remaining > REMIND_LATE_SEC:
            return False
        for key, r in sorted(self.reminders_due.items(), key=lambda kv: (kv[1]["kind"] in ("day", "weather"), kv[1]["start"])):
            if r.get("retry", 0) > time.time():
                continue
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
                text = reminders.say(r, air, host.minutes_words)
            if text is None:
                self.reminders_due.pop(key)
                continue
            try:
                gain = 0.0
                if r["kind"] in ("day", "weather"):
                    if r["kind"] == "weather":
                        wav = await asyncio.to_thread(cached_synthesize, self.tts, text, self._speech_check)
                        if not self._weather_enabled() or now() >= r["expires"]:
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
                    wav = await asyncio.to_thread(self._render_notice, text)
            except Exception as e:
                self.last_error = f"reminder: {e}"
                r["retry"] = time.time() + 30
                return False
            await self.engine.push_replay(wav, "weather" if r["kind"] == "weather" else "reminder", key, gain, "",
                                          ',liq_cross_end_duration="1.5",liq_cross_start_duration="12"')
            self.db.execute("INSERT OR REPLACE INTO reminders(key, fired_at) VALUES(?,?)", (key, iso(now())))
            self.reminders_due.pop(key, None)
            self.reminder_text[key] = r.get("summary", text)
            self.id_pending = True  # one insert at a time, like a station ID
            log(r["kind"], "queued:", text)
            return True
        return False

    def _render_day(self, r: dict, text: str):
        short = text
        if not r["count"] and r["description"]:
            try:
                text = self.editor.retell_day(r["title"], r["description"]) or text
            except Exception as e:
                log("calendar story:", type(e).__name__)  # a failed story still gets a short mention
        try:
            return text, cached_synthesize(self.tts, text, self._speech_check)
        except RuntimeError:
            if text == short:
                raise
            return short, cached_synthesize(self.tts, short, self._speech_check)

    def _render_notice(self, text: str) -> Path:
        try:
            voice = cached_synthesize(self.tts, text, self._speech_check)
        except RuntimeError:  # the voice cannot read this title: say it without the opening word
            voice = cached_synthesize(self.tts, text.removeprefix("Напоминание. "), self._speech_check)
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
            tags, _ = music.song_tags(row, m["crossfade_sec"], plugins.on(self.cfg, "voice_over"))
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

    def _break_due(self, remaining: float) -> bool:
        """A news break ends a music set: the first of a session after one song, then when the set reaches
        music_set_minutes by the end of the current song."""
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
        if ready and ready[0]["id"].startswith("system:"):
            return [ready[0]] if ready[0]["speech_sec"] <= budget + 12 else []  # announcements air alone
        picked, total = [], 0.0
        for p in ready:
            if total + p["speech_sec"] <= budget:
                picked.append(p)
                total += p["speech_sec"]
        order = {}
        for p in picked:
            order.setdefault(p["id"].split(":")[0], len(order))
        return sorted(picked, key=lambda p: order[p["id"].split(":")[0]])

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
                    lead = cached_synthesize(self.tts, back, self._speech_check)
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
            voices = [cached_synthesize(self.tts, text, self._speech_check) for text, _ in segs]
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
        rid = await self.engine.push_replay(Path(ref), "music", ref, gain, replay_id)
        self.replays[replay_id] = {"kind": kind, "ref": ref, "rid": rid}
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

    def _sources_off(self) -> list[str]:
        """Sources whose plugin is switched off: their ready bulletins wait instead of airing."""
        return [src for src, name in plugins.SOURCE_PLUGIN.items() if not plugins.on(self.cfg, name)]

    async def dispatch(self, cmd: str, req: dict | None = None) -> dict:
        if cmd == "plugins":
            return {"ok": True, "plugins": plugins.listing(self.cfg)}
        if cmd == "plugin":
            plugins.set_plugin(str((req or {}).get("name", "")), bool((req or {}).get("on")))
            self.wake.set()
            return {"ok": True, "plugins": plugins.listing(self.cfg)}
        if cmd == "replay":
            return await self.replay(str((req or {}).get("kind", "")), str((req or {}).get("ref", "")))
        if cmd == "unqueue":
            return await self.unqueue(str((req or {}).get("kind", "")), str((req or {}).get("ref", "")))
        if cmd in ("status", "play"):
            if cmd == "play" and self.state == "PAUSED":
                return await self.dispatch("resume")
            return {"ok": True, "status": self.status()}
        if cmd == "pause" and self.state == "PLAYING":
            await self._pause()
        elif cmd == "resume" and self.state == "PAUSED":
            await self._resume()
        elif cmd == "next":
            self.skip_flag = bool(self.current and self.current["kind"] == "news")
            await self.engine.command("skip")
        elif cmd == "skip-news":
            if not (self.current and self.current["kind"] == "news"):
                return {"ok": False, "error": "no bulletin is on air"}
            self.skip_flag = True
            await self.engine.command("skip")
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
            topics = [r[0] for r in (self.db.execute("SELECT i.topic FROM packages p JOIN items i ON i.id=p.item_id "
                                                     "WHERE p.id=?", (pid,)).fetchone() for pid in ref.split(BREAK_SEP)) if r]
            return " · ".join(dict.fromkeys(timeline.topic_name(t) for t in topics)) or "News"
        return {"id": "Station ID", "reminder": "Reminder", "weather": "Weather"}.get(kind, kind)

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
        return {
            "state": self.state, "session": self.session, "current": cur,
            "current_kind": self.current["kind"] if self.current else None, "current_title": title,
            "elapsed_sec": round((self.paused_at if self.state == "PAUSED" else time.time()) - self.current["started"])
            if self.current else 0,
            "duration_sec": round(self.current.get("duration") or 0) if self.current else 0,
            "news_ready": counts.get("READY", 0) + counts.get("QUEUED", 0), "news_preparing": counts.get("PENDING", 0),
            "spoken_last_hour_sec": round(store.spoken_last_hour(self.db)),
            "speech_limit_sec": self.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"],
            "voicestudio": "ok" if self.tts.health() else "unavailable",
            "llm_calls": self.editor.calls, "sources": self.sources, "last_error": self.last_error,
            "output": self.cfg["output"]["mode"], "auto_paused": self.auto_paused,
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
    svc = Service(cfg, output=output)

    async def runner():
        loop = asyncio.get_running_loop()
        import signal
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.ensure_future(svc.stop()))
        await svc.run()

    asyncio.run(runner())
