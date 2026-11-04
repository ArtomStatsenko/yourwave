import subprocess
import unittest
from tests.ui_page import core_source, theme_source


class StationsUiTests(unittest.TestCase):
    """YouTube Music stations: the source line names the station, the picker starts radio from a song or a mood,
    a failed command is said for a few seconds and the Activity row of an error shows its text."""
    def test_labels_and_groups(self):
        code = r'''
const assert=require('node:assert/strict');
assert.equal(RadioModel.stationLabel({kind:'mood',title:'Chill'}),'Station · Chill');
assert.equal(RadioModel.stationLabel({kind:'song',title:'Midnight City'}),'Radio · Midnight City');
assert.equal(RadioModel.musicSourceLabel({state:'PLAYING',music_source:'ytmusic'},true,{station:{kind:'mood',title:'Chill'}},'ytmusic'),'Station · Chill');
assert.equal(RadioModel.musicSourceLabel({state:'PLAYING',music_source:'ytmusic'},true,{station:null},'ytmusic'),'YouTube Music');
const list=Array.from({length:37},(_, i)=>({title:'t'+i,params:'p'+i})),g=RadioModel.stationGroups(list);
assert.equal(g.moods.length,11);assert.equal(g.genres.length,26);assert.equal(g.genres[0].title,'t11');
// a song unfolds to "Radio from this song" only while stations are possible; an error is Activity, not history
const items=[{id:1,kind:'music',ref:'ytmusic:abc',title:'A'},{id:2,kind:'error',title:'Connect YouTube Music first',time:'x'}];
let t=RadioModel.timeline({timeline:items,status:{},radio:true},true);
assert.equal(t.entries.length,1);assert.equal(t.entries[0].radio,true);assert.equal(t.entries[0].expandable,true);assert.equal(t.activity[0].kind,'error');
t=RadioModel.timeline({timeline:items,status:{}},true);assert.equal(!!t.entries[0].radio,false);
'''
        subprocess.run(['node', '-e', core_source() + code], check=True)

    def test_picker_starts_stations_and_says_errors(self):
        source = theme_source()
        code = source[source.index('// ---- YouTube Music stations'):source.index('var catchupBusy=false')]
        setup = r'''
const assert=require('node:assert/strict');
var nodes={},calls=[],answers=[],busy=false,ytOn=true,ytStatus={connected:true,station:null},currentRef='ytmusic:cur',said='',tabs=[];
function mk(){return {hidden:false,textContent:'',innerHTML:'',dataset:{},setAttribute(k,v){this[k]=v},querySelector:()=>null,addEventListener(){},closest:()=>null}}
function $(id){return nodes[id]||(nodes[id]=mk())}
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
var localStorage={d:{},getItem(k){return this.d[k]||null},setItem(k,v){this.d[k]=v}};
var document={addEventListener(){}},refreshes=0,quietPage=()=>true,waits={watch(){}},selectTab=b=>tabs.push(b);
function refresh(){refreshes++}function cmd(n){calls.push([n])}
function api(path,body){calls.push(body);return Promise.resolve(answers.shift())}
$('ttl').textContent='Now song';
'''
        harness = r'''
(async function(){
// not connected: one way out
ytStatus={connected:false};pickerView();assert.match($('station-body').innerHTML,/Connect YouTube Music in Sources/);
ytStatus={connected:true,station:null};
// opening loads the moods once and shows both groups
answers=[{ok:true,moods:Array.from({length:14},(_, i)=>({title:'M'+i,params:'p'+i}))}];
pickerToggle(true);await Promise.resolve();await Promise.resolve();
let h=$('station-body').innerHTML;
assert.match(h,/Radio from the current song/);assert.match(h,/Moods/);assert.match(h,/Genres/);
assert.match(h,/data-station-mood="0"/);assert.match(h,/data-station-mood="13"/);
// starting a mood: one request, the sheet closes, repeat clicks do nothing, the hint names the station
answers=[{ok:true,status:{ytmusic:{station:{kind:'mood',value:'p2',title:'M2'}}}}];
stationClick({target:{closest:()=>({disabled:false,hasAttribute:a=>a==='data-station-mood',dataset:{stationMood:'2'}})}});
stationClick({target:{closest:()=>({disabled:false,hasAttribute:a=>a==='data-station-mood',dataset:{stationMood:'3'}})}});
assert.equal(calls.filter(c=>c.action==='start').length,1);assert.equal(pickerBusy,true);assert.equal($('station-picker').hidden,true);
await new Promise(r=>setImmediate(r));
assert.equal(pickerBusy,false);assert.equal($('hint').textContent,'From the next song: radio from M2');
assert.deepEqual(calls.find(c=>c.action==='start'),{cmd:'station',action:'start',title:'M2',kind:'mood',value:'p2'});
assert.equal(JSON.parse(localStorage.d['radio-stations'])[0].title,'M2');
// Start now stays disabled until the first song is loaded; the station stops with ×
ytStatus={connected:true,station:{kind:'mood',value:'p2',title:'M2'}};
stationControls({station_ready:false});assert.equal($('station-now').hidden,false);assert.equal($('station-now').disabled,true);assert.equal($('station-stop').hidden,false);
stationControls({station_ready:true});assert.equal($('station-now').disabled,false);
stationControls({station_ready:true,upcoming_tracks:[{ref:'ytmusic:cur',station:true}]});currentRef='ytmusic:cur';stationControls({station_ready:true});assert.equal($('station-now').hidden,true,'a station song is on air: nothing to start');
// an error is said, not kept
answers=[{ok:false,error:'YouTube Music has no radio for this song; try another song or a mood'}];
stationClick({target:{closest:()=>({disabled:false,hasAttribute:a=>a==='data-station-song',dataset:{stationSong:'ytmusic:x',title:'X'}})}});
await new Promise(r=>setImmediate(r));
assert.match(said,/no radio for this song/);assert.equal($('hint').textContent,'');
clearTimeout(saidTimer);clearTimeout(hintTimer);
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        subprocess.run(['node', '-e', setup + core_source() + code + harness], check=True)


if __name__ == '__main__':
    unittest.main()
