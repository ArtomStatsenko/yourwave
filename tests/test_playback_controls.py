import json
import subprocess
import unittest
from pathlib import Path


class PlaybackControlsTests(unittest.TestCase):
    def test_station_power_and_device_listening_are_independent(self):
        page = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        audio = page[page.index('var nextBusy=false;'):page.index('function fitTitle(')]
        player = page[page.index('function player('):page.index('var keyFb=')]
        handlers = page[page.index("$('pp').onclick="):page.index('// 2-second refresh:')]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let commands=[],audioCount=0,title='';const nodes={};
function $(id){return nodes[id]||(nodes[id]={textContent:'',style:{},dataset:{},value:'1',
  setAttribute(k,v){this[k]=v},removeAttribute(){}})}
function Audio(){audioCount++;this.buffered={length:0};this.play=()=>Promise.resolve();
  this.pause=()=>{};this.load=()=>{};this.removeAttribute=()=>{this.src=''}}
function AudioContext(){const chain=()=>({connect(){}});this.destination={};
  this.createMediaElementSource=chain;
  this.createAnalyser=()=>({connect(){},frequencyBinCount:4,fftSize:8});
  this.createGain=()=>({connect(){},gain:{}});this.resume=()=>{}}
const media={actions:{},setActionHandler(name,fn){this.actions[name]=fn}};
const c={$ ,Audio,window:{AudioContext,addEventListener(){}},navigator:{mediaSession:media},
  MediaMetadata:function(value){return value},KIND:{},
  document:{activeElement:null},localStorage:{getItem:()=>null,setItem(){}},requestAnimationFrame(){},
  setTitle:t=>title=t,media(){},f:String,musicSourceLabel:()=>'',musicSourceOptions:()=>'',
  st:{state:'STOPPED',output:'browser'},pls:[],ytStatus:{},musicChoice:'local',busy:false,
  LOCAL_CONTROL:true,titleIsTrack:false,cmd:(...args)=>{commands.push(args);return Promise.resolve()},
  rerollNext(){},Date,console};
vm.createContext(c);vm.runInContext(AUDIO+'\n'+PLAYER+'\n'+HANDLERS,c);
c.player();assert.equal($('so').textContent,'Start radio');assert.equal($('pp').disabled,true);
assert.equal($('station-controls').hidden,false);
c.listen();media.actions.play();$('pp').onclick();assert.equal(audioCount,0);assert.deepEqual(commands,[]);
$('so').onclick();assert.deepEqual(commands,[['play']]);assert.equal(audioCount,0);
commands=[];c.st.state='PLAYING';c.player();
assert.equal($('so').textContent,'Stop radio');assert.equal($('pp').disabled,false);
$('pp').onclick();assert.equal(audioCount,1);assert.equal(c.listening,true);assert.deepEqual(commands,[]);
$('pp').onclick();assert.equal(c.listening,false);assert.deepEqual(commands,[]);
$('so').onclick();assert.deepEqual(commands,[['stop']]);
commands=[];c.LOCAL_CONTROL=false;c.player();assert.equal($('station-controls').hidden,true);
$('so').onclick();assert.deepEqual(commands,[]);
c.st.state='STOPPED';c.player();assert.equal(title,'Start on your Mac');
media.actions.play();$('pp').onclick();assert.deepEqual(commands,[]);assert.equal(c.listening,false);
c.st.state='STOPPING';c.player();assert.equal($('so').disabled,true);assert.equal($('pp').disabled,true);
assert.equal($('nx').disabled,true);assert.equal(title,'Stopping radio…');
c.LOCAL_CONTROL=true;$('so').onclick();assert.deepEqual(commands,[]);
c.st={state:'PLAYING',output:'speakers'};c.player();$('pp').onclick();assert.deepEqual(commands,[['pause']]);
commands=[];c.st.state='PAUSED';c.player();$('pp').onclick();assert.deepEqual(commands,[['resume']]);
commands=[];c.st.state='STOPPED';c.player();$('pp').onclick();assert.deepEqual(commands,[]);
c.st.state='PLAYING';c.busy=true;c.player();assert.equal($('so').disabled,true);assert.equal($('pp').disabled,true);
$('so').onclick();$('pp').onclick();assert.deepEqual(commands,[]);
c.st.output='browser';c.player();assert.equal($('pp').disabled,false);
$('pp').onclick();assert.equal(c.listening,true);$('pp').onclick();assert.equal(c.listening,false);
assert.deepEqual(commands,[],'device listening stays independent of pending station commands');
c.busy=false;c.st={state:'PAUSED',output:'browser',auto_paused:true};c.player();
assert.equal($('nx').disabled,true,'Next must not promise a skip while the radio is paused');
c.st.state='PLAYING';c.player();assert.equal($('nx').disabled,false,'Other listeners can keep the station playing');
"""
        result = subprocess.run(['node', '-e', 'const AUDIO=' + json.dumps(audio) + ';\nconst PLAYER=' +
                                json.dumps(player) + ';\nconst HANDLERS=' + json.dumps(handlers) + ';\n' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reroll_button_needs_no_plugin_switch(self):
        page = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        button = page[page.index('function rerollButton()'):page.index('function rerollNext(')]
        harness = r"""
const assert=require('node:assert/strict');
let st={},rerollBusy=false,rerollConfirm=null;const esc=s=>s;
""" + button + r"""
assert.equal(rerollButton(),'');
st.next_reroll={available:true};
assert.match(rerollButton(),/data-reroll/);
assert.doesNotMatch(rerollButton(),/ disabled/);
st.next_reroll.available=false;
assert.match(rerollButton(),/ disabled/);
st.next_reroll.available=true;rerollBusy=true;
assert.match(rerollButton(),/ disabled/);
"""
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
