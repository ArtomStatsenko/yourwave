import subprocess
import unittest
from pathlib import Path


class TitleAnimationTests(unittest.TestCase):
    def test_typing_preserves_prefix_and_follows_transition_duration(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        frame = source[source.index('function titleFrame('):source.index('var tfx=')]
        subprocess.run(['node', '-e', frame + r"""
const assert = require('node:assert/strict');
for (const duration of [100, 3000]) {
  for (let ms = 0; ms < duration; ms += 5) {
    const f = titleFrame('Carbonara - Yesterday', 'Carbonara - Tomorrow', ms, duration);
    assert.ok(f.text.startsWith('Carbonara - '));
    assert.equal(f.done, false);
  }
  assert.deepEqual(titleFrame('Carbonara - Yesterday', 'Carbonara - Tomorrow', duration, duration),
                   {text:'Carbonara - Tomorrow',caret:false,done:true});
}
assert.equal(titleFrame('ab', 'ac', 0, 0).text, 'ac');
assert.equal(titleFrame('ab', 'ac', 4000, NaN).done, true);
assert.equal(titleFrame('😀 old', '😀 new', 50, 100).text.startsWith('😀 '), true);
assert.equal(titleFrame('Long title', 'Long', 100, 100).text, 'Long');
"""], check=True, capture_output=True, text=True)

    def test_next_keeps_stream_open_and_seeks_only_inside_buffered_audio(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        catch_up = source[source.index('function catchUp('):source.index('function listen(')]
        command = source[source.index('function cmd('):source.index('function replay(')]
        subprocess.run(['node', '-e', catch_up + command + r"""
const assert = require('node:assert/strict');
let busy=false,nextBusy=false,listening=true,seeks=0,resets=0,result={ok:true};
let pos=1;
const au={buffered:{length:1,end:()=>3},get currentTime(){return pos},
          set currentTime(v){seeks++;pos=v},set src(v){resets++}};
const api=()=>Promise.resolve(result),refresh=()=>{},player=()=>{},setTimeout=()=>{},$=()=>({textContent:''});
(async()=>{
  const switching=cmd('next');assert.equal(nextBusy,true);
  await switching;assert.equal(nextBusy,false);
  assert.equal(resets,0);
  assert.equal(seeks,1);
  assert.equal(pos,2.75);
  await cmd('next');
  assert.equal(seeks,1);
  pos=1;result={ok:false};await cmd('next');assert.equal(seeks,1);
  result={ok:true};await cmd('reroll-next');assert.equal(seeks,1);
  au.buffered.length=0;await cmd('next');assert.equal(seeks,1);
  assert.equal(busy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
"""], check=True, capture_output=True, text=True)

    def test_music_source_label_distinguishes_configuration_from_playback(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        helper = source[source.index('function musicSourceLabel('):source.index('function player(')]
        subprocess.run(['node', '-e', helper + r"""
const assert = require('node:assert/strict');
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'local'},false,{connected:true}), 'Local music · YouTube Music off');
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'ytmusic'},true,{}), 'YouTube Music');
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'local',ytmusic_active:true},true,{}), 'Local music · YouTube Music fallback');
assert.equal(musicSourceLabel({state:'STOPPED'},true,{connected:true}), 'YouTube Music · configured');
"""], check=True, capture_output=True, text=True)
