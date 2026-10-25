"""Run: python3 -m unittest discover tests   (no VoiceStudio, no speakers, no network needed)"""
import asyncio
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="radio-test-"))
import radio.github_trending  # noqa: E402
radio.github_trending.fetch_readme = lambda owner, repo, **kw: ""  # no network in tests
os.environ["RADIO_DATA_DIR"] = str(TMP / "data")

from radio import config, content, daemon, music, store  # noqa: E402
from radio import settings as radio_settings  # noqa: E402
config.DATA_DIR = TMP / "data"  # isolate even when another test imported config first
config.RUN_DIR = config.DATA_DIR / "run"
config.SOCK = config.RUN_DIR / "radio.sock"
config.DATA_DIR.mkdir(parents=True, exist_ok=True)
# No real AI in tests: the default provider (local Ollama) points at a closed port, so nothing reaches the user's
# Ollama (it loaded a 3 GB model on every test run). Tests that need answers mock urlopen.
(config.DATA_DIR / "config.toml").write_text('[editor]\nbase_url = "http://127.0.0.1:9"\n')
from radio.daemon import Service  # noqa: E402
from radio.tts import MockTTS  # noqa: E402

DIGEST = """# Технологии

- Первый пункт сводки: вышла новая версия компилятора. Подробности в блоге.
- Второй пункт: исследователи показали [новый метод](https://example.com/x) сжатия моделей.

Третий абзац, довольно длинный, чтобы проверить разбиение по предложениям. Здесь второе предложение! А это третье?
"""


def write_digest(name, published, body=DIGEST, topic="Новости технологий"):
    front = f"---\ntopic: {topic}\n" + (f"published_at: {store.iso(published)}\n" if published else "") + "---\n"
    (config.DATA_DIR / "inbox" / name).write_text(front + body, encoding="utf-8")


class ConfigPresetTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory(prefix="radio-presets-")
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        p = patch.object(config, "DATA_DIR", self.data)
        p.start()
        self.addCleanup(p.stop)

    def test_unknown_ai_provider_is_a_clear_error(self):
        (self.data / "config.toml").write_text('[editor]\nprovider = "anthropic"\n')
        with self.assertRaisesRegex(ValueError, "editor.provider"):
            config.load()

    def test_base_requires_only_repository_sounds(self):
        cfg = config.load()
        self.assertEqual(config.audio_presets(), {"selected": "base", "personal_available": False})
        self.assertEqual(cfg["jingles"]["stinger_in"], [config.ASSETS_DIR / "stinger_in.wav"])
        self.assertEqual(cfg["jingles"]["github_intro"], [])

    def test_personal_overlay_preserves_secrets_settings_and_base(self):
        from radio import settings
        original = '[reminders]\ntodoist_token = "private-token"\n[weather]\ncity = "Da Lat"\n'
        (self.data / "config.toml").write_text(original)
        (self.data / "presets").mkdir()
        sound = self.data / "own.wav"
        sound.write_bytes(b"local audio")
        profile = f'[jingles]\nstinger_in = "{sound}"\n'
        (self.data / "presets/personal.toml").write_text(profile)
        settings.set_setting("station_id_every_songs", 7)
        config.select_audio_preset("personal")
        cfg = config.load()
        self.assertEqual(cfg["jingles"]["stinger_in"], [sound])
        self.assertEqual(cfg["reminders"]["todoist_token"], "private-token")
        self.assertEqual(cfg["weather"]["city"], "Da Lat")
        self.assertEqual(next(s["value"] for s in settings.listing(cfg) if s["key"] == "station_id_every_songs"), 7)
        self.assertEqual((self.data / "config.toml").read_text(), original)
        config.select_audio_preset("base")
        self.assertEqual(config.load()["jingles"]["stinger_in"], [config.ASSETS_DIR / "stinger_in.wav"])
        self.assertEqual((self.data / "presets/personal.toml").read_text(), profile)

    def test_invalid_preset_does_not_replace_working_selection(self):
        config.select_audio_preset("base")
        for name in ("personal", "../config", "unknown", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                config.select_audio_preset(name)
        (self.data / "presets").mkdir()
        (self.data / "presets/personal.toml").write_text('[jingles]\nstinger_in = "/missing.wav"\n')
        with self.assertRaises(ValueError):
            config.select_audio_preset("personal")
        self.assertEqual(config.audio_presets()["selected"], "base")
        self.assertTrue(config.load()["jingles"]["stinger_in"][0].exists())


class ContentTests(unittest.TestCase):
    def test_split_is_verbatim_and_on_boundaries(self):
        text = content.speakable(DIGEST)
        self.assertNotIn("https://", text)
        self.assertNotIn("**", text)
        self.assertIn("новый метод сжатия моделей", text)
        for max_chars in (40, 90, 200, 5000):
            parts = content.split_parts(text, max_chars)
            self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))
            for p in parts:
                self.assertRegex(p.strip(), r"[.!?…:]$")  # no part ends mid-sentence

    def test_separators(self):
        md = "# Утро\n\n- Пункт один.\n- Пункт два.\n\n## Экономика\n\n" + "Длинный абзац. " * 30 + "\n\nФинал."
        blocks = content.speakable(md).split("\n\n")
        S = content.SEP
        self.assertEqual([b if b == S else b[:8] for b in blocks],
                         ["Утро.", "Пункт од", "Пункт дв", S, "Экономик", "Длинный ", S, "Финал."])
        script = content.scripts_for({"body": md, "topic": "Сводка"}, 5000)[0]
        segs = content.voice_segments(script)
        self.assertEqual(segs[0], ("Сводка.", False))                  # topic alone: cached once, plain pause
        self.assertEqual([sound for _, sound in segs], [False, False, True, True])
        self.assertFalse(any(m in t for t, _ in segs for m in (S, content.BREAK)))  # markers are never spoken

    def test_pronunciation(self):
        from radio.tts import pronounce
        self.assertEqual(pronounce("GitHub Trending: плагины для Claude Code и Claude, а не claude-cli."),
                         "Гитхаб Трендинг: плагины для Клод Код и Клод, а не claude-cli.")

    def test_time_words(self):
        from datetime import datetime
        from radio.host import time_words
        self.assertEqual([time_words(datetime(2026, 1, 1, h, m)) for h, m in ((23, 5), (9, 0), (0, 41), (21, 12))],
                         ["двадцать три ноль пять", "девять ноль-ноль", "ноль сорок один", "двадцать один двенадцать"])

    def test_tts_removes_urls_before_synthesis_and_cache_lookup(self):
        from radio.tts import cached_synthesize
        config.load()
        said = []
        class Voice(MockTTS):
            def synthesize(self, text, out):
                said.append(text)
                out.write_bytes(b"test audio")
        voice = Voice()
        cached_synthesize(voice, "Командный созвон https://meet.example/room")
        cached_synthesize(voice, "Командный созвон HTTP://other.example/room")
        self.assertEqual(said, ["Командный созвон"])
        with self.assertRaises(ValueError):
            cached_synthesize(voice, "https://meet.example/room")
        self.assertEqual(len(said), 1)

    def test_tts_rejects_bad_cached_audio_before_reusing_it(self):
        from radio.tts import cached_synthesize
        from unittest.mock import Mock
        config.load()
        voice = MockTTS()
        text = "Коротко о погоде."
        out = config.DATA_DIR / "cache/tts" / (voice.cache_key(text) + ".wav")
        out.write_bytes(b"noise")
        voice.synthesize = Mock(side_effect=lambda text, path: path.write_bytes(b"speech"))
        result = cached_synthesize(voice, text, lambda path: None if path.read_bytes() == b"speech" else "noise")
        self.assertEqual(result.read_bytes(), b"speech")
        voice.synthesize.assert_called_once()

    def test_tts_retakes_noisy_clip(self):
        from radio.tts import cached_synthesize

        class FlakyTTS(MockTTS):
            seed, said = 42, []

            def synthesize(self, text, out):
                self.said.append((text, self.seed))
                super().synthesize(text, out)

            def cache_key(self, text):
                return "retake-" + super().cache_key(text)

        config.load()  # creates the cache folders
        tts = FlakyTTS()
        bad_first = lambda path: "noise" if len(tts.said) == 1 else None  # the first take is "noise"
        out = cached_synthesize(tts, "Гитхаб, тренд дня.", bad_first)
        self.assertTrue(out.exists())
        self.assertEqual(tts.said, [("Гитхаб, тренд дня.", 42), ("Гитхаб — тренд дня.", 42)])  # same words, same seed
        self.assertEqual(tts.seed, 42)

    def test_freshness(self):
        n = store.now()
        self.assertTrue(content.is_fresh(n - timedelta(hours=23), 24))
        self.assertFalse(content.is_fresh(n - timedelta(hours=25), 24))
        self.assertFalse(content.is_fresh(None, 24))
        self.assertIsNone(content.parse_time("2026-10-18T12:00:00"))  # no timezone -> unusable


