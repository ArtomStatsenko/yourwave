"""The prism theme: the colour split only where the design allows it, its flags, and the split moving with the sound."""
import json
import re
import subprocess
import unittest

from tests.ui_page import WEB, theme_html

PRISM = WEB / "themes/prism"
SPLIT = re.compile(r"(?:text|box)-shadow:[^;}]*var\(--ab\)")


def split_selectors():
    css = re.sub(r"/\*.*?\*/", "", (PRISM / "theme.css").read_text(), flags=re.S)
    return sorted(sel for sels, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css) if SPLIT.search(body) for sel in [sels.strip()])


class PrismLookTests(unittest.TestCase):
    def test_the_split_sits_only_on_the_title_and_on_shapes(self):
        # the title; the on-air dot, the main button, the progress fill, the open tab's line, a switch knob that is on;
        # the icon buttons and action buttons only while held
        self.assertEqual(split_selectors(), sorted([
            ':root[data-skin="prism"] #air',
            ':root[data-skin="prism"] .title',
            ':root[data-skin="prism"] .bar i',
            ':root[data-skin="prism"] #pp',
            ':root[data-skin="prism"] :is(#nx,#so,.ab):active',
            ':root[data-skin="prism"] .tabs button[aria-selected="true"]::after',
            ':root[data-skin="prism"] :is(.pl[aria-pressed="true"] .sw,input[role=switch]:checked)::after',
        ]))

    def test_long_titles_step_down_and_lose_the_split_below_28px(self):
        css = (PRISM / "theme.css").read_text()
        self.assertIn(':root[data-skin="prism"] .title{font:800 clamp(30px,3.6vw,38px)/1.05', css)
        # both smaller steps stay under 28px at any width, so neither carries the split
        self.assertIn(':root[data-skin="prism"] .title[data-size="m"]{font-size:clamp(22px,2.4vw,27px);text-shadow:none}', css)
        self.assertIn(':root[data-skin="prism"] .title[data-size="s"]{font-size:clamp(20px,2.4vw,26px);text-shadow:none}', css)
        for step in re.findall(r'\.title\[data-size="\w"\]\{font-size:clamp\((\d+)px,[\d.]+vw,(\d+)px\)', css):
            self.assertLess(int(step[1]), 28)
        self.assertIn("-webkit-line-clamp:3", css)

    def test_dark_only_no_mode_or_accent_rows_and_the_split_properties_registered(self):
        html = theme_html("prism")
        self.assertIn('<html lang="en" data-theme="dark" data-skin="prism">', html)
        self.assertIn("typing:false", html)
        self.assertNotIn("appearance-mode-row", html)
        self.assertNotIn("appearance-accent-row", html)
        css = (PRISM / "theme.css").read_text()
        for prop in ('@property --ab{syntax:"<length>"', '@property --c1{syntax:"<color>"', '@property --c2{syntax:"<color>"'):
            self.assertIn(prop, css)
        self.assertIn("@media (prefers-reduced-motion:reduce){", css)
        core = (WEB / "core/core.js").read_text()
        harness = core + r"""
const assert=require('node:assert/strict');
const core=RadioCore.create({theme:'prism',location:{search:'',hash:''}});core.capabilities={themes:['prism','terminal']};
assert.equal(JSON.stringify(core.themes()[0]),JSON.stringify({name:'prism',title:'Prism',current:true,modes:[],accent:false}));
"""
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


