import subprocess
import unittest
from pathlib import Path


class TimelineHistoryTests(unittest.TestCase):
    def test_restarts_stay_in_activity_and_do_not_displace_aired_history(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        render = source[source.index('function timeline('):source.index("$('history-more').onclick")]
        result = subprocess.run(['node', '-e', render + r'''
const assert = require('node:assert/strict');
var st={},planDraft=null,rerollError='',historyExpanded=false;
var document={activeElement:null},nodes={};
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:''})}
function row(x){return x.title+'\n'}function entryTitle(k,t){return t}function esc(s){return String(s)}
const songs=Array.from({length:6},(_,i)=>({id:i,kind:'music',title:'Song '+i}));
const restarts=Array.from({length:4},(_,i)=>({id:10+i,kind:'session',time:'15:20',title:'broadcast started'}));
var items=[songs[0],...restarts,...songs.slice(1)];
timeline();
assert.equal($('tl').innerHTML,songs.slice(0,5).map(row).join(''));
assert.equal($('history-more').hidden,false);
assert.equal($('activity').hidden,false);
assert.equal(($('activity-log').innerHTML.match(/15:20/g)||[]).length,4);
assert.equal(items.length,10,'Rendering must retain the technical events');
historyExpanded=true;timeline();
assert.equal($('tl').innerHTML,songs.map(row).join(''));
assert.equal($('history-more').textContent,'Show less');
items=[...restarts,...songs.slice(0,5)];historyExpanded=false;timeline();
assert.equal($('history-more').hidden,true,'Only aired rows count towards Show earlier');
items=restarts;timeline();
assert.ok($('tl').innerHTML.includes('nothing has aired yet'));
assert.equal($('history-more').hidden,true);
assert.equal($('activity').hidden,false);
items=[];timeline();
assert.equal($('activity').hidden,true);
assert.equal($('activity-log').innerHTML,'');
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
