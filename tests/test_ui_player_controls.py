import subprocess
import unittest
from pathlib import Path
from tests.ui_page import core_source, theme_source


class PlayerControlsTests(unittest.TestCase):
    def test_monitor_and_station_icons_keep_their_distinct_actions(self):
        source=theme_source()
        monitor=source[source.index('  var ppLabel='):source.index("  $('vol').hidden=")]
        result=subprocess.run(['node','-e',core_source()+r'''
const assert=require('node:assert/strict');
const nodes={};function $(id){return nodes[id]||(nodes[id]={dataset:{},setAttribute(k,v){this[k]=v},
 set textContent(value){throw Error('Updating the controls must preserve their SVG icons')}})}
var LOCAL_CONTROL=true,startingUntil=0,running=()=>st.state==='PLAYING'||st.state==='PAUSED';
var listening=false,busy=false,st={state:'PLAYING',output:'both'};
function update(){var p=RadioModel.player(st,{t:0,s:st},{listening,localControl:true,busy});
'''+monitor+r'''
}
update();assert.equal($('pp')['aria-label'],'Stop radio');assert.equal($('pp').title,'Stop radio for all listeners');assert.equal($('pp').dataset.playing,'true');
st.state='STOPPED';update();assert.equal($('pp')['aria-label'],'Start radio');assert.equal($('pp').dataset.playing,'false')
st.state='STARTING';update();assert.equal($('pp')['aria-label'],'Starting…');
st.state='STOPPING';update();assert.equal($('pp')['aria-label'],'Stopping…');
st={state:'PLAYING',output:'browser'};update();assert.equal($('pp')['aria-label'],'Listen','the browser output: the Mac page only listens');
st.state='STOPPED';update();assert.equal($('pp')['aria-label'],'Starting…','and the page server starts the radio');
'''],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
