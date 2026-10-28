import subprocess
import unittest
from pathlib import Path
from tests.ui_page import page_source


class ReactionToggleUiTests(unittest.TestCase):
    def test_unavailable_flag_cannot_toggle_and_recovers_when_engine_changes(self):
        page = page_source()
        script = page[page.index('function isNewsSource('):page.index('function sourceManagerView(')] + page[page.index('function pluginsView()'):page.index("['integrations','experiments','features'].forEach(function(section){$(section).onclick")]
        harness = r'''
const assert=require('node:assert/strict');
var document={activeElement:null},nodes={},calls=[];
function $(id){return nodes[id]||(nodes[id]={innerHTML:''})}
function esc(s){return String(s)}function directionView(){}function sourceManagerView(){}
function cmd(name,args){calls.push([name,args.on])}
var p={name:'news_reactions',section:'experiments',title:'News reactions',description:'Reactions',on:false,available:false,unavailable_reason:'Select OmniVoice'};
var pls=[p];pluginsView();
assert.match($('experiment-plugins').innerHTML,/ disabled/);
assert.match($('experiment-plugins').innerHTML,/Select OmniVoice/);
const event={target:{closest:()=>({dataset:{pl:'news_reactions'}})}};
togglePlugin(event);assert.equal(calls.length,0);assert.equal(p.on,false);
p.available=true;p.unavailable_reason='';pluginsView();
assert.doesNotMatch($('experiment-plugins').innerHTML,/ disabled/);
togglePlugin(event);assert.deepEqual(calls,[['plugin',true]]);
'''
        result = subprocess.run(['node', '-e', script + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
