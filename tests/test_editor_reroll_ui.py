import subprocess
import unittest
from tests.ui_page import core_source, theme_source


class EditorSongRowTests(unittest.TestCase):
    """Edit: every coming song has a dice (the next one: the next-track dice), a later one ✕ or Don't skip; one
    song is replaced at a time and its error shows under its own row."""
    def test_dice_skip_and_error_on_every_song_row(self):
        source = theme_source()
        code = source[source.index('function rerollButton('):source.index('function planReadiness(')]
        harness = r'''
const assert=require('node:assert/strict');
var st={},rerollConfirm=null,rerollBusy=false,rerollError='',planDraft=[],calls=[],answer={ok:true},CROSS='x',rerollFx=null;
var document={documentElement:{dataset:{}},hidden:true},window={},nodes={};
function matchMedia(){return {matches:true}}
function $(id){return nodes[id]||null}function esc(s){return String(s)}
function directionView(){}function timeline(){}function refresh(){}
function api(path,body){calls.push([body.cmd,body.ref]);return Promise.resolve(answer)}
function cmd(name,args){calls.push([name,args.ref]);return Promise.resolve({ok:true})}
const tracks=[{ref:'/m/a',title:'A'},{ref:'/m/b',title:'B'},{ref:'/m/c',title:'C',skipped:true}];
st={upcoming_tracks:tracks,next_reroll:{ref:'/m/a',available:true},announced_tracks:[]};
const rowOf=i=>planTrackRow(Object.assign({},tracks[i],{id:'track:'+i,kind:'music'}),tracks[i].title,'~12:30');
(async function(){
assert.match(rowOf(0),/data-reroll /);assert.doesNotMatch(rowOf(0),/data-track-skip/,'the next song: only its dice');
assert.match(rowOf(1),/data-track-reroll="1"/);assert.match(rowOf(1),/data-track-skip="1"/);
assert.match(rowOf(2),/data-track-unskip="2"/);assert.match(rowOf(2),/class="it music skipped"/);assert.doesNotMatch(rowOf(2),/reroll/);
rerollTrack(1);assert.deepEqual(calls,[['reroll','/m/b']]);
assert.match(rowOf(1),/aria-busy="true" disabled/);assert.match(rowOf(0),/disabled/,'one song at a time');
rerollTrack(0);assert.equal(calls.length,1);
await new Promise(r=>setImmediate(r));assert.equal(trackReroll.busy,null);assert.doesNotMatch(rowOf(1),/disabled/);
answer={ok:false,error:'This song is no longer coming up'};rerollTrack(1);await new Promise(r=>setImmediate(r));
assert.match(rowOf(1),/<p class="l err track-err">This song is no longer coming up<\/p>/);assert.doesNotMatch(rowOf(0),/track-err/);
assert.match(rowOf(1),/data-track-reroll="1"/,'the dice stays after an error');
answer={ok:true};skipTrack(1,true);skipTrack(2,false);assert.deepEqual(calls.slice(-2),[['skip','/m/b'],['unskip','/m/c']]);
assert.doesNotMatch(rowOf(1),/track-err/);
rerollNext();assert.deepEqual(calls.at(-1),['reroll-next','/m/a'],'the next song keeps reroll-next');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', core_source() + '\n' + code + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
