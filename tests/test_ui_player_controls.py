import subprocess
import unittest
from pathlib import Path
from tests.ui_page import core_source, theme_source


class PlayerControlsTests(unittest.TestCase):
    def test_monitor_and_station_icons_keep_their_distinct_actions(self):
        source=theme_source()
        monitor=source[source.index('  var ppLabel='):source.index("  $('vol').hidden=")]
        station=source[source.index('  var soLabel='):source.index("  $('nx').disabled=")]
        result=subprocess.run(['node','-e',core_source()+r'''
const assert=require('node:assert/strict');
const nodes={};function $(id){return nodes[id]||(nodes[id]={dataset:{},setAttribute(k,v){this[k]=v},
 set textContent(value){throw Error('Updating the controls must preserve their SVG icons')}})}
var listening=false,busy=false,st={state:'PLAYING',output:'browser'};
function update(){var p=RadioModel.player(st,{t:0,s:st},{listening,localControl:true,busy});
'''+monitor+station+r'''
}
update();assert.equal($('pp')['aria-label'],'Listen');assert.equal($('pp').dataset.playing,'false');
assert.equal($('so')['aria-label'],'Stop radio');assert.equal($('so').title,'Stop radio for all listeners');
listening=true;update();assert.equal($('pp')['aria-label'],'Pause listening');assert.equal($('pp').dataset.playing,'true');
st.output='speakers';update();assert.equal($('pp')['aria-label'],'Pause');
st.state='PAUSED';update();assert.equal($('pp')['aria-label'],'Play');
st.state='STOPPED';update();assert.equal($('so')['aria-label'],'Start radio');
st.state='STOPPING';update();assert.equal($('so')['aria-label'],'Stopping…');assert.equal($('so').disabled,true);
assert.equal($('so')['aria-busy'],'true');
'''],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
