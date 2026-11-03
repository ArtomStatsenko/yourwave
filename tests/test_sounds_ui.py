import subprocess
import unittest
from tests.ui_page import core_source, theme_source


class SoundsSettingsTests(unittest.TestCase):
    """Settings → Sounds: per slot the files that play now (own, else Base), Upload, Listen and Remove; an error
    shows as one line under its own slot."""
    def test_slots_upload_remove_and_errors(self):
        source = theme_source()
        code = source[source.index('var soundList=['):source.index('function localInput(')]
        setup = r'''
const assert=require('node:assert/strict');
var nodes={'sound-list':{innerHTML:'',contains:()=>false},sounds:{},'sound-audio':{pause(){},removeAttribute(){},load(){}}};
var document={activeElement:null},calls=[],answers=[];
function $(id){return nodes[id]}function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
var core={fetch:function(path,init){calls.push([path,init&&init.method]);return Promise.resolve({ok:true,json:()=>Promise.resolve(answers.shift())})}};
function cmd(name,args){calls.push([name,args.slot,args.name]);return Promise.resolve(answers.shift())}
'''
        harness = r'''
const SL=[{slot:'stinger_in',title:'Intro',hint:'Before the host.',own:['mine.mp3'],base:['base.wav']},
  {slot:'stinger_out',title:'Outro',hint:'',own:[],base:['out.wav']},{slot:'station_id',title:'Station ID',hint:'Needs the plugin.',own:[],base:[]}];
const html=()=>nodes['sound-list'].innerHTML;
(async function(){
soundsView(SL,true);
assert.match(html(),/Playing your file:/);assert.match(html(),/data-sound-remove="stinger_in" data-name="mine.mp3"/);
assert.doesNotMatch(html(),/data-name="base.wav"/,'own files replace Base in the list');
assert.match(html(),/Playing Base:/);assert.doesNotMatch(html(),/data-sound-remove="stinger_out"/,'Base files cannot be removed');
assert.match(html(),/Nothing plays here until you add a file/);
assert.match(html(),/aria-label="Listen to mine.mp3"/);
assert.equal((html().match(/data-sound-file=/g)||[]).length,3);
// upload: the file name is encoded, an error is one line under its slot only
answers=[{ok:false,error:'Choose an MP3, M4A, FLAC or WAV file'}];
await soundUpload('stinger_out',[{name:'a b.ogg'}]);
assert.deepEqual(calls[0],['/api/sound?slot=stinger_out&name=a%20b.ogg','POST']);
assert.match(html(),/a b.ogg: Choose an MP3, M4A, FLAC or WAV file/);assert.equal(soundBusy,null);
// a good upload replaces the list from the answer
answers=[{ok:true,name:'new.mp3',sounds:[SL[0],Object.assign({},SL[1],{own:['new.mp3']}),SL[2]]}];
await soundUpload('stinger_out',[{name:'new.mp3'}]);
assert.match(html(),/data-sound-remove="stinger_out" data-name="new.mp3"/);assert.match(html(),/Added new.mp3/);
// remove calls sound_remove and shows the answer's list
answers=[{ok:true,sounds:SL}];
await nodes.sounds.onclick({target:{closest:s=>s.includes('remove')?{dataset:{soundRemove:'stinger_out',name:'new.mp3'}}:null}});
assert.deepEqual(calls.at(-1),['sound_remove','stinger_out','new.mp3']);assert.doesNotMatch(html(),/data-sound-remove="stinger_out"/);
assert.match(html(),/Removed new.mp3/);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run(['node', '-e', core_source() + '\n' + setup + code + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
