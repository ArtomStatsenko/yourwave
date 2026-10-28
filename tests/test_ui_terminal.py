"""Terminal status follows the broadcast frame, including buffered browser playback."""
import json
from pathlib import Path
import subprocess
import unittest
from tests.ui_page import page_source

PAGE = page_source()
SCRIPT = 'function terminalState' + PAGE.split('function terminalState', 1)[1].split('function player()', 1)[0]


class TerminalTests(unittest.TestCase):
    def test_status_uses_aired_kind_and_stops_when_not_playing(self):
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const nodes={},c={document:{documentElement:{dataset:{presentation:'terminal'}}},stg:[{key:'station_name',value:'My Station'}],listening:false,browser(){return true},$(id){return nodes[id]||(nodes[id]={dataset:{},textContent:''})}};
vm.createContext(c);vm.runInContext(SCRIPT,c);
const live={state:'PLAYING',current_kind:'news'};
assert.equal(c.terminalState(live,{current_kind:'music'}).text,'ON AIR · MUSIC','use buffered audio frame, not the newer engine kind');
for(const kind of ['news','gap','weather','reminder','id','teaser'])assert.equal(c.terminalState(live,{current_kind:kind}).active,true);
assert.equal(c.terminalState(live,{}).text,'ON AIR · CONNECTING');
assert.equal(c.terminalState(live,{}).active,false);
for(const state of ['PAUSED','STARTING','STOPPING','STOPPED'])assert.equal(c.terminalState({state},{current_kind:'news'}).active,false);
assert.equal(c.terminalState({state:'PAUSED',auto_paused:true},{}).text,'STANDBY · WAITING FOR A LISTENER');
c.stationTerminal(live,{current_kind:'news'});assert.equal(nodes.airtxt.textContent,'ON AIR · HOST');
assert.equal(nodes['station-name'].textContent,'My Station');assert.equal(nodes['station-ident'].dataset.active,'true');
assert.equal(nodes['terminal-monitor'].textContent,'MONITOR / OFF');
c.stationTerminal({state:'PAUSED'},{current_kind:'news'});assert.equal(nodes['station-ident'].dataset.active,'false');
c.document.documentElement.dataset.presentation='calm';nodes.airtxt.textContent='original';c.stationTerminal(live,{current_kind:'news'});assert.equal(nodes.airtxt.textContent,'original');
"""
        result = subprocess.run(['node', '-e', 'const SCRIPT=' + json.dumps(SCRIPT) + ';\n' + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
