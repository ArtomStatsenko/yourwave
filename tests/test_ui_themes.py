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
        used = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", behavior)) - {"llm-key", "voice-fb"}  # drawn by the script
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
assert.equal(JSON.stringify(core.themes()),JSON.stringify([{name:'lime',title:'Lime',current:true}]),'before the capabilities: this theme');
core.capabilities={themes:['lime','soft','synthwave','terminal','new-look']};
assert.equal(JSON.stringify(core.themes().map(t=>t.title)),JSON.stringify(['Lime','Soft','Synthwave','Terminal','New-look']));
assert.equal(core.themes().filter(t=>t.current).length,1);
assert.equal(core.themeNote,'Switching reloads the page and stops listening.');
core.setTheme('synthwave');
assert.equal(stored['yourwave.theme'],'synthwave','remembered in this browser');
assert.deepEqual(went.pop(),['assign','/?theme=synthwave']);
"""


class CoreThemeTests(unittest.TestCase):
    def test_themes_are_listed_remembered_and_followed_once(self):
        result = subprocess.run(["node", "-e", "const CORE=" + json.dumps(CORE) + ";" + HARNESS], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
