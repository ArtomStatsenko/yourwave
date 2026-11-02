"""The replay queue: each queued song shows its place, the queue sits above "now" in the order it plays."""
import json
import subprocess
import unittest

from tests.ui_page import WEB, page_source

STATUS = {'replay_queue': [{'kind': 'music', 'ref': '/m/b.mp3'}, {'kind': 'music', 'ref': '/m/a.mp3'}],
          'upcoming': [{'kind': 'music', 'ref': '/m/b.mp3', 'title': 'B', 'stage': 'queued'},
                       {'kind': 'music', 'ref': '/m/a.mp3', 'title': 'A', 'stage': 'queued'},
                       {'kind': 'music', 'ref': '/m/c.mp3', 'title': 'C', 'stage': 'music'}]}


class ReplayQueueTests(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_model_numbers_the_queue_in_play_order_and_next_comes_after_it(self):
        self.run_node((WEB / 'core/core.js').read_text() + (WEB / 'core/model.js').read_text() + r'''
const assert=require('node:assert/strict'),st=''' + json.dumps(STATUS) + r''';
assert.equal(RadioModel.queuePlace({kind:'music',ref:'/m/a.mp3'},st),2);
assert.equal(RadioModel.queuePlace({kind:'music',ref:'/m/c.mp3'},st),0);
const up=RadioModel.upcoming(st,'');
assert.deepEqual(up.queue.map(q=>[q.place,q.title]),[[1,'B'],[2,'A']]);
assert.equal(up.next.ref,'/m/c.mp3','the planned song plays after the queue');
assert.deepEqual(RadioModel.upcoming({upcoming:[{kind:'music',ref:'/m/c.mp3',title:'C',stage:'music'}]},'').queue,[]);
''')

    def test_the_view_shows_the_place_in_a_disc_and_the_queue_above_now(self):
        page = page_source()
        start = page.index('// a thumbs up for one airing')
        rows = page[start:page.index('function backgroundError(', start)]
        render = page[page.index('function backgroundError(', start):page.index("$('history-more').onclick", start)]
        self.run_node((WEB / 'core/core.js').read_text() + (WEB / 'core/model.js').read_text() + rows + render + r'''
const assert=require('node:assert/strict');
function esc(s){return String(s)}var open={},appearance={formatTime:t=>t},items=[],planDraft=null,rerollError='',rerollConfirm=null,historyExpanded=false;
var st=''' + json.dumps(STATUS) + r''',nodes={},document={activeElement:null,documentElement:{dataset:{}}};
function $(id){return nodes[id]||(nodes[id]={scrollTop:0,contains:()=>false,innerHTML:'',querySelector:()=>null,querySelectorAll:()=>[],classList:{toggle(){}},style:{}})}
function running(){return true}function directionView(){}function rerollButton(){return ''}
const song=row(Object.assign({id:5,kind:'music',time:'13:50',kindLabel:'',text:'A',replayable:true},{queued:true,queuePlace:2}));
assert.match(song,/aria-label="Queued, number 2. Remove from the queue"/);assert.match(song,/<span class="qn">2<\/span><span class="qx">✕<\/span>/);
try{timeline()}catch(e){}  // the rest of the timeline needs a page; the upcoming part is drawn first
const up=$('upcoming').innerHTML;
assert.ok(up.indexOf('queue 2 · A')<up.indexOf('queue 1 · B'),'queue 1 is nearest now');
assert.match(up,/<span class="tm">then<\/span><span class="tx[^"]*"[^>]*title="C"/,'the planned song is then');
assert.match(up,/<span class="tm">next<\/span><span class="tx">queue 1 · B/);
assert.match(up,/aria-label="Remove A from the queue"/);
''')


if __name__ == '__main__':
    unittest.main()
