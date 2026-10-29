"""Every theme: the theme picker in Settings → Appearance, and the core that remembers the choice."""
import json
import re
import subprocess
import unittest

from tests.ui_page import WEB

THEMES = sorted(p.parent.name for p in (WEB / "themes").glob("*/index.html"))


class ThemePickerTests(unittest.TestCase):
    def test_every_theme_shows_the_picker_first_in_appearance(self):
        self.assertIn("terminal", THEMES)
        for name in THEMES:
            with self.subTest(theme=name):
                html = (WEB / "themes" / name / "index.html").read_text()
                appearance = html[html.index('id="appearance"'):]
                first = re.search(r"<(?:label|select|input)\b[^>]*>", appearance[appearance.index("</h2>"):]).group(0)
                self.assertEqual(first, '<label for="theme-select">')       # Theme is the first row
                self.assertIn('<select id="theme-select" aria-label="Theme" aria-describedby="theme-note"></select>', html)
                self.assertIn('id="theme-note"', html)
                head = html[:html.index("</head>")]
                self.assertIn(f"RadioCore.followSavedTheme('{name}')", head)  # before anything is drawn
                self.assertIn(f'theme:"{name}"', html)


class CardThemeTests(unittest.TestCase):
    """Themes built on the terminal behavior (lime, synthwave, soft): its elements, the shared bars, one look."""
    def test_card_themes_have_every_element_the_behavior_uses_once(self):
        from collections import Counter
        behavior = (WEB / "themes/terminal/view.js").read_text()
        used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", behavior)) - {"llm-key", "voice-fb", "appearance-mode-row", "appearance-accent-row"}  # drawn by the script; rows only some themes have
        cards = [n for n in THEMES if n != "terminal"]
        self.assertTrue(cards)
        for name in cards:
            with self.subTest(theme=name):
                html = (WEB / "themes" / name / "index.html").read_text()
                ids = Counter(re.findall(r'id="([^"]+)"', html))
                self.assertEqual(sorted(used - set(ids)), [])
                self.assertEqual([k for k, n in ids.items() if n > 1], [])
                scripts = re.findall(r'<script src="([^"]+)"></script>', html)
                self.assertEqual(scripts, ["/ui/core/core.js", "/ui/core/model.js", "/ui/themes/terminal/view.js",
                                           "/ui/themes/shared/bars.js", f"/ui/themes/{name}/view.js"])
                self.assertIn('id="spectrum-bars"', html)
                self.assertIn(f'data-skin="{name}"', html)
                self.assertIn('@import url("/ui/themes/shared/cards.css");', (WEB / "themes" / name / "theme.css").read_text())


CORE = (WEB / "core/core.js").read_text()
HARNESS = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let stored={};const went=[];
const loc=(search,hash='')=>({search,hash,replace:u=>went.push(['replace',u]),assign:u=>went.push(['assign',u])});
const c={localStorage:{getItem:k=>k in stored?stored[k]:null,setItem:(k,v)=>stored[k]=String(v)},window:{},navigator:{}};
vm.createContext(c);vm.runInContext(CORE+';this.RadioCore=RadioCore',c);
const R=c.RadioCore;
assert.equal(R.followSavedTheme('lime',loc('')),false,'nothing saved: stay');
stored['yourwave.theme']='soft';
assert.equal(R.followSavedTheme('soft',loc('')),false,'already the saved one');
assert.equal(R.followSavedTheme('lime',loc('?theme=lime')),false,'an explicit ?theme= wins');
assert.equal(R.followSavedTheme('lime',loc('','#st')),true);
assert.deepEqual(went.pop(),['replace','/?theme=soft#st'],'once, to the saved theme');
const here=loc('');
const core=R.create({theme:'lime',location:here});
assert.equal(JSON.stringify(core.themes()),JSON.stringify([{name:'lime',title:'Lime',current:true,modes:[],accent:false}]),'before the capabilities: this theme');
core.capabilities={themes:['lime','soft','synthwave','terminal','new-look']};
assert.equal(JSON.stringify(core.themes().map(t=>t.title)),JSON.stringify(['Lime','Soft','Synthwave','Terminal','New-look']));
assert.equal(core.themes().filter(t=>t.current).length,1);
const flags=Object.fromEntries(core.themes().map(t=>[t.name,JSON.stringify([t.modes,t.accent])]));
assert.equal(flags.soft,JSON.stringify([['light','dark','system'],true]));
assert.equal(flags.lime,JSON.stringify([[],false]));assert.equal(flags.synthwave,flags.lime);assert.equal(flags['new-look'],flags.lime);
assert.equal(core.themeNote,'Switching reloads the page and stops listening.');
core.setTheme('synthwave');
assert.equal(stored['yourwave.theme'],'synthwave','remembered in this browser');
assert.deepEqual(went.pop(),['assign','/?theme=synthwave']);
"""


class CoreThemeTests(unittest.TestCase):
    def test_every_soft_accent_reads_at_4_5_to_1_in_both_modes(self):
        harness = CORE + r"""
const assert=require('node:assert/strict');
for(const accent of ['#5668f0','#ef5a6f','#139a92','#d98410','#9459ee','#2f9e55','#ffff00','#000080']){
  for(const [ground,dark] of [['#e8ecf4',false],['#23262e',true]]){
    const text=RadioCore.textAccent(accent,ground,dark);
    assert.ok(RadioCore.contrast(text,ground)>=4.5,accent+' on '+ground+': '+text);
    if(RadioCore.contrast(accent,ground)>=4.5)assert.equal(text,accent,'kept when it already reads')}
  const ink=RadioCore.onFill(accent),other=ink==='#ffffff'?'#111111':'#ffffff';
  assert.ok(RadioCore.contrast(ink,accent)>=RadioCore.contrast(other,accent),'the label colour that reads better on '+accent)}
assert.equal(RadioCore.onFill('#5668f0'),'#ffffff');
assert.ok(RadioCore.contrast(RadioCore.onFill('#9459ee'),'#9459ee')<4.5,'violet: a mid-tone fill (its labels are bold)');
"""
        result = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_mode_and_accent_rows_follow_the_theme_flags(self):
        behavior = (WEB / "themes/terminal/view.js").read_text()
        picker = behavior[behavior.index("function themePicker(){"):behavior.index("core.subscribe(function(core,change){if(change==='capabilities')themePicker()});")]
        for name in ("soft", "lime", "synthwave"):
            with self.subTest(theme=name):
                html = (WEB / "themes" / name / "index.html").read_text()
                rows = re.findall(r'id="(appearance-(?:mode|accent)-row)" hidden', html)
                harness = CORE + r"""
const assert=require('node:assert/strict');
const nodes={'theme-select':{},'theme-note':{}};ROWS.forEach(id=>nodes[id]={hidden:true});
const $=id=>nodes[id],esc=s=>s;
const core=RadioCore.create({theme:NAME,location:{search:'',hash:''}});
""" + picker + r"""
themePicker();
ROWS.forEach(id=>assert.equal(nodes[id].hidden,false,id+' shown'));
"""
                result = subprocess.run(["node", "-e", f"const ROWS={json.dumps(rows)},NAME={json.dumps(name)};" + harness], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(rows), 2 if name == "soft" else 0)   # lime and synthwave: no rows at all


    def test_themes_are_listed_remembered_and_followed_once(self):
        result = subprocess.run(["node", "-e", "const CORE=" + json.dumps(CORE) + ";" + HARNESS], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
