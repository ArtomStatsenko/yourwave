import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from radio import config, ui


class StatusSyncTests(unittest.TestCase):
    def test_busy_daemon_is_an_error_not_a_stopped_station(self):
        with tempfile.TemporaryDirectory(prefix='radio-status-sync-') as root, \
             patch.object(config, 'DATA_DIR', Path(root)), patch.object(config, 'RUN_DIR', Path(root) / 'run'):
            config.load()
            with patch.object(ui, 'Stream'), patch('radio.__main__.send', return_value={'ok': False, 'error': 'Busy'}):
                server = ui.make_server(0)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                try:
                    req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/status',
                                                 headers={'X-Radio-Token': server.token})
                    try:
                        response = urllib.request.urlopen(req, timeout=5)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        data = json.load(response)
                        self.assertEqual(response.code, 503)
                        self.assertNotIn('status', data)
                finally:
                    server.shutdown()
                    server.server_close()

    def test_old_responses_and_timeouts_do_not_overwrite_current_playback(self):
        source = ui.PAGE.read_text()
        source = source[source.index('function refresh(){'):source.index('function selectTab(')]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
let pending=[],stops=0;const noop=()=>{},nodes={};
const c={st:{state:'PLAYING',current_title:'Initial'},listening:true,running:()=>c.st.state==='PLAYING',
 api:()=>new Promise(resolve=>pending.push(resolve)),$:id=>nodes[id]||(nodes[id]={}),
 document:{activeElement:null},hist:[],Date,unlisten:()=>stops++,watchdog:noop,player:noop,
 timeline:noop,pluginsView:noop,settingsView:noop,directionView:noop,hooksView:noop};
vm.createContext(c);vm.runInContext(SOURCE,c);
(async()=>{
 const old=c.refresh(),fresh=c.refresh();
 pending[1]({status:{state:'PLAYING',current_title:'New'}});await fresh;
 pending[0]({status:{state:'PLAYING',current_title:'Old'}});await old;
 assert.equal(c.st.current_title,'New');
 const failed=c.refresh();pending[2]({ok:false,error:'Busy'});await failed;
 assert.equal(c.st.current_title,'New');assert.equal(stops,0);
 assert.match(nodes.er.textContent,/Busy/);
 const recovered=c.refresh();pending[3]({status:{state:'PLAYING',current_title:'Recovered'}});await recovered;
 assert.equal(c.st.current_title,'Recovered');assert.equal(nodes.er.textContent,'');
 const slow=c.refresh(),later=c.refresh();
 pending[4]({status:{state:'PLAYING',current_title:'Slow response'}});await slow;
 assert.equal(c.st.current_title,'Slow response','A newer pending poll must not starve a valid response');
 pending[5]({status:{state:'PLAYING',current_title:'Latest'}});await later;
 assert.equal(c.st.current_title,'Latest');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
