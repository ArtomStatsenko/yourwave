"""The lime theme: the terminal theme's behavior in its own markup and look, plus a 20-bar spectrum strip."""
import json
import re
import subprocess
import unittest
from collections import Counter

from tests.ui_page import WEB, theme_source

LIME = WEB / "themes/lime"


class LimeMarkupTests(unittest.TestCase):
    def test_every_element_the_shared_behavior_uses_is_there_once(self):
        behavior = theme_source("terminal")
        used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", behavior))
        html = (LIME / "index.html").read_text()
        ids = Counter(re.findall(r'id="([^"]+)"', html))
        made_by_script = {"llm-key", "voice-fb"}  # drawn with the AI and Voice blocks
        self.assertEqual(sorted(used - set(ids) - made_by_script), [])
        self.assertEqual([k for k, n in ids.items() if n > 1], [])
        self.assertEqual(len(re.findall(r"<i></i>", html[html.index('id="spectrum-bars"'):html.index("</div>", html.index('id="spectrum-bars"'))])), 20)

    def test_dark_only_fixed_look_and_load_order(self):
        html = (LIME / "index.html").read_text()
        self.assertIn('<html lang="en" data-theme="dark" data-skin="lime">', html)
        self.assertIn('<meta name="color-scheme" content="dark">', html)
        self.assertIn("typing:false", html)                 # no typed titles
        self.assertNotIn('appearance-palette', html)        # no palette, accent or time-format choices
        self.assertNotIn('appearance-time-format', html)
        scripts = re.findall(r'<script src="([^"]+)"></script>', html)
        self.assertEqual(scripts, ["/ui/core/core.js", "/ui/core/model.js", "/ui/themes/terminal/view.js", "/ui/themes/shared/bars.js", "/ui/themes/lime/view.js"])
        self.assertLess(html.index("/ui/core/core.js"), html.index("</head>"))  # the saved theme is followed before drawing
        self.assertIn("RadioTheme.mount(RadioCore.create(", html)
        css = (LIME / "theme.css").read_text()
        self.assertIn('@import url("/ui/themes/shared/cards.css");', css)
        self.assertIn('@import url("/ui/themes/terminal/theme.css");', (WEB / "themes/shared/cards.css").read_text())
        self.assertIn("color-scheme:dark", css)


SPECTRUM = r"""
const assert=require('node:assert/strict');
let frames=[],reduced=false,listeners=[],motionListener=null;
global.requestAnimationFrame=f=>{frames.push(f);return frames.length};
global.matchMedia=()=>({get matches(){return reduced},addEventListener:(k,f)=>motionListener=f});
var RadioTheme={mount(){}};  // the terminal behavior: not needed for the strip
LIME
const bars=Array.from({length:20},()=>({style:{}}));
const root={querySelectorAll:()=>bars};
let heard=null;
const core={audio:{listening:false,read:()=>heard},subscribe:f=>listeners.push(f)};
RadioTheme.mount(core,root);
const tick=()=>{const f=frames.shift();if(f)f()};
const heights=()=>bars.map(b=>b.style.height||'0').join(',');
assert.deepEqual(RadioTheme.bars(Array(32).fill(0).map((_,i)=>i/31)).length,20);
const one=Array(32).fill(0);one[0]=1;one[1]=.3;assert.deepEqual(RadioTheme.bars(one).slice(0,2),[1,.3],'each bar is the loudest of its bands');
core.audio.listening=true;listeners.forEach(f=>f(core,'listening'));assert.equal(frames.length,1);
heard={bands:Array(32).fill(.5)};tick();assert.equal(heights(),Array(20).fill('50%').join(','));assert.equal(frames.length,1,'the loop runs while listening');
heard=null;tick();assert.equal(heights(),Array(20).fill('0').join(','),'no sound: a flat line');
core.audio.listening=false;listeners.forEach(f=>f(core,'listening'));tick();assert.equal(frames.length,0,'the loop stops');
reduced=true;core.audio.listening=true;listeners.forEach(f=>f(core,'listening'));assert.equal(frames.length,0,'no animation with reduced motion');
reduced=false;motionListener();assert.equal(frames.length,1,'it starts when motion is allowed again');
"""


class LimeSpectrumTests(unittest.TestCase):
    def test_bars_follow_the_sound_and_stop_with_it(self):
        result = subprocess.run(["node", "-e", SPECTRUM.replace("LIME", (WEB / "themes/shared/bars.js").read_text() + (LIME / "view.js").read_text())],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_theme_can_turn_off_title_typing(self):
        behavior = theme_source("terminal")
        title = behavior[behavior.index("var tfx="):behavior.index("window.addEventListener('resize'")]
        harness = r"""
const assert=require('node:assert/strict');
const nodes={ttl:{textContent:''},caret:{hidden:true},'ttl-sr':{}},box={title:'',classList:{remove(){},add(){added=true}},style:{setProperty(){}}};
let added=false,frames=0;
const document={querySelector:()=>box,hidden:false},$=id=>nodes[id],matchMedia=()=>({matches:false});
const requestAnimationFrame=()=>++frames,cancelAnimationFrame=()=>{},performance={now:()=>0},fitTitle=()=>{};
var appearance={typing:false};
""" + title + r"""
setTitle('First',true,1000);setTitle('Second',false,1000);
assert.equal(nodes.ttl.textContent,'Second','the new title appears at once');assert.equal(frames,0);assert.equal(added,false);
"""
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
