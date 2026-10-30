"""Run the real early theme script against browser storage and control events."""
import json
from pathlib import Path
import re
import subprocess
import unittest
from tests.ui_page import WEB, page_source

PAGE = page_source()
SCRIPT = re.search(r'<script id="appearance-script">(.*?)</script>', PAGE, re.S).group(1)
CORE = (WEB / "core/core.js").read_text()  # loads first in <head>; the script uses its contrast


class AppearanceTests(unittest.TestCase):
    def test_preferences_preview_contrast_and_storage_failures(self):
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
function setup(saved=null,dark=false,blocked=false,session={}){
  const styles={},nodes={},radios={},listeners={};let value=saved,writes=0;
  const root={dataset:{},style:{setProperty(k,v){styles[k]=v}}};
  for(const kind of ['mode','palette'])radios[kind]=(kind==='mode'?['system','light','dark']:['ember','forest','ocean']).map(value=>({value,checked:false}));
  function node(id){return nodes[id]||(nodes[id]={value:'',disabled:false,textContent:'',style:{setProperty(){}},setAttribute(k,v){this[k]=v},getAttribute(k){return this[k]},addEventListener(k,f){this[k]=f},getAnimations(){return[]},querySelector(){return{focus(){}}}})}
  const media={matches:dark,addEventListener(k,f){listeners[k]=f}};
  const canvas={getContext:()=>({createImageData:(w,h)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData(){}}),toDataURL:()=>'data:image/png;map'};
  const c={timeline(){},directionView(){},player(){},document:{documentElement:root,getElementById:node,createElement:()=>canvas,querySelectorAll(q){return radios[q.includes('palette')?'palette':'mode']}},matchMedia(q){return q.includes('min-width')?{matches:true}:media},raf:[],requestAnimationFrame(f){c.raf.push(f)},
    sessionStorage:{getItem:k=>k in session?session[k]:null,setItem:(k,v)=>session[k]=v},
    localStorage:{getItem(){if(blocked)throw Error('blocked');return value},setItem(k,v){if(blocked)throw Error('blocked');value=v;writes++}}};
  vm.createContext(c);vm.runInContext(CORE+';this.RadioCore=RadioCore',c);vm.runInContext(SCRIPT,c);
  const beforeMount={...styles};c.appearance.mount();
  return {styles,nodes,beforeMount,root,session,frames(ms,n){let t=0;for(let i=0;i<n&&c.raf.length;i++){t+=ms;c.raf.shift()(t)}},formatTime:c.appearance.formatTime,select(kind,value){const n=radios[kind].find(n=>n.value===value);n.onchange()},
    input(value){node('accent-hex').value=value;node('accent-hex').oninput()},apply(){node('accent-form').onsubmit({preventDefault(){}})},
    system(dark){media.matches=dark;listeners.change()},get saved(){return value},get writes(){return writes}};
}
let clock=setup();assert.equal(clock.formatTime('00:05'),'00:05');
clock.nodes['appearance-time-format'].value='12';clock.nodes['appearance-time-format'].onchange();
for(const [input,expected] of [['00:05','12:05 AM'],['12:00','12:00 PM'],['13:30','01:30 PM'],['23:59','11:59 PM'],['—','—']])assert.equal(clock.formatTime(input),expected);
assert.equal(JSON.parse(clock.saved).timeFormat,'12');assert.equal(setup(clock.saved).formatTime('13:30'),'01:30 PM');
assert.equal(clock.root.dataset.timeFormat,'12');assert.equal(setup(JSON.stringify({timeFormat:'invalid'})).formatTime('13:30'),'13:30');
clock.nodes['appearance-reset'].onclick();assert.equal(clock.formatTime('13:30'),'13:30');
let effect=setup(JSON.stringify({timelineEffect:'off'}));assert.equal(effect.root.dataset.timelineEffect,'off');
assert.equal(effect.nodes['appearance-timeline-effect'].value,'off');
for(const value of ['fade','line','off']){effect.nodes['appearance-timeline-effect'].value=value;effect.nodes['appearance-timeline-effect'].onchange();assert.equal(effect.root.dataset.timelineEffect,value);assert.equal(JSON.parse(effect.saved).timelineEffect,value);assert.equal(setup(effect.saved).root.dataset.timelineEffect,value)}
assert.equal(setup(JSON.stringify({timelineEffect:'invalid'})).root.dataset.timelineEffect,'line');
let terminal=setup(JSON.stringify({presentation:'terminal'}));assert.equal(terminal.root.dataset.presentation,'terminal');
terminal.nodes['appearance-presentation'].value='calm';terminal.nodes['appearance-presentation'].onchange();assert.equal(terminal.root.dataset.presentation,'calm');assert.equal(JSON.parse(terminal.saved).presentation,'calm');
let t=setup(JSON.stringify({mode:'dark',palette:'ocean',accent:'#80bce7'}));
assert.equal(t.beforeMount['--bg'],'#111c27','saved theme must apply before controls mount');
assert.equal(t.styles['--accent'],'#80bce7');assert.equal(t.writes,0,'reading a page must not write a preference');
t=setup('corrupt JSON',true);assert.equal(t.root.dataset.theme,'dark');assert.equal(t.styles['--bg'],'#121210');
t.system(false);assert.equal(t.root.dataset.theme,'light');
t.select('mode','light');t.system(true);assert.equal(t.root.dataset.theme,'light','explicit mode ignores OS changes');
t.select('palette','forest');assert.equal(t.styles['--bg'],'#f1f3eb');assert.equal(JSON.parse(t.saved).palette,'forest');
const prior=t.saved;t.input('#ffffff');assert.equal(t.nodes['accent-apply'].disabled,true);assert.equal(t.nodes['accent-hex']['aria-invalid'],'true');
t.apply();assert.equal(t.saved,prior,'unreadable accent cannot be saved');
t.input('bad');assert.equal(t.nodes['accent-apply'].disabled,true);assert.match(t.nodes['accent-feedback'].textContent,/#RRGGBB/);
t.input('#154f84');assert.equal(t.styles['--accent'],'#154f84','valid colour previews immediately');assert.equal(t.saved,prior,'preview does not persist');
t.nodes['accent-cancel'].onclick();assert.equal(t.styles['--accent'],'#346347');
t.input('#154f84');t.apply();assert.equal(JSON.parse(t.saved).accent,'#154f84');
let reopened=setup(t.saved);assert.equal(reopened.styles['--accent'],'#154f84');
t.select('mode','dark');assert.equal(t.styles['--accent'],'#93cba5','incompatible saved accent falls back');
assert.match(t.nodes['appearance-status'].textContent,/needs more contrast/);
t.select('mode','light');assert.equal(t.styles['--accent'],'#154f84','custom accent survives fallback');
t.nodes['appearance-reset'].onclick();assert.deepEqual(JSON.parse(t.saved),{mode:'system',palette:'ember',accent:'',presentation:'calm',timelineEffect:'line',timeFormat:'24',crt:'on'});
t=setup(null,false,true);t.select('palette','ocean');assert.equal(t.styles['--bg'],'#edf3f6');assert.match(t.nodes['appearance-status'].textContent,/could not save/);
t=setup(JSON.stringify({mode:'invalid',palette:'__proto__',accent:'red'}));assert.equal(t.styles['--bg'],'#f2f2ee');
let crt=setup();assert.equal(crt.root.dataset.crt,'on','CRT screen: on by default');assert.equal(crt.nodes['appearance-crt'].checked,true);
assert.equal(crt.nodes['crt-map'].href,'data:image/png;map','the bend map is drawn once the screen is on');
for(const on of [false,true,false]){crt.nodes['appearance-crt'].checked=on;crt.nodes['appearance-crt'].onchange();const value=on?'on':'off';
  assert.equal(crt.root.dataset.crt,value);assert.equal(JSON.parse(crt.saved).crt,value);assert.equal(setup(crt.saved).root.dataset.crt,value,'saved with the other appearance settings')}
for(const old of ['subtle','classic','heavy','blinding','__proto__'])assert.equal(setup(JSON.stringify({crt:old})).root.dataset.crt,'on','the old presets and anything unknown: on');
let slow=setup();slow.frames(30,200);assert.equal(slow.root.dataset.crtBend,'off','slow frames with the bend: the bend goes');assert.equal(slow.session['yourwave.crt-bend'],'off');
assert.equal(slow.root.dataset.crt,'on','the rest of the screen stays');assert.equal(JSON.parse(slow.saved||'{}').crt===undefined||JSON.parse(slow.saved).crt==='on',true,'not in the saved setting');
let fast=setup();fast.frames(16,200);assert.equal(fast.root.dataset.crtBend,undefined,'fast frames: the bend stays');
let again=setup(null,false,false,{'yourwave.crt-bend':'off'});assert.equal(again.root.dataset.crtBend,'off','for the rest of the session');assert.equal(again.frames(16,1),undefined);
let off=setup(JSON.stringify({crt:'off'}));assert.equal(off.nodes['crt-map'].href,undefined,'Off never draws the map');
"""
        result = subprocess.run(['node', '-e', 'const SCRIPT=' + json.dumps(SCRIPT) + ',CORE=' + json.dumps(CORE) + ';\n' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_crt_switch_markup_and_where_each_layer_applies(self):
        self.assertIn('<label for="appearance-crt"><input type="checkbox" role="switch" id="appearance-crt" aria-label="CRT screen" aria-describedby="crt-hint" checked> CRT screen</label>', PAGE)
        self.assertIn('<p id="crt-hint" class="l">On phones the effect is reduced to light lines and a vignette.</p>', PAGE)
        self.assertRegex(PAGE, r'<svg[^>]*aria-hidden="true"[^>]*><filter id="crt"')
        self.assertIn('scale="35"', PAGE)
        css = (WEB / "themes/terminal/theme.css").read_text()
        crt = css[css.index("/* CRT screen"):]
        bend = "@media (min-width:701px) and (not (pointer:coarse)) and (not (prefers-contrast:more)){"
        self.assertEqual(crt.count("filter:url(#crt)"), 1)
        self.assertGreater(crt.index("filter:url(#crt)"), crt.index(bend), "phones (narrow or touch) never get the bend")
        self.assertIn('@media (max-width:700px),(pointer:coarse){:root[data-crt="on"]{--crt-lines:.08;--crt-vignette:.2;--crt-glow:.25}}', crt)
        # the other themes import this stylesheet and never set data-crt: every rule needs data-crt="on"
        for selectors, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", crt, flags=re.S)):
            depth, parts = 0, [""]
            for ch in selectors:                                   # top-level commas only, not those inside :is()
                depth += (ch == "(") - (ch == ")")
                parts[-1:] = [parts[-1], ""] if ch == "," and depth == 0 else [parts[-1] + ch]
            for sel in parts:
                self.assertRegex(sel.strip(), r'^:root(\[[^\]]*\])*\[data-crt="on"\]', sel.strip())
        layers = crt[crt.index("@media not (prefers-contrast:more){"):crt.index(bend)]
        self.assertIn("pointer-events:none", layers)
        self.assertIn("content:\"\"", layers)                                    # no text: nothing for a screen reader
        self.assertNotIn("animation", crt)
        self.assertNotIn("::after", crt)

    def test_presets_keep_text_and_status_contrast(self):
        # Check actual shipped text and accent values.
        palette_rows = re.findall(r"(?:light|dark):\[([^\]]+)\]", SCRIPT)
        self.assertEqual(len(palette_rows), 6)
        def luminance(color):
            values = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
            return sum(a * b for a, b in zip(linear, [.2126, .7152, .0722]))
        def contrast(a, b):
            lo, hi = sorted([luminance(a), luminance(b)])
            return (hi + .05) / (lo + .05)
        for row in palette_rows:
            bg, surface, ink, muted, line, accent, accent_ink = re.findall(r'#[0-9a-f]{6}', row)
            for fg in (ink, muted):
                for backdrop in (bg, surface):
                    self.assertGreaterEqual(contrast(fg, backdrop), 4.5, (fg, backdrop))
            self.assertGreaterEqual(contrast(accent, bg), 4.5)
            self.assertGreaterEqual(contrast(accent_ink, accent), 4.5)
