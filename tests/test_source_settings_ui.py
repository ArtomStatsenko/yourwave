"""Single-source controls preserve failed drafts and leave existing choices intact."""
import subprocess
import unittest
from pathlib import Path


class SourceSettingsUiTests(unittest.TestCase):
    def test_source_manager_groups_news_controls_and_keeps_editing_state(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        script = source[source.index('function isNewsSource('):source.index('function settingValue(')]
        script += source[source.index('function pluginsView()'):source.index("['integrations','experiments','features'].forEach(function(section){$(section).onclick")]
        result=subprocess.run(['node','-e',script+r'''
const assert=require('node:assert/strict');
var document={activeElement:null,querySelector:()=>null},sourceOpen={},nodes={},st={sources:{reddit:'2 new'}},stg=[{key:'reddit_communities',value:['python']}];
function $(id){return nodes[id]||(nodes[id]={innerHTML:'',contains:()=>false,querySelectorAll:()=>[]})}
function esc(s){return String(s)}function sourceSettingView(){return '<input id="source-reddit_communities">'}
var pls=[{name:'horizon',title:'Horizon',on:false,section:'integrations',description:'General news'},
 {name:'reddit',title:'Reddit',on:true,section:'experiments',description:'Public posts'},
 {name:'news_reactions',title:'Reactions',on:false,section:'experiments',description:'Voice'},
 {name:'weather',title:'Weather',on:true,section:'integrations',description:'Forecast'},
 {name:'ytmusic',title:'YouTube Music',on:true,section:'integrations',description:'Music source'}];
pluginsView();
assert($('news-source-list').innerHTML.includes('Technology news · Horizon'));
assert($('news-source-list').innerHTML.includes('aria-pressed="false"'));
assert($('news-source-list').innerHTML.includes('Subreddits (1)'));
assert($('news-source-list').innerHTML.includes('2 new'));
assert(!$('news-source-list').innerHTML.includes('waiting for the next source check'));
assert(!$('news-source-list').innerHTML.includes('Off · excluded'));
assert(!$('news-source-list').innerHTML.includes('id="help-plugin-github_new" open'));
helpText.open['plugin-horizon']=true;
pluginsView();
assert($('news-source-list').innerHTML.includes('id="help-plugin-horizon" open'));
assert($('news-source-list').innerHTML.includes('Details about Horizon'));
assert($('news-source-list').innerHTML.includes('Experimental'));
assert.equal(helpText('empty','','Empty'),'');
assert(!$('experiment-plugins').innerHTML.includes('data-pl="reddit"'));
assert($('experiment-plugins').innerHTML.includes('news_reactions'));
assert(!$('integration-plugins').innerHTML.includes('horizon'));
assert($('integration-plugins').innerHTML.includes('weather'));
const saved=$('news-source-list').innerHTML;
$('news-source-list').contains=()=>true;document.activeElement={matches:()=>true};
stg[0].value.push('programming');sourceManagerView();
assert.equal($('news-source-list').innerHTML,saved,'Polling must not replace an active draft');
document.activeElement=null;$('news-source-list').contains=()=>false;
helpText.open['plugin-reddit']=true;
sourceManagerView();assert($('news-source-list').innerHTML.includes('id="help-plugin-reddit" open'));
const redditBlock=$('news-source-list').innerHTML.match(/<section aria-label="Reddit">([\s\S]*?)<\/section>/)[1];
assert.equal((redditBlock.match(/<details/g)||[]).length,1);
assert(redditBlock.indexOf('id="source-reddit_communities"')<redditBlock.indexOf('</details>'));
assert(!redditBlock.includes('source-manage-reddit'));
assert($('news-source-list').innerHTML.includes('Subreddits (2)'));
'''],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_single_source_controls_for_reddit_and_telegram(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        editor = source[source.index('var sourceDrafts='):source.index('function settingsView(')]
        subprocess.run(['node', '-e', editor + r'''
const assert=require('node:assert/strict');
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
let lastBody,stg=[],pls=[],st={},document={activeElement:null},result={ok:true,settings:[]},focused=0;
let field={value:'r/python',contains:()=>false,querySelectorAll:()=>[],reportValidity:()=>true,focus:()=>focused++};
const $=()=>field,settingsView=()=>{},api=(url,body)=>{lastBody=body;return Promise.resolve(result)};
const reddit={key:'reddit_communities',title:'Reddit · Subreddits',hint:'One at a time',value:['python']};
const telegram={key:'telegram_channels',title:'Telegram · Channels',hint:'Public only',value:['example']};
assert.ok(sourceSettingView(reddit).includes('Add subreddit'));
assert.ok(sourceSettingView(reddit).includes('aria-label="Remove r/python"'));
assert.ok(sourceSettingView(telegram).includes('Add channel'));
assert.ok(sourceSettingView(telegram).includes('aria-label="Remove @example"'));
assert.ok(sourceSettingView(telegram).includes('href="https://t.me/example"'));
assert.ok(!sourceSettingView(reddit).includes('<textarea'));
sourceDrafts.telegram_channels='<draft>';sourceFeedback.telegram_channels={state:'error',text:'Invalid <link>'};
let html=sourceSettingView(telegram);
assert.ok(html.includes('&lt;draft&gt;'));assert.ok(html.includes('&lt;link&gt;'));
assert.ok(html.includes('aria-invalid="true"'));
(async()=>{
 for(const [s,value,cmd] of [[reddit,'r/technology','reddit_community'],[telegram,'@telegram','telegram_channel']]){
  field.value=value;result={ok:true,settings:[s]};
  editSourceName(s.key);
  assert.equal(sourceFeedback[s.key].state,'saving');
  assert.ok(sourceSettingView(s).includes('disabled'));
  await new Promise(setImmediate);
  assert.deepEqual(lastBody,{cmd,action:'add',value});
  assert.equal(sourceDrafts[s.key],undefined);assert.equal(sourceFeedback[s.key].text,'Added');
  field.value='draft';editSourceName(s.key,s.value[0]);await new Promise(setImmediate);
  assert.equal(lastBody.action,'remove');assert.equal(lastBody.value,s.value[0]);
  assert.equal(sourceDrafts[s.key],'draft');
  field.value='missing';result={ok:false,error:'Public source unavailable'};
  editSourceName(s.key);await new Promise(setImmediate);
  assert.equal(sourceDrafts[s.key],'missing');assert.equal(sourceFeedback[s.key].text,'Public source unavailable');
 }
 assert.equal(focused,6);
})().catch(e=>{console.error(e);process.exitCode=1});
'''], check=True, capture_output=True, text=True)
