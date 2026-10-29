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


PLAYHEAD = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const c={window:{},navigator:{}};vm.createContext(c);vm.runInContext(CORE+';this.RadioCore=RadioCore',c);
const step=c.RadioCore.playhead;
let seed=7;const rand=()=>(seed=seed*16807%2147483647)/2147483647;
const CLOCK=5000,TRUE_LAG=2.0,T0=1000;           // this clock runs 5 s ahead of the server's; the buffer is 2 s
// polls every 2 s with a network delay of 50..650 ms; the page redraws every 250 ms with a lag that wobbles ±0.4 s
function run(t0,seconds,status){
  let head=null,shown=[],truth=[],polls=[];
  for(let ms=0;ms<=seconds*1000;ms+=250){
    const server=t0*1000+ms;
    if(ms%2000===0)polls.push({received:server+CLOCK+50+rand()*600,s:status(server/1000)});
    const now=server+CLOCK,lag=TRUE_LAG+(rand()-.5)*.8;
    const ready=polls.filter(p=>p.received<=now-lag*1000);   // what this device hears: a poll older than its buffer
    if(!ready.length)continue;
    const p=ready[ready.length-1];
    head=step(head,p.s,{now,received:p.received,lag});
    shown.push(head.shown);truth.push(server/1000-T0-TRUE_LAG);
  }
  return {shown,truth};
}
const playing=t=>({state:'PLAYING',server_time:+t.toFixed(3),elapsed_sec:+(t-T0).toFixed(2),duration_sec:400});
let r=run(T0+30,60,playing);
for(let i=1;i<r.shown.length;i++)assert(r.shown[i]>=r.shown[i-1],'the shown time never goes back');
const worst=Math.max(...r.shown.map((v,i)=>Math.abs(v-r.truth[i])));
assert(worst<=.7,'within 0.7 s of what is heard: '+worst.toFixed(2));

// a new track: the position starts again near zero
let head=null;
head=step(head,playing(T0+100),{now:(T0+100)*1000+CLOCK+100,received:(T0+100)*1000+CLOCK+100,lag:0});
assert(Math.abs(head.shown-100)<.2);
const next=t=>({state:'PLAYING',server_time:t,elapsed_sec:+(t-(T0+101)).toFixed(2),duration_sec:200});
head=step(head,next(T0+103),{now:(T0+103)*1000+CLOCK+100,received:(T0+103)*1000+CLOCK+100,lag:0});
assert(Math.abs(head.shown-2)<.2,'new track: '+head.shown);
// paused: the time stands where it stopped; resumed 30 s later: it goes on from there
const paused={state:'PAUSED',server_time:T0+110,elapsed_sec:9,duration_sec:200};
head=step(head,paused,{now:(T0+110)*1000+CLOCK,received:(T0+110)*1000+CLOCK,lag:0});assert.equal(head.shown,9);
head=step(head,paused,{now:(T0+125)*1000+CLOCK,received:(T0+125)*1000+CLOCK,lag:0});assert.equal(head.shown,9);
const resumed={state:'PLAYING',server_time:T0+141,elapsed_sec:10,duration_sec:200};
head=step(head,resumed,{now:(T0+141)*1000+CLOCK+80,received:(T0+141)*1000+CLOCK+80,lag:0});
assert(Math.abs(head.shown-10)<.2,'resumed: '+head.shown);
// never past the end; an older server without its clock still shows a time
head=step(head,{state:'PLAYING',server_time:T0+400,elapsed_sec:230,duration_sec:200},{now:(T0+400)*1000+CLOCK,received:(T0+400)*1000+CLOCK,lag:0});
assert.equal(head.shown,200);
assert.equal(step(null,{state:'PLAYING',elapsed_sec:12,duration_sec:200},{now:3000,received:1000,lag:0}).shown,14);
"""


class PlayheadTests(unittest.TestCase):
    def test_the_shown_time_is_steady_through_noisy_polls_and_follows_tracks_and_pauses(self):
        result = subprocess.run(['node', '-e', 'const CORE=' + json.dumps(CORE) + ';' + PLAYHEAD], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
