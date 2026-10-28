import re
import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class TitleAnimationTests(unittest.TestCase):
    def test_typing_preserves_prefix_and_follows_transition_duration(self):
        source = page_source()
        frame = source[source.index('function titleFrame('):source.index('var tfx=')]
        subprocess.run(['node', '-e', frame + r"""
const assert = require('node:assert/strict');
for (const duration of [100, 3000]) {
  const delay = duration / 2;
  for (let ms = 0; ms < delay; ms += 5) {
    assert.deepEqual(titleFrame('Carbonara - Yesterday', 'Carbonara - Tomorrow', ms, duration),
                     {text:'Carbonara - Yesterday',caret:false,done:false});
  }
  for (let ms = 0; ms < duration; ms += 5) {
    const f = titleFrame('Carbonara - Yesterday', 'Carbonara - Tomorrow', delay + ms, duration);
    assert.ok(f.text.startsWith('Carbonara - '));
    assert.equal(f.done, false);
  }
  assert.deepEqual(titleFrame('Carbonara - Yesterday', 'Carbonara - Tomorrow', delay + duration, duration),
                   {text:'Carbonara - Tomorrow',caret:false,done:true});
}
assert.equal(titleFrame('ab', 'ac', 0, 0).text, 'ac');
assert.equal(titleFrame('ab', 'ac', 4000, NaN).done, true);
assert.equal(titleFrame('😀 old', '😀 new', 50, 100).text.startsWith('😀 '), true);
assert.equal(titleFrame('Long title', 'Long', 150, 100).text, 'Long');
"""], check=True, capture_output=True, text=True)

    def test_next_keeps_stream_open_and_seeks_only_inside_buffered_audio(self):
        source = page_source()
        command = source[source.index('function cmd('):source.index('function replay(')]
        subprocess.run(['node', '-e', command + r"""
const assert = require('node:assert/strict');
let busy=false,nextBusy=false,catchUps=0,result={ok:true};
const catchUp=()=>catchUps++;  // the core keeps the stream open and seeks only inside buffered audio
const api=()=>Promise.resolve(result),refresh=()=>{},player=()=>{},setTimeout=()=>{},$=()=>({textContent:''});
(async()=>{
  const switching=cmd('next');assert.equal(nextBusy,true);
  await switching;assert.equal(nextBusy,false);
  assert.equal(catchUps,1);
  result={ok:false};await cmd('next');assert.equal(catchUps,1);
  result={ok:true};await cmd('reroll-next');assert.equal(catchUps,1);
  await cmd('next');assert.equal(catchUps,2);
  assert.equal(busy,false);
})().catch(e=>{console.error(e);process.exitCode=1});
"""], check=True, capture_output=True, text=True)

    def test_music_source_label_distinguishes_configuration_from_playback(self):
        source = page_source()
        helper = source[source.index('function musicSourceLabel('):source.index('function player(')]
        subprocess.run(['node', '-e', helper + r"""
const assert = require('node:assert/strict');
assert.equal(musicSourceLabel({state:'STOPPED'},true,{connected:true},'local'), 'Local music');
assert.ok(!musicSourceOptions(false).includes('ytmusic'));
assert.ok(musicSourceOptions(true).includes('ytmusic'));
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'local'},false,{connected:true}), 'Local music · YouTube Music off');
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'ytmusic'},true,{}), 'YouTube Music');
assert.equal(musicSourceLabel({state:'PLAYING',music_source:'local',ytmusic_active:true},true,{}), 'Local music · YouTube Music fallback');
assert.equal(musicSourceLabel({state:'STOPPED'},true,{connected:true},'ytmusic'), 'YouTube Music · configured');
assert.equal(musicSourceLabel({state:'PLAYING',music_source_state:{current:'local',target:'ytmusic',phase:'loading'}},true,{}), 'Local music · loading YouTube Music');
assert.equal(musicSourceLabel({state:'PLAYING',music_source_state:{current:'local',target:'ytmusic',phase:'waiting'}},true,{}), 'Local music → YouTube Music · after this track');
assert.equal(musicSourceLabel({state:'PLAYING',music_source_state:{current:'ytmusic',from:'local',phase:'crossfade'}},true,{}), 'Local music → YouTube Music · crossfading');
assert.equal(musicSourceLabel({state:'PLAYING',music_source_state:{current:null,target:'local',phase:'waiting'}},false,{}), 'Host on air → Local music · after the host');

"""], check=True, capture_output=True, text=True)

    def test_editor_hour_groups_and_autosave_rollback(self):
        source = page_source()
        code = "var appearance={formatTime:value=>value};\n" + re.search(r'function planDragClock\(at\)\{[^\n]+', source).group(0) + '\n' + source[source.index('function planGroups('):source.index('function planMove(')]
        result = subprocess.run(['node', '-e', code + r"""
const assert=require('node:assert/strict');
const at=Date.parse('2026-10-24T06:00:00Z');Date.now=()=>at;
var planDraft=Array.from({length:10},(_,i)=>({id:'clock:'+i,kind:'news',title:'Host edition',edition:true,segments:[{title:'Weather',items:[]}],at:new Date(at+i*1800e3).toISOString()}));
var pls=[{name:'air_direction',on:true}],st={broadcast_plan:{rows:structuredClone(planDraft),version:'v1'},direction_timezone:'UTC'},
 dirLoaded=true,dirBusy=false,planDrag=null,planTimeId=null,planConfirm=null,planOpen={},planHours={},planHorizon=3,planUndo=null,planInsertKey=null,planVersion='v1';
planDraft.splice(1,0,{id:'music-between',kind:'music',at:new Date(at+900e3).toISOString(),duration:300});
st.broadcast_plan.rows=structuredClone(planDraft);
var document={activeElement:null,body:{classList:{toggle(){}}}};
var nodes={};function $(id){return nodes[id]||(nodes[id]={innerHTML:'',hidden:false,classList:{toggle(){}},contains:()=>false,querySelectorAll:()=>[]})}
var esc=s=>String(s),planReadiness=()=>'',localInput=s=>s;
var requests=[],response;function api(path,body){requests.push(body);return Promise.resolve(response)}
(async()=>{
 const groups=planGroups(planDraft);assert.equal(groups.length,3);assert.equal(groups[0].entries[0].row.id,'clock:5');
 planDraft[0].title='Host edition · Weather';planDraft[0].expected_at=new Date(at+120e3).toISOString();
 st.broadcast_plan.rows[0].expected_at=planDraft[0].expected_at;
 directionView();var html=$('direction-list').innerHTML;planDraft[0].title='Host edition';delete planDraft[0].expected_at;delete st.broadcast_plan.rows[0].expected_at;
 assert(html.includes('Host edition<span class="l plan-expected"> · expected ~06:02</span></button>'));
 assert(!html.includes('class="l plan-detail"'),'An edition with expected time uses no detail row');
 planDraft.push({id:'reminder:test',kind:'reminder',title:'Reminder',content_title:'Standup',estimated:false,
   at:new Date(at).toISOString(),expected_at:new Date(at+120e3).toISOString()});
 st.broadcast_plan.rows=structuredClone(planDraft);directionView();
 const reminder=$('direction-list').innerHTML.split('data-plan-key="reminder:test"')[1].split('class="plan-inline"')[0];
 assert(reminder.includes('>06:00</button>'));assert(!reminder.includes('>~06:00'));
 assert(reminder.includes('Standup · expected ~06:02'));
 planDraft.pop();st.broadcast_plan.rows=structuredClone(planDraft);directionView();
 assert.ok(!html.includes('data-plan-key="music-between"'),'Music filler is hidden from the program');
 assert.ok(html.includes('data-plan-index="2" data-plan-key="clock:1"'),'Visible rows retain their original edit indices');
 assert.equal((html.match(/ open>/g)||[]).length,0);assert.ok(html.indexOf('clock:5')<html.indexOf('clock:0'));
 assert.ok(html.indexOf('data-plan-add=')<html.indexOf('<details'),'Collapsed hours must still expose their insertion gaps');
 assert.ok(!html.includes('Weather'),'Collapsed editions hide their categories and title suffix');
 assert.ok(html.includes('data-plan-action="segments"'));
 planOpen['clock:0']=true;directionView();
 assert.ok($('direction-list').innerHTML.includes('<span>Weather</span>'),'The expanded edition keeps its composition');
 planOpen={};directionView();
 assert.ok(!html.includes('This hour'),'Current-hour entries stay directly above now without a heading');
 assert.equal((html.match(/class="plan-hour"/g)||[]).length,2,'Only later hours are collapsible');
 assert.ok(html.lastIndexOf('</details>')<html.indexOf('data-plan-key="clock:1"'),'Current-hour entries remain outside the later groups');
 planHours[groups[0].key]=true;directionView();assert.equal(($('direction-list').innerHTML.match(/ open>/g)||[]).length,1);
 planHours={};directionView();assert.equal(($('direction-list').innerHTML.match(/ open>/g)||[]).length,0);
 // Expanding an hour compensates the scroll without changing the current-hour rows.
 const folded={open:false,dataset:{hour:groups[0].key},querySelector(){return summary}};
 const summary={getBoundingClientRect:()=>({top:folded.open?200:80})};
 $('direction-list').querySelectorAll=selector=>selector==='details[data-hour]'?[folded]:[];
 $('broadcast-timeline').scrollTop=40;directionView();
 let prevented=false;summary.onclick.call(summary,{preventDefault(){prevented=true}});
 assert(prevented);assert.equal(folded.open,true);assert.equal(planHours[folded.dataset.hour],true);
 assert.equal($('broadcast-timeline').scrollTop,160);
 summary.onclick.call(summary,{preventDefault(){}});
 assert.equal(folded.open,false);assert.equal($('broadcast-timeline').scrollTop,40);
 $('direction-list').querySelectorAll=()=>[];
 st.upcoming_tracks=[{ref:'first',title:'First song',at:new Date(at+600e3).toISOString(),set:true},
 {ref:'later',title:'Later song',at:new Date(at+2400e3).toISOString()},
 {ref:'future',title:'Future song',at:new Date(at+4*3600e3).toISOString()},
 {ref:'unknown',title:'Unknown time',at:null}];st.announced_tracks=['first'];
 const before=JSON.stringify(planDraft);directionView();html=$('direction-list').innerHTML;
 assert.equal(JSON.stringify(planDraft),before,'Queue entries must not become editable draft rows');
 const mixed=planGroups(planDraft),hour=mixed.find(g=>g.current);
 assert.deepEqual(hour.entries.map(e=>e.row.title),['Later song','Host edition','First song','Host edition']);
 assert.equal(hour.entries.find(e=>e.row.title==='First song').index,null);
 assert(html.includes('announced · set'));assert(!html.includes('Time pending'));assert(html.includes('Unknown time'));
 assert(!html.includes('Future song'));assert.equal($('plan-later').hidden,false);
 assert(html.includes('data-plan-at="'+new Date(at+2400e3).toISOString()+'"'));
 assert(html.includes('data-plan-index="2" data-plan-key="clock:1"'),'Music entries must preserve host edit indices');
 planHorizon=6;directionView();assert($('direction-list').innerHTML.includes('Future song'));
 st.upcoming_tracks=[];st.announced_tracks=[];planHorizon=3;
 st.state='PAUSED';st.paused_at=at/1000;Date.now=()=>at+8*3600e3;
 directionView();assert.equal($('plan-pause').hidden,false);
 assert(planGroups(planDraft).some(g=>g.current));
 assert($('direction-list').innerHTML.includes('clock:0'),'Paused hour groups stay anchored to the pause');
 let anchorPass=0,anchorHour={open:false,dataset:{hour:'new-hour'}};
 const anchorRow={dataset:{planKey:'track:0'},getClientRects:()=>[{}],getBoundingClientRect:()=>({top:anchorPass===1?20:35,bottom:50}),closest:()=>anchorHour};
 $('broadcast-timeline').scrollTop=100;$('broadcast-timeline').getBoundingClientRect=()=>({top:0});
 $('direction-list').querySelectorAll=selector=>selector==='[data-plan-key]'?(anchorPass++,[anchorRow]):[];
 st.state='PLAYING';Date.now=()=>at;directionView();assert.equal($('plan-pause').hidden,true);
 assert.equal($('broadcast-timeline').scrollTop,115,'Resume retains the visible entry when its hour changes');
 assert.equal(anchorHour.open,true);
 $('direction-list').querySelectorAll=()=>[];
 planDraft[0].dirty=true;planDraft[0].skipped=true;response={ok:false,error:'Changed elsewhere'};
 await planSave();assert.equal(requests[0].remember_undo,true);assert.equal(planDraft[0].skipped,undefined);assert.equal(dirBusy,false);assert.equal($('direction-message').textContent,'Changed elsewhere');
 planDraft[0].dirty=true;response={ok:true,broadcast_plan:st.broadcast_plan,undo_token:'undo-one'};
 await planSave();assert.equal(planUndo,'undo-one');assert.equal(planDraft[0].dirty,undefined);
 planDraft=[{id:'music-only',kind:'music',at:new Date(at+60000).toISOString(),duration:60},{id:'later-music',kind:'music',at:new Date(at+5*3600e3).toISOString(),duration:60}];
 st.broadcast_plan.rows=structuredClone(planDraft);directionView();
 assert.ok(!$('direction-list').innerHTML.includes('class="it music'));
 assert.ok(!$('direction-list').innerHTML.includes('This hour'));assert.ok(!$('direction-list').innerHTML.includes('0 blocks'));
 assert.ok($('direction-list').innerHTML.includes('data-plan-add='),'Insertion remains available in a music-only program');
 assert.equal($('plan-later').hidden,true,'Hidden music must not create empty future pages');
})().catch(e=>{console.error(e);process.exitCode=1});
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_history_rejects_drop_and_arrows_follow_upward_time(self):
        source = page_source()
        handlers = source[source.index("$('tl').ondragover="):source.index('function cmd(')]
        subprocess.run(['node', '-e', "var nodes={};" + handlers + r"""
const assert=require('node:assert/strict');var planDrag=3;function $(id){return nodes[id]||(nodes[id]={})}
var prevented=0,e={preventDefault(){prevented++},dataTransfer:{}};
$('tl').ondragover(e);assert.equal(e.dataTransfer.dropEffect,'none');$('tl').ondrop(e);assert.equal(planDrag,null);assert.equal(prevented,2);
"""], check=True, capture_output=True, text=True)
        self.assertIn("button.dataset.planAction==='up'?1:-1", source)
        self.assertIn('aired.slice(0,15)', source)

    def test_gap_times_stay_between_future_neighbors(self):
        source = page_source()
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
        source = page_source()
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
        source = page_source()
        render = source[source.index('function sourceLabel('):source.index('function timeline(')]
        subprocess.run(['node', '-e', render + r"""
const assert=require('node:assert/strict');var appearance={formatTime:value=>value};var current=true,st={state:'PLAYING'},open={},KIND={music:'music'};
function isCur(){return current}function esc(s){return String(s)}function entryTitle(k,t){return t}
const track={id:1,kind:'music',title:'Track',time:'15:00'};
assert.ok(row(track).includes('<span class="tm">now</span>'));
st.state='PAUSED';assert.ok(row(track).includes('cur paused'));
current=false;assert.ok(row(track).includes('<span class="tm">15:00</span>'));
assert(!row(track).includes('cur paused'));
open['2']=true;
const url='https://example.com/a-long-article?ref=radio';
const news={id:2,kind:'news',title:'Edition',time:'15:00',parts:[{links:[{url,title:'A clear article title'}]}]};
let html=row(news);
assert(html.includes('>A clear article title</a>'));
assert(html.includes('href="'+url+'" title="'+url+'"'));
news.parts[0].links[0].title='example.com/a-long-article?ref=radio';
assert(row(news).includes('>Source · example.com</a>'));
delete news.parts[0].links[0].title;
assert(row(news).includes('>Source · example.com</a>'));
news.parts[0].links=[{url:'https://www.reddit.com/r/BestGitHubRepos/comments/abc/mlxserve_native_llm/',title:'Reddit: bestgithubrepos · 2026-10-27'}];
assert(row(news).includes('>Reddit · mlxserve native llm</a>'));
"""], check=True, capture_output=True, text=True)
        self.assertNotIn('id="plan-history"', source)
        self.assertNotIn('<summary>Add block</summary>', source)
        self.assertIn('id="broadcast-timeline"', source)
