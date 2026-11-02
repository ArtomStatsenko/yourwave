"""Waiting texts pulse every 1.4 s while the station chooses or prepares: at most 3 at once, none after 20 s or when quiet."""
import subprocess
import unittest

from tests.ui_page import theme_source


class WaiterTests(unittest.TestCase):
    def test_pulses_repeat_while_waiting_and_stop_on_answer_give_up_and_quiet(self):
        source = theme_source()
        waiter = source[source.index('function waiter('):source.index('var CSS_WAIT=')]
        harness = r'''
const assert=require('node:assert/strict');
let t=0,timers=[],quiet=false,log=[];
const els={};function $(id){return els[id]||(els[id]={id})}
const fx={begin:e=>log.push('begin '+e.id),pulse:e=>log.push('pulse '+e.id),stop:e=>log.push('stop '+e.id)};
const w=waiter({fx:()=>fx,now:()=>t,later:(fn,ms)=>{timers.push({fn,at:t+ms});return timers.length},quiet:()=>quiet});
function run(ms){const end=t+ms;for(;;){timers.sort((a,b)=>a.at-b.at);const n=timers[0];if(!n||n.at>end)break;timers.shift();t=n.at;n.fn()}t=end}
let a=true,b=true,c=true,d=true;
w.watch('a',()=>a);assert.deepEqual(log,['begin a'],'at once on the press');w.watch('a',()=>a);assert.equal(w.count(),1,'once per text');
log=[];run(1400*3);assert.deepEqual(log,['pulse a','pulse a','pulse a'],'every 1.4 s while waiting');
a=false;log=[];run(1400);assert.deepEqual(log,['stop a'],'the answer stops it');assert.equal(timers.length,0,'and the timer sleeps');
['a','b','c','d'].forEach(id=>w.watch(id,()=>({a,b,c,d})[id]||id!=='a'));a=true;
log=[];run(1400);assert.equal(log.filter(x=>x.startsWith('pulse')).length,3,'at most 3 at once');
quiet=true;log=[];run(1400*2);assert.deepEqual(log,[],'a hidden tab or reduced motion: no pulses');quiet=false;
log=[];run(20000);assert.deepEqual(log.filter(x=>x.startsWith('stop')).sort(),['stop a','stop b','stop c','stop d'],'none after 20 s');
assert.equal(w.count(),0);
'''
        result = subprocess.run(['node', '-e', waiter + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
