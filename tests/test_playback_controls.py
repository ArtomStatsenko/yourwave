import json
import subprocess
import unittest
from pathlib import Path
from tests.ui_page import core_source, page_source, theme_source


class PlaybackControlsTests(unittest.TestCase):
    def test_station_power_and_device_listening_are_independent(self):
        page = page_source()
        audio = page[page.index('var nextBusy=false;'):page.index('function fitTitle(')]
        player = page[page.index('function terminalState('):page.index('var keyFb=')]
        player = page[page.index('function backgroundError('):page.index('function hostHistory(')] + player
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
  document:{activeElement:null,documentElement:{dataset:{presentation:'calm'}}},localStorage:{getItem:()=>null,setItem(){}},requestAnimationFrame(){},
  setTitle:t=>title=t,media(){},f:String,musicSourceLabel:()=>'',musicSourceOptions:()=>'',
  st:{state:'STOPPED',output:'browser'},pls:[],ytStatus:{},musicChoice:'local',busy:false,
  LOCAL_CONTROL:true,titleIsTrack:false,cmd:(...args)=>{commands.push(args);return Promise.resolve()},
  rerollNext(){},Date,console};
vm.createContext(c);vm.runInContext(CORE+'\nvar core=RadioCore.create({localControl:true});\n'+AUDIO+'\n'+PLAYER+'\n'+HANDLERS,c);
c.player();assert.equal($('so')['aria-label'],'Start radio');assert.equal($('pp').disabled,true);
assert.equal($('station-controls').hidden,false);
c.listen();media.actions.play();$('pp').onclick();assert.equal(audioCount,0);assert.deepEqual(commands,[]);
$('so').onclick();assert.deepEqual(commands,[['play']]);assert.equal(audioCount,0);
commands=[];c.st.state='PLAYING';c.player();
assert.equal($('so')['aria-label'],'Stop radio');assert.equal($('pp').disabled,false);
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
        core = core_source()  # the theme's player renders RadioModel.player() from core.heard()
        result = subprocess.run(['node', '-e', 'const CORE=' + json.dumps(core) + ';\nconst AUDIO=' + json.dumps(audio) + ';\nconst PLAYER=' +
                                json.dumps(player) + ';\nconst HANDLERS=' + json.dumps(handlers) + ';\n' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reroll_button_needs_no_plugin_switch(self):
        page = page_source()
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