MOTION = r"""
const assert=require('node:assert/strict');
let frames=[],reduced=false,observers=[],time=1000,state='PLAYING',reachable=true;
global.requestAnimationFrame=f=>{frames.push(f);return frames.length};
global.matchMedia=()=>({get matches(){return reduced},addEventListener(){}});
global.document={hidden:false,documentElement:{classList:{on:false,toggle(k,v){this.on=v}}},addEventListener(){}};
global.localStorage={getItem:()=>null,setItem(){}};
global.MutationObserver=function(f){this.observe=()=>observers.push(f)};
var RadioTheme={mount(){}},RadioBars={wrap:t=>t};
CORE
VIEW
const el=()=>({textContent:'',classList:{add(){},remove(){}},addEventListener(){},offsetWidth:0,dataset:{},style:{}});
const style={},bodyStyle={},moved=el=>Object.assign(el,{style:{setProperty:(k,v)=>style[k]=v,removeProperty:k=>delete style[k]}});
const parts={'#np':moved(el()),'.tabbar':moved(el())};  // the player and the tab bar share one record: both get the same width
const root={style:{setProperty:(k,v)=>bodyStyle[k]=v,removeProperty(){}},querySelector:q=>parts[q]||el(),addEventListener(){}};
let heard=null;
const core=RadioCore.create({fetch:()=>reachable?Promise.resolve({json:()=>Promise.resolve({status:{state}})}):Promise.reject(Error('the page cannot reach the radio'))});
core.audio.read=()=>heard;core.audio.watchdog=()=>{};  // the stand-in sound
const tick=(ms=1000/60)=>{time+=ms;const f=frames.shift();if(f)f(time)};
(async()=>{
assert.equal(RadioTheme.step('Maria También'),'');assert.equal(RadioTheme.step('x'.repeat(30)),'');assert.equal(RadioTheme.step('x'.repeat(31)),'m');
assert.equal(RadioTheme.step('Twenty One Pilots - At The Risk Of Feeling Dumb'),'m','47 characters');assert.equal(RadioTheme.step('x'.repeat(48)),'m');assert.equal(RadioTheme.step('x'.repeat(49)),'s');
assert.equal(RadioTheme.width(0),'1.50px');assert.equal(RadioTheme.width(1),'4.30px');
assert.equal(RadioTheme.low([1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]),1,'the low quarter of the bands');
RadioTheme.mount(core,root);tick();assert.equal(frames.length,0,'no sound: it rests');
core.audio.listening=true;await core.refresh();assert.equal(frames.length,1,'listening and the station plays: it wakes');
heard={bands:Array(32).fill(1)};tick();assert.equal(style['--ab'],'2.20px','smoothed: a quarter of the way to loud');
tick();tick();assert.ok(parseFloat(style['--ab'])>2.2&&parseFloat(style['--ab'])<4.3,'and rising');assert.equal(frames.length,1,'one loop');
state='PAUSED';await core.refresh();tick();assert.equal(style['--ab'],undefined,'paused: back to the still split');assert.equal(frames.length,0);
state='PLAYING';await core.refresh();assert.equal(frames.length,1,'playing again: it wakes');
heard={bands:Array(32).fill(0)};for(let i=0;i<60&&frames.length;i++)tick();assert.equal(frames.length,0,'silence: it settles and rests');assert.equal(style['--ab'],undefined);
heard={bands:Array(32).fill(1)};core.frames.wake();tick();
reachable=false;await core.refresh();tick();assert.equal(style['--ab'],undefined,'an error: the still red split');assert.equal(frames.length,0);
assert.equal(document.documentElement.classList.on,true);
reachable=true;await core.refresh();assert.equal(document.documentElement.classList.on,false);assert.equal(frames.length,1);
reduced=true;tick();assert.equal(frames.length,0,'reduced motion: nothing moves');
reduced=false;core.frames.wake();tick();assert.equal(frames.length,1);
core.audio.listening=false;tick();assert.equal(frames.length,0,'stops with the sound');
assert.deepEqual(bodyStyle,{},'never on the root: that restyles the whole page');
})().catch(e=>{console.error(e);process.exit(1)});
"""


class PrismMotionTests(unittest.TestCase):
    def test_the_split_follows_the_sound_and_stops_with_it(self):
        result = subprocess.run(["node", "-e", MOTION.replace("CORE", (WEB / "core/core.js").read_text()).replace("VIEW", (PRISM / "view.js").read_text())], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
