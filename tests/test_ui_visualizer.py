import json
import subprocess
import unittest
from tests.ui_page import WEB, page_source


class VisualizerTests(unittest.TestCase):
    def test_modes_audio_decay_storage_resize_and_motion(self):
        source = page_source()
        start = source.index('var radioViz=')
        visualizer = source[start:source.index('\n}\nreturn {mount:mount};', start)]  # up to the end of the theme's mount()
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
function setup(saved='pulse',reduced=false){
  let frames=[],width=640,stored=saved,motion,layout,visible,enabled='true',subs=[];
  const nodes={viz:{parentNode:{get clientWidth(){return width}}},
    ruler:{getBoundingClientRect:()=>({width:200})},vzb:{setAttribute(k,v){this[k]=v}},vzprev:{},vznext:{}};
  const core={audio:{listening:false,heard:null,read(){return this.heard}},snapshot:{status:{state:'PLAYING'}},subscribe:f=>subs.push(f)};
  const c={Array,Math,String,core,document:{hidden:false,addEventListener:(k,fn)=>visible=fn},$:id=>nodes[id],
    localStorage:{getItem:k=>k==='vizEnabled'?enabled:stored,setItem:(k,v)=>{if(k==='vizEnabled')enabled=v;else stored=v}},
    matchMedia:()=>({get matches(){return reduced},addEventListener:(k,fn)=>motion=fn}),
    window:{addEventListener:(k,fn)=>layout=fn},requestAnimationFrame:fn=>frames.push(fn)};
  vm.createContext(c);vm.runInContext(CORE+';\n'+SOURCE,c);
  let time=1000;
  return {c,nodes,core,
    listen(on=true){core.audio.listening=on;subs.forEach(f=>f(core,'listening'))},
    frame(ms=1000/60){time+=ms;const f=frames.shift();if(f)f(time)},
    get pending(){return frames.length},get stored(){return stored},
    resize(w){width=w;layout()},motion(v){reduced=v;motion()},show(hidden){c.document.hidden=hidden;visible()}};
}
const sound=t=>({bands:Array.from({length:32},(_,i)=>.2+.7*i/31),wave:Array.from({length:t.c.radioViz.dw()},(_,i)=>.5+.4*Math.sin(i/7))});
const modes=['pulse','scope','bars','peaks','fall','vu','ribbon'];
for(const mode of modes){
  const t=setup(mode),idle=t.nodes.viz.textContent;assert.equal(t.pending,0,mode+': no loop before listening');
  t.listen();t.core.audio.heard=sound(t);for(let i=0;i<6;i++)t.frame();const active=t.nodes.viz.textContent;  // fall shows its first row after 1/15 s
  assert.notEqual(active,idle,mode+' must react to audio');
  assert.equal(active.split('\n').length,2);
  assert.ok(active.split('\n').every(row=>row.length===t.c.radioViz.dw()/2));
  assert.equal(t.pending,1,mode+': drawn on every frame');
  t.listen(false);t.frame();
  assert.equal(t.pending,0,mode+' must stop without audio');
  assert.equal(t.nodes.viz.className,'idle');
}
// rates are per second: 60 and 30 frames a second (both whole multiples of the 15 steps) show the same picture
for(const mode of ['pulse','peaks','fall','scope','ribbon']){
  const shots=[60,30].map(fps=>{const t=setup(mode);t.listen();t.core.audio.heard=sound(t);t.frame(0);  /* the first frame at the same moment */
    for(let i=0;i<fps*.5;i++)t.frame(1000/fps);
    t.core.audio.heard=null;for(let i=0;i<fps*1.1;i++)t.frame(1000/fps);
    return t.nodes.viz.textContent});
  // the same picture up to rounding on the grid: lit dots within 5%
  const dots=text=>[...text].reduce((n,ch)=>{let m=ch.charCodeAt(0)-0x2800;for(;m>0;m>>=1)n+=m&1;return n},0);
  const [a,b]=shots.map(dots);assert.ok(Math.abs(a-b)<=Math.max(2,.05*Math.max(a,b)),mode+' moves at the same speed at 60 and at 30 frames a second: '+a+' / '+b+' dots');
}
let t=setup();for(let i=1;i<=modes.length;i++){
  t.nodes.vznext.onclick();assert.equal(t.stored,modes[i%modes.length]);
  assert.equal(t.nodes.vzb.textContent,t.stored);
}
for(let i=1;i<=modes.length;i++){
  t.nodes.vzprev.onclick();assert.equal(t.stored,modes[(modes.length-i)%modes.length]);
  assert.equal(t.nodes.vzb.textContent,t.stored);
}
assert.equal(setup('unknown').nodes.vzb.textContent,'pulse');
t=setup('off');assert.equal(t.nodes.vzb.textContent,'pulse');assert.equal(t.nodes.vzb['aria-pressed'],'false');
t=setup('peaks');t.nodes.vzb.onclick();assert.equal(t.nodes.vzb['aria-pressed'],'false');
assert.equal(t.nodes.viz.textContent,'');t.listen();t.core.audio.heard=sound(t);assert.equal(t.pending,0,'switched off: no loop');
t.nodes.vznext.onclick();assert.equal(t.stored,'fall');assert.equal(t.nodes.viz.textContent,'');
t.nodes.vzprev.onclick();assert.equal(t.stored,'peaks');assert.equal(t.nodes.vzb['aria-pressed'],'false');
t.nodes.vzb.onclick();assert.equal(t.nodes.vzb['aria-pressed'],'true');assert.equal(t.pending,1,'switched on while listening: it starts');
t.frame();assert.notEqual(t.nodes.viz.textContent,'');
t=setup();t.resize(320);assert.equal(t.c.radioViz.dw(),64);t.resize(960);assert.equal(t.c.radioViz.dw(),192);
t=setup('peaks',true);t.listen();assert.equal(t.pending,0,'reduced motion: no loop');
t=setup();t.c.document.hidden=true;t.listen();assert.equal(t.pending,0,'hidden tab: no loop');t.show(false);assert.equal(t.pending,1,'shown again: it starts');
t=setup();t.core.snapshot.status.state='PAUSED';t.listen();assert.equal(t.pending,0,'paused: no loop');
t=setup();t.listen();t.core.audio.heard=sound(t);t.frame();t.motion(true);t.frame();assert.equal(t.pending,0);assert.equal(t.nodes.viz.className,'idle','a flat line');
"""
        core = (WEB / "core/core.js").read_text()
        result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(visualizer) + ',CORE=' + json.dumps(core) + ';\n' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
