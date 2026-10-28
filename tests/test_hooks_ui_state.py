import json
import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class HookReviewStateTests(unittest.TestCase):
    def test_review_removes_candidate_stops_preview_and_confirms_result(self):
        source = page_source()
        script = source[source.index("var hookDataKey="):source.index('function localInput(')]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
async function review(approved){
  const nodes={};let paused=0,revoked=0;
  const $=id=>nodes[id]||(nodes[id]={hidden:true,textContent:'',innerHTML:'',src:'',
    play:async()=>{},pause:()=>paused++,removeAttribute(k){this[k]=''},load(){}});
  const before={counts:{CANDIDATE:1},candidates:[{id:11,ref:'Dad, Look!',provider:'test',start:1,end:7}]};
  const after={counts:approved?{APPROVED:1}:{REJECTED:1},candidates:[]};
  const c={$: $,esc:x=>x,TOKEN:'test',fetch:async()=>({ok:true,blob:async()=>({})}),
    URL:{createObjectURL:()=> 'blob:hook',revokeObjectURL:()=>revoked++},
    api:async()=>({ok:true,hooks:after})};
  vm.createContext(c);vm.runInContext(SOURCE,c);c.hooksView(before);
  const play={dataset:{hookPlay:'11'}},button={dataset:{hookReview:'11',approved:String(approved)}};
  await $('hook-review').onclick({target:{closest:s=>s==='[data-hook-play]'?play:null}});
  assert.equal($('hook-audio').hidden,false);
  await $('hook-review').onclick({target:{closest:s=>s==='[data-hook-review]'?button:null}});
  assert.ok(!$('hook-candidates').innerHTML.includes('data-hook-review'));
  assert.equal($('hook-audio').hidden,true,'Reviewed hook must not remain as the active preview');
  assert.equal($('hook-audio').src,'');assert.equal(paused,1);assert.equal(revoked,1);
  assert.match($('hook-feedback').textContent,approved?/approved/i:/rejected/i);
}
(async()=>{await review(true);await review(false)})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run(['node', '-e', 'const SOURCE=' + json.dumps(script) + ';\n' + harness],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
