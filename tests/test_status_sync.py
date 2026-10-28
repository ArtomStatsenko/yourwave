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
        source = (Path(__file__).resolve().parents[1] / 'radio/web/core/core.js').read_text()
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
const pending=[],seen=[];const answer=(i,body)=>pending[i]({json:async()=>body});
const c={};vm.createContext(c);vm.runInContext(SOURCE+';this.RadioCore=RadioCore',c);
const core=c.RadioCore.create({token:'t',fetch:(url,init)=>{assert.equal(init.headers['X-Radio-Token'],'t');return new Promise(r=>pending.push(r))}});
core.subscribe((core,change)=>seen.push(change));
const title=()=>core.snapshot.status.current_title;
(async()=>{
 const old=core.refresh(),fresh=core.refresh();
 answer(1,{status:{state:'PLAYING',current_title:'New'}});await fresh;
 answer(0,{status:{state:'PLAYING',current_title:'Old'}});await old;
 assert.equal(title(),'New');
 const failed=core.refresh();answer(2,{ok:false,error:'Busy'});await failed;
 assert.equal(title(),'New');assert.match(core.error,/Busy/);
 assert.deepEqual(seen,['snapshot','error'],'a late answer is dropped without telling the theme');
 const recovered=core.refresh();answer(3,{status:{state:'PLAYING',current_title:'Recovered'}});await recovered;
 assert.equal(title(),'Recovered');assert.equal(core.error,'');
 const slow=core.refresh(),later=core.refresh();
 answer(4,{status:{state:'PLAYING',current_title:'Slow response'}});await slow;
 assert.equal(title(),'Slow response','A newer pending poll must not starve a valid response');
 answer(5,{status:{state:'PLAYING',current_title:'Latest'}});await later;
 assert.equal(title(),'Latest');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(source) + ';' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
