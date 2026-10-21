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
        self.cfg = config.load()
        self.svc = Service(self.cfg, tts=MockTTS(), output="x")
        for t in ("packages", "items", "events"):
            self.svc.db.execute(f"DELETE FROM {t}")

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
            async def push_station_id(self, f, gain, tail=0.0):
                pushed.append(("id", f.name))

            async def push_news(self, wav, pid, talkover=0.0):
                pushed.append(("news", pid, wav.name))

            async def next_music(self):
                return "/m/Next Artist - Song.mp3"

            async def push_replay(self, wav, kind, ref, gain, rid, tags="", queue="news"):
                pushed.append((queue, ref))
                return str(len(pushed))

            async def remove(self, rid, queue="news"):
                pushed.append(("removed", rid, queue))
        self.svc.engine, self.svc.state = FakeEngine(), "PLAYING"

    def test_station_id_inside_music_set(self):
        pushed = []
        self.fake_engine(pushed)
        self.cfg["broadcast"]["station_id_every_songs"] = 3
        self.svc.on_event({"kind": "music", "ref": "/m/0.mp3"})
        self.svc.on_event({"kind": "news", "ref": "x#1"})        # a break has aired: a set is running
        for i in range(3):
            self.svc.on_event({"kind": "music", "ref": f"/m/{i}.mp3"})
            asyncio.run(self.svc.schedule())
        self.assertEqual([k[0] for k in pushed], ["id"])          # after the 3rd song, not before
        self.add("d", "chatgpt", 1)
        self.svc.on_event({"kind": "id", "ref": pushed[0][1]})
        self.svc.on_event({"kind": "music", "ref": "/m/4.mp3"})
        asyncio.run(self.svc.schedule())
        self.assertEqual(len(pushed), 1)                           # ready news waits for the end of the set

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
        self.svc.files = songs
        self.svc.intro_rooms = {str(f): None for f in songs}
        self.svc.replays = {}
        self.add("a", "chatgpt", 1)
        self.add("b", "chatgpt", 1)
        self.svc.on_event({"kind": "music", "ref": str(old)})
        self.svc.current["duration"], self.svc.current["started"] = 30.0, time.time() - 29
        asyncio.run(self.svc.schedule())                                      # break 1: a normal set
        self.assertEqual((said, [p[0] for p in pushed]), ([(("Next Artist", "Song"), False)], ["news"]))
        self.svc.on_event({"kind": "news", "ref": pushed[0][1]})
        self.svc.on_event({"kind": "music", "ref": str(old)})
        self.add("c", "chatgpt", 1)
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
        svc._render_notice = lambda text: Path("/tmp/notice.wav")
        svc.cfg = dict(self.cfg, reminders=dict(self.cfg["reminders"], calendar_url="https://example.test/secret.ics",
                                                todoist_token="tok"))
        soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y%m%dT%H%M%SZ")
        ics = ReminderTests.ics(f"UID:e1\r\nSUMMARY:Планёрка\r\nDTSTART:{soon}")
        real = reminders.fetch_ics, reminders.fetch_todoist
        reminders.fetch_ics, reminders.fetch_todoist = lambda url: ics, lambda token: []
        try:
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.sources["calendar"], "ok")
            self.assertEqual(len(svc.reminders_due), 1)
            asyncio.run(svc._reminders_tick())
            self.assertEqual(len(svc.reminders_due), 1)                     # not added twice
            self.assertFalse(asyncio.run(svc._remind(300.0)))               # the song has 5 minutes left: too early
            self.assertTrue(asyncio.run(svc._remind(20.0)))
            key = pushed[0][1]
            self.assertEqual(pushed[0][0], "reminder")
            self.assertIn("Через десять минут — Планёрка", svc.reminder_text[key])
            self.assertTrue(svc.id_pending and not svc.reminders_due)
            asyncio.run(svc._reminders_tick())
            self.assertEqual(svc.reminders_due, {})                         # announced once
            svc.on_event({"kind": "reminder", "ref": key})
            self.assertFalse(svc.id_pending)
            row = svc.db.execute("SELECT type, detail FROM events WHERE type='REMINDER' ORDER BY id DESC").fetchone()
            self.assertIn("Планёрка", row["detail"])
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
            with patch.object(reminders, "fetch_ics", return_value=ics), patch.object(daemon, "now", return_value=at):
                asyncio.run(svc._reminders_tick())
                self.assertEqual([r["kind"] for r in svc.reminders_due.values()], ["day"])
                self.assertFalse(asyncio.run(svc._remind(300)))
                self.assertTrue(asyncio.run(svc._remind(20)))
                self.assertEqual(rendered, ["Сегодня — День радио."])
                svc.on_event({"kind": "reminder", "ref": pushed[0][1]})
                self.assertIn("День радио", svc.db.execute("SELECT detail FROM events WHERE type='REMINDER'").fetchone()[0])
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
                self.assertFalse(svc.reminders_due)
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
        svc._render_notice = lambda text: Path("/tmp/notice.wav")
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
        self.assertEqual(news[0]["title"], "Tech news · GitHub: find of the day · 2 stories")
        self.assertEqual(len(news[0]["parts"]), 2)
        self.assertEqual(news[0]["parts"][0]["title"], "Tech news")      # in the order they were said
        self.assertFalse(any(k in news[0] for k in ("_at", "_topics")))
        svc.current = {"kind": "news", "ref": "horizon:1#1|github:b#1", "started": time.time()}
        self.assertEqual(svc._title(), "Tech news · GitHub: find of the day")  # no "1/1" or length
        for kind, title in (("id", "Station ID"), ("reminder", "Reminder")):
            svc.current = {"kind": kind, "ref": "x", "started": time.time()}
            self.assertEqual(svc.status()["current_title"], title)           # status must not fail on these

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
            plugins.set_plugin("voicestudio", False)
            tts_mod.port_open = lambda port: False
            asyncio.run(svc._voice_tick())
            self.assertEqual(len(started), 1)                               # plugin off: the user runs it by hand
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
        self.assertTrue(any(b and "«A», «C» и «D» с песней «d»" in b for b in backs))
        self.assertTrue(any(b and "«A», «C» и «D»." in b for b in backs))  # artists only
        self.assertTrue(any(b and "«D»" in b and "«A»" not in b for b in backs))  # just the last song
        opens = {host.set_opener(20, ("A", "t"), {}, f"s{i}") for i in range(40)}
        self.assertTrue(any("«A»" in o for o in opens) and any("«A»" not in o for o in opens))
        self.assertEqual(host.back_sell([], {}, "s"), None)

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
            path.write_text("not json")                                                          # a broken file changes nothing
            settings.apply(svc.cfg, d)
            self.assertEqual(svc.cfg["broadcast"]["new_every_sets"], d["new_every_sets"])
        finally:
            path.unlink(missing_ok=True)
            settings.apply(svc.cfg, d)

    def test_longer_sets_make_breaks_rarer(self):
        from radio import settings
        svc, pushed = self.svc, []
        self.fake_engine(pushed)
        svc._assemble_break = lambda pkgs, air, links, first, room, novelty: (Path("/tmp/b.wav"), 0)
        self.cfg["broadcast"]["station_id_every_songs"] = 0
        self.add("a", "chatgpt", 1)
        svc.on_event({"kind": "music", "ref": "/m/1.mp3"})
        svc.on_event({"kind": "news", "ref": "x#1"})          # a break has aired: a set is running
        svc.on_event({"kind": "music", "ref": "/m/2.mp3"})
        svc.set_started -= 25 * 60                            # the set has already run 25 minutes
        svc.current["duration"], svc.current["started"] = 30.0, time.time() - 29
        path = config.DATA_DIR / "settings.json"
        try:
            settings.set_setting("music_set_minutes", 40)
            asyncio.run(svc.schedule())
            self.assertEqual(pushed, [])                       # 25 min < 40: the break waits
            settings.set_setting("music_set_minutes", 20)
            asyncio.run(svc.schedule())
            self.assertEqual([p[0] for p in pushed], ["news"])  # live, without a restart
        finally:
            path.unlink(missing_ok=True)
            settings.apply(svc.cfg, svc.defaults or settings.base(svc.cfg))

    def test_plugin_switches(self):
        from radio import plugins
        (config.DATA_DIR / "plugins.json").unlink(missing_ok=True)
        self.assertFalse(plugins.on(self.cfg, "horizon"))                 # default from config
        plugins.set_plugin("horizon", True)
        self.assertTrue(plugins.on(self.cfg, "horizon"))                  # read live from the file
        grouped = {p["name"]: p for p in plugins.listing(self.cfg)}
        self.assertEqual(set(grouped), set(plugins.PLUGINS))
        self.assertEqual({n for n, p in grouped.items() if p["section"] == "integrations"},
                         {"github_trending", "github_new", "horizon", "reminders_calendar", "reminders_todoist", "voicestudio", "weather", "ytmusic"})
        self.assertEqual({n for n, p in grouped.items() if p["section"] == "experiments"}, {"voice_over"})
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
        at = store.now()
        report = {"kind": "weather", "key": f"weather:{int(at.timestamp())}", "start": at,
                  "expires": at + timedelta(minutes=30), "text": "Коротко о погоде. Далат: плюс 20 градусов.",
                  "summary": "Da Lat · 20° · clear · wind 2 m/s"}

        class Engine:
            async def push_replay(self, wav, kind, ref, gain, rid, tags=""):
                pushed.append((kind, ref, gain))

        svc.engine = Engine()
        try:
            with patch.object(weather, "fetch", return_value=report) as fetch, \
                    patch.object(daemon, "now", return_value=at), \
                    patch.object(daemon, "cached_synthesize", return_value=Path("/tmp/weather.wav")) as synth, \
                    patch.object(daemon.audio, "lufs", return_value=-20):
                asyncio.run(svc._weather_tick())
                asyncio.run(svc._weather_tick())
                fetch.assert_called_once_with("Da Lat", at)
                self.assertFalse(asyncio.run(svc._remind(300)))
                self.assertTrue(asyncio.run(svc._remind(20)))
                self.assertEqual(pushed, [("weather", report["key"], 4)])
                synth.assert_called_once_with(svc.tts, report["text"], svc._speech_check)
                svc.on_event({"kind": "weather", "ref": report["key"]})
                self.assertFalse(svc.id_pending)
                self.assertEqual(svc.status()["current_title"], "Weather")
                self.assertIn("ПОГОДА", svc.status()["current"])
                self.assertEqual(svc.db.execute("SELECT detail FROM events WHERE type='WEATHER'").fetchone()[0], report["summary"])
                entry = next(e for e in daemon.timeline._build(svc.db, 12, 50) if e["kind"] == "weather")
                self.assertEqual(entry["title"], report["summary"])
                self.assertNotIn("parts", entry)  # a weather line is not expandable
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

    def test_weather_outage_retries_without_announcing(self):
        from radio import weather
        from unittest.mock import patch
        svc = self.svc
        svc.cfg = dict(self.cfg, weather={"city": "Da Lat"})
        svc.state = "PLAYING"
        svc.db.execute("DELETE FROM reminders WHERE key LIKE 'weather:%'")
        with patch.object(weather, "fetch", side_effect=OSError("offline")) as fetch:
            asyncio.run(svc._weather_tick())
            asyncio.run(svc._weather_tick())
            fetch.assert_called_once()
        self.assertFalse(svc.reminders_due)
        self.assertEqual(svc.sources["weather"], "error: OSError")
        self.assertGreater(svc.next_weather_read, time.time() + 14 * 60)

    def test_no_news_once_and_only_after_successful_checks(self):
        db = self.svc.db
        self.svc._maybe_announce_no_news()  # sources not checked yet (e.g. network error)
        self.assertFalse(db.execute("SELECT 1 FROM items WHERE source='system'").fetchone())
        self.svc.all_sources_checked = True
        self.svc._maybe_announce_no_news()
        self.svc._maybe_announce_no_news()
        rows = db.execute("SELECT p.status FROM items i JOIN packages p ON p.item_id=i.id WHERE i.source='system'").fetchall()
        self.assertEqual([r[0] for r in rows], ["PENDING"])
        text = Path(db.execute("SELECT script_path FROM packages WHERE item_id LIKE 'system:%'").fetchone()[0]).read_text()
        self.assertEqual(text, "Свежих новостей пока нет. Продолжаем музыку.")

    def test_no_announcement_when_news_waiting(self):
        self.add("d", "chatgpt", 1)
        self.svc.all_sources_checked = True
        self.svc._maybe_announce_no_news()
        self.assertFalse(self.svc.db.execute("SELECT 1 FROM items WHERE source='system'").fetchone())