class TrendingTests(unittest.TestCase):
    def test_parse_fixture(self):
        from radio import github_trending as g
        tops = g.parse_list((Path(__file__).parent / "fixtures/trending.html").read_text(), 3)
        self.assertEqual([(t["rank"], t["repo"], t["stars_total"], t["stars_today"]) for t in tops],
                         [(1, "claude-plugins-official", 42218, 1187), (2, "ai-job-search", 51254, 2213),
                          (3, "ECC", 282900, 1073)])
        with self.assertRaises(g.ParseError):
            g.parse_list("<html>new layout</html>")
        self.assertEqual([g._stars_phrase(n) for n in (1, 3, 5, 12, 560, 21345, 51254)],
                         ["1 звезда", "3 звезды", "5 звёзд", "12 звёзд", "более пятисот звёзд",
                          "более двадцати одной тысячи звёзд", "более пятидесяти одной тысячи звёзд"])

    def test_select_top_and_indie(self):
        from radio import github_trending as g
        t = lambda rank, total, desc="Tool.": {"owner": "o", "repo": f"r{rank}", "rank": rank, "stars_total": total,
                                              "stars_today": 100, "description": desc}
        tops = [t(1, 50000), t(2, 900), t(3, 40000), t(4, 1200), t(5, 2000)]
        pick = lambda seen=(): [(x["rank"], role) for x, role in g.select_mix(tops, set(seen), 1, 2, 3000)]
        self.assertEqual(pick(), [(1, "top"), (2, "indie"), (4, "indie")])
        self.assertEqual(pick(["github:o/r1", "github:o/r2"]), [(3, "top"), (4, "indie"), (5, "indie")])
        self.assertFalse(g.airable({"owner": "x", "repo": "nsfw-pose-skill", "description": "LoRA"}, ("nsfw",)))
        self.assertTrue(g.airable({"owner": "x", "repo": "nsfw-pose-skill", "description": "LoRA"}))  # [] = no filter
        self.assertFalse(g.airable({"owner": "x", "repo": "ok", "description": ""}))

    def test_short_bulletin_text(self):
        from radio import github_trending as g
        t = {"owner": "o", "repo": "rocket-app", "rank": 1, "stars_total": 51254, "stars_today": 2213, "description": "x"}
        body = g.to_item(t, "Ракеты для всех.", "top")["body"]
        self.assertTrue(body.endswith("rocket app. Ракеты для всех. Уже более пятидесяти одной тысячи звёзд, "
                                      "и более двух тысяч новых за сутки."), body)  # lead-in names the project
        named = g.to_item(t, "Rocket-app запускает ракеты.", "top")["body"]
        self.assertEqual(named.lower().count("rocket"), 1, named)  # not said twice
        small = dict(t, stars_total=860, stars_today=40)
        self.assertEqual(g.to_item(small, "Ракеты.", "indie")["topic"], "GitHub, находка дня")
        self.assertIn("Пока у него более восьмисот звёзд, но он уже в трендах дня.",
                      g.to_item(small, "Ракеты.", "indie")["body"])
        self.assertIn("Проекту меньше недели", g.to_item(small, "Ракеты.", "new", source="github_new")["body"])
        self.assertIsNone(g.to_item(t, None, "top"))

    def test_llm_output_check(self):
        from radio.editor import check
        self.assertEqual(check("Каталог плагинов для Claude Code"), "Каталог плагинов для Claude Code.")
        self.assertEqual(check("Это маркетплейс плагинов для Cursor."), "Маркетплейс плагинов для Cursor.")
        from radio.editor import fix_name
        self.assertEqual(fix_name("Jasна восстанавливает видео.", "jasna"), "jasna восстанавливает видео.")
        self.assertIsNone(check("SKIP"))
        self.assertIsNone(check("A catalog of plugins for Claude Code."))  # not Russian
        self.assertIsNone(check("Да."))
        self.assertIsNone(check(" ".join(["слово"] * 60)))

    def test_github_mix_never_repeats(self):
        svc = Service(config.load(), tts=MockTTS(), output="x")
        calls = []
        svc.editor.retell = lambda name, desc, readme="": calls.append(name) or "Проект про ракеты."
        r = lambda rank, repo, total: {"owner": "acme", "repo": repo, "rank": rank, "description": "Rockets.",
                                        "language": "", "stars_total": total, "stars_today": 900}
        main = [r(1, "big", 90000), r(2, "huge", 80000)]
        langs = [r(1, "tiny", 500), r(2, "small", 2000), r(3, "mini", 100)]
        new = [r(1, "fresh", 300)]
        self.assertEqual(svc._take_github(main, langs, new),
                         "top acme/big, indie acme/tiny, indie acme/small, new acme/fresh new")
        n = svc.db.execute("SELECT COUNT(*) FROM packages WHERE item_id LIKE 'github:acme/%'").fetchone()[0]
        self.assertEqual(n, 4)  # one short bulletin per repo
        origin = json.loads(svc.db.execute("SELECT origin_json FROM items WHERE id='github:acme/tiny'").fetchone()[0])
        self.assertEqual((origin[0]["role"], origin[0]["description"]), ("indie", "Rockets."))
        self.assertEqual(svc._take_github(main, langs, new), "top acme/huge, indie acme/mini new")
        self.assertEqual(svc._take_github(main, langs, new), "nothing new")
        self.assertEqual(calls, ["big", "tiny", "small", "fresh", "huge", "mini"])  # repeats never reach the LLM


    def test_failed_retelling_gives_slot_to_next(self):
        svc = Service(config.load(), tts=MockTTS(), output="x")
        svc.editor.retell = lambda name, desc, readme="": None if name == "lost" else "Маленький проект."
        r = lambda rank, repo: {"owner": "zeta", "repo": repo, "rank": rank, "description": "Tool.",
                                "language": "", "stars_total": 400, "stars_today": 10}
        self.assertEqual(svc._take_github([], [r(1, "lost"), r(2, "kept"), r(3, "also")], []),
                         "indie zeta/kept, indie zeta/also new")


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        clock_patch = patch('radio.daemon.now', return_value=store.now().replace(minute=0, second=0, microsecond=0))
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.cfg = config.load()
        self.svc = Service(self.cfg, tts=MockTTS(), output="x")
        for t in ("packages", "items", "events", "air_plan"):
            self.svc.db.execute(f"DELETE FROM {t}")

    def test_gap_filler_covers_a_song_youtube_music_has_not_loaded(self):
        from unittest.mock import AsyncMock, Mock, patch
        from radio import plugins
        svc = self.svc
        plugins.set_plugin("gap_filler", True)
        self.addCleanup(plugins.set_plugin, "gap_filler", False)
        svc.ytmusic_active, svc.state = True, "PLAYING"
        svc.gap_voice = [("Секунду.", Path("/tmp/gap.wav"))]
        svc.engine = Mock(next_music=AsyncMock(return_value=""), push_replay=AsyncMock(return_value="7"))
        def song(started):
            svc.current = {"kind": "music", "ref": f"ytmusic:{started}", "started": started, "duration": 200}
        def tick(at):
            with patch("radio.daemon.time.time", return_value=at), patch("radio.audio.duration", return_value=3), \
                 patch("radio.audio.lufs", return_value=-16):
                asyncio.run(svc._gap_tick())
        ready = [{"id": "news#1"}]
        def queue_break(**kw):
            svc.queued_break = ("5", ["news#1"])
        with patch.object(svc, "_pick_break", return_value=ready), \
             patch.object(svc, "schedule", AsyncMock(side_effect=queue_break)) as schedule:
            song(9_000)
            tick(9_000 + 192)                                               # a ready edition fills the gap
            schedule.assert_awaited_once_with(immediate=True, gap=True)
            svc.engine.push_replay.assert_not_awaited()
            svc.queued_break = None
            schedule.side_effect = None
            song(9_100)
            tick(9_100 + 192)                                               # the edition could not be queued: a line
            self.assertEqual(svc.engine.push_replay.await_count, 1)
        svc.engine.push_replay.reset_mock()
        svc.id_pending, svc.gap_said = False, 0.0
        song(10_000)
        tick(10_000 + 150)                                                  # 50 s left: too early to tell
        svc.engine.push_replay.assert_not_awaited()
        tick(10_000 + 192)                                                  # 8 s left, next song not loaded
        self.assertEqual(svc.engine.push_replay.await_args.args[1:3], ("gap", "Секунду."))
        self.assertTrue(svc.id_pending)
        svc.on_event({"kind": "gap", "ref": "Секунду."})
        self.assertFalse(svc.id_pending)
        self.assertEqual(svc.db.execute("SELECT detail FROM events WHERE type='GAP'").fetchone()[0], "Секунду.")
        song(10_300)
        tick(10_300 + 192)                                                  # 5 min later: not again yet
        self.assertEqual(svc.engine.push_replay.await_count, 1)
        song(11_000)
        svc.engine.next_music.return_value = "ytmusic:next"
        tick(11_000 + 192)                                                  # loaded in time: no line
        self.assertEqual(svc.engine.push_replay.await_count, 1)
        song(12_000)
        svc.engine.next_music.return_value = ""
        svc.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"] = 2
        tick(12_000 + 192)                                                  # the speech limit is used up
        self.assertEqual(svc.engine.push_replay.await_count, 1)
        svc.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"] = 300
        svc.ytmusic_active = False
        song(13_000)
        tick(13_000 + 192)                                                  # local music has no loading gaps
        self.assertEqual(svc.engine.push_replay.await_count, 1)

    def test_audio_preset_rebuilds_prepared_packages(self):
        (config.DATA_DIR / "cache/audio-config.json").unlink(missing_ok=True)
        self.add("preset-story", "inbox", 0)
        db = self.svc.db
        self.svc._refresh_audio_cache()
        self.assertEqual(db.execute("SELECT status FROM packages").fetchone()[0], "PENDING")
        db.execute("UPDATE packages SET status='READY'")
        self.svc._refresh_audio_cache()
        self.assertEqual(db.execute("SELECT status FROM packages").fetchone()[0], "READY")
        self.svc.cfg["jingles"]["stinger_in"] = [Path("/local/personal-intro.wav")]
        self.svc._refresh_audio_cache()
        self.assertEqual(db.execute("SELECT status FROM packages").fetchone()[0], "PENDING")
        self.assertTrue(db.execute("SELECT script_path FROM packages").fetchone()[0])

    def test_auto_pause_without_listeners(self):
        svc, sent = self.svc, []

        class Engine:
            async def command(self, c):
                sent.append(c)
        svc.engine, svc.state, svc.current = Engine(), "PLAYING", {"kind": "music", "ref": "/m/1.mp3", "started": 0}
        svc.cfg = dict(self.cfg, output=dict(self.cfg["output"], idle_pause_sec=0))
        beat = config.RUN_DIR / "listeners.json"

        async def scenario():
            task = asyncio.create_task(svc.listeners_loop())
            beat.write_text(json.dumps({"n": 0, "t": time.time()}))
            await asyncio.sleep(1.5)
            paused = svc.state, svc.auto_paused
            beat.write_text(json.dumps({"n": 1, "t": time.time()}))
            await asyncio.sleep(1.5)
            svc.stopping = True
            await task
            return paused

        try:
            self.assertEqual(asyncio.run(scenario()), ("PAUSED", True))
        finally:
            beat.unlink(missing_ok=True)
        self.assertEqual((svc.state, svc.auto_paused, sent), ("PLAYING", False, ["pause", "resume"]))
        beat.write_text(json.dumps({"n": 3, "t": time.time() - 60}))  # UI server gone: stale beat = nobody
        self.assertEqual(daemon.listeners(), 0)
        beat.unlink()

    def add(self, item_id, source, hours_ago, status="READY", played_minutes_ago=None):
        db = self.svc.db
        content.add_item(db, self.cfg, {"id": item_id, "topic": "T", "source": source, "url": "",
                                        "published_at": store.now() - timedelta(hours=hours_ago),
                                        "body": "Текст сюжета."}, kind="x")
        played = store.iso(store.now() - timedelta(minutes=played_minutes_ago)) if played_minutes_ago else None
        db.execute("UPDATE packages SET status=?, played_at=?, speech_sec=5, audio_path='/tmp/x.wav' WHERE item_id=?",
                   (status, played, item_id))

    def test_round_robin(self):
        self.add("g-old", "github_trending", 2, "PLAYED", played_minutes_ago=5)
        self.add("g-new", "github_trending", 3)  # older than the digest, but github aired last
        self.add("d-new", "chatgpt", 1)
        self.assertEqual(content.next_to_air(self.svc.db)["item_id"], "d-new")
        self.add("d-old", "chatgpt", 2, "PLAYED", played_minutes_ago=1)
        self.assertEqual(content.next_to_air(self.svc.db)["item_id"], "g-new")

    def fake_engine(self, pushed):
        class FakeEngine:
            async def push_station_id(self, f, gain, tail=0.0, expires=0.0):
                pushed.append(("id", f.name))

            async def push_news(self, wav, pid, talkover=0.0, expires=0.0):
                pushed.append(("news", pid, wav.name))

            async def next_music(self):
                return "/m/Next Artist - Song.mp3"

            async def push_replay(self, wav, kind, ref, gain, rid, tags="", queue="news"):
                pushed.append((queue, ref))
                return str(len(pushed))

            async def remove(self, rid, queue="news"):
                pushed.append(("removed", rid, queue))
        self.svc.engine, self.svc.state = FakeEngine(), "PLAYING"

    def test_single_bulletin_without_host_links_queues_without_new_tracks(self):
        from radio import plugins
        from unittest.mock import AsyncMock, Mock, patch
        original = plugins.on
        for item_id, links in (("system:plain", True), ("digest:plain", False)):
            with self.subTest(item_id=item_id):
                for table in ("packages", "items", "events"):
                    self.svc.db.execute(f"DELETE FROM {table}")
                pushed = []
                self.fake_engine(pushed)
                self.add(item_id, "inbox", 0)
                self.svc.current = {"kind": "music", "started": time.time(), "duration": 30}
                self.svc.songs_since_break = 1
                self.svc._queue_fresh = AsyncMock()
                self.svc._assemble_break = Mock()
                with patch.object(plugins, "on", side_effect=lambda c, n: links if n == "host_links" else original(c, n)):
                    asyncio.run(self.svc.schedule())
                self.assertEqual(len(pushed), 1)
                self.assertEqual(self.svc.db.execute("SELECT status FROM packages").fetchone()[0], "QUEUED")
                self.svc._queue_fresh.assert_not_awaited()
                self.svc._assemble_break.assert_not_called()

    def test_station_id_is_not_triggered_by_song_starts(self):
        pushed = []
        self.fake_engine(pushed)
        self.cfg['broadcast']['station_id_every_songs'] = 3
        self.svc.defaults = radio_settings.base(self.cfg)
        self.clock.return_value += timedelta(minutes=10)
        for i in range(10):
            self.svc.on_event({'kind': 'music', 'ref': f'/m/{i}.mp3'})
            asyncio.run(self.svc.schedule())
        self.assertEqual(pushed, [])

    def test_late_assembly_waits_for_song_end(self):
        pushed, got = [], []
        self.fake_engine(pushed)
        self.svc._assemble_break = lambda pkgs, air, links, first, room, novelty: (got.append((first, room)), (Path(f"/tmp/live-{air:%H%M}.wav"), 0))[1]
        self.svc.intro_rooms = {"/m/Next Artist - Song.mp3": 6.5}
        self.svc.db.execute("INSERT OR REPLACE INTO tracks(path, sig, artist, title) VALUES(?,?,?,?)",
                            ("/m/Next Artist - Song.mp3", "", "Next Artist", "Song"))
        self.add("d", "chatgpt", 1)
        self.svc.current = {"kind": "music", "ref": "/m/a.mp3", "started": time.time(), "duration": 200.0}
        self.svc.songs_since_break = 1
        asyncio.run(self.svc.schedule())
        self.assertEqual(pushed, [])                          # 200 s left: too early for the time check
        self.svc.current["started"] -= 170                    # 30 s left
        asyncio.run(self.svc.schedule())
        self.assertEqual(len(pushed), 1)
        self.assertTrue(pushed[0][2].startswith("live-"))     # the assembled break went out
        self.assertEqual(got, [(("Next Artist", "Song"), 6.5)])  # set opener names the next song, over its intro

    def test_breaks_end_music_sets(self):
        pushed = []
        self.fake_engine(pushed)
        self.svc._assemble_break = lambda pkgs, air, links, first, room, novelty: (Path("/tmp/break.wav"), 0)
        self.cfg["broadcast"]["station_id_every_songs"] = 0
        self.cfg["broadcast"]["music_set_minutes"] = 20
        self.add("a", "chatgpt", 1)
        self.add("github:b", "github_trending", 1)
        self.svc.on_event({"kind": "music", "ref": "/m/1.mp3"})
        asyncio.run(self.svc.schedule())
        self.assertEqual(len(pushed), 1)                       # first break of the session: after one song
        self.assertEqual(pushed[0][1], "a#1|github:b#1")              # both bulletins in one break, rotation order
        self.svc.on_event({"kind": "news", "ref": pushed[0][1]})
        self.add("c", "chatgpt", 1)
        self.svc.on_event({"kind": "music", "ref": "/m/2.mp3"})
        asyncio.run(self.svc.schedule())
        self.assertEqual(len(pushed), 1)                       # a set has just started: no break
        self.clock.return_value += timedelta(minutes=30)
        self.svc.set_started -= 19 * 60 + 55                   # the set reaches 20 minutes at this song's end
        asyncio.run(self.svc.schedule())
        self.assertEqual([p[1] for p in pushed[1:]], ["c#1"])

    def test_playlist_talk_up_points(self):
        from radio import music
        db = self.svc.db
        files = [TMP / "long-tail.mp3", TMP / "short-tail.mp3"]
        for f, tail in zip(files, (5.5, 0.5)):
            f.write_bytes(b"x")
            db.execute("INSERT OR REPLACE INTO tracks(path, sig, lufs, ok, tail_sec) VALUES(?,?,?,?,?)",
                       (str(f), music._sig(f), -16.0, 1, tail))
        m3u = TMP / "t.m3u"
        music.write_m3u(db, files, m3u, -16.0, 4.0, xfade=3.0)
        long_line, short_line = m3u.read_text().splitlines()[1:]
        # news starts 1.5 s before the audible end: window 7 s from the end, at its start
        self.assertIn('liq_cross_end_duration="7.00",liq_talk_up="0.00"', long_line)
        # default 4 s window: news 2 s into it, i.e. 1.5 s before the audible end at -0.5 s
        self.assertNotIn("liq_cross_end_duration", short_line)
        self.assertIn('liq_talk_up="2.00"', short_line)

    def test_names_come_from_tags(self):
        from radio import audio, music
        f = TMP / "untitled-file-name.mp3"
        cover = TMP / "cover.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=16x16:d=1", "-frames:v", "1", str(cover)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1", "-i", str(cover), "-map", "0", "-map", "1",
                        "-c:v", "png", "-disposition:v", "attached_pic", "-metadata:s:v", "title=Cover",
                        "-metadata", "artist=Glimlip/Rosko", "-metadata", "title=Could You Check Her Bag?", str(f)], check=True)
        self.assertEqual(audio.tags(f), ("Glimlip/Rosko", "Could You Check Her Bag?"))
        db = self.svc.db
        db.execute("INSERT OR REPLACE INTO tracks(path, sig, artist, title) VALUES(?,?,?,?)", (str(f), "", *audio.tags(f)))
        self.assertEqual(music.song(db, f), ("Glimlip, Rosko", "Could You Check Her Bag?"))
        self.assertEqual(music.label(db, f), "Glimlip, Rosko - Could You Check Her Bag?")
        db.execute("UPDATE tracks SET artist='' WHERE path=?", (str(f),))     # untagged: not named, file name shown
        self.assertIsNone(music.song(db, f))
        self.assertEqual(music.label(db, f), "untitled-file-name")

    def test_vocal_start_from_synced_lyrics(self):
        from radio import lyrics, music
        synced = "[00:00.50] \n[00:19.85] Hello there\n[00:22.10] next"
        found = [{"duration": 200.0, "syncedLyrics": "[00:05.00] wrong version"},
                 {"duration": 184.0, "syncedLyrics": synced}]
        self.assertEqual(lyrics.pick(found, 183.4), ("synced", 19.85, 26.1))   # the version of the same length
        self.assertEqual(lyrics.pick([{"duration": 120, "instrumental": True}], 121), ("instrumental", None, None))
        self.assertEqual(lyrics.pick([{"duration": 300, "syncedLyrics": synced}], 184), ("none", None, None))
        self.assertEqual(lyrics.pick([], 184), ("none", None, None))
        self.assertEqual(lyrics.last_line_end_sec("[00:10.00] a\n[03:12.50] last words\n[03:16.00] "), 196.0)  # end mark
        self.assertEqual(lyrics.last_line_end_sec("[00:10.00] a\n[03:12.50] last words"), 196.5)  # guess: + 4 s
        row = lambda st, v: {"lyrics": st, "vocal_sec": v}
        self.assertEqual(music.intro_room(row("synced", 19.85)), 12.0)       # long intro: capped
        self.assertAlmostEqual(music.intro_room(row("synced", 7.6)), 5.1)  # done 2.5 s before the vocals
        self.assertIsNone(music.intro_room(row("synced", 3.0)))           # no room to talk
        self.assertEqual(music.intro_room(row("instrumental", None)), 12.0)
        self.assertIsNone(music.intro_room(row("none", None)))

    def test_vocal_timing_skips_a_song_deleted_while_playing(self):
        import threading
        from radio import music
        db = self.svc.db
        gone, kept = TMP / "Gone - Song.mp3", TMP / "Kept - Song.mp3"
        for f in (gone, kept):
            f.write_bytes(b"z")
            db.execute("INSERT OR REPLACE INTO tracks(path, sig, ok, lyrics, artist, title) VALUES(?,?,1,'none','A','T')",
                       (str(f), music._sig(f)))
        gone.unlink()                                            # renamed in Finder while the radio runs
        self.assertEqual(music.to_hear(db, [gone, kept]), kept)  # no crash: the next song gets its turn
        music.find_vocals(db, [gone], threading.Event())         # no crash in the background thread either

    def test_playlist_outro_talk_point(self):
        from radio import music
        db = self.svc.db
        f = TMP / "Artist - Outro Song.mp3"
        f.write_bytes(b"z")
        db.execute("INSERT OR REPLACE INTO tracks(path, sig, lufs, ok, tail_sec, lyrics, vocal_sec, outro_sec) "
                   "VALUES(?,?,?,?,?,?,?,?)", (str(f), music._sig(f), -16.0, 1, 0.5, "synced", 10.0, 9.0))
        m3u = TMP / "o.m3u"
        music.write_m3u(db, [f], m3u, -16.0, 4.0, xfade=3.0, outro_talk=True)
        # 9 s after the last sung line: start 1.5 s after it, i.e. 7.5 s before the end of the file
        self.assertIn('liq_cross_end_duration="7.50",liq_talk_up="0.00"', m3u.read_text())
        music.write_m3u(db, [f], m3u, -16.0, 4.0, xfade=3.0, outro_talk=False)   # plugin off: 1.5 s before the audible end
        self.assertNotIn("liq_cross_end_duration", m3u.read_text())
        db.execute("UPDATE tracks SET outro_sec=2.0 WHERE path=?", (str(f),))      # vocals run to the end: as before
        music.write_m3u(db, [f], m3u, -16.0, 4.0, xfade=3.0, outro_talk=True)
        self.assertNotIn("liq_cross_end_duration", m3u.read_text())

    def test_playlist_intro_windows(self):
        from radio import music
        db = self.svc.db
        f = TMP / "Artist - Intro Song.mp3"
        f.write_bytes(b"y")
        db.execute("INSERT OR REPLACE INTO tracks(path, sig, lufs, ok, tail_sec, lyrics, vocal_sec) VALUES(?,?,?,?,?,?,?)",
                   (str(f), music._sig(f), -16.0, 1, 0.5, "synced", 10.0))
        m3u = TMP / "i.m3u"
        rooms = music.write_m3u(db, [f], m3u, -16.0, 4.0, xfade=3.0)
        self.assertEqual(rooms, {str(f): 7.5})
        self.assertIn('liq_cross_start_duration="8.50"', m3u.read_text())  # the overlap fits the start window

    def test_section_intro_or_short_phrase(self):
        svc = self.svc
        svc.cfg = dict(self.cfg, jingles=dict(self.cfg["jingles"],
                                            horizon_intro=self.cfg["jingles"]["stinger_in"],
                                            github_intro=self.cfg["jingles"]["stinger_in"]))
        def pkg(pid, body):
            f = TMP / (pid.replace(":", "_").replace("/", "_") + ".txt")
            f.write_text(body, encoding="utf-8")
            return {"id": pid, "script_path": str(f)}
        news = pkg("horizon:1", "Новости технологий.\n\n⁑\n\nТекст новости.")   # a script made before the intro existed
        repo = pkg("github:a/b", "Текст про репозиторий.")
        said = lambda p, prev: [t for t, _ in svc._body_segments(p, prev)]
        self.assertEqual(said(news, None), ["Текст новости."])                         # opens the break: the intro jingle names it
        self.assertEqual(said(news, "github"), ["Новости технологий.", "Текст новости."])  # follows GitHub: short phrase
        self.assertEqual(said(news, "horizon"), ["Текст новости."])                    # same section goes on
        self.assertEqual(said(repo, "horizon"), ["GitHub.", "Текст про репозиторий."])
        self.assertEqual(said(repo, None), ["Текст про репозиторий."])
        svc.cfg = dict(self.cfg, jingles=dict(self.cfg["jingles"], horizon_intro=[]))  # no intro configured: unchanged
        self.assertEqual(said(news, "github"), ["Новости технологий.", "Текст новости."])
        self.assertEqual(said(news, None), ["Новости технологий.", "Текст новости."])

    def test_new_tracks_set(self):
        pushed, said = [], []
        self.fake_engine(pushed)
        self.svc._assemble_break = lambda pkgs, air, links, first, room, novelty: (said.append((first, novelty)), (Path("/tmp/b.wav"), 0))[1]
        songs = []
        for i, (artist, title) in enumerate([("Old", "Aired"), ("New", "One"), ("New", "Two"), ("New", "Three")]):
            f = TMP / f"{artist} - {title}.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=30", str(f)], check=True)
            self.svc.db.execute("INSERT OR REPLACE INTO tracks(path, sig, lufs, ok, artist, title) VALUES(?,?,?,?,?,?)",
                                (str(f), "", -16.0, 1, artist, title))
            songs.append(f)
        self.svc.db.execute("INSERT OR REPLACE INTO tracks(path, sig, artist, title) VALUES(?,?,?,?)",
                            ("/m/Next Artist - Song.mp3", "", "Next Artist", "Song"))
        old = songs[0]
        os.utime(old, (time.time() - 40 * 86400,) * 2)                       # added long ago: not new
        b = self.cfg["broadcast"]
        b.update(new_every_sets=2, new_days=14, new_min_tracks=3, music_set_minutes=1, station_id_every_songs=0)
        self.svc.defaults = radio_settings.base(self.cfg)  # these edits stand for config.toml values
        self.svc.files = songs
        self.svc.intro_rooms = {str(f): None for f in songs}
        self.svc.replays = {}
        self.add("a", "chatgpt", 1)
        self.add("b", "chatgpt", 1)
        self.svc.on_event({"kind": "music", "ref": str(old)})
        self.svc.current["duration"], self.svc.current["started"] = 30.0, time.time() - 29
        asyncio.run(self.svc.schedule())                                      # unplayed songs already take priority
        self.assertEqual(said, [(("New", "Three"), True)])
        self.assertEqual([p[0] for p in pushed], ["fresh", "fresh", "news"])
        self.svc.on_event({"kind": "news", "ref": next(ref for kind, ref, *_ in pushed if kind == 'news')})
        self.svc.on_event({"kind": "music", "ref": str(old)})
        self.add("c", "chatgpt", 1)
        self.clock.return_value += timedelta(minutes=30)
        self.svc.set_started -= 19 * 60 + 55
        self.svc.current["duration"], self.svc.current["started"] = 30.0, time.time() - 29
        asyncio.run(self.svc.schedule())                                      # break 2: the new tracks (1 min = 2 songs)
        self.assertEqual(said[1], (("New", "Three"), True))                  # newest first, announced as a new-tracks set
        self.assertEqual([p for p in pushed if p[0] == "fresh"], [("fresh", str(songs[3])), ("fresh", str(songs[2]))])
        self.svc.on_event({"kind": "music", "ref": str(songs[3])})           # aired: no longer new
        self.assertEqual([f.name for f in music.unaired_new(self.svc.db, songs, 14)], ["New - Two.mp3", "New - One.mp3"])

    def test_reminder_flow(self):
        from radio import reminders
        pushed = []

        class FakeEngine:
            async def push_replay(self, wav, kind, ref, gain, rid, tags="", queue="news"):
                pushed.append((kind, ref, wav.name))
                return "1"
        svc = self.svc
        svc.engine, svc.state = FakeEngine(), "PLAYING"
        svc._render_notice = lambda text, fallback="": Path("/tmp/notice.wav")
        svc.cfg = dict(self.cfg, reminders=dict(self.cfg["reminders"], calendar_url="https://example.test/secret.ics",
                                                todoist_token="tok"))
        soon = (self.clock.return_value + timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
        ics = ReminderTests.ics(f"UID:e1\r\nSUMMARY:Стендап\r\nDTSTART:{soon}")
        real = reminders.fetch_ics, reminders.fetch_todoist
        reminders.fetch_ics, reminders.fetch_todoist = lambda url: ics, lambda token: []
        try:
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.sources["calendar"], "ok")
            self.assertEqual(len(svc.reminders_due), 1)
            asyncio.run(svc._reminders_tick())
            self.assertEqual(len(svc.reminders_due), 1)                     # not added twice
            self.assertTrue(asyncio.run(svc._remind(300.0)))                # prepare while TTS still has time
            key = pushed[0][1]
            self.assertEqual(pushed[0][0], "reminder")
            self.assertIn("через пять минут", svc.reminder_text[key].lower())
            self.assertIn("Стендап", svc.reminder_text[key])
            self.assertTrue(svc.id_pending and not svc.reminders_due)
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.reminders_due, {})                         # announced once
            svc.on_event({"kind": "reminder", "ref": key})
            self.assertFalse(svc.id_pending)
            row = svc.db.execute("SELECT type, detail FROM events WHERE type='REMINDER' ORDER BY id DESC").fetchone()
            self.assertIn("Стендап", row["detail"])
            from radio import plugins
            plugins.set_plugin("reminders_calendar", True)                 # switched on by hand without a link
            svc.cfg["reminders"]["calendar_url"] = ""                       # not set up: said in the status, nothing read
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.sources["calendar"], "not set up")
            def boom(url):
                raise OSError("https://example.test/secret.ics unreachable")
            svc.cfg["reminders"]["calendar_url"] = "https://example.test/secret.ics"
            svc.next_read["calendar"] = 0.0
            reminders.fetch_ics = boom
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.sources["calendar"], "error: OSError")    # the link is never shown
        finally:
            reminders.fetch_ics, reminders.fetch_todoist = real
            (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)
        svc.db.execute("DELETE FROM reminders")

    def test_timed_reminder_prepares_early_and_expires_while_paused(self):
        from unittest.mock import AsyncMock, Mock, patch
        svc, at = self.svc, store.now()
        svc.state = "PLAYING"
        svc.current = {"kind": "music", "started": time.time(), "duration": 300}
        svc.engine = Mock(push_replay=AsyncMock(return_value="42"))
        svc.engine.remove = AsyncMock()
        svc._render_notice = lambda text, fallback="": Path("/tmp/notice.wav")
        r = {"key": "todoist:early", "kind": "task", "start": at + timedelta(minutes=10), "title": "Созвон"}
        svc.reminders_due = {r["key"]: r}
        with patch.object(daemon, "now", return_value=at):
            self.assertTrue(asyncio.run(svc._remind(300)))
        self.assertIn("через пять минут", svc.reminder_text[r["key"]].lower())
        self.assertIsNone(svc.db.execute("SELECT 1 FROM reminders WHERE key=?", (r["key"],)).fetchone())
        svc.state = "PAUSED"
        with patch.object(daemon, "now", return_value=r["start"] + timedelta(seconds=1)):
            asyncio.run(svc._reminders_tick())
        svc.engine.remove.assert_awaited_once_with("42")
        self.assertFalse(svc.id_pending)

    def test_timed_reminder_render_crossing_song_boundary_is_discarded(self):
        from unittest.mock import AsyncMock, Mock, patch
        svc, at = self.svc, store.now()
        svc.current = {"kind": "music", "started": time.time(), "duration": 30}
        svc.engine = Mock(push_replay=AsyncMock(return_value="42"))
        def render(text, fallback=""):
            svc.current = {"kind": "music", "started": time.time(), "duration": 240}
            return Path("/tmp/notice.wav")
        svc._render_notice = render
        r = {"key": "todoist:cross", "kind": "task", "start": at + timedelta(minutes=2), "title": "Созвон"}
        svc.reminders_due = {r["key"]: r}
        with patch.object(daemon, "now", return_value=at):
            self.assertFalse(asyncio.run(svc._remind(20)))
        svc.engine.push_replay.assert_not_awaited()
        self.assertIn(r["key"], svc.reminders_due)

    def test_pause_removes_notice_before_resume_and_recalculates_countdown(self):
        from unittest.mock import AsyncMock, Mock, patch
        svc, at = self.svc, store.now()
        svc.state = "PLAYING"
        svc.current = {"kind": "music", "started": time.time(), "duration": 300}
        svc.engine = Mock(push_replay=AsyncMock(return_value="42"), remove=AsyncMock(), command=AsyncMock())
        svc._render_notice = lambda text, fallback="": Path("/tmp/notice.wav")
        svc.cfg["reminders"]["todoist_token"] = "test"
        r = {"key": "todoist:pause", "kind": "task", "start": at + timedelta(minutes=10), "title": "Созвон"}
        svc.reminders_due = {r["key"]: r}
        with patch.object(daemon, "now", return_value=at):
            self.assertTrue(asyncio.run(svc._remind(300)))
            asyncio.run(svc._pause())
        self.assertEqual(svc.engine.mock_calls[-2:], [unittest.mock.call.command("pause"), unittest.mock.call.remove("42")])
        self.assertFalse(svc.queued_reminders)
        self.assertIn(r["key"], svc.reminders_due)
        with patch.object(daemon, "now", return_value=at + timedelta(minutes=2)):
            asyncio.run(svc._resume())
            self.assertTrue(asyncio.run(svc._remind(300)))
        self.assertIn("через три минуты", svc.reminder_text[r["key"]].lower())

    def test_slow_notice_missing_boundary_is_not_marked_as_announced(self):
        from unittest.mock import AsyncMock, Mock, patch
        svc, at = self.svc, store.now()
        svc.engine = Mock(push_replay=AsyncMock())
        clock = [at]
        def render(text, fallback=""):
            clock[0] += timedelta(seconds=35)
            return Path("/tmp/notice.wav")
        svc._render_notice = render
        r = {"key": "todoist:slow", "kind": "task", "start": at + timedelta(minutes=2), "title": "Созвон"}
        svc.reminders_due = {r["key"]: r}
        with patch.object(daemon, "now", side_effect=lambda: clock[0]):
            self.assertFalse(asyncio.run(svc._remind(20)))
        svc.engine.push_replay.assert_not_awaited()
        self.assertIsNone(svc.db.execute("SELECT 1 FROM reminders WHERE key=?", (r["key"],)).fetchone())

    def test_todoist_link_reaches_persistent_timeline_without_being_spoken(self):
        from radio import reminders, timeline
        from unittest.mock import AsyncMock, Mock, patch
        svc, at = self.svc, store.now()
        task = {"id": "linked", "content": "Созвон", "description": "Командная встреча\nhttps://meet.example/room\nОбсудить планы.",
                "due": {"date": (at + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")}}
        r, = reminders.todoist_due([task], at, timedelta(minutes=15))
        self.assertEqual(r["description"], task["description"])
        svc.reminders_due = {r["key"]: r}
        svc.engine = Mock(push_replay=AsyncMock(return_value="42"))
        spoken = []
        svc._render_notice = lambda text, fallback="": spoken.append(text) or Path("/tmp/notice.wav")
        with patch.object(daemon, "now", return_value=at):
            self.assertTrue(asyncio.run(svc._remind(20)))
            svc.on_event({"kind": "reminder", "ref": r["key"]})
            svc.cfg["reminders"].update(todoist_token="test", calendar_url="")
            with patch.object(reminders, "fetch_todoist", return_value=[task]):
                asyncio.run(svc._reminders_tick())
                self.assertFalse(svc.reminders_due)
        self.assertNotIn("https://", spoken[0])
        self.assertNotIn("Обсудить планы", spoken[0])
        reopened = store.connect()
        try:
            e = next(e for e in timeline._build(reopened, 12, 50) if e["kind"] == "reminder")
            self.assertEqual(e["title"], spoken[0])
            self.assertEqual(e["description"], task["description"])
        finally:
            reopened.close()

    def test_all_day_mention_flow_and_restart(self):
        from radio import plugins, reminders
        from unittest.mock import patch
        svc, pushed, rendered = self.svc, [], []
        svc.db.execute("DELETE FROM reminders")
        at = datetime(2026, 10, 20, 9, tzinfo=ReminderTests.MSK)
        ics = ReminderTests.ics("UID:holiday\r\nSUMMARY:День радио\r\nDTSTART;VALUE=DATE:20261020\r\n"
                                "DESCRIPTION:В этот день проходят встречи радиолюбителей.")
        svc.cfg = dict(self.cfg, reminders=dict(self.cfg["reminders"], calendar_url="https://example.test/cal.ics"))

        class Engine:
            async def push_replay(self, wav, kind, ref, gain, rid, tags=""):
                pushed.append((kind, ref))

        svc.engine = Engine()
        svc._render_day = lambda r, text: (rendered.append(text) or text, Path("/tmp/day.wav"))
        try:
            with patch.object(reminders, "fetch_ics", return_value=ics), patch.object(daemon, "now", return_value=at), patch("radio.content.now", return_value=at), patch.object(daemon.audio, "duration", return_value=5):
                asyncio.run(svc._reminders_tick())
                self.assertEqual([r["kind"] for r in svc.reminders_due.values()], ["day"])
                self.assertFalse(asyncio.run(svc._remind(300)))
                svc.set_started = time.time()
                self.assertFalse(asyncio.run(svc._remind(20)))
                self.assertEqual(rendered, ["Сегодня — День радио."])
                self.assertEqual(pushed, [])
                pid = svc.db.execute("SELECT id FROM packages WHERE item_id LIKE 'system:day:%'").fetchone()[0]
                svc.on_event({'kind': 'news', 'ref': pid})
                script = svc.db.execute('SELECT script_path FROM packages WHERE id=?', (pid,)).fetchone()[0]
                self.assertIn('День радио', Path(script).read_text())
                restarted = Service(svc.cfg, tts=MockTTS(), output="x")
                try:
                    restarted.cal_events = reminders.parse_events(ics, ReminderTests.MSK)
                    self.assertIsNone(restarted._day_due(at + timedelta(hours=3)))
                    self.assertEqual(restarted._day_due(at + timedelta(hours=4))["count"], 1)
                finally:
                    restarted.db.close()
                asyncio.run(svc._reminders_tick())
                self.assertFalse(svc.reminders_due)
            svc.db.execute("DELETE FROM reminders")
            with patch.object(daemon, "now", return_value=at):
                plugins.set_plugin("host_links", False)
                asyncio.run(svc._reminders_tick())
                self.assertTrue(svc.reminders_due)
                plugins.set_plugin("host_links", True)
                asyncio.run(svc._reminders_tick())
                self.assertTrue(svc.reminders_due)
                plugins.set_plugin("reminders_calendar", False)
                self.assertFalse(asyncio.run(svc._remind(20)))  # disabled while waiting
                self.assertFalse(svc.reminders_due)
        finally:
            svc.db.execute("DELETE FROM reminders")
            (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)

    def test_timed_reminder_before_day_mention(self):
        svc, pushed = self.svc, []
        at = store.now()

        class Engine:
            async def push_replay(self, wav, kind, ref, gain, rid, tags=""):
                pushed.append(ref)

        svc.engine = Engine()
        svc._render_notice = lambda text, fallback="": Path("/tmp/notice.wav")
        svc.reminders_due = {
            "day:test": {"kind": "day", "start": at.replace(hour=0)},
            "cal:urgent": {"kind": "event", "start": at + timedelta(minutes=10), "title": "Встреча"},
        }
        try:
            self.assertTrue(asyncio.run(svc._remind(20)))
            self.assertEqual(pushed, ["cal:urgent"])
            self.assertIn("day:test", svc.reminders_due)
        finally:
            svc.db.execute("DELETE FROM reminders WHERE key='cal:urgent'")

    def test_calendar_story_fallback(self):
        from unittest.mock import patch
        svc = self.svc
        r = {"title": "День радио", "description": "Факты из календаря.", "count": 0}
        with patch.object(daemon, "cached_synthesize", return_value=Path("/tmp/day.wav")) as synth, \
                patch.object(svc.editor, "retell_day", return_value="Сегодня встречаются радиолюбители.") as retell:
            self.assertEqual(svc._render_day(r, "Сегодня — День радио.")[0], "Сегодня встречаются радиолюбители.")
            retell.assert_called_once_with(r["title"], r["description"])
            retell.reset_mock()
            self.assertEqual(svc._render_day({**r, "count": 1}, "К слову, сегодня — День радио.")[0],
                             "К слову, сегодня — День радио.")
            self.assertEqual(svc._render_day({**r, "description": ""}, "Сегодня — День радио.")[0],
                             "Сегодня — День радио.")
            retell.assert_not_called()
            retell.side_effect = OSError("offline")
            self.assertEqual(svc._render_day(r, "Сегодня — День радио.")[0], "Сегодня — День радио.")
            retell.side_effect = None
            retell.return_value = None
            self.assertEqual(svc._render_day(r, "Сегодня — День радио.")[0], "Сегодня — День радио.")
            retell.return_value = "Сегодня встречаются радиолюбители."
            synth.side_effect = [RuntimeError("bad voice"), Path("/tmp/short.wav")]
            self.assertEqual(svc._render_day(r, "Сегодня — День радио."),
                             ("Сегодня — День радио.", Path("/tmp/short.wav")))

    def test_timeline_merges_a_break_and_titles_are_clean(self):
        from radio import timeline
        svc, db = self.svc, self.svc.db
        self.add("horizon:1", "horizon", 1, "PLAYED")
        self.add("github:b", "github_trending", 1, "PLAYED")
        db.execute("UPDATE items SET topic='Новости технологий' WHERE id='horizon:1'")
        db.execute("UPDATE items SET topic='GitHub, находка дня' WHERE id='github:b'")
        for pid in ("horizon:1#1", "github:b#1"):                    # one break: the stories start together
            store.log_event(db, svc.session, "PACKAGE_STARTED", pid)
        store.log_event(db, svc.session, "MUSIC_STARTED", "/m/Artist - Song.mp3")
        entries = timeline._build(db, 12, 50)
        news = [e for e in entries if e["kind"] == "news"]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["title"], "Host edition")
        self.assertEqual(len(news[0]["parts"]), 2)
        self.assertEqual(news[0]["parts"][0]["title"], "Tech news")      # in the order they were said
        self.assertFalse(any(k in news[0] for k in ("_at", "_topics")))
        svc.current = {"kind": "news", "ref": "horizon:1#1|github:b#1", "started": time.time()}
        self.assertEqual(svc._title(), "Host edition")  # no "1/1" or length
        for kind, title in (("id", "Station ID"), ("reminder", "Reminder")):
            svc.current = {"kind": kind, "ref": "x", "started": time.time()}
            self.assertEqual(svc.status()["current_title"], title)           # status must not fail on these

    def test_stop_ends_a_horizon_run_in_flight(self):
        import threading
        from unittest.mock import patch
        from radio import horizon
        h = horizon.folder(self.cfg)
        (h / "data").mkdir(parents=True, exist_ok=True)
        (h / "data/config.json").write_text("{}")
        bin_dir = TMP / "fake-uv"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "uv").write_text("#!/bin/sh\nsleep 30\n")          # a child process, as uv starts horizon
        (bin_dir / "uv").chmod(0o755)
        errors = []
        def run():
            try:
                horizon.run(self.cfg)
            except Exception as e:
                errors.append(e)
        from unittest.mock import Mock
        proxy = Mock()
        with patch.object(horizon, "_proxy", proxy), \
             patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ['PATH']}"}):
            worker = threading.Thread(target=run)
            worker.start()
            while horizon._running is None:
                time.sleep(.05)
            started = time.monotonic()
            horizon.stop()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsInstance(errors[0], RuntimeError)
        self.assertIsNone(horizon._running)
        proxy.server_close.assert_called_once()   # the Horizon port is free before the next radio starts
        self.assertIsNone(horizon._proxy)

    def test_voicestudio_server_started_and_stopped_with_the_radio(self):
        from radio import plugins
        from radio import tts as tts_mod
        started = []

        class Proc:
            pid, alive = 123, True

            def poll(self):
                return None if self.alive else 0

            def terminate(self):
                self.alive = False

            def wait(self, timeout=None):
                return 0

        class Voice:
            def __init__(self, ok):
                self.ok = ok

            def health(self):
                return self.ok
        real = tts_mod.start_backend, tts_mod.port_open
        tts_mod.start_backend = lambda project, port, log: (started.append((project, port)), Proc())[1]
        tts_mod.port_open = lambda port: False
        svc = self.svc
        try:
            svc.tts = Voice(True)
            asyncio.run(svc._voice_tick())
            self.assertEqual(started, [])                                   # answering (maybe the app): leave it alone
            svc.tts = Voice(False)
            asyncio.run(svc._voice_tick())
            asyncio.run(svc._voice_tick())
            self.assertEqual(started, [(self.cfg["voice"]["server_dir"], 3900)])   # once, not every 20 s while it loads
            proc = svc.voice_proc
            asyncio.run(svc._stop_voice())
            self.assertFalse(proc.alive)                                    # stops with the radio
            self.assertIsNone(svc.voice_proc)
            svc.voice_started = 0.0
            tts_mod.port_open = lambda port: True                           # something already listens (loading): wait
            asyncio.run(svc._voice_tick())
            svc.voice_started = 0.0
            (config.DATA_DIR / "plugins.json").write_text('{"voicestudio": false}')
            tts_mod.port_open = lambda port: False
            asyncio.run(svc._voice_tick())
            self.assertEqual(len(started), 2)                               # the retired switch no longer disables startup
            asyncio.run(svc._stop_voice())
        finally:
            tts_mod.start_backend, tts_mod.port_open = real
            (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)

    def test_host_lines(self):
        from radio import host
        self.assertEqual(host.minutes_words(20), "двадцать минут")
        self.assertEqual(host.minutes_words(21), "двадцать одну минуту")
        self.assertEqual(host.minutes_words(12), "двенадцать минут")
        self.assertIn("«playboy shit»", host.back_sell([("Powfu", "playboy shit (feat. Lil Aaron)")] * 2, {}, "b1"))
        songs = [(a, a.lower()) for a in "ABACD"]
        backs = {host.back_sell(songs, {}, f"s{i}") for i in range(40)}
        self.assertIn(None, backs)                                        # sometimes no back-sell at all
        self.assertTrue(any(b and "«A», «C», «D». Последней прозвучала «d»." in b for b in backs))
        self.assertTrue(any(b and b.endswith("«A», «C», «D».") for b in backs))  # the set, artists only
        self.assertFalse(any(b and " и «" in b for b in backs))  # never "X и Y": it sounds like a joint track
        self.assertTrue(any(b and "«D»" in b and "«A»" not in b for b in backs))  # just the last song
        opens = {host.set_opener(20, ("A", "t"), {}, f"s{i}") for i in range(40)}
        self.assertTrue(any("«A»" in o for o in opens) and any("«A»" not in o for o in opens))
        self.assertEqual(host.back_sell([], {}, "s"), None)

    def test_settings_apply_before_the_first_job(self):
        from radio import settings
        try:
            settings.set_setting("llm_provider", "deepseek")
            svc = Service(config.load(), tts=MockTTS(), output="x")
            self.assertEqual(svc.cfg["editor"]["provider"], "deepseek")  # not the config default (ollama)
            svc.db.close()
        finally:
            settings.reset("llm_provider")

    def test_settings_from_the_ui(self):
        from radio import settings
        path = config.DATA_DIR / "settings.json"
        path.unlink(missing_ok=True)
        svc, d = self.svc, settings.base(self.cfg)
        try:
            settings.apply(svc.cfg, d)
            self.assertEqual(svc.cfg["broadcast"]["music_set_minutes"], d["music_set_minutes"])   # nothing saved: config.toml
            settings.set_setting("music_set_minutes", 35)
            settings.set_setting("max_spoken_seconds_per_rolling_hour", 100000)                 # clamped to the maximum
            settings.set_setting("new_every_sets", "0")                                          # 0 = off is allowed
            settings.apply(svc.cfg, d)
            self.assertEqual((svc.cfg["broadcast"]["music_set_minutes"], svc.cfg["broadcast"]["new_every_sets"],
                              svc.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"]), (35, 0, 900))
            row = {r["key"]: r for r in settings.listing(config.load())}["music_set_minutes"]
            self.assertEqual((row["value"], row["default"]), (35, d["music_set_minutes"]))
            with self.assertRaises(ValueError):
                settings.set_setting("nope", 1)
            with self.assertRaises(ValueError):
                settings.set_setting("music_set_minutes", "abc")
            settings.reset("music_set_minutes")                                                  # one back to config.toml
            settings.apply(svc.cfg, d)
            self.assertEqual(svc.cfg["broadcast"]["music_set_minutes"], d["music_set_minutes"])
            self.assertEqual(svc.cfg["broadcast"]["new_every_sets"], 0)
            settings.set_setting("station_name", "  Ночной \n  эфир ")                         # a text: one line
            settings.apply(svc.cfg, d)
            self.assertEqual(svc.cfg["broadcast"]["station_name"], "Ночной эфир")
            settings.set_setting("station_name", "x" * 100)
            self.assertEqual(len({r["key"]: r for r in settings.listing(config.load())}["station_name"]["value"]), 40)
            with self.assertRaises(ValueError):
                settings.set_setting("station_name", "   ")
            path.write_text("not json")                                                          # a broken file changes nothing
            settings.apply(svc.cfg, d)
            self.assertEqual(svc.cfg["broadcast"]["new_every_sets"], d["new_every_sets"])
        finally:
            path.unlink(missing_ok=True)
            settings.apply(svc.cfg, d)

    def test_new_track_set_length_does_not_move_host_editions(self):
        from radio import settings
        before = self.svc._clock_rows()
        for length in (20, 40):
            settings.set_setting('music_set_minutes', length)
            self.svc._live_settings()
            self.assertEqual(self.svc._clock_rows(), before)
        (config.DATA_DIR / 'settings.json').unlink(missing_ok=True)

    def test_plugin_switches(self):
        from radio import plugins
        (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)
        self.assertFalse(plugins.on(self.cfg, "horizon"))                 # default from config
        plugins.set_plugin("horizon", True)
        self.assertTrue(plugins.on(self.cfg, "horizon"))                  # read live from the file
        grouped = {p["name"]: p for p in plugins.listing(self.cfg)}
        self.assertEqual(set(grouped), set(plugins.PLUGINS))
        self.assertEqual({n for n, p in grouped.items() if p["section"] == "integrations"},
                         {"github_trending", "github_new", "horizon", "reminders_calendar", "reminders_todoist", "weather", "ytmusic", "music_facts"})
        self.assertNotIn("voicestudio", grouped)
        self.assertNotIn("voice_over", grouped)
        self.assertEqual({n for n, p in grouped.items() if p["section"] == "experiments"}, {"air_direction", "reroll_next", "gap_filler", "duo_hosts", "reddit", "telegram", "news_reactions"})
        self.assertEqual({n for n, p in grouped.items() if p["section"] == "features"},
                         {"station_ids", "host_links", "new_tracks"})
        self.assertTrue(grouped["horizon"]["on"])  # grouping preserves the existing saved switches
        self.add("gh", "github_trending", 1)
        self.add("dg", "chatgpt", 2)
        plugins.set_plugin("github_trending", False)
        self.assertEqual(content.next_to_air(self.svc.db, self.svc._sources_off())["item_id"], "dg")
        plugins.set_plugin("github_trending", True)
        self.assertEqual(content.next_to_air(self.svc.db, self.svc._sources_off())["item_id"], "dg")  # older first
        with self.assertRaises(ValueError):
            plugins.set_plugin("nope", True)
        (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)

    def test_weather_flow_cadence_restart_and_expiry(self):
        from radio import plugins, weather
        from unittest.mock import patch
        svc, pushed = self.svc, []
        svc.db.execute("DELETE FROM reminders WHERE key LIKE 'weather:%'")
        svc.cfg = dict(self.cfg, weather={"city": "Da Lat"})
        svc.state = "PLAYING"
        at = weather.slot(store.now(), "Asia/Ho_Chi_Minh")
        report = {"kind": "weather", "key": f"weather:slot:{int(at.timestamp())}", "start": at,
                  "expires": at + timedelta(minutes=30), "text": "Коротко о погоде. Далат: плюс 20 градусов.",
                  "summary": "Da Lat · 20° · clear · wind 2 m/s"}

        class Engine:
            async def push_replay(self, wav, kind, ref, gain, rid, tags=""):
                pushed.append((kind, ref, gain))

        svc.engine = Engine()
        try:
            with patch.object(weather, "fetch", return_value=report) as fetch, \
                    patch.object(daemon, "now", return_value=at), \
                    patch.object(svc, "_render_weather", return_value=Path("/tmp/weather.wav")) as synth, \
                    patch.object(daemon.audio, "lufs", return_value=-20), patch.object(daemon.audio, "duration", return_value=5), patch("radio.content.now", return_value=at):
                asyncio.run(svc._weather_tick())
                asyncio.run(svc._weather_tick())
                fetch.assert_called_once_with("Da Lat", at)
                self.assertFalse(asyncio.run(svc._remind(280)))
                self.assertFalse(asyncio.run(svc._remind(20)))
                self.assertEqual(pushed, [])  # weather is material inside an edition, never a separate replay
                pid = 'system:' + report['key'] + '#1'
                self.assertEqual(svc.db.execute('SELECT status FROM packages WHERE id=?', (pid,)).fetchone()[0], 'READY')
                synth.assert_called_once_with(report['text'])
                svc.on_event({'kind': 'news', 'ref': pid})
                self.assertEqual(svc.status()['current_title'], 'Host edition')
                self.assertTrue(svc.db.execute('SELECT 1 FROM reminders WHERE key=?', (report['key'],)).fetchone())
                entry = next(e for e in daemon.timeline._build(svc.db, 12, 50) if e['kind'] == 'news')
                self.assertEqual(entry['title'], 'Host edition · Weather')
                self.assertIn('Далат', entry['parts'][0]['text'])
                restarted = Service(svc.cfg, tts=MockTTS(), output="x")
                restarted.state = "PLAYING"
                try:
                    fetch.reset_mock()
                    with patch.object(daemon, "now", return_value=at + timedelta(hours=2, minutes=59)):
                        asyncio.run(restarted._weather_tick())
                    fetch.assert_not_called()
                    with patch.object(daemon, "now", return_value=at + timedelta(hours=3)):
                        asyncio.run(restarted._weather_tick())
                    fetch.assert_called_once()
                finally:
                    restarted.db.close()
                svc.reminders_due[report["key"]] = {**report, "expires": at}
                self.assertFalse(asyncio.run(svc._remind(20)))
                self.assertFalse(svc.reminders_due)
                svc.reminders_due[report["key"]] = report
                plugins.set_plugin("weather", False)
                self.assertFalse(asyncio.run(svc._remind(20)))
                self.assertFalse(svc.reminders_due)
        finally:
            svc.db.execute("DELETE FROM reminders WHERE key LIKE 'weather:%'")
            (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)

    def test_whisper_timing_for_songs_lrclib_does_not_know(self):
        from unittest.mock import patch
        from radio import tts as tts_mod
        db, svc = self.svc.db, self.svc
        song = TMP / "heard-song.wav"
        MockTTS().synthesize("Песня " * 40, song)  # ~17 s
        db.execute("INSERT OR REPLACE INTO tracks(path, sig, ok, lyrics, tail_sec) VALUES(?,?,1,'none',0.5)", (str(song), music._sig(song)))
        self.assertEqual(music.to_hear(db, [song]), song)
        with patch.object(tts_mod, "vocal_span", return_value=(6.0, 12.0)):
            music.hear(db, song)
        row = db.execute("SELECT * FROM tracks WHERE path=?", (str(song),)).fetchone()
        self.assertEqual((row["lyrics"], row["vocal_sec"]), ("heard", 6.0))
        self.assertIsNone(music.to_hear(db, [song]))                       # done: not heard twice
        self.assertAlmostEqual(music.intro_room(row), 6.0 - music.HEARD_VOCAL_MARGIN_SEC)  # wider margin than LRCLIB
        db.execute("UPDATE tracks SET lyrics='none' WHERE path=?", (str(song),))
        with patch.object(tts_mod, "vocal_span", return_value=None):
            music.hear(db, song)
        self.assertEqual(db.execute("SELECT lyrics FROM tracks WHERE path=?", (str(song),)).fetchone()[0], "unheard")

    def test_host_line_between_songs_is_voice_first_then_jingle(self):
        from radio import audio
        voice = TMP / "link-voice.wav"
        MockTTS().synthesize("Вы слушали трек. А мы продолжаем, оставайтесь с нами.", voice)
        jingle = config.ASSETS_DIR / "stinger_out.wav"
        out = TMP / "link.wav"
        audio.render_link([voice], out, jingle, 4.0)
        a, b = audio.speech_span(voice)
        self.assertLess(audio.active_span(out)[0], 0.2)  # no jingle or bed before the voice: it starts at once
        # the jingle follows the last word: the file is the speech plus the (trimmed) jingle
        self.assertAlmostEqual(audio.duration(out), (b - a) + audio.JINGLE_AFTER_SEC + min(4.0, audio.duration(jingle)), delta=1.2)

    def test_retry_seed_goes_with_the_request_not_onto_the_shared_voice(self):
        from radio import tts as tts_mod
        seen = []

        class Voice:
            seed = 42

            def cache_key(self, text):
                return "seedtest-" + str(abs(hash(text)))

            def synthesize(self, text, out, seed=None):
                seen.append((seed, self.seed))
                out.write_bytes(b"x")
        bad = iter([True, True, True, False])  # no comma in the text: the dash take equals the first one
        tts_mod.cached_synthesize(Voice(), "Фраза для проверки зерна.", lambda p: "noise" if next(bad) else None)
        self.assertEqual([s for s, _ in seen], [None, None, 44, 47])  # retries with neighbouring seeds
        self.assertEqual({shared for _, shared in seen}, {42})              # the shared voice never changed

    def test_two_hosts_enter_without_handoff_or_thanks(self):
        svc = self.svc
        svc.tts2 = object()  # the co-host's voice (only its identity matters here)
        svc.cfg["broadcast"].update(host_name="Артём", cohost_name="Маша")
        segs = [("Добрый вечер.", False), ("Новость один.", False), ("Новость два.", True), ("Новость три.", True),
                ("Дальше музыка.", False)]
        owners = ["seed", "a", "b", "c", "seed"]
        s, o, v = svc._duo(segs, owners, [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        self.assertEqual(v, [None, None, svc.tts2, None, None])
        self.assertEqual(s, segs)
        self.assertEqual(o, owners)
        from unittest.mock import patch
        with patch("radio.daemon.plugins.on", return_value=True), \
                patch.object(svc, "_body_segments", return_value=[("Новость.", True)]), \
                patch.object(svc, "_segment_voice", return_value=Path("voice.wav")), \
                patch.object(svc, "_jingles_for", return_value={}), \
                patch("radio.daemon.audio.duration", return_value=1), \
                patch("radio.daemon.audio.render_package", return_value=0) as render:
            self.assertIsNotNone(svc._assemble_break(
                [{"id": "a"}, {"id": "b"}, {"id": "c"}], self.clock(), False, None))
            self.assertEqual(render.call_args.kwargs["speaker_changes"], [True, True])
        svc.tts2 = svc.tts

    def test_every_fresh_take_is_checked_by_transcript(self):
        from unittest.mock import Mock
        svc = self.svc
        text = "Новости технологий. Компания выпустила новую версию редактора кода."
        svc.tts.transcribe = Mock(side_effect=["Продолжение следует...", text])
        first = svc._say(text)  # the first take is heard as Whisper's noise hallucination, the second matches
        self.assertEqual(svc.tts.transcribe.call_count, 2)
        self.assertEqual(svc._say(text), first)  # cached: trusted, not transcribed again
        self.assertEqual(svc.tts.transcribe.call_count, 2)
        svc.tts.transcribe = Mock(side_effect=RuntimeError("no whisper"))
        svc._say("Другая фраза ведущего для проверки без распознавания.")  # verification unavailable: spectrum only
        del svc.tts.transcribe

    def test_weather_replaces_noise_that_passes_spectrum_check(self):
        from unittest.mock import Mock, patch
        svc = self.svc
        text = "Коротко о погоде. Далат: плюс семнадцать градусов, пасмурно. Ветер два метра в секунду."
        spoken = text.replace("Далат: ", "Далат — ")  # the colon is spoken as a dash (it made the voice noisy)
        path = config.DATA_DIR / "cache/tts" / (svc.tts.cache_key(spoken) + ".wav")
        path.write_bytes(b"noise")
        svc.tts.transcribe = Mock(side_effect=["Субтитры сделал кто-то", text])
        svc.tts.synthesize = Mock(side_effect=lambda text, path: path.write_bytes(b"speech"))
        with patch.object(svc, "_speech_check", return_value=None):
            self.assertEqual(svc._render_weather(text).read_bytes(), b"speech")
        self.assertEqual(svc.tts.transcribe.call_count, 2)
        svc.tts.synthesize.assert_called_once()
        path.write_bytes(b"noise")
        svc.tts.transcribe.side_effect = None
        svc.tts.transcribe.return_value = "Субтитры"
        with patch.object(svc, "_speech_check", return_value=None), self.assertRaisesRegex(RuntimeError, "does not match"):
            svc._render_weather(text)
        self.assertEqual(path.read_bytes(), b"noise")  # no failed retake replaces the cache

    def test_weather_assembly_checks_host_time_as_host_speech(self):
        from unittest.mock import Mock, patch
        svc = self.svc
        text = "Далат: плюс 25 градусов, пасмурно, без дождя."
        spoken = {}
        def synthesize(text, path):
            spoken[path] = text
            path.write_bytes(b"speech")
        svc.tts.synthesize = Mock(side_effect=synthesize)
        svc.tts.transcribe = Mock(side_effect=lambda path: spoken[path].replace("Двенадцать ноль три", "12:03"))
        self.addCleanup(delattr, svc.tts, "transcribe")
        with patch.object(svc, "_speech_check", return_value=None), \
                patch.object(svc, "_render_weather", wraps=svc._render_weather) as forecast, \
                patch("radio.daemon.audio.duration", return_value=3), \
                patch("radio.daemon.audio.render_package", return_value=0) as render, \
                patch("radio.daemon.host.back_sell", return_value=""), \
                patch("radio.daemon.host.opener", return_value="Двенадцать ноль три. Вы слушаете «Yourwave»."), \
                patch("radio.daemon.host.set_opener", return_value="Возвращаемся к музыке."), \
                patch.object(svc, "_jingles_for", return_value={}):
            svc._store_host_segment("weather:slot:test", {"kind": "weather", "summary": "Da Lat · 25°"},
                                    text, Path("weather.wav"))
            package = dict(svc.db.execute("SELECT * FROM packages WHERE item_id='system:weather:slot:test'").fetchone())
            result = svc._assemble_break([package], self.clock(), True, None)
        self.assertIsNotNone(result, svc.last_error)
        render.assert_called_once()
        forecast.assert_called_once_with(text)

    def test_weather_outage_retries_without_announcing(self):
        from radio import weather
        from unittest.mock import patch
        svc = self.svc
        svc.cfg = dict(self.cfg, weather={"city": "Da Lat"})
        svc.state = "PLAYING"
        svc.db.execute("DELETE FROM reminders WHERE key LIKE 'weather:%'")
        with patch.object(daemon, "now", return_value=weather.slot(store.now(), "Asia/Ho_Chi_Minh")), \
                patch.object(weather, "fetch", side_effect=OSError("offline")) as fetch:
            asyncio.run(svc._weather_tick())
            asyncio.run(svc._weather_tick())
            fetch.assert_called_once()
        self.assertFalse(svc.reminders_due)
        self.assertEqual(svc.sources["weather"], "error: OSError")
        self.assertGreater(svc.next_weather_read, time.time() + 14 * 60)

    def test_music_link_gates_cache_queue_and_history(self):
        from radio import music_facts, plugins, timeline
        from unittest.mock import patch
        svc, db = self.svc, self.svc.db
        plugins.set_plugin("music_facts", True)
        self.addCleanup((config.DATA_DIR / "plugins.json").unlink, missing_ok=True)
        svc.state = "PLAYING"
        svc.current = {"kind": "music", "ref": "/m/track.mp3"}
        svc.set_started = time.time()
        svc.set_songs = [("Band", "Song")]
        facts = {"text": "Коллектив появился в 2009 году.", "urls": ["https://musicbrainz.org/artist/test"]}
        with patch.object(music_facts, "fetch", return_value=facts) as fetch:
            asyncio.run(svc._maybe_music_link())
            asyncio.run(svc._maybe_music_link())
            asyncio.run(svc._maybe_music_link())
            fetch.assert_called_once_with("Band", "Song")
            pk = db.execute("SELECT * FROM packages").fetchone()
            text = Path(pk["script_path"]).read_text()
            self.assertIn(facts["text"], text)
            self.assertIn("Song", text)
            self.assertNotIn("новостей", text)
            self.assertNotIn("https://", text)
            db.execute("UPDATE packages SET status='READY', speech_sec=10")
            self.add("real-news", "inbox", 0)
            self.assertEqual([p["item_id"] for p in svc._pick_break()], [pk['item_id']])
            self.assertEqual(db.execute("SELECT status FROM packages WHERE item_id='real-news'").fetchone()[0], 'READY')
            db.execute("UPDATE packages SET status='PLAYED'")
            db.execute("UPDATE items SET state='PLAYED'")
            store.log_event(db, svc.session, "PACKAGE_STARTED", pk["id"])
            entry = next(e for e in timeline._build(db, 12, 50) if e["kind"] == "host")
            self.assertIn(facts["text"], entry["parts"][0]["text"])
            self.assertEqual(entry["parts"][0]["links"][0]["url"], facts["urls"][0])
            self.clock.return_value += timedelta(minutes=10)
            asyncio.run(svc._maybe_music_link())
            self.assertEqual(fetch.call_count, 1)  # next clock edition is outside the preparation window
            self.clock.return_value += timedelta(minutes=20)
            fetch.return_value = None
            with patch("radio.content.now", return_value=self.clock.return_value):
                asyncio.run(svc._maybe_music_link())
            self.assertEqual(fetch.call_count, 2)
            latest = db.execute("SELECT script_path FROM packages WHERE status='PENDING'").fetchone()
            self.assertNotIn("2009", Path(latest[0]).read_text())
            plugins.set_plugin("music_facts", False)
            db.execute("UPDATE packages SET status='READY', speech_sec=10 WHERE status='PENDING'")
            self.assertEqual(svc._pick_break(), [])

    def test_music_link_is_independent_of_waiting_news(self):
        from radio import music_facts, plugins
        from unittest.mock import patch
        plugins.set_plugin("music_facts", True)
        self.addCleanup((config.DATA_DIR / "plugins.json").unlink, missing_ok=True)
        self.add("d", "chatgpt", 1)
        self.svc.state = "PLAYING"
        self.svc.current = {"kind": "music", "ref": "/m/track.mp3"}
        self.svc.set_started = time.time()
        self.svc.set_songs = [('Band', 'Song')]
        with patch.object(music_facts, "fetch", return_value=None) as fetch:
            asyncio.run(self.svc._maybe_music_link())
            fetch.assert_called_once()
        self.assertTrue(self.svc.db.execute("SELECT 1 FROM items WHERE source='music_facts'").fetchone())


class JingleRotationTests(unittest.TestCase):
    def test_no_repeat_until_folder_used_up(self):
        from radio import audio
        cfg = {"bed": [Path(f"b{i}.mp3") for i in range(4)], "stinger_out_max_sec": 4.0}
        recent = {}
        picks = [audio.choose_jingles(cfg, f"pkg{i}", recent)["bed"] for i in range(12)]
        for i in range(0, 12, 4):
            self.assertEqual(len(set(picks[i:i + 4])), 4, picks)  # every 4 packages use all 4 beds
        self.assertTrue(all(a != b for a, b in zip(picks, picks[1:])))  # never twice in a row


class LlmProviderTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory(prefix="radio-llm-")
        self.addCleanup(tmp.cleanup)
        p = patch.object(config, "DATA_DIR", Path(tmp.name))
        p.start()
        self.addCleanup(p.stop)

    def test_deepseek_retries_old_empty_cache_without_thinking(self):
        from unittest.mock import patch
        from radio import editor
        cfg = config.load()
        cfg['editor']['provider'] = 'deepseek'
        ed = editor.Editor(cfg)
        cached = ed._cache('prompt', editor.target(cfg))
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text('')
        answer = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'Проект помогает быстро писать код.'}}]}
        with patch.object(ed, '_post', return_value=answer) as post:
            self.assertEqual(ed._ask('prompt'), 'Проект помогает быстро писать код.')
            self.assertEqual(post.call_args.args[2]['thinking'], {'type': 'disabled'})
            self.assertEqual(ed._ask('prompt'), 'Проект помогает быстро писать код.')
            self.assertEqual(post.call_count, 1)

    def test_empty_or_truncated_reply_is_not_cached_but_skip_is(self):
        from unittest.mock import patch
        from radio import editor
        cfg = config.load()
        ed = editor.Editor(cfg)
        for reason, raw in [('length', 'Проект помогает быстро писать код.'), ('stop', '')]:
            with self.subTest(reason=reason), patch.object(ed, '_post', return_value={
                    'choices': [{'finish_reason': reason, 'message': {'content': raw}}]}):
                with self.assertRaisesRegex(ValueError, 'empty or truncated'):
                    ed._ask('prompt')
                self.assertFalse(ed._cache('prompt', editor.target(cfg)).exists())
        with patch.object(ed, '_post', return_value={
                'choices': [{'finish_reason': 'stop', 'message': {'content': 'SKIP'}}]}) as post:
            self.assertIsNone(ed._ask('prompt'))
            self.assertIsNone(ed._ask('prompt'))
            self.assertEqual(post.call_count, 1)

    def test_llm_limit_is_per_hour_not_per_session(self):
        import io
        import urllib.request
        from unittest.mock import patch
        from radio import editor
        cfg = config.load()
        cfg["editor"]["max_llm_calls_per_session"] = 2
        answer = {"choices": [{"message": {"content": "Проект помогает быстро писать код."}}]}
        ed, clock = editor.Editor(cfg), [1000.0]
        with patch.object(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(answer).encode())), \
             patch("radio.editor.time.time", lambda: clock[0]):
            self.assertTrue(ed._ask("one") and ed._ask("two"))
            with self.assertRaisesRegex(ValueError, 'hourly request limit'):
                ed._ask("three")                         # the limit within the hour
            clock[0] += 3601
            self.assertTrue(ed._ask("three"))             # an hour later the radio retells again

    def test_provider_switch_reaches_editor_and_horizon_proxy(self):
        import io
        import threading
        import urllib.error
        import urllib.request
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from unittest.mock import patch
        from radio import editor, ollama_proxy, settings
        cfg = config.load()
        cfg["editor"].update(openai_api_key="sk-test", openai_base_url="http://upstream.test/v1")
        t = editor.target(cfg)
        self.assertEqual((t["provider"], t["root"], t["model"]), ("ollama", "http://127.0.0.1:11434", "qwen3.5:4b"))
        with self.assertRaises(ValueError):
            settings.set_setting("llm_provider", "nope")
        settings.set_setting("llm_provider", "openai")
        settings.apply(cfg, settings.base(config.load()))
        self.assertEqual(editor.target(cfg), {"provider": "openai", "root": "http://upstream.test", "model": "gpt-4.1-mini",
                                              "key": "sk-test"})
        seen = []

        def fake_open(req, timeout):
            seen.append((req.full_url, req.headers.get("Authorization"), json.loads(req.data)))
            return io.BytesIO(json.dumps({"output": [{"type": "message", "content": [
                {"type": "output_text", "text": "Проект помогает быстро писать код."}]}]}).encode())
        with patch.object(urllib.request, "urlopen", fake_open):
            self.assertEqual(editor.Editor(cfg)._ask("prompt"), "Проект помогает быстро писать код.")
        url, auth, body = seen[0]
        self.assertEqual((url, auth), ("http://upstream.test/v1/responses", "Bearer sk-test"))
        self.assertEqual((body["model"], body["input"], body["max_output_tokens"]), ("gpt-4.1-mini", "prompt", 300))

        got = []

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                got.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                out = json.dumps({"id": "r1", "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"ok": 1}'}]}], "usage": {"input_tokens": 5, "output_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        up = HTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        cfg["editor"]["openai_base_url"] = f"http://127.0.0.1:{up.server_port}/v1"
        proxy = ollama_proxy.start(0, lambda: editor.target(cfg))
        chat = {"model": "qwen3.5:4b", "messages": [{"role": "user", "content": "json please"}], "max_tokens": 50,
                "response_format": {"type": "json_object"}}
        try:
            with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
                                                               json.dumps(chat).encode(), {"Content-Type": "application/json"})) as r:
                answer = json.load(r)
            # a web page (it always sends Origin) must not get the user's key spent for it
            with self.assertRaises(urllib.error.HTTPError) as no:
                urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
                                                              b"{}", {"Origin": "https://evil.example", "Content-Type": "text/plain"}))
            self.assertEqual(no.exception.code, 403)
        finally:
            proxy.shutdown()
            up.shutdown()
        self.assertEqual(got, [("/v1/responses", "Bearer sk-test", {"model": "gpt-4.1-mini", "input": chat["messages"],
                                "max_output_tokens": 50, "text": {"format": {"type": "json_object"}}})])
        self.assertEqual((answer["choices"][0]["message"]["content"], answer["usage"]["completion_tokens"]), ('{"ok": 1}', 2))

    def test_deepseek_key_pasted_in_settings(self):
        from radio import editor, settings
        with self.assertRaises(ValueError):
            editor.set_key("deepseek", "bad key\n")
        with self.assertRaises(ValueError):
            editor.set_key("ollama", "sk-x")
        editor.set_key("deepseek", " sk-deep ")
        self.assertEqual((config.DATA_DIR / "llm_keys.json").stat().st_mode & 0o777, 0o600)
        settings.set_setting("llm_provider", "deepseek")
        cfg = config.load()
        settings.apply(cfg, settings.base(config.load()))
        self.assertEqual(editor.target(cfg), {"provider": "deepseek", "root": "https://api.deepseek.com",
                                              "model": "deepseek-flash", "key": "sk-deep"})
        rows = {r["key"]: r for r in settings.listing(config.load())}
        self.assertEqual(rows["llm_provider"]["key_field"], {"provider": "deepseek", "saved": True})
        self.assertNotIn("sk-deep", json.dumps(rows))  # the key never goes back to the page
        self.assertNotIn("deepseek_model", rows)  # no model row until the provider has answered
        editor.set_key("deepseek", "")
        self.assertEqual(editor.saved_key("deepseek"), "")

    def test_connection_state_for_the_settings_page(self):
        import io
        import urllib.error
        import urllib.request
        from unittest.mock import patch
        from radio import editor
        cfg = config.load()
        cfg["editor"]["provider"] = "deepseek"
        self.assertEqual(editor.connection(cfg)["state"], "no_key")
        editor.set_key("deepseek", "sk-1")
        answers = {"sk-1": lambda: io.BytesIO(b'{"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]}'),
                   "sk-2": lambda: (_ for _ in ()).throw(urllib.error.HTTPError("u", 401, "no", {}, None)),
                   "sk-3": lambda: (_ for _ in ()).throw(urllib.error.URLError("down"))}

        def settled():
            for _ in range(100):
                c = editor.connection(cfg)
                if c["state"] != "checking":
                    return c
                time.sleep(0.02)
        with patch.object(urllib.request, "urlopen", lambda req, timeout: answers[req.headers["Authorization"][7:]]()):
            self.assertEqual(settled()["detail"], "Connected · 2 models")
            cfg["editor"]["deepseek_model"] = "deepseek-x"
            self.assertEqual(settled()["state"], "no_model")
            editor.set_key("deepseek", "sk-2")
            self.assertEqual(settled()["state"], "bad_key")
            editor.set_key("deepseek", "sk-3")
            self.assertEqual(settled()["detail"], "Can't reach api.deepseek.com")

    def test_sign_in_with_chatgpt(self):
        import io
        import urllib.parse
        import urllib.request
        from unittest.mock import patch
        from radio import chatgpt, editor
        url = urllib.parse.urlparse(chatgpt.login_url("http://127.0.0.1:8765/auth/callback"))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        self.assertEqual((q["client_id"], q["redirect_uri"], q["code_challenge_method"]),
                         ("dynamic_agent_client", "http://127.0.0.1:8765/auth/callback", "S256"))
        self.assertIn("chatgpt.tokens.use.direct", q["scope"])
        self.assertTrue(q["ext_agent_host_id"].startswith("urn:uuid:"))
        forms = []

        def fake_open(req, timeout):
            forms.append(dict(urllib.parse.parse_qsl(req.data.decode())))
            n = len(forms)
            return io.BytesIO(json.dumps({"access_token": f"at{n}", "refresh_token": f"rt{n}", "expires_in": 3600 if n == 1 else 7200,
                                          "scope": "openid offline_access chatgpt.tokens.use.direct", "id_token": "x"}).encode())
        with patch.object(urllib.request, "urlopen", fake_open):
            with self.assertRaises(ValueError):
                chatgpt.finish("forged", "code", "client-1")
            chatgpt.finish(q["state"], "code", "client-1")
            self.assertEqual((forms[0]["grant_type"], forms[0]["client_id"], forms[0]["resource"]),
                             ("authorization_code", "client-1", "https://api.openai.com/v1"))
            cfg = config.load()
            cfg["editor"]["provider"] = "openai"
            self.assertEqual(editor.target(cfg)["key"], "at1")  # the sign-in wins over an API key
            data = json.loads((config.DATA_DIR / "chatgpt.json").read_text())
            data["expires"] = 0
            (config.DATA_DIR / "chatgpt.json").write_text(json.dumps(data))
            self.assertEqual(chatgpt.token(), "at2")  # refreshed
            self.assertEqual((forms[1]["grant_type"], forms[1]["refresh_token"]), ("refresh_token", "rt1"))
        self.assertEqual((config.DATA_DIR / "chatgpt.json").stat().st_mode & 0o777, 0o600)
        data = json.loads((config.DATA_DIR / "chatgpt.json").read_text())
        data["expires"] = 0
        (config.DATA_DIR / "chatgpt.json").write_text(json.dumps(data))
        calls = []

        def offline(req, timeout):
            calls.append(1)
            raise urllib.error.URLError("offline")
        import urllib.error
        with patch.object(urllib.request, "urlopen", offline):
            self.assertEqual((chatgpt.token(), chatgpt.token()), ("", ""))
        self.assertEqual(len(calls), 1)  # a failed refresh waits a minute: no retry on every page poll
        chatgpt.sign_out()
        self.assertFalse(chatgpt.signed_in())


class SignInTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        tmp = tempfile.TemporaryDirectory(prefix="radio-signin-")
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        p = patch.object(config, "DATA_DIR", self.data)
        p.start()
        self.addCleanup(p.stop)

    def test_openrouter_pkce_sign_in_and_model_row(self):
        import base64
        import hashlib
        import io
        import urllib.parse
        import urllib.request
        from unittest.mock import patch
        from radio import editor, openrouter, settings
        url = urllib.parse.urlparse(openrouter.login_url("http://127.0.0.1:8765/openrouter/callback"))
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        state = urllib.parse.parse_qs(urllib.parse.urlparse(q["callback_url"]).query)["state"][0]
        with self.assertRaises(ValueError):
            openrouter.finish("forged", "code")
        sent = []

        def fake_open(req, timeout):
            sent.append(json.loads(req.data))
            return io.BytesIO(b'{"key": "sk-or-v1-test"}')
        with patch.object(urllib.request, "urlopen", fake_open):
            openrouter.finish(state, "the-code")
        verifier = sent[0]["code_verifier"]
        self.assertEqual(q["code_challenge"], base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode())
        self.assertEqual(openrouter.key(), "sk-or-v1-test")
        self.assertEqual((self.data / "openrouter.key").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            openrouter.finish(state, "the-code")  # a state is used once

        settings.set_setting("llm_provider", "openrouter")
        fp = hashlib.sha256(b"sk-or-v1-test").hexdigest()[:12]
        editor._checks[("openrouter", "https://openrouter.ai/api", fp)] = {
            "at": time.time(), "state": "ok", "detail": "Connected · 1 models", "ids": ["anthropic/claude-sonnet-5.5"]}
        rows = {r["key"]: r for r in settings.listing(config.load())}
        self.assertNotIn("ollama_model", rows)
        self.assertEqual(rows["openrouter_model"]["options"], ["anthropic/claude-sonnet-5.5"])  # only what it offers
        self.assertEqual(rows["llm_provider"]["connection"]["state"], "no_model")  # the default is not offered
        self.assertEqual(rows["llm_provider"]["action"]["cmd"], "openrouter_logout")
        cfg = config.load()
        settings.set_setting("openrouter_model", "anthropic/claude-sonnet-5.5")
        settings.apply(cfg, settings.base(config.load()))
        t = editor.target(cfg)
        self.assertEqual((t["root"], t["model"], t["key"]), ("https://openrouter.ai/api", "anthropic/claude-sonnet-5.5", "sk-or-v1-test"))

    def test_youtube_sign_in_from_browser_keeps_only_youtube_cookies(self):
        from unittest.mock import patch
        from radio import ytmusic
        out = self.data / "jar.txt"

        def fake_run(args, **kw):
            Path(args[args.index("--cookies") + 1]).write_text(
                "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tabc\n"
                "#HttpOnly_.music.youtube.com\tTRUE\t/\tTRUE\t0\tLOGIN\tx\n"
                ".bank.example\tTRUE\t/\tTRUE\t0\tsession\tsecret\n.evilyoutube.com\tTRUE\t/\tTRUE\t0\tSAPISID\tz\n")
        with patch.object(ytmusic.subprocess, "run", fake_run):
            ytmusic.browser_jar("chrome", ytmusic.DEFAULT_PLAYLIST, out)
        text = out.read_text()
        self.assertIn("SAPISID\tabc", text)
        self.assertIn("LOGIN", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("evilyoutube", text)
        with patch.object(ytmusic.subprocess, "run", lambda args, **kw: Path(args[args.index("--cookies") + 1]).write_text(".bank.example\tTRUE\t/\tTRUE\t0\tsession\tsecret\n")):
            with self.assertRaisesRegex(ValueError, "Sign in to YouTube Music in Chrome"):
                ytmusic.browser_jar("chrome", ytmusic.DEFAULT_PLAYLIST, out)


class HorizonTests(unittest.TestCase):
    def test_briefing_items_retold(self):
        from radio import horizon
        items = horizon.parse((Path(__file__).parent / "fixtures/horizon-en.md").read_text(encoding="utf-8"))
        self.assertEqual([round(i["score"]) for i in items], [8, 8, 7, 7])
        self.assertTrue(items[0]["discussion"].startswith("https://news.ycombinator.com/"))
        svc = Service(config.load(), tts=MockTTS(), output="x")
        told = []
        svc.editor.retell_news = lambda title, summary: told.append(title) or (None if "Pascal" in title else "Новость.")
        self.assertEqual(svc._take_horizon(items), "3 new")              # the failed one gives its slot on
        self.assertEqual(len(told), 4)
        self.assertEqual(svc._take_horizon(items), "nothing new")
        self.assertEqual(len(told), 5)                                    # only the failed one is retried


class TimelineTests(unittest.TestCase):
    def test_no_file_leak_on_polling(self):
        import resource
        from radio.timeline import timeline
        config.load()
        store.connect().close()
        opened = lambda: len(list(Path("/dev/fd").iterdir()))
        before = opened()
        for _ in range(50):  # the page polls every 2 s for hours
            timeline()
        self.assertLessEqual(opened() - before, 2)


class MusicTests(unittest.TestCase):
    def test_enhanced_copy_is_skipped(self):
        from radio import music
        d = TMP / "dupes"
        d.mkdir()
        for n in ("Song.mp3", "Song.enhanced-ai-cleaned-mastered.wav", "Solo.enhanced-x.wav"):
            (d / n).touch()
        self.assertEqual([p.name for p in music.scan(d)], ["Solo.enhanced-x.wav", "Song.mp3"])


class EndToEnd(unittest.TestCase):
    def setUp(self):
        (config.RUN_DIR / "session.json").unlink(missing_ok=True)  # each run starts fresh, not resumed

    @classmethod
    def setUpClass(cls):
        music = TMP / "music"
        music.mkdir()
        for i, f in enumerate((220, 330, 440)):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={f}:d=20",
                            "-ac", "2", str(music / f"track{i}.mp3")], check=True)
        (music / "broken.mp3").write_bytes(b"not audio" * 200)
        cls.cfg = config.load()
        cls.cfg["music"]["path"] = music
        cls.cfg["broadcast"]["max_package_speech_sec"] = 12   # forces several parts
        cls.cfg["broadcast"]["max_spoken_seconds_per_rolling_hour"] = 25  # one part + host links fits, two do not
        cls.cfg["sources"]["inbox_poll_sec"] = 1
        cls.cfg["sources"]["github_trending"]["enabled"] = False  # no network in tests

    def test_broadcast(self):
        n = store.now()
        write_digest("fresh.md", n - timedelta(hours=1))
        write_digest("old.md", n - timedelta(hours=30), body="Старая новость.")
        write_digest("nodate.md", None, body="Новость без даты.")
        out = TMP / "broadcast.wav"
        svc = Service(self.cfg, tts=MockTTS(), output=str(out))
        # Schedule this integration edition now, independent of when the test runs.
        from radio import broadcast_clock
        svc.db.execute("INSERT INTO air_plan(id,kind,not_before) VALUES(?,'news',?)",
                       (broadcast_clock.PREFIX + str(int(time.time())), store.iso(store.now())))

        async def scenario():
            task = asyncio.create_task(svc.run())
            deadline = time.time() + 60
            while time.time() < deadline:
                await asyncio.sleep(0.5)
                if svc.db.execute("SELECT 1 FROM packages WHERE status='PLAYED'").fetchone():
                    await asyncio.sleep(2)  # let the window logic try to queue more
                    break
            await svc.stop()
            await task

        from unittest.mock import patch
        with patch.dict(os.environ, {'RADIO_FILE_SYNC': 'realtime'}), \
                patch.object(svc.editor, '_ask', return_value='Разработчики выпустили новую версию компилятора.'):
            asyncio.run(scenario())
        db = svc.db
        states = dict(db.execute("SELECT file, state FROM items").fetchall())
        self.assertEqual(states[str(config.DATA_DIR / "inbox/old.md")], "EXPIRED")
        self.assertEqual(states[str(config.DATA_DIR / "inbox/nodate.md")], "NEEDS_METADATA")
        pk = db.execute("SELECT part, status, speech_sec FROM packages ORDER BY part").fetchall()
        self.assertGreater(len(pk), 1)
        self.assertEqual(pk[0]["status"], "PLAYED")
        # Rolling-hour budget: the next part must wait, never air past the limit.
        self.assertTrue(all(p["status"] != "PLAYED" for p in pk[1:]), [dict(p) for p in pk])
        self.assertLessEqual(sum(p["speech_sec"] for p in pk if p["status"] == "PLAYED"), 25)
        events = [r[0] for r in db.execute("SELECT type FROM events WHERE session_id=? ORDER BY id", (svc.session,))]
        on_air = [e for e in events if e in ("MUSIC_STARTED", "PACKAGE_STARTED")]
        self.assertEqual(on_air[0], "MUSIC_STARTED")  # Play starts with music, news only later
        self.assertIn("PACKAGE_COMPLETED", events)
        self.assertGreater(out.stat().st_size, 0)  # recorded; its length depends on machine speed (events prove the air)
        # After stop nothing is left in flight.
        self.assertFalse(db.execute("SELECT 1 FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone())

    def test_replay_unqueue(self):
        svc = Service(self.cfg, tts=MockTTS(), output=str(TMP / "replay.wav"))
        kept = None

        async def scenario():
            # Real time: an unsynced file output plays a queued replay before unqueue is even sent.
            from unittest.mock import patch
            with patch.dict(os.environ, {"RADIO_FILE_SYNC": "realtime"}):
                task = asyncio.create_task(svc.run())
                while not svc.current:
                    await asyncio.sleep(0.2)
            nonlocal kept
            kept, gone = sorted(f for f in svc.library if "track" in f)[:2]
            await svc.dispatch("replay", {"kind": "music", "ref": gone})
            await svc.dispatch("replay", {"kind": "music", "ref": kept})
            queued = (await svc.dispatch("unqueue", {"kind": "music", "ref": gone}))["status"]["replay_queue"]
            await svc.engine.command("skip")
            deadline = time.time() + 30
            while svc.replays and time.time() < deadline:  # the kept one airs and leaves the queue
                await asyncio.sleep(0.2)
            await svc.stop()
            await task
            return queued

        self.assertEqual(asyncio.run(scenario()), [{"kind": "music", "ref": kept}])
        self.assertEqual(svc.replays, {})


class WeatherTests(unittest.TestCase):
    AT = datetime(2026, 10, 20, 9, 15, tzinfo=timezone.utc)

    def data(self):
        return {"current": {"time": int(self.AT.timestamp()), "temperature_2m": 21.4, "weather_code": 3}}

    def test_short_report_and_current_weather_validation(self):
        import copy
        from radio import weather
        data = self.data()
        report = weather.report(data, "Далат", self.AT, "Da Lat")
        self.assertEqual(report["summary"], "Da Lat · 21° · overcast")
        self.assertTrue(report["text"].endswith("Далат: плюс 21 градус, пасмурно, без дождя."), report["text"])
        openers = {weather.report({"current": {**data["current"], "time": int((self.AT + timedelta(hours=h)).timestamp())}},
                                  "Далат", self.AT + timedelta(hours=h), "Da Lat")["text"].split("Далат")[0] for h in range(5)}
        self.assertGreater(len(openers), 1)                              # the framing rotates by the hour
        self.assertEqual(report["expires"], self.AT + timedelta(minutes=30))
        for key, value in [("temperature_2m", None), ("temperature_2m", float('nan')),
                           ("weather_code", 4), ("time", 0)]:
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(data)
                bad["current"][key] = value
                with self.assertRaises(ValueError):
                    weather.report(bad, "Далат", self.AT, "Da Lat")
        data["current"].update(temperature_2m=-12, weather_code=63)
        self.assertTrue(weather.report(data, "Далат", self.AT, "Da Lat")["text"].endswith(
            "Далат: минус 12 градусов, дождь. Зонт сегодня точно пригодится."))

    def test_fetch_uses_resolved_city_and_explicit_units(self):
        from radio import weather
        from unittest.mock import patch
        place = {"name": "Далат", "latitude": 11.94646, "longitude": 108.44193}
        with patch.object(weather, "_get", side_effect=[{"results": [place]}, self.data()]) as get:
            self.assertIn("Далат", weather.fetch("Da Lat", self.AT)["text"])
            self.assertEqual(get.call_args_list[0].kwargs["name"], "Da Lat")
            self.assertEqual(get.call_args_list[1].kwargs["current"], "temperature_2m,weather_code")
            self.assertNotIn("hourly", get.call_args_list[1].kwargs)
            self.assertEqual(get.call_args_list[1].kwargs["timeformat"], "unixtime")
        with patch.object(weather, "_get", return_value={}):
            with self.assertRaises(ValueError):
                weather.fetch("Unknown", self.AT)


class ReminderTests(unittest.TestCase):
    NOW = datetime(2026, 10, 20, 8, 50, tzinfo=timezone.utc)   # Tuesday, 11:50 in Moscow
    LEAD = timedelta(minutes=15)
    MSK = timezone(timedelta(hours=3))

    @staticmethod
    def ics(*events):
        return "BEGIN:VCALENDAR\r\n" + "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in events) + "END:VCALENDAR"

    def due(self, *events):
        from radio import reminders
        evs = reminders.parse_events(self.ics(*events), self.MSK)
        return [(r["title"], r["start"].astimezone(self.MSK).strftime("%H:%M")) for r in reminders.calendar_due(evs, self.NOW, self.LEAD)]

    def test_single_utc_and_all_day_events(self):
        single = "UID:1\r\nSUMMARY:Стендап\r\nDTSTART;TZID=Europe/Moscow:20261020T120000"
        later = "UID:2\r\nSUMMARY:Позже\r\nDTSTART;TZID=Europe/Moscow:20261020T130000"
        utc = "UID:3\r\nSUMMARY:UTC\r\nDTSTART:20261020T090500Z"
        allday = "UID:4\r\nSUMMARY:Весь день\r\nDTSTART;VALUE=DATE:20261020"
        gone = "UID:5\r\nSUMMARY:Отменено\r\nSTATUS:CANCELLED\r\nDTSTART:20261020T090500Z"
        self.assertEqual(self.due(single, later, utc, allday, gone), [("Стендап", "12:00"), ("UTC", "12:05")])

    def test_recurring_events(self):
        weekly = "UID:w\r\nSUMMARY:Еженедельная\r\nDTSTART;TZID=Europe/Moscow:20260106T120000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU,TH"
        skipped = weekly.replace("UID:w", "UID:x") + "\r\nEXDATE;TZID=Europe/Moscow:20261020T120000"
        third_tuesday = "UID:m\r\nSUMMARY:Третий вторник\r\nDTSTART;TZID=Europe/Moscow:20260101T120000\r\nRRULE:FREQ=MONTHLY;BYDAY=3TU"
        second_tuesday = third_tuesday.replace("UID:m", "UID:n").replace("3TU", "2TU")
        done = "UID:c\r\nSUMMARY:Закончилась\r\nDTSTART;TZID=Europe/Moscow:20261013T120000\r\nRRULE:FREQ=DAILY;COUNT=3"
        every_other_week = "UID:i\r\nSUMMARY:Раз в две недели\r\nDTSTART;TZID=Europe/Moscow:20261013T120000\r\nRRULE:FREQ=WEEKLY;INTERVAL=2"
        off_week = every_other_week.replace("UID:i", "UID:j").replace("20261013", "20261006")
        self.assertEqual(self.due(weekly, skipped, third_tuesday, second_tuesday, done, every_other_week, off_week),
                         [("Еженедельная", "12:00"), ("Третий вторник", "12:00"), ("Раз в две недели", "12:00")])

    def test_day_mentions_dates_recurrence_and_limits(self):
        from radio import reminders
        at = self.NOW.astimezone(self.MSK)
        holiday = "UID:h\r\nSUMMARY:Праздник\r\nDTSTART;VALUE=DATE:20251020\r\nRRULE:FREQ=YEARLY\r\nDESCRIPTION:Справка."
        evs = reminders.parse_events(self.ics(holiday), self.MSK)
        first = reminders.day_due(evs, at, [])
        self.assertEqual((first["title"], first["description"], first["count"]), ("Праздник", "Справка.", 0))
        history = [(first["key"], store.iso(at))]
        self.assertIsNone(reminders.day_due(evs, at + timedelta(hours=3, minutes=59), history))
        second = reminders.day_due(evs, at + timedelta(hours=4), history)
        self.assertEqual(second["count"], 1)
        history.append((second["key"], store.iso(at + timedelta(hours=4))))
        third = reminders.day_due(evs, at + timedelta(hours=8), history)
        history.append((third["key"], store.iso(at + timedelta(hours=8))))
        self.assertIsNone(reminders.day_due(evs, at + timedelta(hours=12), history))
        self.assertIsNone(reminders.day_due(evs, at + timedelta(days=1), []))
        self.assertEqual(reminders.day_due(evs, at + timedelta(days=365), [])["count"], 0)
        skipped = holiday + "\r\nEXDATE;VALUE=DATE:20261020"
        self.assertIsNone(reminders.day_due(reminders.parse_events(self.ics(skipped), self.MSK), at, []))
        tomorrow = "UID:t\r\nSUMMARY:Завтра\r\nDTSTART;VALUE=DATE:20261021"
        self.assertIsNone(reminders.day_due(reminders.parse_events(self.ics(tomorrow), self.MSK), at, []))

    def test_day_mentions_spans_overrides_and_rotation(self):
        from radio import reminders
        at = self.NOW.astimezone(self.MSK)
        trip = "UID:trip\r\nSUMMARY:Поездка\r\nDTSTART;VALUE=DATE:20261019\r\nDTEND;VALUE=DATE:20261021"
        evs = reminders.parse_events(self.ics(trip), self.MSK)
        self.assertEqual(reminders.day_due(evs, at, [])["title"], "Поездка")
        self.assertIsNone(reminders.day_due(evs, at + timedelta(days=1), []))  # DTEND is exclusive
        holiday = "UID:h\r\nSUMMARY:Праздник\r\nDTSTART;VALUE=DATE:20251020\r\nRRULE:FREQ=YEARLY"
        moved = "UID:h\r\nSUMMARY:Перенос\r\nDTSTART;VALUE=DATE:20261021\r\nRECURRENCE-ID;VALUE=DATE:20261020"
        evs = reminders.parse_events(self.ics(holiday, moved), self.MSK)
        self.assertIsNone(reminders.day_due(evs, at, []))
        self.assertEqual(reminders.day_due(evs, at + timedelta(days=1), [])["title"], "Перенос")
        cancelled = moved.replace("20261021", "20261020") + "\r\nSTATUS:CANCELLED"
        self.assertIsNone(reminders.day_due(reminders.parse_events(self.ics(holiday, cancelled), self.MSK), at, []))
        evs = reminders.parse_events(self.ics(holiday, trip), self.MSK)
        first = reminders.day_due(evs, at, [])
        second = reminders.day_due(evs, at + timedelta(hours=4), [(first["key"], store.iso(at))])
        self.assertNotEqual(first["title"], second["title"])
        local_midnight = at.replace(hour=0, minute=5)
        self.assertEqual(reminders.day_due(evs, local_midnight, [])["start"].date(), at.date())

    def test_moved_and_cancelled_occurrences(self):
        master = "UID:s\r\nSUMMARY:Серия\r\nDTSTART;TZID=Europe/Moscow:20260106T100000\r\nRRULE:FREQ=WEEKLY;BYDAY=TU"
        moved = ("UID:s\r\nSUMMARY:Серия (перенесено)\r\nRECURRENCE-ID;TZID=Europe/Moscow:20261020T100000\r\n"
                 "DTSTART;TZID=Europe/Moscow:20261020T120000")
        self.assertEqual(self.due(master, moved), [("Серия (перенесено)", "12:00")])
        master12 = master.replace("T100000", "T120000")
        cancelled = ("UID:s\r\nSTATUS:CANCELLED\r\nRECURRENCE-ID;TZID=Europe/Moscow:20261020T120000\r\n"
                     "DTSTART;TZID=Europe/Moscow:20261020T120000")
        self.assertEqual(self.due(master12, cancelled), [])
        self.assertEqual(self.due(master12), [("Серия", "12:00")])

    def test_unsupported_rule_is_reported_not_guessed(self):
        from radio import reminders
        rule = "UID:u\r\nSUMMARY:Сложная\r\nDTSTART;TZID=Europe/Moscow:20261020T120000\r\nRRULE:FREQ=MONTHLY;BYDAY=TU;BYSETPOS=-1"
        evs = reminders.parse_events(self.ics(rule), self.MSK)
        self.assertEqual(reminders.unsupported(evs), ["Сложная"])
        self.assertEqual(reminders.calendar_due(evs, self.NOW, self.LEAD), [])

    def test_todoist_tasks_with_a_time(self):
        from radio import reminders
        task = lambda i, d: {"id": i, "content": f"t{i}", "due": {"date": d} if d else None}
        tasks = [task("1", "2026-10-20T11:58:00"), task("2", "2026-10-20T09:05:00Z"), task("3", "2026-10-20"),
                 task("4", "2026-10-20T15:00:00"), task("5", "2026-10-20T11:00:00"), task("6", None)]
        got = reminders.todoist_due(tasks, self.NOW, self.LEAD, self.MSK)
        self.assertEqual([r["title"] for r in got], ["t1", "t2"])      # floating = local time, Z = UTC; all-day and others not
        self.assertEqual(got[0]["key"], "todoist:1:20261020T0858")     # stable per occurrence: announced once

    def test_todoist_offsets_named_zones_and_links(self):
        from radio import reminders
        task = {"id": "links", "content": "Созвон [Meet](https://meet.example/room)",
                "description": 'https://meet.example/room\nhttps://docs.example/notes. javascript:alert(1) https://[bad',
                "due": {"date": "2026-10-20T12:00:00+03:00"}}
        got, = reminders.todoist_due([task], self.NOW, self.LEAD)
        self.assertEqual(got["start"], self.NOW + timedelta(minutes=10))
        self.assertEqual(got["description"], task["description"])
        self.assertEqual(got["title"], task["content"])
        task["due"] = {"date": "2026-10-20T12:00:00", "timezone": "Europe/Moscow"}
        self.assertEqual(reminders.todoist_due([task], self.NOW, self.LEAD)[0]["key"], got["key"])
        task["due"] = {"date": "2026-10-21T12:00:00", "timezone": "Europe/Moscow"}
        next_day, = reminders.todoist_due([task], self.NOW + timedelta(days=1), self.LEAD)
        self.assertNotEqual(next_day["key"], got["key"])
        task["due"] = {"date": "brokenTdate"}
        self.assertEqual(reminders.todoist_due([task], self.NOW, self.LEAD), [])

    def test_spoken_text(self):
        from radio import host, reminders
        r = {"title": "Стендап https://meet.example/x 📅", "start": self.NOW + timedelta(minutes=15), "kind": "event"}
        before = reminders.say(r, self.NOW, host.span_words)
        self.assertIn("через пятнадцать минут", before.lower())
        self.assertIn("— Стендап", before)                              # a dash before the title, not a colon
        self.assertNotIn("http", before)
        self.assertNotIn("Напоминание.", before)
        self.assertEqual(before, reminders.say(r, self.NOW, host.span_words))      # stable for one reminder
        task = reminders.say({**r, "kind": "task", "title": "Позвонить"}, self.NOW, host.span_words)
        self.assertIn("Позвонить", task)
        self.assertIn("сейчас", reminders.say(r, r["start"], host.span_words).lower())
        self.assertIn("час тридцать минут", reminders.say(r, r["start"] - timedelta(minutes=90), host.span_words))
        late = reminders.say(r, r["start"] + timedelta(minutes=3), host.span_words)  # moved past the start
        self.assertIn("три минуты", late)
        self.assertIn("Стендап", late)
        self.assertIsNone(reminders.say(r, r["start"] + timedelta(minutes=15), host.span_words))   # no end: 15 min
        long = {**r, "end": r["start"] + timedelta(hours=1)}
        self.assertIsNotNone(reminders.say(long, r["start"] + timedelta(minutes=40), host.span_words))
        self.assertIsNone(reminders.say(long, r["start"] + timedelta(hours=1), host.span_words))   # over
        self.assertEqual(reminders.plain(r, self.NOW, host.span_words), "Через пятнадцать минут — Стендап.")
        variants = {reminders.say({**r, "key": f"cal:{i}"}, self.NOW, host.span_words) for i in range(30)}
        self.assertGreater(len(variants), 1)                                # the phrasing rotates


if __name__ == "__main__":
    unittest.main()
