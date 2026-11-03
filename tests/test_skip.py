"""Skipping a coming song this time: the round goes on without it, and "Don't skip" puts it back in its place."""
import asyncio
import json
import os
import re
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from radio import config
from radio.engine import Liquidsoap
from tests.ui_page import WEB


def plan_refs(plan):
    def ref_of(row):
        return row['ref'] or json.loads(re.search(r'ref=("(?:[^"\\]|\\.)*")', row['uri'])[1])
    return [(ref_of(r), r['skipped']) for r in plan]


class EngineSkipTests(unittest.TestCase):
    def test_skip_and_take_back_a_queued_and_a_later_song(self):
        with tempfile.TemporaryDirectory(prefix='radio-skip-') as folder, patch.object(config, 'DATA_DIR', Path(folder)), \
                patch.object(config, 'RUN_DIR', Path(folder) / 'run'):
            root = Path(folder)
            config.load()
            for sub in ('db', 'run', 'logs'):
                (root / sub).mkdir(exist_ok=True)
            tone = root / 'tone.wav'
            with wave.open(str(tone), 'wb') as stream:
                stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\xe8\x03' * 2 * 44100 * 2)
            playlist = root / 'music.m3u'
            playlist.write_text(''.join(f'annotate:kind="music",ref="track-{i}":{tone}\n' for i in range(6)))
            events = []

            async def scenario():
                engine = Liquidsoap(playlist, .1, str(root / 'out.wav'))
                engine.music_shuffle = False
                try:
                    await engine._start(events.append, True)
                    deadline = time.monotonic() + 10
                    while not await engine.next_music():
                        self.assertLess(time.monotonic(), deadline)
                        await asyncio.sleep(.1)
                    first = await engine.next_music()
                    self.assertEqual(await engine.command('music.skip ' + first), 'OK')
                    self.assertEqual(await engine.command('music.skip ' + first), 'OK', 'idempotent')
                    self.assertNotEqual(await engine.next_music(), first, 'the next one moves up')
                    plan = plan_refs(await engine.music_plan())
                    self.assertEqual(plan[0], (first, True), 'the skipped one stays in its place, marked')
                    self.assertEqual(await engine.command('music.unskip ' + first), 'OK')
                    self.assertEqual(await engine.next_music(), first, "Don't skip: back first")
                    self.assertEqual(await engine.command('music.unskip ' + first), 'STALE')
                    self.assertEqual(await engine.command('music.skip track-3'), 'OK')
                    self.assertIn(('track-3', True), plan_refs(await engine.music_plan()))
                    self.assertEqual(await engine.command('music.skip nothing'), 'STALE')
                    await engine.command('resume')
                    deadline = time.monotonic() + 30
                    while len([e for e in events if e.get('kind') == 'music']) < 5:
                        self.assertLess(time.monotonic(), deadline, events)
                        await asyncio.sleep(.1)
                    return [e['ref'] for e in events if e.get('kind') == 'music'][:5]
                finally:
                    await engine.stop()
            with patch.dict(os.environ, RADIO_FILE_SYNC='realtime'):
                aired = asyncio.run(scenario())
            self.assertEqual(aired, ['track-0', 'track-1', 'track-2', 'track-4', 'track-5'], 'track-3 was passed over this round')



class FutureRowsTests(unittest.TestCase):
    def run_model(self, script):
        import subprocess
        result = subprocess.run(['node', '-e', (WEB / 'core/core.js').read_text() + (WEB / 'core/model.js').read_text() + script],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_next_two_songs_blocks_within_the_hour_skipped_in_place_at_most_8(self):
        self.run_model(r"""
const assert=require('node:assert/strict'),T=Date.now(),at=m=>new Date(T+m*60000).toISOString();
const song=(n,m,sk)=>({ref:'/m/'+n,title:n,at:at(m),skipped:!!sk});
const st={state:'PLAYING',upcoming:[{kind:'music',ref:'/m/B',title:'B',stage:'music'}],
  upcoming_tracks:[song('A',2,true),song('B',2),song('C',6),song('D',10),song('E',14)],
  broadcast_plan:{version:'v',rows:[{id:'w1',kind:'weather',title:'Weather · Da Lat',at:at(30),status:'PENDING'},
    {id:'n1',kind:'news',title:'',edition:true,at:at(8),status:'PENDING',skipped:true},
    {id:'n2',kind:'news',title:'',edition:true,at:at(90),status:'PENDING'},{id:'m1',kind:'music',at:at(5),status:'PENDING'}]}};
const f=RadioModel.future(st,'');
assert.deepEqual(f.rows.map(r=>[r.label,r.title,r.skipped]),[['skipped','A',true],['next','B',false],['then','C',false],
  ['news','Host edition',true],['then','D',false],['weather','Weather · Da Lat',false]]);
assert.equal(f.rows[1].ref,'/m/B');assert.equal(f.rows[3].id,'n1');assert.equal(f.rows[3].rank,1);
assert.ok(f.rows.every(r=>r.skippable));
const many={state:'PLAYING',upcoming:[{kind:'news',title:'Host edition',stage:'next'}],upcoming_tracks:[song('B',1)],
  broadcast_plan:{rows:Array.from({length:12},(_,i)=>({id:'w'+i,kind:'weather',title:'W'+i,at:at(3+i),status:'PENDING'}))}};
const g=RadioModel.future(many,'');
assert.equal(g.rows.length,8);assert.equal(g.rows[0].label,'next');assert.equal(g.rows[0].skippable,false,'a break about to start is not skipped here');
const waiting={state:'PLAYING',upcoming:[{kind:'news',title:'Host edition',stage:'next',members:['reddit:t3_a#1']}],upcoming_tracks:[song('B',1)],
  broadcast_plan:{rows:[{id:'section:news:clock:1',kind:'news',title:'Host edition',at:at(2),status:'READY',members:['section:news:clock:1','reddit:t3_a#1']},
    {id:'section:news:clock:2',kind:'news',title:'Host edition',at:at(30),status:'PENDING',members:['section:news:clock:2']}]}};
assert.deepEqual(RadioModel.future(waiting,'').rows.map(r=>[r.label,r.id||'']),[['next',''],['then',''],['news','section:news:clock:2']],
  'the waiting edition is one row: next, not its plan block again');
""")

if __name__ == '__main__':
    unittest.main()
