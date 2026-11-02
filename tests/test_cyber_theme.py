"""The Cyber theme: three accents with readable text, no text under 16px, and a glitch on events only."""
import json
import re
import subprocess
import unittest

from tests.ui_page import WEB

CSS = (WEB / 'themes/cyber/theme.css').read_text()


def node(script):
    return subprocess.run(['node', '-e', script], capture_output=True, text=True)


class CyberThemeTests(unittest.TestCase):
    def test_the_core_lists_it_dark_only_with_accents(self):
        result = node((WEB / 'core/core.js').read_text() + r'''
const t=RadioCore.create({theme:'cyber'}).themes().find(x=>x.name==='cyber');
console.log(JSON.stringify([t.title,t.modes,t.accent]))''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['Cyber', [], True])

    def test_accent_text_reads_on_its_panel_in_every_preset(self):
        def tokens(selector):
            block = re.search(re.escape(selector) + r'\{([^}]*)\}', CSS)[1]
            return dict(re.findall(r'--([a-z-]+):(#[0-9a-f]{6})', block))
        base = tokens(':root[data-skin="cyber"]')
        presets = {'yellow': base, 'cyan': {**base, **tokens(':root[data-skin="cyber"][data-accent="cyan"]')},
                   'red': {**base, **tokens(':root[data-skin="cyber"][data-accent="red"]')}}
        self.assertEqual({k: v['accent'] for k, v in presets.items()}, {'yellow': '#fcee09', 'cyan': '#19d9ff', 'red': '#ff2a4d'})
        pairs = [[k, v['accent'], v['panel'], v['alt'], v['bad-ink'], v['bad-ground'], v['muted']] for k, v in presets.items()]
        result = node((WEB / 'core/core.js').read_text() + r'''
const c=RadioCore.contrast;
for(const [name,accent,panel,alt,badInk,badGround,muted] of ''' + json.dumps(pairs) + r''')
  console.log(name,Math.min(c(accent,panel),c(alt,panel),c(badInk,badGround),c(muted,panel)).toFixed(2))''')
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.split('\n')[:-1]:
            name, lowest = line.split()
            self.assertGreaterEqual(float(lowest), 4.5, name)

    def test_no_text_under_16px(self):
        sizes = [int(s) for s in re.findall(r'font(?:-size)?:\s*(?:\d+\s+)?(\d+)px', CSS)]
        self.assertTrue(sizes)
        self.assertEqual([s for s in sizes if 0 < s < 16], [])

    def test_the_glitch_fires_on_events_and_never_with_reduced_motion_off_or_in_the_background(self):
        script = r'''
var RadioTheme={mount(){}},RadioBars={wrap:(t,n)=>({mount:t.mount,bars:n})};
''' + (WEB / 'themes/cyber/view.js').read_text() + r'''
const assert=require('node:assert/strict'),e=RadioTheme.effect;
assert.equal(RadioTheme.bars,24);
assert.equal(e('title','on',false,false),'decode');assert.equal(e('title','reduced',false,false),'decode-still');
assert.equal(e('signal','on',false,false),'slice');assert.equal(e('power','on',false,false),'slice');
assert.equal(e('signal','reduced',false,false),null,'reduced: only the still state');
for(const ev of ['title','signal','power']){assert.equal(e(ev,'off',false,false),null);assert.equal(e(ev,'on',true,false),null,'reduced motion');
  assert.equal(e(ev,'on',false,true),null,'a hidden tab')}
'''
        result = node(script)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
