import subprocess
import unittest
from pathlib import Path


class TimelineHistoryTests(unittest.TestCase):
    def test_restarts_stay_in_activity_and_do_not_displace_aired_history(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        render = source[source.index('function timeline('):source.index("$('history-more').onclick")]
        result = subprocess.run(['node', '-e', render + r'''
const assert = require('node:assert/strict');
var st={},planDraft=null,rerollError='',rerollConfirm=null,historyExpanded=false;
var document={activeElement:null},nodes={};
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
