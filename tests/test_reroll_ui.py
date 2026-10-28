import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class RerollUiTests(unittest.TestCase):
    def test_confirmation_cancel_refresh_and_changed_track(self):
        source = page_source()
        controls = source[source.index('function rerollButton('):source.index('function planReadiness(')]
        render = source[source.index('function backgroundError('):source.index("$('history-more').onclick")]
        events = source[source.index("$('upcoming').onclick="):source.index("$('music-source-select').onchange=")]
        harness = r'''
const assert=require('node:assert/strict');
var st={},rerollConfirm=null,rerollBusy=false,rerollError='',historyExpanded=false,planDraft=null,items=[],calls=[];
var document={documentElement:{dataset:{}},activeElement:null},nodes={};
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:''})}
function esc(s){return String(s)}function entryTitle(k,t){return t}function directionView(){}function row(){return ''}
function cmd(name,args){calls.push([name,args.ref]);return Promise.resolve({ok:true})}
function state(ref='one',announced=true){st={next_reroll:{ref,available:true},announced_tracks:announced?[ref]:[],upcoming:[{ref,kind:'music',title:ref,stage:'queued'}]};timeline()}
function click(action='',ref){$('upcoming').onclick({target:{closest:()=>({dataset:{reroll:action,ref}})}})}
(async function(){
state();click();assert.equal(calls.length,0);assert.equal(rerollConfirm,'one');
assert.match($('upcoming').innerHTML,/>Confirm<\/button>/);assert.match($('upcoming').innerHTML,/>Cancel<\/button>/);
timeline();assert.equal(rerollConfirm,'one','polling preserves confirmation');
click('cancel');assert.equal(rerollConfirm,null);assert.equal(calls.length,0);
click();let prevented=false;$('upcoming').onkeydown({key:'Escape',preventDefault(){prevented=true}});
assert(prevented);assert.equal(rerollConfirm,null);
click();state('two');assert.equal(rerollConfirm,null);click('confirm','one');assert.equal(calls.length,0,'stale confirm cannot replace another track');
click();st.next_reroll.available=false;timeline();assert.equal(rerollConfirm,null);
state();click();st.next_reroll.pending={title:'Replacement'};timeline();assert.equal(rerollConfirm,null);
state();click();click('confirm','one');assert.deepEqual(calls,[['reroll-next','one']]);
click('confirm','one');assert.equal(calls.length,1,'loading prevents duplicate requests');
await new Promise(resolve=>setImmediate(resolve));assert.equal(rerollBusy,false);
state('ordinary',false);click();assert.deepEqual(calls[1],['reroll-next','ordinary']);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', controls + render + harness.replace('(async function(){', events + '(async function(){')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
