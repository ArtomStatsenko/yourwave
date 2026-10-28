import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class TimelineHistoryTests(unittest.TestCase):
    def test_background_errors_live_in_activity_without_replacing_on_air_history(self):
        source=page_source()
        script=next(line for line in source.splitlines() if line.startswith('function esc('))+'\n'
        script+=source[source.index('function backgroundError('):source.index("$('history-more').onclick")]
        result=subprocess.run(['node','-e',script+r'''
const assert=require('node:assert/strict');
var appearance={formatTime:x=>x},document={activeElement:null,documentElement:{dataset:{timelineEffect:'off'}}},nodes={},planDraft=null,rerollError='',rerollConfirm=null,historyExpanded=false;
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:''})}
function row(x){return x.title}function entryTitle(k,t){return t}
var items=[{id:1,kind:'music',title:'Song'}],st={last_error:'render reddit:item#1: editor hourly request limit reached <retry>'};
const RealDate=Date;let clock=Date.now();Date=class extends RealDate{constructor(...args){super(...(args.length?args:[clock]))}};
timeline();assert.equal($('activity').hidden,false);
assert($('activity-log').innerHTML.includes('editor hourly request limit reached &lt;retry&gt;'));
assert(!$('activity-log').innerHTML.includes('Latest background error'));
assert($('activity-log').innerHTML.includes('class="tm"'));
const firstNotice=$('activity-log').innerHTML;clock+=5*60*1000;timeline();
assert.equal($('activity-log').innerHTML,firstNotice,'Polling does not change when the error was first observed');
assert.equal($('tl').innerHTML,'Song');
assert(backgroundError(st.last_error));
assert(backgroundError('VoiceStudio unavailable, will retry later'));
assert(!backgroundError('Audio output has been silent for 20 seconds'));
assert(!backgroundError('liquidsoap exited with code 1'));
assert(!backgroundError('the radio is not answering'));
st.last_error='';timeline();assert.equal($('activity').hidden,true);
assert.equal($('activity-log').innerHTML,'');
'''],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_announcements_are_part_of_the_same_host_appearance(self):
        source=page_source()
        script=source[source.index('function hostHistory('):source.index('function timeline(')]
        script+=source[source.index('function isCur('):source.index('function rk(')]
        result=subprocess.run(['node','-e',script+r'''
const assert=require('node:assert/strict');
const news={id:1,kind:'news',type:'PACKAGE_STARTED',parts:[{text:'News',links:[{url:'https://example.com'}]}]};
const teaser={id:2,kind:'host',type:'MUSIC_TEASER',parts:[{text:'Next song'}]};
var items=[teaser,news];function running(){return true}
let rows=hostHistory(items);
assert.equal(rows.length,1);assert.equal(rows[0].id,1,'Opening the edition remains stable when its outro airs');
assert.deepEqual(rows[0].parts.map(p=>p.text),['News','Next song']);
assert.equal(rows[0].parts[0].links[0].url,'https://example.com');
assert(isCur(rows[0]),'The whole edition stays current during the announcement');
assert.equal(news.parts.length,1,'Rendering must not mutate source history');
assert.deepEqual(hostHistory([teaser,{id:3,kind:'music',type:'MUSIC_STARTED'},news]).map(e=>e.id),[2,3,1],
 'A song boundary must never be folded into a host appearance');
assert.equal(hostHistory([teaser]).length,1,'An orphan announcement remains inspectable');
assert.equal(hostHistory([teaser,{id:3,kind:'session',type:'SESSION_STARTED'},news]).length,3);
const musicStory={id:3,kind:'host',type:'PACKAGE_STARTED',parts:[{text:'Music story'}]};
const weather={id:4,kind:'news',type:'PACKAGE_STARTED',parts:[{text:'Weather'}]};
rows=hostHistory([teaser,news,musicStory,weather]);
assert.equal(rows.length,1,'Weather, music stories, news and announcements share one host appearance');
assert.equal(rows[0].kind,'news');
assert.deepEqual(rows[0].parts.map(p=>p.text),['Weather','Music story','News','Next song']);
assert.deepEqual(rows[0].event_ids,[4,3,1,2]);
'''],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_only_new_broadcast_rows_animate(self):
        source = page_source()
        render = source[source.index('function backgroundError('):source.index("$('history-more').onclick")]
        result = subprocess.run(['node', '-e', render + r"""
const assert=require('node:assert/strict');
var appearance={formatTime:value=>value};
var st={},planDraft=null,rerollError='',rerollConfirm=null,historyExpanded=false,items=[];
var document={documentElement:{dataset:{}},activeElement:null,hidden:false},nodes={},motion=false,animations=[],writes=0;
function matchMedia(){return {matches:motion}}
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:''})}
function row(x){return String(x.id)+','}function entryTitle(k,t){return t}function esc(s){return String(s)}
var html='',children=[];
Object.defineProperties($('tl'),{
 innerHTML:{get:()=>html,set(value){html=value;writes++;children=value.split(',').filter(x=>/^\d+$/.test(x)).map((id,i)=>({
  style:{removeProperty(){}},dataset:{historyId:id},getBoundingClientRect:()=>({top:i*40,height:40}),
  firstElementChild:{animate(frames,options){animations.push({id,frames,options});return {}}}
 }))}},children:{get:()=>children}});
function song(id){return {id,kind:'music',title:'Song '+id}}
items=[song(2),song(1)];timeline();assert.equal(animations.length,0,'Initial load stays still');
items.unshift(song(3));timeline();
assert.deepEqual(animations.map(x=>x.id),['3','2','1']);
assert.equal(animations[0].frames[0].transform,'translateY(40px)');
assert.equal(animations[0].frames[0].opacity,0);
assert.equal(animations[1].frames[0].transform,'translateY(-40px)');
assert.equal(animations[0].options.duration,240);
const count=writes;animations=[];timeline();assert.equal(writes,count,'Polling preserves the current animation');
assert.equal(animations.length,0);
historyExpanded=true;timeline();assert.equal(animations.length,0,'Show earlier does not animate');
motion=true;items.unshift(song(4));timeline();assert.equal(animations.length,0);
motion=false;document.hidden=true;items.unshift(song(5));timeline();assert.equal(animations.length,0);
document.hidden=false;$('tl-panel').hidden=true;items.unshift(song(6));timeline();assert.equal(animations.length,0);
$('tl-panel').hidden=false;planDraft=[];items.unshift(song(7));timeline();assert.equal(animations.length,0);
planDraft=null;timeline();assert.equal(animations.length,0,'Returning from the editor does not replay motion');
items.unshift({id:8,kind:'session'});timeline();assert.equal(animations.length,0,'Activity is not a printed broadcast');
document.documentElement.dataset.timelineEffect='off';items.unshift(song(9));timeline();assert.equal(animations.length,0,'Off displays rows immediately');
document.documentElement.dataset.timelineEffect='fade';items.unshift(song(10));timeline();assert.equal(animations.length,1,'Fade affects only the new row');
assert.deepEqual(animations[0].frames,[{opacity:0},{opacity:1}]);
"""], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_restarts_stay_in_activity_and_do_not_displace_aired_history(self):
        source = page_source()
        render = source[source.index('function backgroundError('):source.index("$('history-more').onclick")]
        result = subprocess.run(['node', '-e', render + r'''
const assert = require('node:assert/strict');
var appearance={formatTime:value=>value};
var st={},planDraft=null,rerollError='',rerollConfirm=null,historyExpanded=false;
var document={documentElement:{dataset:{}},activeElement:null},nodes={};
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:''})}
function row(x){return x.title+'\n'}function entryTitle(k,t){return t}function esc(s){return String(s)}
const KIND={music:'Music'};
const songs=Array.from({length:16},(_,i)=>({id:i,kind:'music',title:'Song '+i}));
const restarts=Array.from({length:4},(_,i)=>({id:10+i,kind:'session',time:'15:20',title:'broadcast started'}));
var items=[songs[0],...restarts,...songs.slice(1)];
timeline();
assert.equal($('tl').innerHTML,songs.slice(0,15).map(row).join(''));
assert.equal($('history-more').hidden,false);
assert.equal($('activity').hidden,false);
assert.equal(($('activity-log').innerHTML.match(/15:20/g)||[]).length,4);
assert.equal(items.length,20,'Rendering must retain the technical events');
historyExpanded=true;timeline();
assert.equal($('tl').innerHTML,songs.map(row).join(''));
assert.equal($('history-more').textContent,'Show less');
items=[...restarts,...songs.slice(0,15)];historyExpanded=false;timeline();
assert.equal($('history-more').hidden,true,'Only aired rows count towards Show earlier');
items=restarts;timeline();
assert.ok($('tl').innerHTML.includes('nothing has aired yet'));
assert.equal($('history-more').hidden,true);
assert.equal($('activity').hidden,false);
items=[];timeline();
assert.equal($('activity').hidden,true);
assert.equal($('activity-log').innerHTML,'');
// Normal listening shows only Next; the editor shows the future from bottom to top.
st={upcoming:[{kind:'music',stage:'queued',ref:'manual',title:'Manual'},
 {kind:'music',stage:'music',ref:'first',title:'First'}],
 direction_timezone:'UTC',upcoming_tracks:[{ref:'first',title:'First',set:true,at:'2026-10-25T22:10:00Z'},
 {ref:'later',title:'Later',at:'2026-10-25T22:13:00Z'},{ref:'last',title:'Last',at:null}]};
timeline();let future=$('upcoming').innerHTML;
assert.equal((future.match(/Manual/g)||[]).length,2,'Next title appears in visible text and title attribute');
assert(future.includes('>next</span>'));
assert(!future.includes('First'));assert(!future.includes('Later'));assert(!future.includes('Last'));
st.announced_tracks=['manual','later'];timeline();future=$('upcoming').innerHTML;
assert.equal((future.match(/announced-track/g)||[]).length,1);
assert(future.includes('Manual<span class="l announced-track">'));
st.next_reroll={pending:{title:'Loading replacement'}};timeline();future=$('upcoming').innerHTML;
assert(!future.includes('Loading replacement<span class="l announced-track">'));
assert.equal((future.match(/announced-track/g)||[]).length,0,'Pending replacement has not been announced');
delete st.next_reroll;
// A reroll replaces the next library song; later songs keep their order.
st.upcoming[0]={kind:'music',stage:'queued',ref:'replacement',title:'Replacement'};
st.upcoming_tracks[0]={ref:'replacement',title:'Replacement'};
timeline();future=$('upcoming').innerHTML;
assert(future.includes('Replacement'));assert(!future.includes('Later'));
assert.equal((future.match(/Replacement/g)||[]).length,2,'Next must not be repeated in the future rows');
assert(!future.includes('Replacement<span class="l announced-track">'));
planDraft=null;timeline();future=$('upcoming').innerHTML;
assert(future.includes('Replacement'));assert(!future.includes('Later'));
st={upcoming:[],upcoming_tracks:[{ref:'only',title:'Only future'}]};timeline();
assert.equal($('upcoming').hidden,true);
planDraft=[];timeline();assert.equal($('upcoming').hidden,true);
st={upcoming:[],upcoming_tracks:[]};timeline();
assert.equal($('upcoming').hidden,true);assert.equal($('upcoming').innerHTML,'');
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
