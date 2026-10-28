import json
import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class VisualizerTests(unittest.TestCase):
    def test_modes_audio_decay_storage_resize_and_motion(self):
        source = page_source()
        start = source.index('var radioViz=')
        visualizer = source[start:source.index('\n}\nreturn {mount:mount};', start)]  # up to the end of the theme's mount()
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
function setup(saved='pulse',reduced=false){
  let frames=[],timers=[],time=100,width=640,stored=saved,motion,layout,enabled='true';
  const nodes={viz:{parentNode:{get clientWidth(){return width}}},
    ruler:{getBoundingClientRect:()=>({width:200})},vzb:{setAttribute(k,v){this[k]=v}},vzprev:{},vznext:{}};
  const c={Array,Math,String,document:{hidden:false},$:id=>nodes[id],
    localStorage:{getItem:k=>k==='vizEnabled'?enabled:stored,setItem:(k,v)=>{if(k==='vizEnabled')enabled=v;else stored=v}},performance:{now:()=>time},
    matchMedia:()=>({matches:reduced,addEventListener:(k,fn)=>motion=fn}),
    window:{addEventListener:(k,fn)=>layout=fn},requestAnimationFrame:fn=>frames.push(fn),setTimeout:fn=>timers.push(fn)};
  vm.createContext(c);vm.runInContext(SOURCE,c);
  return {c,nodes,frame(){time+=50;const f=frames.shift();if(f)f(time);const t=timers.shift();if(t)t()},
    get pending(){return frames.length+timers.length},get stored(){return stored},
    resize(w){width=w;layout()},motion(v){motion({matches:v})}};
}
const modes=['pulse','scope','bars','peaks','fall','vu','ribbon'];
for(const mode of modes){
  const t=setup(mode),v=t.c.radioViz,idle=t.nodes.viz.textContent;
  v.set(Array.from({length:32},(_,i)=>.2+.7*i/31),Array.from({length:v.dw()},(_,i)=>.5+.4*Math.sin(i/7)));
  t.frame();const active=t.nodes.viz.textContent;
  assert.notEqual(active,idle,mode+' must react to audio');
  assert.equal(active.split('\n').length,2);
  assert.ok(active.split('\n').every(row=>row.length===v.dw()/2));
  for(let i=0;i<120&&t.pending;i++)t.frame();
  assert.equal(t.pending,0,mode+' must stop without audio');
  assert.equal(t.nodes.viz.className,'idle');
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
assert.equal(t.nodes.viz.textContent,'');t.c.radioViz.set(Array(32).fill(1),[]);assert.equal(t.pending,0);
t.nodes.vznext.onclick();assert.equal(t.stored,'fall');assert.equal(t.nodes.viz.textContent,'');
t.nodes.vzprev.onclick();assert.equal(t.stored,'peaks');assert.equal(t.nodes.vzb['aria-pressed'],'false');
t.nodes.vzb.onclick();assert.equal(t.nodes.vzb['aria-pressed'],'true');
t.c.radioViz.set(Array(32).fill(1),[]);t.frame();assert.notEqual(t.nodes.viz.textContent,'');
t=setup();t.resize(320);assert.equal(t.c.radioViz.dw(),64);t.resize(960);assert.equal(t.c.radioViz.dw(),192);
t=setup('peaks',true);t.c.radioViz.set(Array(32).fill(1),Array(128).fill(1));assert.equal(t.pending,0);
t=setup();t.c.document.hidden=true;t.c.radioViz.set(Array(32).fill(1),[]);assert.equal(t.pending,0);
t=setup();t.c.radioViz.set(Array(32).fill(1),[]);t.motion(true);t.frame();assert.equal(t.pending,0);
"""
        result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(visualizer) + ';\n' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
