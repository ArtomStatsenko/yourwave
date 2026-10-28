"""The page core (radio/web/core/core.js) without a browser: node with stand-ins for Audio and Web Audio."""
import json
import subprocess
import unittest
from pathlib import Path

CORE = (Path(__file__).resolve().parents[1] / 'radio/web/core/core.js').read_text()
SETUP = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let audios=[],stored={};const media={actions:{},setActionHandler(name,fn){this.actions[name]=fn}};
function Audio(){audios.push(this);this.buffered={length:0};this.currentTime=0;this.paused=false;this.readyState=4;
  this.srcs=0;this.play=()=>Promise.resolve();this.pause=()=>{this.paused=true};this.load=()=>{};
  this.removeAttribute=()=>{};Object.defineProperty(this,'src',{set:()=>this.srcs++})}
function AudioContext(){this.sampleRate=48000;this.destination={};this.resume=()=>{};
  this.createMediaElementSource=()=>({connect(){}});this.createGain=()=>({connect(){},gain:{value:1}});
  this.createAnalyser=()=>({connect(){},frequencyBinCount:1024,fftSize:2048,
    getByteFrequencyData:a=>a.fill(255),getByteTimeDomainData:a=>a.fill(128)})}
const c={Audio,window:{AudioContext},navigator:{mediaSession:media},MediaMetadata:function(v){return v},Uint8Array,Math,
  localStorage:{getItem:k=>k in stored?stored[k]:null,setItem:(k,v)=>stored[k]=String(v)}};
vm.createContext(c);vm.runInContext(CORE+';this.RadioCore=RadioCore',c);
const commands=[];
const core=c.RadioCore.create({fetch:(url,init)=>{commands.push(JSON.parse(init.body||'null'));return Promise.resolve({json:async()=>({ok:true})})}});
const events=[];core.subscribe((core,change)=>events.push(change));
const onAir=state=>{core.snapshot={status:{state,current_title:'Artist - Song',current_kind:'music'}}};
"""


def run(harness):
    return subprocess.run(['node', '-e', 'const CORE=' + json.dumps(CORE) + ';' + SETUP + harness],
                          capture_output=True, text=True)


class CoreAudioTests(unittest.TestCase):
    def test_next_keeps_the_stream_open_and_seeks_only_inside_buffered_audio(self):
        result = run(r"""
core.audio.catchUp();assert.equal(audios.length,0,'not listening: nothing to catch up');
onAir('PLAYING');core.audio.listen();const au=audios[0];assert.equal(au.srcs,1);
au.buffered={length:1,end:()=>3};au.currentTime=1;
core.audio.catchUp();assert.equal(au.currentTime,2.75);assert.equal(au.srcs,1,'the stream is not reopened');
core.audio.catchUp();assert.equal(au.currentTime,2.75,'already near the live edge');
au.buffered.length=0;au.currentTime=1;core.audio.catchUp();assert.equal(au.currentTime,1);
""")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_listening_volume_watchdog_spectrum_and_media_session(self):
        result = run(r"""
media.actions.play();assert.equal(audios.length,0,'the lock screen cannot start listening while off air');
onAir('PLAYING');media.actions.play();assert.equal(core.audio.listening,true);assert.deepEqual(events,['listening']);
core.audio.listen();assert.deepEqual(events,['listening'],'reconnecting is not a new listening event');
core.audio.setVolume(.4);assert.equal(stored.vol,'0.4');
const au=audios[0];au.currentTime=5;core.audio.watchdog();core.audio.watchdog();assert.equal(au.srcs,2);
core.audio.watchdog();assert.equal(au.srcs,3,'a stream stuck for two polls is reopened');
const heard=core.audio.read(10);assert.equal(heard.bands.length,32);assert.equal(heard.wave.length,10);
assert.ok(heard.bands.every(v=>v===1));
core.audio.media(core.snapshot.status);assert.equal(JSON.stringify(media.metadata),JSON.stringify({title:'Song',artist:'Artist',album:'Yourwave'}));
media.actions.nexttrack();assert.deepEqual(commands,[{cmd:'next'}]);
let routed=0;core.audio.onnext=()=>routed++;media.actions.nexttrack();assert.equal(routed,1);
media.actions.pause();assert.equal(core.audio.listening,false);assert.equal(core.audio.read(10),null);
assert.deepEqual(events,['listening','listening']);
""")
        self.assertEqual(result.returncode, 0, result.stderr)
