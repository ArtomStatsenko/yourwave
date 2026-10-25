"""Single-source controls preserve failed drafts and leave existing choices intact."""
import subprocess
import unittest
from pathlib import Path


class SourceSettingsUiTests(unittest.TestCase):
    def test_single_source_controls_for_reddit_and_telegram(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        editor = source[source.index('var sourceDrafts='):source.index('function settingsView(')]
        subprocess.run(['node', '-e', editor + r'''
const assert=require('node:assert/strict');
const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
let lastBody,stg=[],result={ok:true,settings:[]},focused=0;
let field={value:'r/python',reportValidity:()=>true,focus:()=>focused++};
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