class JingleRotationTests(unittest.TestCase):
    def test_no_repeat_until_folder_used_up(self):
        from radio import audio
        cfg = {"bed": [Path(f"b{i}.mp3") for i in range(4)], "stinger_out_max_sec": 4.0}
        recent = {}
        picks = [audio.choose_jingles(cfg, f"pkg{i}", recent)["bed"] for i in range(12)]
        for i in range(0, 12, 4):
            self.assertEqual(len(set(picks[i:i + 4])), 4, picks)  # every 4 packages use all 4 beds
        self.assertTrue(all(a != b for a, b in zip(picks, picks[1:])))  # never twice in a row


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
        self.assertGreater(out.stat().st_size, 1_000_000)
        # After stop nothing is left in flight.
        self.assertFalse(db.execute("SELECT 1 FROM packages WHERE status IN ('QUEUED','PLAYING')").fetchone())

    def test_replay_unqueue(self):
        svc = Service(self.cfg, tts=MockTTS(), output=str(TMP / "replay.wav"))
        kept = None

        async def scenario():
            task = asyncio.create_task(svc.run())
            while not svc.current:
                await asyncio.sleep(0.2)
            nonlocal kept
            kept, gone = sorted(f for f in svc.library if "track" in f)[:2]
            await svc.dispatch("replay", {"kind": "music", "ref": gone})
            await svc.dispatch("replay", {"kind": "music", "ref": kept})
            queued = (await svc.dispatch("unqueue", {"kind": "music", "ref": gone}))["status"]["replay_queue"]
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
        stamp = int(self.AT.timestamp())
        return {"current": {"time": stamp, "temperature_2m": 21.4, "wind_speed_10m": 2.1, "weather_code": 3},
                "hourly": {"time": [stamp - 900 + i * 3600 for i in range(4)],
                           "precipitation_probability": [0, 20, 70, 50]}}

    def test_report_and_forecast_validation(self):
        import copy
        from radio import weather
        data = self.data()
        report = weather.report(data, "Далат", self.AT, "Da Lat")
        self.assertEqual(report["summary"], "Da Lat · 21° · overcast · wind 2 m/s · rain likely")
        self.assertIn("Далат: по прогнозу сейчас плюс 21 градус, пасмурно", report["text"])
        self.assertIn("высока вероятность осадков", report["text"])
        self.assertIn("2 метра в секунду", report["text"])
        self.assertEqual(report["expires"], self.AT + timedelta(minutes=30))
        for section, key, value in [("current", "temperature_2m", None), ("current", "temperature_2m", float('nan')),
                                    ("current", "weather_code", 4), ("current", "time", 0),
                                    ("hourly", "precipitation_probability", [0, None, 70, 50]),
                                    ("hourly", "precipitation_probability", [0, 200, 70, 50]),
                                    ("hourly", "time", [0]), ("hourly", "time", [0, 0, 0, 0])]:
            with self.subTest(key=key, value=value):
                bad = copy.deepcopy(data)
                bad[section][key] = value
                with self.assertRaises(ValueError):
                    weather.report(bad, "Далат", self.AT, "Da Lat")
        data["current"].update(temperature_2m=-12, wind_speed_10m=1)
        data["hourly"]["precipitation_probability"] = [0, 10, 10, 10]
        text = weather.report(data, "Далат", self.AT, "Da Lat")["text"]
        self.assertIn("минус 12 градусов", text)
        self.assertIn("1 метр в секунду", text)
        self.assertIn("вероятность осадков невысокая", text)

    def test_fetch_uses_resolved_city_and_explicit_units(self):
        from radio import weather
        from unittest.mock import patch
        place = {"name": "Далат", "latitude": 11.94646, "longitude": 108.44193}
        with patch.object(weather, "_get", side_effect=[{"results": [place]}, self.data()]) as get:
            self.assertIn("Далат", weather.fetch("Da Lat", self.AT)["text"])
            self.assertEqual(get.call_args_list[0].kwargs["name"], "Da Lat")
            self.assertEqual(get.call_args_list[1].kwargs["wind_speed_unit"], "ms")
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
        single = "UID:1\r\nSUMMARY:Планёрка\r\nDTSTART;TZID=Europe/Moscow:20261020T120000"
        later = "UID:2\r\nSUMMARY:Позже\r\nDTSTART;TZID=Europe/Moscow:20261020T130000"
        utc = "UID:3\r\nSUMMARY:UTC\r\nDTSTART:20261020T090500Z"
        allday = "UID:4\r\nSUMMARY:Весь день\r\nDTSTART;VALUE=DATE:20261020"
        gone = "UID:5\r\nSUMMARY:Отменено\r\nSTATUS:CANCELLED\r\nDTSTART:20261020T090500Z"
        self.assertEqual(self.due(single, later, utc, allday, gone), [("Планёрка", "12:00"), ("UTC", "12:05")])

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

    def test_spoken_text(self):
        from radio import host, reminders
        r = {"title": "Планёрка https://meet.example/x 📅", "start": self.NOW + timedelta(minutes=15), "kind": "event"}
        self.assertEqual(reminders.say(r, self.NOW, host.minutes_words), "Напоминание. Через пятнадцать минут — Планёрка.")
        self.assertEqual(reminders.say({**r, "kind": "task", "title": "Позвонить"}, self.NOW, host.minutes_words),
                         "Напоминание. Через пятнадцать минут задача — Позвонить.")
        self.assertEqual(reminders.say(r, r["start"], host.minutes_words), "Напоминание. Сейчас — Планёрка.")
        self.assertIsNone(reminders.say(r, r["start"] + timedelta(minutes=3), host.minutes_words))   # already started


if __name__ == "__main__":
    unittest.main()
