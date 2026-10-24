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

    def test_editor_hour_groups_and_autosave_rollback(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        code = source[source.index('function planGroups('):source.index('function planMove(')]
        result = subprocess.run(['node', '-e', code + r"""
const assert=require('node:assert/strict');
const at=Date.parse('2026-10-24T06:00:00Z');Date.now=()=>at;
var planDraft=Array.from({length:10},(_,i)=>({id:'clock:'+i,kind:'news',title:'Host edition',edition:true,segments:[{title:'Weather',items:[]}],at:new Date(at+i*1800e3).toISOString()}));
var pls=[{name:'air_direction',on:true}],st={broadcast_plan:{rows:structuredClone(planDraft),version:'v1'},direction_timezone:'UTC'},
 dirLoaded=true,dirBusy=false,planDrag=null,planTimeId=null,planConfirm=null,planOpen={},planHours={},planHorizon=3,planUndo=null,planInsertKey=null,planVersion='v1';
var document={activeElement:null,body:{classList:{toggle(){}}}};
var nodes={};function $(id){return nodes[id]||(nodes[id]={innerHTML:'',hidden:false,classList:{toggle(){}},contains:()=>false,querySelectorAll:()=>[]})}
var esc=s=>String(s),planReadiness=()=>'',localInput=s=>s;
var requests=[],response;function api(path,body){requests.push(body);return Promise.resolve(response)}
(async()=>{
 const groups=planGroups(planDraft);assert.equal(groups.length,3);assert.equal(groups[0].entries[0].row.id,'clock:5');
 directionView();var html=$('direction-list').innerHTML;
 assert.equal((html.match(/ open>/g)||[]).length,1);assert.ok(html.indexOf('clock:5')<html.indexOf('clock:0'));
 assert.ok(html.indexOf('data-plan-add=')<html.indexOf('<details'),'Collapsed hours must still expose their insertion gaps');
 assert.ok(html.includes('Weather'));assert.ok(html.includes('data-plan-action="segments"'));
 planHours[groups[0].key]=true;directionView();assert.equal(($('direction-list').innerHTML.match(/ open>/g)||[]).length,2);
 planHours={};directionView();assert.equal(($('direction-list').innerHTML.match(/ open>/g)||[]).length,1);
 planDraft[0].dirty=true;planDraft[0].skipped=true;response={ok:false,error:'Changed elsewhere'};
 await planSave();assert.equal(requests[0].remember_undo,true);assert.equal(planDraft[0].skipped,undefined);assert.equal(dirBusy,false);assert.equal($('direction-message').textContent,'Changed elsewhere');
 planDraft[0].dirty=true;response={ok:true,broadcast_plan:st.broadcast_plan,undo_token:'undo-one'};
 await planSave();assert.equal(planUndo,'undo-one');assert.equal(planDraft[0].dirty,undefined);
})().catch(e=>{console.error(e);process.exitCode=1});
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_history_rejects_drop_and_arrows_follow_upward_time(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        handlers = source[source.index("$('tl').ondragover="):source.index('function cmd(')]
        subprocess.run(['node', '-e', "var nodes={};" + handlers + r"""
const assert=require('node:assert/strict');var planDrag=3;function $(id){return nodes[id]||(nodes[id]={})}
var prevented=0,e={preventDefault(){prevented++},dataTransfer:{}};
$('tl').ondragover(e);assert.equal(e.dataTransfer.dropEffect,'none');$('tl').ondrop(e);assert.equal(planDrag,null);assert.equal(prevented,2);
"""], check=True, capture_output=True, text=True)
        self.assertIn("button.dataset.planAction==='up'?1:-1", source)
        self.assertIn('items.slice(0,5)', source)

    def test_gap_times_stay_between_future_neighbors(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        helper = source[source.index('function planGapTime('):source.index('function findPlanGap(')]
        subprocess.run(['node', '-e', helper + r"""
const assert=require('node:assert/strict');const at=Date.parse('2026-10-24T06:00:00Z');Date.now=()=>at;
const rows=[{at:new Date(at+3600e3).toISOString()},{at:new Date(at+1800e3).toISOString()}];
assert.equal(Date.parse(planGapTime(rows,1)),at+2700e3);
assert.equal(Date.parse(planGapTime(rows,2)),at+900e3);
assert.ok(Date.parse(planGapTime(rows,0))>Date.parse(rows[0].at));
assert.ok(Date.parse(planGapTime([],0))>=at);
assert.equal(planGapTime([{at:new Date(at-60000).toISOString()}],1),null);
assert.equal(planGapTime([rows[0],rows[0]],1),null);
"""], check=True, capture_output=True, text=True)

    def test_gap_opens_form_in_place_and_uses_latest_version_on_submit(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        helpers = source[source.index('function findPlanGap('):source.index('function planSave(')]
        local_input = source[source.index('function localInput('):source.index('function directionType(')]
        submit = source[source.index("$('direction-form').onsubmit="):source.index("$('direction-list').onclick=")]
        change = source[source.index("$('direction-list').onchange="):source.index("$('direction-list').ondragstart=")]
        result = subprocess.run(['node', '-e', "var nodes={};function $(id){return nodes[id]||(nodes[id]={focus(){this.focused=true},querySelectorAll(){return []}})};" + helpers + local_input + submit + change + r"""
const assert=require('node:assert/strict');var planDraft=[],planInsertKey=null,dirBusy=false,planVersion='old',dirBlocks=[{kind:'text',title:'Spoken text'}];
var st={broadcast_plan:{rows:[],version:'current'}},rendered=0,captured;
function directionView(){rendered++}function planSave(){captured=structuredClone(planDraft);return Promise.resolve(true)}
const slot={dataset:{planGap:'future-slot'},appendChild(el){this.child=el}};
const at='2026-10-24T08:15:22.000Z';
openPlanInsert({parentNode:slot,dataset:{planAdd:at},closest:()=>null});
assert.equal(slot.child,$('plan-add'));assert.equal($('plan-add').hidden,false);assert.equal($('direction-block').focused,true);
assert.equal($('direction-at').value,localInput(at,true));
$('direction-block').value='0';$('direction-text').value='Hello station';
$('direction-list').onchange({target:{type:'datetime-local',value:'2026-10-24T15:00',closest:()=>null}});
assert.equal(planDraft.length,0,'Changing insertion time must not edit an existing row');
(async()=>{
 $('direction-form').onsubmit({preventDefault(){}});await Promise.resolve();
 assert.equal(planVersion,'current');assert.equal(captured.length,1);assert.equal(captured[0].at,at);assert.equal(captured[0].text,'Hello station');
 assert.equal(planInsertKey,null);assert.equal($('plan-add').hidden,true);assert.equal($('direction-text').value,'');
})().catch(e=>{console.error(e);process.exitCode=1});
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_row_keeps_now_label_without_a_separate_editor_boundary(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        render = source[source.index('function row('):source.index('function timeline(')]
        subprocess.run(['node', '-e', render + r"""
const assert=require('node:assert/strict');var current=true,open={},KIND={music:'music'};
function isCur(){return current}function esc(s){return String(s)}function entryTitle(k,t){return t}
const track={id:1,kind:'music',title:'Track',time:'15:00'};
assert.ok(row(track).includes('<span class="tm">now</span>'));
current=false;assert.ok(row(track).includes('<span class="tm">15:00</span>'));
"""], check=True, capture_output=True, text=True)
        self.assertNotIn('id="plan-history"', source)
        self.assertNotIn('<summary>Add block</summary>', source)
        self.assertIn('id="broadcast-timeline"', source)
