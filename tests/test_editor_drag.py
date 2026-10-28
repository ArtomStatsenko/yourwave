import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class EditorDragTests(unittest.TestCase):
    def run_js(self, checks):
        source = page_source()
        helpers = source[source.index('function planSnap('):source.index("$('plan-edit').onclick=")]
        setup = r'''
const assert = require('node:assert/strict');
const now = Date.parse('2026-10-25T11:00:00Z'); Date.now = () => now;
const iso = minutes => new Date(now + minutes * 60000).toISOString();
var st = {direction_timezone:'Asia/Ho_Chi_Minh',broadcast_plan:{starts_at:iso(0)}},
    planDraft=[],dirBusy=false,planPointer=null,planDrag=null,planHours={},planOpen={},
    planExpandTimer=null,planFineTimer=null,planSuppressClick=false;
var nodes={},timers=new Map(),timerID=0;
function $(id){return nodes[id]||(nodes[id]={textContent:'',querySelectorAll:()=>[],
    getBoundingClientRect:()=>({left:0,right:100,top:0,bottom:100})})}
function setTimeout(fn,ms){timers.set(++timerID,{fn,ms});return timerID;}
function clearTimeout(id){timers.delete(id);}
var saves=0; function planSave(){saves++;}
var renders=[]; function directionView(force){renders.push(force);}
'''
        result = subprocess.run(['node', '-e', setup + helpers + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_station_clock_snap_and_exact_save_do_not_reorder_other_blocks(self):
        self.run_js(r'''
assert.equal(planSnap(now+14*60000,30),now);
assert.equal(planSnap(now+16*60000,30),now+30*60000);
assert.equal(planSnap(now+16*60000+31e3,1),now+17*60000);
st.direction_timezone='Asia/Kathmandu';
const before=Date.parse('2026-10-25T12:11:00Z'); // 17:56 in Kathmandu
assert.equal(new Intl.DateTimeFormat('en-GB',{timeZone:st.direction_timezone,hour:'2-digit',minute:'2-digit'}).format(new Date(planSnap(before,30))),'18:00');
st.direction_timezone='Asia/Ho_Chi_Minh';
planDraft=[{id:'news',kind:'news',edition:true,at:iso(30),segments:[{title:'Weather'}]},
           {id:'song',kind:'music',at:iso(45),locked:true},
           {id:'later',kind:'news',at:iso(60)}];
planSetTime(0,now+47*60000);
assert.equal(saves,1);
assert.equal(planDraft.find(r=>r.id==='news').at,iso(47));
assert.equal(planDraft.find(r=>r.id==='news').estimated,false);
assert.equal(planDraft.find(r=>r.id==='news').dirty,true);
assert.equal(planDraft.find(r=>r.id==='news').segments[0].title,'Weather');
assert.equal(planDraft.find(r=>r.id==='song').at,iso(45));
assert.equal(planDraft.find(r=>r.id==='later').at,iso(60));
''')

    def test_drop_validation_preserves_locked_blocks_and_reminder_deadlines(self):
        self.run_js(r'''
planDraft=[{id:'news',kind:'news',at:iso(30)},
           {id:'selected',kind:'news',at:iso(60),locked:true},
           {id:'reminder',kind:'reminder',at:iso(70),ends:iso(90)}];
for(const at of [now-1,now+61*60000,NaN]){
  planSetTime(0,at);
  assert.equal(saves,0);
  assert.equal(planDraft[0].at,iso(30));
}
assert.match(planDragError(1,now+80*60000),/cannot be moved/);
assert.match(planDragError(2,now+90*60000),/event is over/);
assert.equal(planDragError(2,now+85*60000),'');
planSetTime(0,now+30*60000);assert.equal(saves,0,'Unchanged time must not replace Undo');
''')

    def test_hover_opens_collapsed_hour_and_edition_and_cancels_stale_hover(self):
        self.run_js(r'''
var previews=[];planDragPreview=(x,y)=>previews.push([x,y]);planPaintDrag=()=>{};
const hour={open:false,dataset:{hour:'later'}};
const summary={closest(selector){return selector==='summary'?this:selector==='[data-hour]'?hour:null;}};
planPointer={index:0,x:30,y:40,at:now+30*60000,raw:now+31*60000};
planQueueDragHover(summary,'hour:later');
timers.get(planExpandTimer).fn();
assert.equal(hour.open,true);assert.equal(planHours.later,true);
assert.deepEqual(previews,[[30,40]]);
timers.get(planFineTimer).fn();assert.equal(planPointer.fine.base,now+31*60000);
planDraft=[{id:'source',kind:'news',at:iso(10)},
           {id:'target',kind:'news',at:iso(40),edition:true}];
const row={dataset:{planIndex:'1'},closest(selector){return selector==='[data-plan-index]'?this:null;}};
planPointer={index:0,x:30,y:40,at:now+40*60000,raw:now+40*60000};
planQueueDragHover(row,'row:target');
const stale=timers.get(planExpandTimer).fn;
planPointer.hoverKey='row:elsewhere';stale();
assert.equal(planOpen.target,undefined,'Leaving a block cancels its pending expansion');
planPointer.hoverKey=null;planQueueDragHover(row,'row:target');
timers.get(planExpandTimer).fn();
assert.equal(planOpen.target,true);assert.equal(renders.at(-1),true);
planStopPointer();assert.equal(planDrag,null);assert.equal(planPointer,null);assert.equal(timers.size,0);
''')

    def test_dropping_over_history_cancels_without_saving(self):
        self.run_js(r'''
planDraft=[{id:'news',kind:'news',at:iso(30)}];
planDrag=0;planPointer={index:0,moved:true,x:50,y:50,at:now+60*60000,fine:{base:now+60*60000,y:50}};
planDragPreview(50,150); // below the future program, in history
assert.equal(planPointer.at,null);
planPointerUp({clientX:50,clientY:150,preventDefault(){}});
assert.equal(saves,0);assert.equal(planDraft[0].at,iso(30));assert.equal(planDrag,null);
''')
