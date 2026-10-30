"""The core's frames: one animation frame for everything that moves, asleep when nothing does."""
import json
import subprocess
import unittest

from tests.ui_page import WEB

HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let frames=[],visible=null,motion=null,state='PLAYING',now=1000;
const c={console,Promise,JSON,Math,Date,Array,Object,String,Error,
  document:{hidden:false,addEventListener:(k,f)=>visible=f},
  matchMedia:()=>({matches:false,addEventListener:(k,f)=>motion=f}),
  localStorage:{getItem:()=>null,setItem(){}},
  requestAnimationFrame:f=>{frames.push(f);return frames.length},
  fetch:()=>Promise.resolve({json:()=>Promise.resolve({status:{state}})})};
vm.createContext(c);vm.runInContext(CORE+';this.RadioCore=RadioCore',c);
const core=c.RadioCore.create({});core.audio.watchdog=()=>{};  // no audio element behind the stand-in listening
const tick=(ms=1000/60)=>{now+=ms;const f=frames.shift();if(f)f(now)};
(async()=>{
  assert.equal(frames.length,0,'no one moves: no frames');
  let a=[],b=[],aMoves=true,bMoves=true;
  const offA=core.frames.add((dt,t)=>{a.push(dt);return aMoves});
  core.frames.add((dt,t)=>{b.push(dt);return bMoves});
  assert.equal(frames.length,1,'two movers share one frame');
  tick();tick(20);assert.equal(a[0],0,'dt: none on the first frame');assert.ok(Math.abs(a[1]-.02)<1e-9,'then the seconds since the last');assert.equal(b.length,2);
  aMoves=false;tick();assert.equal(frames.length,1,'one still moves: the loop goes on');
  bMoves=false;tick();assert.equal(frames.length,0,'all at rest: the loop sleeps');
  tick();assert.equal(a.length,4,'asleep: nobody is called');
  bMoves=true;core.audio.listening=true;state='PAUSED';await core.refresh();
  assert.equal(frames.length,0,'a paused station does not wake it');
  state='PLAYING';await core.refresh();assert.equal(frames.length,1,'the station plays while this device listens: it wakes');
  tick();assert.deepEqual(a.slice(-1),[0],'a fresh start: dt 0 again');
  bMoves=false;tick();assert.equal(frames.length,0);
  core.audio.listening=false;await core.refresh();assert.equal(frames.length,0,'not listening: a poll does not wake it');
  c.document.hidden=true;core.frames.wake();assert.equal(frames.length,0,'a hidden tab: no frames');
  c.document.hidden=false;visible();assert.equal(frames.length,1,'the tab shows again: it wakes');
  tick();assert.equal(frames.length,0);motion();assert.equal(frames.length,1,'motion settings change: it wakes (the movers decide)');
  tick();offA();const n=a.length;bMoves=true;core.frames.wake();tick();assert.equal(a.length,n,'a removed mover is not called');assert.equal(b.length>0,true);
})().catch(e=>{console.error(e);process.exit(1)});
"""


class FramesTests(unittest.TestCase):
    def test_one_frame_for_all_and_asleep_when_nothing_moves(self):
        result = subprocess.run(["node", "-e", "const CORE=" + json.dumps((WEB / "core/core.js").read_text()) + ";" + HARNESS],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
