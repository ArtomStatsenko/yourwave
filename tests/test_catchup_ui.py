import json
import subprocess
import unittest
from pathlib import Path


class CatchupUiTests(unittest.TestCase):
    def test_pending_empty_failure_and_success_states_use_native_command(self):
        source=(Path(__file__).resolve().parents[1]/'radio/ui.html').read_text()
        script=next(line for line in source.splitlines() if line.startswith('function entryTitle('))+'\n'+source[source.index('var catchupBusy='):source.index('function refresh(){')]
        harness=r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const nodes={};const $=id=>nodes[id]||(nodes[id]={hidden:false,disabled:false,textContent:''});
let finish;const c={$,st:{state:'PLAYING',catchup:{state:'idle',available:8,error:''}},running:()=>true,
api:(path,body)=>{assert.equal(path,'/api/cmd');assert.equal(body.cmd,'catchup');return new Promise(r=>finish=r)}};
vm.createContext(c);vm.runInContext(SOURCE,c);
assert.equal(c.entryTitle('news','What I missed · 48h'),'Host edition');
assert.equal(c.entryTitle('host','Coming up'),'Host edition');
assert.equal(c.entryTitle('news','Some title'),'Host edition');
(async()=>{
c.catchupView(c.st.catchup);assert.equal($('catchup-build').disabled,false);
assert.equal($('catchup-status').textContent,'');
assert.match($('catchup-build').title,/48 hours/);
let pending=$('catchup-build').onclick();assert.equal($('catchup-build').disabled,true);
finish({ok:false,error:'Retry later'});await pending;
assert.equal($('catchup-build').disabled,false);assert.equal($('catchup-status').textContent,'Retry later');
pending=$('catchup-build').onclick();finish({ok:true,catchup:{state:'preparing',available:8,error:''}});await pending;
assert.equal($('catchup-build').disabled,true);assert.match($('catchup-status').textContent,/Preparing/);
c.catchupView({state:'idle',available:0,error:''});assert.equal($('catchup-build').disabled,true);
c.catchupView(undefined);assert.equal($('catchup-controls').hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result=subprocess.run(['node','-e','const SOURCE='+json.dumps(script)+';\n'+harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
