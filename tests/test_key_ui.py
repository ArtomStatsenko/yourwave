import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class KeyUiTests(unittest.TestCase):
    def test_empty_save_and_enter_do_not_remove_a_saved_key(self):
        source = page_source()
        save = source[source.index('function saveKey('):source.index('function aiView(')]
        events = source[source.index("$('stg').onkeydown="):source.index('function pluginsView(')]
        harness = r'''
const assert=require('node:assert/strict');
var keyBusy=false,keyFb={},calls=[],focused=false,stg=[];
var nodes={'llm-key':{value:'',focus(){focused=true}},stg:{}};
function $(id){return nodes[id]}function settingsView(){}
var document={querySelector:()=>({dataset:{key:'openrouter'}})};
function api(path,body){calls.push(body);return Promise.resolve({ok:true})}
'''
        checks = r'''
(async function(){
function click(dataset){nodes.stg.onclick({target:{closest:selector=>selector==='[data-key],[data-key-rm]'?{dataset}:null}})}
click({key:'openrouter'});assert.equal(calls.length,0);assert.equal(keyFb.invalid,true);assert(focused);
nodes['llm-key'].value='   ';click({key:'openrouter'});assert.equal(calls.length,0);
let prevented=false;nodes['llm-key'].value='';
nodes.stg.onkeydown({key:'Enter',target:{id:'llm-key',dataset:{},value:''},preventDefault(){prevented=true}});
assert(prevented);assert.equal(calls.length,0,'empty Enter does not remove the saved key');
nodes['llm-key'].value='test-placeholder';click({key:'openrouter'});
assert.deepEqual(calls,[{cmd:'llm_key',provider:'openrouter',key:'test-placeholder'}]);
await new Promise(resolve=>setImmediate(resolve));
click({keyRm:'openrouter'});assert.deepEqual(calls[1],{cmd:'llm_key',provider:'openrouter',key:''});
await new Promise(resolve=>setImmediate(resolve));assert.equal(keyFb.text,'Key removed.');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', harness + save + events + checks], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
