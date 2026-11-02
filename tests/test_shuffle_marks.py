"""Marked songs come up more often in the shuffle round, spread apart, without starving the others."""
import asyncio
import random
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from radio import config, settings, shuffle, store
from radio.engine import Liquidsoap


def line(name, remote=False):
    uri = f'ytmusic:{name}' if remote else f'/m/{name}.mp3'
    return f'annotate:plan_queue="music":annotate:kind="music",ref="{uri}",liq_amplify="0 dB":{uri}'


def plays(round_):
    out = {}
    for i, entry in enumerate(round_):
        out.setdefault(shuffle.ref(entry), []).append(i)
    return out


class WeightedRoundTests(unittest.TestCase):
    songs = [line(f's{i}') for i in range(20)]

    def test_marked_songs_play_boost_times_spread_apart_and_nobody_starves(self):
        marks = {'/m/s1.mp3': (3, '2026-11-01'), '/m/s2.mp3': (1, '2026-11-02')}
        for seed in range(50):
            round_ = shuffle.order(self.songs, marks, 3, random.Random(seed))
            where = plays(round_)
            self.assertEqual(len(where), 20, 'every song is in the round')
            self.assertEqual({r: len(p) for r, p in where.items() if len(p) > 1}, {'/m/s1.mp3': 3, '/m/s2.mp3': 3})
            gap = len(round_) // 4
            for spots in where.values():
                self.assertTrue(all(b - a >= gap for a, b in zip(spots, spots[1:])), (seed, spots))
                self.assertNotIn(0, spots[1:] if len(spots) > 1 else [])
            self.assertTrue(all(a != b for a, b in zip(round_, round_[1:])), 'never the same song twice in a row')

    def test_extra_plays_stop_at_30_percent_by_most_marks_then_latest(self):
        marks = {f'/m/s{i}.mp3': (1, f'2026-11-{i + 1:02d}') for i in range(10)}
        marks['/m/s0.mp3'] = (4, '2026-10-01')
        round_ = shuffle.order(self.songs, marks, 5, random.Random(7))
        extra = {r: len(p) - 1 for r, p in plays(round_).items() if len(p) > 1}
        self.assertLessEqual(sum(extra.values()), 6)                 # 30% of 20
        self.assertEqual(extra.get('/m/s0.mp3'), 4, 'most marks first, at most boost plays')
        self.assertEqual(sorted(extra), ['/m/s0.mp3', '/m/s9.mp3'], 'then the latest mark')
        self.assertTrue(all(n <= 4 for n in extra.values()))

    def test_no_marks_or_a_small_round_is_the_plain_shuffle(self):
        mixed = [line(f'r{i}', remote=True) for i in range(5)] + [line(f'l{i}') for i in range(5)]
        self.assertEqual(shuffle.order(mixed, {}, 3, random.Random(3)), shuffle.base(mixed, random.Random(3)))
        alternating = shuffle.order(mixed, {}, 3, random.Random(3))
        self.assertEqual(['ytmusic:' in x for x in alternating], [True, False] * 5, 'the sources alternate as before')
        small = self.songs[:7]
        self.assertEqual(shuffle.order(small, {'/m/s1.mp3': (2, 'x')}, 5, random.Random(1)), shuffle.base(small, random.Random(1)))

    def test_the_command_prints_the_round_and_settings_offer_the_three_choices(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)), \
                patch.object(config, 'RUN_DIR', Path(folder) / 'run'):
            (Path(folder) / 'db').mkdir()
            (Path(folder) / 'run').mkdir()
            db = store.connect()
            db.execute("INSERT INTO marks(event_id,ref,at) VALUES(1,'/m/s3.mp3',?)", (store.iso(store.now()),))
            db.close()
            listing = Path(folder) / 'round.txt'
            listing.write_text('\n'.join(self.songs))
            out = subprocess.run([sys.executable, '-m', 'radio.shuffle', str(listing)], capture_output=True, text=True,
                                 env={'RADIO_DATA_DIR': folder, 'PATH': '/usr/bin:/bin'}, cwd=Path(__file__).parents[1])
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(plays(out.stdout.split('\n'))['/m/s3.mp3']), 3, 'More (×3) by default')
            row = next(r for r in settings.listing(config.load()) if r['key'] == 'marked_boost')
            self.assertEqual((row['options'], row['value'], row['labels']['5']), (('2', '3', '5'), '3', 'Much more (×5)'))
            self.assertEqual(row['note'], 'Needs at least 8 songs in the round.')
            (Path(folder) / 'run/music.m3u').write_text('#EXTM3U\n' + '\n'.join(self.songs) + '\n')
            row = next(r for r in settings.listing(config.load()) if r['key'] == 'marked_boost')
            self.assertNotIn('note', row)



class EngineRoundTests(unittest.TestCase):
    def test_liquidsoap_takes_its_round_from_python(self):
        with tempfile.TemporaryDirectory(prefix='radio-marked-') as folder, patch.object(config, 'DATA_DIR', Path(folder)), \
                patch.object(config, 'RUN_DIR', Path(folder) / 'run'):
            root = Path(folder)
            config.load()
            for sub in ('db', 'run', 'logs'):
                (root / sub).mkdir(exist_ok=True)
            db = store.connect()
            db.execute("INSERT INTO marks(event_id,ref,at) VALUES(1,'track-3',?)", (store.iso(store.now()),))
            db.close()
            tone = root / 'tone.wav'
            with wave.open(str(tone), 'wb') as stream:
                stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\xe8\x03' * 2 * 44100 * 3)
            playlist = root / 'music.m3u'
            playlist.write_text(''.join(f'annotate:kind="music",ref="track-{i}":{tone}\n' for i in range(10)))

            async def scenario():
                engine = Liquidsoap(playlist, .1, str(root / 'out.wav'))
                try:
                    await engine._start(lambda event: None, True)
                    deadline = time.monotonic() + 10
                    while not await engine.next_music():
                        self.assertLess(time.monotonic(), deadline)
                        await asyncio.sleep(.1)
                    return await engine.upcoming_music()
                finally:
                    await engine.stop()
            upcoming = asyncio.run(scenario())
            self.assertEqual(len(upcoming), 12, upcoming)
            self.assertEqual(upcoming.count('track-3'), 3, 'the marked song three times (More, ×3)')
            self.assertEqual(sorted(set(upcoming)), [f'track-{i}' for i in range(10)])

if __name__ == '__main__':
    unittest.main()
