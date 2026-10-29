// The terminal theme. RadioCore (radio/web/core) polls the radio and holds the data; this file renders it.
// Rendering is always from state, so a row expanded by its id stays expanded across the 2-second refresh.
var RadioTheme=(function(){
function mount(core,root){
var LOCAL_CONTROL=core.localControl;
var planOpen={},planHours={},planHorizon=3,planUndo=null,planInsertKey=null,historyExpanded=false,rerollBusy=false,rerollError='',rerollConfirm=null;
var KIND=core.kinds;
var dirBlocks=[],dirLoaded=false,dirBusy=false,planDraft=null,planVersion=null,planDrag=null,planTimeId=null,planConfirm=null;
var planPointer=null,planExpandTimer=null,planFineTimer=null,planSuppressClick=false;
var items=[],st={state:'STOPPED'},open={},busy=false,pls=[],stg=[],ytStatus={},musicChoice='local';
function $(s){return document.getElementById(s)}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function linkedText(s){return esc(s).replace(/https?:\/\/[^\s]+/gi,function(url){
  var u=url.replace(/[.,;!?)]+$/,'');
  return '<a href="'+u+'" target="_blank" rel="noopener">'+u+'</a>'+url.slice(u.length);
})}
function entryTitle(kind,title){return RadioModel.entryTitle(kind,title)}
function f(s){return RadioModel.clockText(s)}
function num(n){return n>=1000?(n/1000).toFixed(n>=10000?0:1).replace(/\.0$/,'')+'k':String(n)}
function api(path,body){return core.api(path,body)}
// ---- listening: the core plays the live stream on this device (core.audio); this theme draws its spectrum.
var nextBusy=false;
var listening=false;  // mirrors core.audio.listening
function browser(){return st.output!=='speakers'}
function lag(){return core.audio.lag()}
function catchUp(){core.audio.catchUp()}
function listen(){
  if(!running())return;
  core.audio.listen();
  player()}
function unlisten(){core.audio.unlisten()}
function vol(){core.audio.setVolume(+$('vol').value)}
$('vol').value=core.audio.volume;
$('vol').oninput=vol;
// the spectrum for radioViz while this device listens
function feed(){
  var heard=core.audio.read(radioViz.dw());
  if(heard)radioViz.set(heard.bands,heard.wave);
  if(core.audio.listening)requestAnimationFrame(feed)}
function media(ds){core.audio.media(ds)}
core.audio.onnext=function(){cmd('next')};
core.subscribe(function(core,change){if(change==='listening'){listening=core.audio.listening;if(listening)requestAnimationFrame(feed);player()}});
function running(){return st.state==='PLAYING'||st.state==='PAUSED'}

// The title line: text that fits stays still, a longer one scrolls to its end and back (no scrolling with reduced motion).
function fitTitle(){
  var box=$('ttl').parentNode,t=$('ttl');box.classList.remove('mq');
  if(box.classList.contains('typing'))return;           // the marquee starts when the typing is done
  if(matchMedia('(prefers-reduced-motion:reduce)').matches)return;
  var over=Math.ceil(t.getBoundingClientRect().width-box.clientWidth);
  if(over<2)return;
  box.style.setProperty('--mq',-over+'px');box.style.setProperty('--dur',Math.min(60,Math.max(8,over/12))+'s');
  box.classList.add('mq')}
/* Keep the shared prefix; start halfway into the crossfade, closer to the incoming track. */
function titleFrame(oldT,newT,ms,duration){
  var o=Array.from(oldT),n=Array.from(newT),keep=0;
  while(keep<o.length&&keep<n.length&&o[keep]===n[keep])keep++;
  var prefix=o.slice(0,keep).join('');o=o.slice(keep);n=n.slice(keep);
  var A=80,De=Math.min(50*o.length,450),Dt=Math.min(32*n.length,600),H=100,t,total=A+De+Dt+H;
  if(Number.isFinite(duration)){
    if(duration<=0)return{text:newT,caret:false,done:true};
    ms-=duration/2;
    if(ms<0)return{text:oldT,caret:false,done:false};
    ms=ms*total/duration;
  }
  if(ms>=total)return{text:newT,caret:false,done:true};
  if(ms<A)return{text:oldT,caret:true,done:false};
  t=ms-A;
  if(t<De)return{text:prefix+o.slice(0,Math.round(o.length*(1-t/De))).join(''),caret:true,done:false};
  t-=De;
  return{text:prefix+(t<Dt?n.slice(0,Math.round(n.length*t/Dt)).join(''):n.join('')),caret:true,done:false}}

var tfx={raf:0,shown:'',target:null,from:'',to:'',t0:0,duration:0};
function setTitle(text,instant,duration){
  if(text===tfx.target)return;                          // the poll repeats the same title every 250 ms
  tfx.target=text;
  var box=document.querySelector('.title'),el=$('ttl'),car=$('caret');
  $('ttl-sr').textContent=text;box.title=text;           // a screen reader hears the finished title once
  cancelAnimationFrame(tfx.raf);
  /* no animation: first load, reduced motion, hidden tab, nothing shown yet, messages instead of a track, or a
     theme that reuses this script without the typing (appearance.typing===false, e.g. lime) */
  if(instant||appearance.typing===false||duration===0||matchMedia('(prefers-reduced-motion:reduce)').matches||document.hidden||!tfx.shown||text===tfx.shown){
    el.textContent=tfx.shown=text;car.hidden=true;box.classList.remove('typing');box.scrollLeft=0;fitTitle();return}
  tfx.from=tfx.shown;tfx.to=text;tfx.t0=performance.now();tfx.duration=duration;   // a change mid-animation restarts from the text on screen
  box.classList.remove('mq');box.classList.add('typing');car.hidden=false;
  (function step(){
    var f=titleFrame(tfx.from,tfx.to,performance.now()-tfx.t0,tfx.duration);
    el.textContent=tfx.shown=f.text;car.hidden=!f.caret;
    box.scrollLeft=box.scrollWidth;                      // a long title scrolls along with the caret
    if(f.done){car.hidden=true;box.classList.remove('typing');box.scrollLeft=0;fitTitle();return}
    tfx.raf=requestAnimationFrame(step)})()}
window.addEventListener('resize',function(){if(tfx.target!==null)fitTitle()});
var titleIsTrack=false;
function musicSourceLabel(status,enabled,yt,choice){return RadioModel.musicSourceLabel(status,enabled,yt,choice)}
function musicSourceOptions(enabled){return '<option value="local">Local</option>'+(enabled?'<option value="ytmusic">YouTube Music</option>':'')}
function terminalState(live,heard){
  if(live.state==='PLAYING'){
    var names={music:'MUSIC',news:'HOST',weather:'WEATHER',reminder:'REMINDER',id:'STATION ID',teaser:'HOST',gap:'HOST'};
    return {text:'ON AIR · '+(names[heard.current_kind]||'CONNECTING'),active:!!names[heard.current_kind]};
  }
  return {text:live.auto_paused?'STANDBY · WAITING FOR A LISTENER':({PAUSED:'PAUSED',STARTING:'STARTING',STOPPING:'STOPPING'}[live.state]||'OFF AIR'),active:false};
}
function stationTerminal(live,heard){
  if(document.documentElement.dataset.presentation!=='terminal')return;
  var frame=terminalState(live,heard),name=stg.find(function(s){return s.key==='station_name'});
  $('station-name').textContent=name&&name.value||'Yourwave';
  $('station-ident').dataset.active=String(frame.active);
  $('airtxt').textContent=frame.text;
  $('terminal-monitor').textContent=browser()?(listening?'MONITOR / LISTENING':'MONITOR / OFF'):'OUTPUT / LOCAL';
}
function player(){
  var p=RadioModel.player(st,core.heard(st),{listening:listening,localControl:LOCAL_CONTROL,busy:busy,nextBusy:nextBusy,lag:lag()}),ds=p.frame;
  $('air').className=p.on?'':'off';$('air').textContent=p.airGlyph;
  $('airtxt').textContent=p.airText;
  stationTerminal(st,ds);
  setTitle(p.title,!(p.isTrack&&titleIsTrack),ds.transition_sec*1000);titleIsTrack=p.isTrack;  // only a track-to-track change is typed
  media(ds);
  $('fill').style.width=p.progress+'%';
  $('time').textContent=p.timeText;
  var ytEnabled=pls.some(function(p){return p.name==='ytmusic'&&p.on});
  $('music-source').textContent=musicSourceLabel(ds,ytEnabled,ytStatus,musicChoice);
  var select=$('music-source-select');
  if(select.dataset.ytEnabled!==String(ytEnabled)){select.innerHTML=musicSourceOptions(ytEnabled);select.dataset.ytEnabled=String(ytEnabled)}
  if(!ytEnabled||document.activeElement!==select)select.value=ytEnabled?musicChoice:'local';
  $('music-source-select').disabled=busy;
  $('yt-summary').textContent='YouTube Music: '+(ytEnabled?'on':'off')+' · '+(ytStatus.connected?ytStatus.tracks+' tracks configured':'not connected');
  var sp=p.speech.minutes,lim=p.speech.limit;
  $('sp').textContent=sp.toFixed(1);$('splim').textContent=lim%1?lim.toFixed(1):lim;
  $('spb').style.width=Math.min(100,sp/lim*100)+'%';$('spb').style.background=p.speech.near?'var(--warn)':'var(--ok)';
  $('vd').style.background={off:'var(--line)',warn:'var(--warn)',ok:'var(--ok)'}[p.voiceTone];
  $('vs').textContent=p.voiceText;
  var ppLabel=p.listen.label;
  $('pp').dataset.playing=String(p.listen.playing);$('pp').setAttribute('aria-label',ppLabel);$('pp').title=ppLabel;
  $('vol').hidden=!p.browser;
  $('pp').disabled=p.listen.disabled;
  $('station-controls').hidden=p.station.hidden;
  var soLabel=p.station.label;
  $('so').setAttribute('aria-label',soLabel);$('so').title=p.station.title;$('so').setAttribute('aria-busy',String(p.station.busy));
  $('so').disabled=p.station.disabled;
  $('nx').disabled=p.next.disabled;
  $('nx').textContent=p.next.text;$('nx').setAttribute('aria-busy',String(p.next.busy));
  $('nx').setAttribute('aria-label',p.next.label);$('nx').title=p.next.title;
  $('sk').hidden=p.skipNewsHidden;
  $('er').textContent=p.error;
}
var keyFb={cls:'',text:''},keyBusy=false;  // feedback under the API key field (never in the player's error line)
function saveKey(provider,key,remove){
  if(!remove&&!key.trim()){keyFb=Object.assign({},RadioModel.KEY_EMPTY);settingsView(true);$('llm-key').focus();return}
  keyBusy=true;settingsView(true);
  api('/api/cmd',{cmd:'llm_key',provider:provider,key:key}).then(function(d){if(d&&d.ok===false)throw Error(d.error);if(d&&d.settings)stg=d.settings;
    keyFb=RadioModel.keySaved(key);$('llm-key').value=''})
  .catch(function(e){keyFb={cls:'bad',text:(e.message||'Could not save the key')+'.',invalid:true}}).finally(function(){keyBusy=false;settingsView(true);
    var f=$('llm-key');if(f&&keyFb.invalid&&key){f.value=key;f.focus();f.select()}})}  // a failed key stays, selected: a paste replaces it
function aiView(p,m){  // the AI block: summary, provider, key or sign-in, model (design system: AiSettings)
  var a=RadioModel.ai(p),st=a.state,g=a.tone;
  if(keyFb.checking&&st!=='checking')keyFb=RadioModel.keyChecked(st);
  var h='<section class="ai" aria-label="AI"><div class="ai-h">AI</div>'
    +'<div class="sum" role="status" aria-live="polite"><div class="nm">'+esc(a.name)+'</div>'
    +'<div class="stt '+g[0]+'"><span class="g" aria-hidden="true">'+g[1]+'</span><span>'+esc(a.status)+(a.note?' <span class="d">· '+esc(a.note)+'</span>':'')+'</span></div></div>'
    +'<div class="st"><div class="hd2"><label for="st-llm_provider">'+esc(p.title)+'</label>'+resetButton(p)+'</div><select id="st-llm_provider" data-st="llm_provider">'
    +p.options.map(function(o){return '<option value="'+esc(o)+'"'+(o===p.value?' selected':'')+'>'+esc(p.labels[o]||o)+'</option>'}).join('')+'</select>'
    +(p.action?'<div class="row"><button type="button" class="ab" data-act="'+esc(p.action.cmd)+'">'+esc(p.action.label)+'</button></div>':'')+'</div>';
  if(p.key_field)h+='<div class="st"><label for="llm-key">API key</label><div class="keyrow">'
    +'<input type="password" id="llm-key" autocomplete="off" spellcheck="false" maxlength="300" aria-describedby="llm-key-fb" aria-invalid="'+!!keyFb.invalid+'" placeholder="'+(p.key_field.saved?'Key saved; paste a replacement':'Paste your API key')+'">'
    +'<button type="button" class="ab" data-key="'+esc(p.key_field.provider)+'"'+(keyBusy?' disabled':'')+'>'+(keyBusy?'Saving…':'Save key')+'</button>'
    +(p.key_field.saved?'<button type="button" class="rs" data-key-rm="'+esc(p.key_field.provider)+'">Remove key</button>':'')+'</div>'
    +'<div class="fb '+keyFb.cls+'" id="llm-key-fb" role="status" aria-live="polite">'+esc(keyFb.text)+'</div></div>';
  if(m)h+='<div class="st"><div class="hd2"><label for="st-'+m.key+'">Model</label>'+resetButton(m)+'</div>'
    +(m.options?'<select id="st-'+m.key+'" data-st="'+m.key+'">'+(st==='no_model'?'<option value="" selected disabled>Choose a model</option>':'')+m.options.map(function(o){return '<option'+(o===m.value?' selected':'')+'>'+esc(o)+'</option>'}).join('')+'</select>'
      :'<input type="text" id="st-'+m.key+'" data-st="'+m.key+'" minlength="'+m.min+'" maxlength="'+m.max+'" value="'+esc(m.value)+'">')
    +(a.modelHint?'<div class="l pick-hint">'+esc(a.modelHint)+'</div>':(m.hint?'<div class="l">'+esc(m.hint)+'</div>':''))+'</div>';
  return h+'</section>'}
var sample={st:'idle',err:'',t0:0},sampleTick=null,sampleAudio=null,voicePending=null;  // VoiceSettings: idle|busy|playing|error
function sampleFb(){return RadioModel.sampleFeedback(sample,stg.find(function(x){return x.voice_state}))}  // under the sample button (never the player's #er)
function voiceView(s){  // design system: VoiceSettings
  var v=RadioModel.voice(s,voicePending,sample),g=v.tone,fb=v.feedback,label=v.label;
  return '<section class="ai" aria-label="Voice"><div class="ai-h">Voice</div>'
    +'<div class="sum" role="status" aria-live="polite"><div class="nm">'+esc(v.name)+'</div>'
    +'<div class="stt '+g[0]+'"><span class="g" aria-hidden="true">'+g[1]+'</span><span>'+esc(v.status)+(v.note?' <span class="d">· '+esc(v.note)+'</span>':'')+'</span></div></div>'
    +'<div class="st"><div class="hd2"><label for="st-voice_engine">'+esc(s.title)+'</label>'+resetButton(s)+'</div><select id="st-voice_engine" data-st="voice_engine">'
    +s.options.map(function(o){return '<option value="'+esc(o)+'"'+(o===s.value?' selected':'')+'>'+esc(label(o))+'</option>'}).join('')+'</select>'
    +'<div class="l">'+esc(s.hint)+'</div></div>'
    +'<div class="st"><div class="act"><button type="button" class="ab" id="voice-sample"'+(sample.st==='busy'?' disabled aria-busy="true"':'')+'>'
    +v.sampleLabel+'</button></div>'
    +'<div class="fb '+fb[0]+'" id="voice-fb" role="status" aria-live="polite">'+esc(fb[1])+'</div></div></section>'}
function sampleState(st,err){sample.st=st;sample.err=err||'';clearInterval(sampleTick);
  if(st==='busy'){sample.t0=Date.now();sampleTick=setInterval(function(){var f=$('voice-fb'),t=sampleFb();if(f){f.textContent=t[1]}},1000)}
  settingsView(true)}
function playSample(){
  if(sample.st==='playing'){if(sampleAudio)sampleAudio.pause();sampleState('idle');return}
  if(sample.st==='busy')return;
  sampleState('busy');
  api('/api/cmd',{cmd:'voice_sample'}).then(function(d){if(!d||d.ok===false)throw Error(d&&d.error||'the radio did not answer');
    sampleAudio=new Audio(d.url);sampleAudio.onended=function(){sampleState('idle')};
    return sampleAudio.play().then(function(){sampleState('playing')})})
  .catch(function(e){sampleState('error',(e&&e.message)||'the voice engine did not answer')})}
var sourceDrafts={},sourceFeedback={};
function sourceSettingView(s){
  var reddit=s.key==='reddit_communities',fb=sourceFeedback[s.key]||{},disabled=fb.state==='saving'?' disabled':'',prefix=reddit?'r/':'@',base=reddit?'https://www.reddit.com/r/':'https://t.me/',label=reddit?'Add subreddit':'Add channel';
  return '<div class="st source-editor">'
    +'<ul class="community-list" aria-label="'+(reddit?'Added subreddits':'Added public channels')+'">'+s.value.map(function(name){return '<li class="row"><a href="'+base+esc(name)+'" target="_blank" rel="noopener noreferrer">'+prefix+esc(name)+'</a><button type="button" class="source-remove" data-source-remove="'+s.key+'" data-name="'+esc(name)+'" aria-label="Remove '+prefix+esc(name)+'"'+disabled+'>Remove</button></li>'}).join('')+'</ul>'
    +'<div class="source-add"><input type="text" class="source-name-input" id="source-'+s.key+'" data-source-input="'+s.key+'" aria-label="'+label+'" maxlength="512" placeholder="'+(reddit?'Subreddit name or URL':'Public channel name or URL')+'" value="'+esc(sourceDrafts[s.key]||'')+'" aria-describedby="sf-'+s.key+'" aria-invalid="'+(fb.state==='error')+'"'+disabled+'><button type="button" data-source-add="'+s.key+'" aria-label="'+label+'"'+disabled+'>'+(disabled?'Checking…':'Add')+'</button></div>'
    +'<div class="l source-feedback" id="sf-'+s.key+'" role="status" aria-live="polite">'+esc(fb.text||'')+'</div></div>';
}
function editSourceName(key,remove){
  var field=$('source-'+key);
  if(sourceFeedback[key]&&sourceFeedback[key].state==='saving')return;
  if(!remove&&(!field||!field.value.trim()||!field.reportValidity()))return;
  var value=remove||field.value;sourceDrafts[key]=field?field.value:'';
  sourceFeedback[key]={state:'saving',text:remove?'Removing…':'Checking public source…'};sourceManagerView(true);
  api('/api/cmd',{cmd:key==='reddit_communities'?'reddit_community':'telegram_channel',action:remove?'remove':'add',value:value}).then(function(d){
    if(!d||!d.ok)throw Error(d&&d.error||'Could not update sources. Try again.');
    stg=d.settings;if(!remove)delete sourceDrafts[key];sourceFeedback[key]={state:'saved',text:remove?'Removed':'Added'};
  }).catch(function(e){sourceFeedback[key]={state:'error',text:e.message}}).finally(function(){sourceManagerView(true);var f=$('source-'+key);if(f)f.focus()});
}
function isNewsSource(p){return ['horizon','github_trending','github_new','reddit','telegram'].includes(p.name)}
function helpText(id,text,label,content){
  if(!text)return '';
  helpText.open=helpText.open||{};
  return '<details class="control-help" id="help-'+esc(id)+'"'+(helpText.open[id]?' open':'')+'><summary id="help-toggle-'+esc(id)+'" aria-label="Details about '+esc(label)+'">Details</summary><div class="l" id="hint-'+esc(id)+'">'+esc(text)+'</div>'+(content||'')+'</details>';
}
function resetButton(s){return s.value!==s.default?'<button type="button" class="rs" data-rs="'+esc(s.key)+'" aria-label="Reset '+esc(s.title||s.key)+'">Reset</button>':''}
function hostCharacterView(s){return '<div class="host-character"><div class="hd2"><label for="st-'+s.key+'">'+esc(s.title)+'</label>'+resetButton(s)+'</div><textarea rows="4" id="st-'+s.key+'" data-st="'+s.key+'" maxlength="'+s.max+'" aria-describedby="hint-'+s.key+'">'+esc(s.value)+'</textarea><div class="l" id="hint-'+s.key+'">'+esc(s.hint)+'</div></div>'}
function pluginButton(p,source){
  var warning=p.unavailable_reason||({holiday_previews:'Requires Calendar and Host links · restart after installation',energy_wave:'Analyze the library first · restart after installation',track_previews:'Restart after installation',reddit:'Experimental · RSS support ends November 13, 2026',telegram:'Experimental · public channels only'})[p.name]||'';
  return '<div class="plugin-row"><button class="pl" data-pl="'+esc(p.name)+'" aria-pressed="'+p.on+'" aria-describedby="hint-plugin-'+esc(p.name)+'" title="'+esc(p.description)+'"'+(p.available===false?' disabled':'')+'><span class="sw">'+(p.on?'● on':'○ off')+'</span><span class="nm">'+esc(p.name==='horizon'?'Technology news · Horizon':p.title)+'</span></button>'
    +helpText('plugin-'+p.name,p.description,p.title,source?'<p class="l">'+(p.name==='reddit'?'Subreddits':'Channels')+' ('+source.value.length+')</p>'+sourceSettingView(source):'')+(warning?'<div class="l plugin-warning">'+esc(warning)+'</div>':'')+'</div>';
}
function sourceManagerView(force){
  var box=$('news-source-list'),f=document.activeElement;
  if(!force&&box.contains(f)&&(f.matches('input,textarea')||f.closest('[data-source-add],[data-source-remove]')))return;
  box.innerHTML=pls.filter(isNewsSource).map(function(p){
    var key=p.name==='reddit'?'reddit_communities':p.name==='telegram'?'telegram_channels':null,s=stg.find(s=>s.key===key);
    var description=p.name==='reddit'?'Read selected subreddits through RSS.':p.name==='telegram'?'Read selected public channels through web previews.':p.description;
    var status=p.on?(st.sources&&st.sources[p.name]||''):'';
    return '<section aria-label="'+esc(p.title)+'">'+pluginButton(Object.assign({},p,{description:description}),s)
      +(status?'<p class="l" role="status">'+esc(status)+'</p>':'')+'</section>';
  }).join('');
  if(f&&f.id&&$(f.id))$(f.id).focus({preventScroll:true});
}
function settingValue(s,value){return RadioModel.settingValue(s,value)}
function settingsView(force){  // force: after this page saved a value; the poll does not redraw under the user's hand
  var box=$('settings'),f=document.activeElement;
  if(!force&&(sample.st==='busy'||sample.st==='playing'))return;  // preserve a sample in progress
  if(!force&&(box.contains(f)&&['range','text','textarea','select-one','password'].includes(f.type)||$('llm-key')&&$('llm-key').value))return;  // not while dragging or typing
  var m=RadioModel.settings(stg),prov=m.provider,model=m.model,voice=m.voice;
  box.innerHTML=m.rows.filter(s=>!['host_character','cohost_character'].includes(s.key)).map(function(s){if(s.type==='ai')return aiView(prov,model)+(voice?voiceView(voice):'');
    var character=['host_name','cohost_name'].includes(s.key)?m.rows.find(c=>c.key===s.key.replace('_name','_character')):null;
    if(s.type==='switch')return '<div class="st"><div class="hd2"><label for="st-music_shuffle"><input type="checkbox" role="switch" id="st-music_shuffle" data-st="music_shuffle"'+(s.value?' checked':'')+' aria-describedby="hint-music_shuffle"> Shuffle</label>'+resetButton(s)+'</div>'+helpText(s.key,s.hint,s.title)+'</div>';
    return '<div class="st"><div class="hd2"><label for="st-'+s.key+'">'+esc(s.title)+'</label><span class="setting-actions">'+(s.step===null?'':'<span class="v" id="sv-'+s.key+'">'+esc(s.valueText)+'</span>')+resetButton(s)+'</span></div>'
    +(s.type==='select'?'<select id="st-'+s.key+'" data-st="'+s.key+'">'+s.options.map(function(o){return '<option'+(o===s.value?' selected':'')+'>'+esc(o)+'</option>'}).join('')+'</select>':s.type==='paragraph'?'<textarea rows="4" id="st-'+s.key+'" data-st="'+s.key+'" maxlength="'+s.max+'" aria-describedby="hint-'+s.key+'">'+esc(s.value)+'</textarea>':s.type==='text'?'<input type="text" id="st-'+s.key+'" data-st="'+s.key+'" minlength="'+s.min+'" maxlength="'+s.max+'" value="'+esc(s.value)+'">'
      :'<input type="range"'+(s.type==='stepped'?' class="stepped-range"':'')+' id="st-'+s.key+'" data-st="'+s.key+'" min="'+s.min+'" max="'+s.max+'" step="'+s.step+'" value="'+s.value+'" aria-valuetext="'+esc(s.valueText)+'" aria-describedby="hint-'+s.key+'">')
    +(s.value_labels?'<div class="range-labels" aria-hidden="true">'+s.value_labels.map(function(label){return '<span>'+esc(label)+'</span>'}).join('')+'</div>':'')
    +helpText(s.key,s.hint,s.title,character?hostCharacterView(character):'')+'</div>'}).join('');
  if(f&&f.id&&(force||f.id.startsWith('help-toggle-'))&&!box.contains(f)&&$(f.id))$(f.id).focus({preventScroll:true})}
$('audio-preset').onchange=function(){
  api('/api/cmd',{cmd:'audio_preset',name:this.value}).then(function(d){
    $('audio-preset-hint').textContent=d.ok===false?d.error:'Restart the station to apply.';
    refresh();
  }).catch(function(){$('audio-preset-hint').textContent='Could not save the audio preset. Try again.'})
};
$('stg').oninput=function(e){if(e.target.dataset.sourceInput){var key=e.target.dataset.sourceInput;sourceDrafts[key]=e.target.value;sourceFeedback[key]={};e.target.setAttribute('aria-invalid','false');return}var r=e.target.closest('[data-st]');if(!r)return;
  var s=stg.find(function(x){return x.key===r.dataset.st});if(s.multiline){sourceDrafts[s.key]=r.value;sourceFeedback[s.key]={};r.setAttribute('aria-invalid','false');return}if(s.step!==null&&r.type!=='checkbox'){$('sv-'+s.key).textContent=settingValue(s,r.value);r.setAttribute('aria-valuetext',settingValue(s,r.value))}};
$('stg').onchange=function(e){var r=e.target.closest('[data-st]');if(!r)return;
  var s=stg.find(function(x){return x.key===r.dataset.st});if(s.multiline)return;var value=r.type==='checkbox'?+r.checked:s.step===null?r.value:+r.value;s.value=value;
  if(s.key==='llm_provider')keyFb={cls:'',text:''};
  if(s.key==='voice_engine'){voicePending=value;setTimeout(function(){voicePending=null},6000)}
  api('/api/cmd',{cmd:'setting',key:s.key,value:value}).then(function(d){if(d&&d.ok===false)throw Error(d.error);if(d&&d.settings)stg=d.settings;settingsView(true)}).catch(function(e){$('er').textContent=e.message||'could not save'})};
$('stg').onkeydown=function(e){if(e.key==='Enter'&&e.target.dataset.sourceInput){e.preventDefault();editSourceName(e.target.dataset.sourceInput);return}if(e.key==='Enter'&&e.target.id==='llm-key'){e.preventDefault();var b=document.querySelector('[data-key]');if(b)saveKey(b.dataset.key,e.target.value)}};
$('stg').onclick=function(e){if(e.target.closest('#voice-sample')){playSample();return}
  var source=e.target.closest('[data-source-add],[data-source-remove]');if(source){editSourceName(source.dataset.sourceAdd||source.dataset.sourceRemove,source.dataset.name);return}
  var k=e.target.closest('[data-key],[data-key-rm]');
  if(k){saveKey(k.dataset.key||k.dataset.keyRm,k.dataset.key?$('llm-key').value:'',!!k.dataset.keyRm);return}
  var a=e.target.closest('[data-act]');
  if(a){api('/api/cmd',{cmd:a.dataset.act}).then(function(d){if(d.url){location.href=d.url;return}  // same tab: no popup to block; the callback page links back
      if(d.settings)stg=d.settings;settingsView(true)}).catch(function(){$('er').textContent='could not reach the radio'});return}
  var b=e.target.closest('[data-rs]');if(!b)return;
  var key=b.dataset.rs;
  api('/api/cmd',{cmd:'settings_reset',key:key}).then(function(d){if(!d||!d.ok)throw Error(d&&d.error||'Could not reset this setting.');if(d.settings)stg=d.settings;delete sourceDrafts[key];delete sourceFeedback[key];settingsView(true);var field=$('st-'+key);if(field)field.focus({preventScroll:true})}).catch(function(e){$('er').textContent=e.message;sourceFeedback[key]={state:'error',text:e.message};settingsView(true)})};
function pluginsView(){
  $('yt-form').hidden=!pls.some(function(p){return p.name==='ytmusic'&&p.on});
  var focused=document.activeElement&&document.activeElement.dataset.pl,helpFocus=document.activeElement&&document.activeElement.id;
  sourceManagerView();
  ['integrations','experiments','features'].forEach(function(section){
    $(section==='integrations'?'integration-plugins':section==='experiments'?'experiment-plugins':section).innerHTML=pls.filter(function(p){return p.section===section&&!isNewsSource(p)}).map(function(p){return pluginButton(p)}).join('') || (section==='experiments'?'<p class="l">No experiments yet.</p>':'')});
  if(focused){var b=document.querySelector('[data-pl="'+focused+'"]');if(b)b.focus({preventScroll:true})}
  if(helpFocus&&helpFocus.startsWith('help-toggle-')&&$(helpFocus))$(helpFocus).focus({preventScroll:true})}
function togglePlugin(e){var b=e.target.closest('[data-pl]');if(!b)return;
  var p=pls.find(function(x){return x.name===b.dataset.pl});if(!p||p.available===false)return;
  p.on=!p.on;pluginsView();directionView();cmd('plugin',{name:p.name,on:p.on})};
['integrations','experiments','features'].forEach(function(section){$(section).onclick=togglePlugin});
$('integrations').oninput=$('stg').oninput;
$('integrations').onkeydown=$('stg').onkeydown;
$('integrations').onclick=function(e){var source=e.target.closest('[data-source-add],[data-source-remove]');if(source){editSourceName(source.dataset.sourceAdd||source.dataset.sourceRemove,source.dataset.name);return}togglePlugin(e)};
var hookDataKey='',hookObjectUrl=null;
function hooksView(data){
  $('hook-review').hidden=!data;if(!data)return;
  var key=JSON.stringify(data);if(key===hookDataKey)return;hookDataKey=key;
  var counts=data.counts||{};
  $('hook-counts').textContent='Approved: '+(counts.APPROVED||0)+' · Automatic: '+(counts.AUTO||0)+' · Review: '+(counts.CANDIDATE||0)+' · No hook: '+(counts.NO_HOOK||0)+' · Retry: '+(counts.ERROR||0);
  $('hook-candidates').innerHTML=(data.candidates||[]).map(function(h){return '<div class="st"><div>'+esc(h.ref.split('/').pop())+' · '+esc(h.provider)+' · '+h.start.toFixed(1)+'–'+h.end.toFixed(1)+' s</div><div class="row"><button type="button" data-hook-play="'+h.id+'">Listen</button><button type="button" data-hook-review="'+h.id+'" data-approved="true">Approve</button><button type="button" data-hook-review="'+h.id+'" data-approved="false">Reject</button></div></div>'}).join('')||'<p class="l">No hooks waiting for review.</p>';
}
$('hook-review').onclick=async function(e){
  var play=e.target.closest('[data-hook-play]'),review=e.target.closest('[data-hook-review]');if(!play&&!review)return;
  var button=play||review;button.disabled=true;$('hook-feedback').textContent='';
  try{
    if(play){var response=await core.fetch('/hook-preview.wav?id='+play.dataset.hookPlay);if(!response.ok)throw Error('Hook is unavailable.');
      var blob=await response.blob();if(hookObjectUrl)URL.revokeObjectURL(hookObjectUrl);hookObjectUrl=URL.createObjectURL(blob);
      $('hook-audio').src=hookObjectUrl;$('hook-audio').hidden=false;await $('hook-audio').play();
    }else{var approved=review.dataset.approved==='true';
      var result=await api('/api/cmd',{cmd:'hook_review',id:+review.dataset.hookReview,approved:approved});if(!result.ok)throw Error(result.error);hooksView(result.hooks);
      $('hook-audio').pause();$('hook-audio').removeAttribute('src');$('hook-audio').load();$('hook-audio').hidden=true;
      if(hookObjectUrl){URL.revokeObjectURL(hookObjectUrl);hookObjectUrl=null;}
      $('hook-feedback').textContent=approved?'Hook approved. It can air when this track is announced.':'Hook rejected.';
    }
  }catch(error){$('hook-feedback').textContent=error.message||'Could not load the hook.';}finally{button.disabled=false;}
};
function localInput(iso,seconds){var d=iso?new Date(iso):new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+'T'+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+(seconds?':'+String(d.getSeconds()).padStart(2,'0'):'')}
function directionType(){var b=dirBlocks[+$('direction-block').value],text=!!b&&b.kind==='text';$('direction-text').hidden=!text;$('direction-text-label').hidden=!text;$('direction-text').required=text}
function directionCatalog(){return api('/api/cmd',{cmd:'direction',action:'catalog'}).then(function(d){if(!d.ok)throw Error(d.error);dirBlocks=d.blocks||[];
  $('direction-block').innerHTML=dirBlocks.map(function(b,i){return '<option value="'+i+'">'+esc(b.title)+'</option>'}).join('');dirLoaded=true;directionType()}).catch(function(e){$('direction-message').textContent=e.message})}
function rerollButton(){
  var r=st.next_reroll;if(!r)return '';
  if(rerollConfirm===r.ref)return '<span class="reroll-confirm" role="group" aria-label="Already announced on air. Replace next track?" title="Already announced on air"><button type="button" data-reroll="confirm" data-ref="'+esc(r.ref)+'">Confirm</button><button type="button" data-reroll="cancel">Cancel</button></span>';
  var label='Choose another random track';
  return '<button type="button" class="reroll-track" data-reroll data-plan-action="reroll" aria-label="'+label+'" title="'+esc(r.reason||'Replace next track now')+'" aria-busy="'+rerollBusy+'"'+(!r.available||rerollBusy?' disabled':'')+'><svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="3"/><g fill="currentColor" stroke="none"><circle cx="8" cy="8" r="1.3"/><circle cx="16" cy="8" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="8" cy="16" r="1.3"/><circle cx="16" cy="16" r="1.3"/></g></svg></button>';
}
function rerollNext(action,ref){
  if(action==='cancel'){rerollConfirm=null;timeline();return}
  var r=st.next_reroll;
  if(rerollBusy||!r||!r.available)return;
  if(action==='confirm'&&(rerollConfirm!==r.ref||ref!==r.ref))return;
  if(action!=='confirm'&&(st.announced_tracks||[]).includes(r.ref)&&!r.pending){rerollConfirm=r.ref;timeline();return}
  rerollConfirm=null;rerollBusy=true;rerollError='';directionView();timeline();
  cmd('reroll-next',{ref:st.next_reroll.ref}).then(function(r){rerollError=r&&r.ok?'':r&&r.error||'Could not replace the next track. Try again.'}).finally(function(){rerollBusy=false;directionView();timeline()});
}
function planReadiness(r){return RadioModel.planReadiness(r)}
function planGroups(rows){return RadioModel.planGroups(rows,st,planHorizon)}
function planNow(){return RadioModel.planNow(st)}
function directionView(force){
  var enabled=pls.some(function(p){return p.name==='air_direction'&&p.on});if(!enabled)planDraft=null;
  var editing=!!planDraft;$('plan-pause').hidden=!editing||st.state!=='PAUSED';$('direction-controls').hidden=!enabled;document.body.classList.toggle('program-editing',editing);
  $('direction-controls').classList.toggle('editing',editing);$('upcoming').hidden=editing||!$('upcoming').innerHTML;
  $('plan-edit').hidden=!enabled||editing;
  ['plan-done','direction-list','plan-later'].forEach(function(id){$(id).hidden=!editing});
  $('plan-undo').hidden=!editing||!planUndo;['plan-edit','plan-done','plan-undo','plan-later'].forEach(function(id){$(id).disabled=dirBusy});
  if(!enabled||!editing){if(typeof planPointer!=='undefined'&&planPointer)planStopPointer();return;}
  if(!dirLoaded&&!dirBusy){dirLoaded=true;directionCatalog()}
  var box=$('direction-list');if(planInsertKey!==null&&!dirBusy)return;if(planDrag!==null&&!force||!force&&!dirBusy&&box.contains(document.activeElement)&&document.activeElement.type==='datetime-local')return;
  if(planDrag===null&&!dirBusy&&!planDraft.some(function(r){return r.dirty})){planDraft=JSON.parse(JSON.stringify(st.broadcast_plan.rows));planVersion=st.broadcast_plan.version}
  var viewport=$('broadcast-timeline'),resuming=directionView.paused&&st.state!=='PAUSED';directionView.paused=st.state==='PAUSED';
  var anchor=resuming?Array.from(box.querySelectorAll('[data-plan-key]')).find(function(el){return el.getClientRects().length&&el.getBoundingClientRect().bottom>viewport.getBoundingClientRect().top}):null;
  var anchorKey=anchor&&anchor.dataset.planKey,anchorTop=anchor&&anchor.getBoundingClientRect().top;
  var focused=box.contains(document.activeElement)?document.activeElement:null,fr=focused&&focused.closest('[data-plan-index]'),focusId=fr&&fr.dataset.planKey,focusAction=focused&&focused.dataset.planAction,focusGap=focused&&focused.hasAttribute('data-plan-add')&&focused.parentNode.dataset.planGap,focusHour=focused&&focused.closest('summary')&&focused.closest('[data-hour]').dataset.hour;
  function clock(at){return at?planDragClock(at):'—'}
  function render(entry){var r=entry.row,i=entry.index,p=RadioModel.planRow(r),title=p.title;
    if(r.kind==='music')return '<div class="it music" data-plan-key="'+esc(r.id)+'"'+(r.at?' data-plan-at="'+esc(r.at)+'"':'')+'><div class="hd"><div class="tg"><span class="tm plan-time">'+(r.at?'~':'')+esc(clock(r.at))+'</span><span class="tx" title="'+esc(title)+'">'+'<span class="track-title">'+esc(title)+'</span> <span class="l track-labels">'+(r.announced?'announced':'')+(r.set?(r.announced?' · ':'')+'set':'')+'</span></span></div></div></div>';
    var editable=p.editable,skippable=p.skippable,detail=p.detail;
    var expected=p.expectedAt?(detail?' · ':'')+'expected ~'+clock(p.expectedAt):'';
    return '<div class="it '+esc(r.kind)+(r.skipped?' skipped':'')+'" data-plan-index="'+i+'" data-plan-key="'+esc(r.id)+'" data-plan-draggable="'+editable+'"><div class="hd"><div class="tg">'
      +(editable?'<button type="button" class="tm plan-time" data-plan-action="time" aria-label="Change time: '+esc(title)+'">':'<span class="tm plan-time">')+(r.estimated?'~':'')+esc(clock(r.at))+(editable?'</button>':'</span>')
      +(r.edition?'<button type="button" class="tx edition-toggle" data-plan-action="segments" aria-expanded="'+!!planOpen[r.id]+'">':'<span class="tx">')+esc(title)+(r.edition&&expected?'<span class="l plan-expected"> · '+esc(expected)+'</span>':'')+(r.edition?'</button>':'</span>')
      +'<span class="l plan-readiness">'+esc(p.readiness)+'</span>'+(!r.edition&&(detail||expected)?'<span class="l plan-detail">'+esc(detail+expected)+'</span>':'')+'</div>'
      +(editable||skippable?'<div class="plan-inline">'+(editable?'<button type="button" data-plan-action="up" aria-label="Move later: '+esc(title)+'">↑</button><button type="button" data-plan-action="down" aria-label="Move earlier: '+esc(title)+'">↓</button>':'')+'<button type="button" data-plan-action="skip" aria-label="'+(r.skipped?'Restore block':planConfirm===r.id?'Confirm skip reminder':'Skip block')+'">'+(r.skipped?'↶':planConfirm===r.id?'Confirm skip':'×')+'</button></div>':'')+'</div>'
      +(editable&&planTimeId===r.id?'<div class="plan-actions"><label class="sr" for="plan-time-'+i+'">Play at or after</label><input type="datetime-local" id="plan-time-'+i+'" aria-label="Play at or after" value="'+localInput(r.at)+'"></div>':'')
      +(r.edition&&planOpen[r.id]?'<div class="ex edition-segments">'+(r.segments||[]).map(function(seg){return '<div class="edition-segment"><span>'+esc(seg.title)+'</span><span class="l">'+esc(planReadiness(seg))+'</span>'+((seg.items||[]).length?'<p class="l">'+seg.items.map(esc).join(' · ')+'</p>':'')+'</div>'}).join('')+'</div>':'')
      +(r.error?'<p class="l err">'+esc(r.error)+'</p>':'')+'</div>';
  }
  var groups=planGroups(planDraft);
  var ordered=groups.flatMap(function(g){return g.entries}).filter(function(e){return e.row.at});
  function gap(index){var at=planGapTime(ordered.map(function(e){return e.row}),index);if(!at)return '';
    var key=index<ordered.length?ordered[index].row.id:'now';
    var lower=index<ordered.length?Date.parse(ordered[index].row.at):Date.now(),upper=index?Date.parse(ordered[index-1].row.at):Date.now()+planHorizon*3600e3;
    return '<div class="plan-gap" data-plan-gap="'+esc(key)+'" data-plan-lower="'+lower+'" data-plan-upper="'+upper+'"><button type="button" data-plan-add="'+esc(at)+'" aria-label="Insert block at '+esc(clock(at))+'" title="Insert block at '+esc(clock(at))+'">Insert</button></div>';
  }
  var add=$('plan-add');if(box.contains(add))$('tl-panel').appendChild(add);

  box.innerHTML=groups.map(function(g){if(g.key==='pending')return g.entries.map(render).join('');if(g.current)return g.entries.map(function(entry){return gap(ordered.indexOf(entry))+render(entry)}).join('');var expanded=!!planHours[g.key],count=g.entries.length;
    return gap(ordered.indexOf(g.entries[0]))+'<details class="plan-hour" data-hour="'+esc(g.key)+'"'+(expanded?' open':'')+'><summary data-plan-at="'+esc(g.entries[0].row.at)+'">'+esc(clock(g.entries[g.entries.length-1].row.at).replace(/:\d{2}/,':00'))+' <span class="l">· '+count+(count===1?' entry':' entries')+'</span></summary>'+g.entries.map(function(entry,index){return (index?gap(ordered.indexOf(entry)):'')+render(entry)}).join('')+'</details>'}).join('')||'<p class="l">No entries scheduled in the next '+planHorizon+' hours.</p>';
  box.innerHTML+=gap(ordered.length);
  add.hidden=planInsertKey===null;
  if(planInsertKey!==null){var slot=findPlanGap(planInsertKey);if(slot)slot.appendChild(add);else{planInsertKey=null;add.hidden=true}}
  box.querySelectorAll('details[data-hour]').forEach(function(el){
    el.ontoggle=function(){planHours[el.dataset.hour]=el.open};
    el.querySelector('summary').onclick=function(e){
      e.preventDefault();var top=this.getBoundingClientRect().top;
      el.open=!el.open;planHours[el.dataset.hour]=el.open;
      viewport.scrollTop+=this.getBoundingClientRect().top-top;
    };
  });
  box.querySelectorAll('button,input').forEach(function(el){el.disabled=dirBusy});$('direction-add').disabled=dirBusy;
  $('plan-later').hidden=!planDraft.filter(function(r){return r.kind!=='music'}).concat(st.upcoming_tracks||[]).some(function(r){return !r.placeholder&&Date.parse(r.at)>=planNow()+planHorizon*3600e3});
  if(anchorKey){var restored=Array.from(box.querySelectorAll('[data-plan-key]')).find(function(el){return el.dataset.planKey===anchorKey});if(restored){var hour=restored.closest('[data-hour]');if(hour){hour.open=true;planHours[hour.dataset.hour]=true}viewport.scrollTop+=restored.getBoundingClientRect().top-anchorTop}}
  if(focusGap){var gapButton=findPlanGap(focusGap);if(gapButton)gapButton.querySelector('button').focus({preventScroll:true})}
  if(focusHour){var hour=Array.from(box.querySelectorAll('[data-hour]')).find(function(el){return el.dataset.hour===focusHour});if(hour)hour.querySelector('summary').focus({preventScroll:true})}
  if(focusId&&focusAction){var i=planDraft.findIndex(function(r){return r.id===focusId}),el=box.querySelector('[data-plan-index="'+i+'"] [data-plan-action="'+focusAction+'"]');if(el)el.focus({preventScroll:true})}
}
function planGapTime(rows,index){return RadioModel.planGapTime(rows,index)}
function findPlanGap(key){return Array.from($('direction-list').querySelectorAll('[data-plan-gap]')).find(function(el){return el.dataset.planGap===key})}
function closePlanInsert(){var key=planInsertKey;planInsertKey=null;$('plan-add').hidden=true;directionView();var slot=findPlanGap(key);if(slot)slot.querySelector('button').focus({preventScroll:true})}
function openPlanInsert(button){
  var hour=button.closest('[data-hour]')||button.parentNode.nextElementSibling;if(hour&&hour.dataset.hour){planHours[hour.dataset.hour]=true;hour.open=true}
  planInsertKey=button.parentNode.dataset.planGap;$('direction-at').value=localInput(button.dataset.planAdd,true);
  button.parentNode.appendChild($('plan-add'));$('plan-add').hidden=false;$('direction-block').focus();
}

function planSave(){
  if(!planDraft||dirBusy)return Promise.resolve();
  var changes=planDraft.map(function(r,i){return {id:r.id,kind:r.kind,text:r.text,at:r.at,skipped:r.skipped,rank:i,dirty:r.dirty,skip_only:!!r.locked,skip_confirmed:!!r.skipConfirmed}}).filter(function(r){return r.dirty}).map(function(r){delete r.dirty;return r});
  if(!changes.length)return Promise.resolve();dirBusy=true;planTimeId=null;planConfirm=null;$('direction-message').textContent='Saving…';directionView();
  return api('/api/cmd',{cmd:'direction',action:'save',version:planVersion,changes:changes,remember_undo:true}).then(function(d){if(!d||!d.ok)throw Error(d&&d.error||'Could not save. Try again.');st.broadcast_plan=d.broadcast_plan;planUndo=d.undo_token||null;$('direction-message').textContent='Saved';return true})
    .catch(function(e){$('direction-message').textContent=e.message;return false}).finally(function(){dirBusy=false;planDraft=JSON.parse(JSON.stringify(st.broadcast_plan.rows));planVersion=st.broadcast_plan.version;directionView()});
}
function planMove(from,to){
  if(!planDraft||planDraft[from].kind==='music')return;
  var step=to>from?1:-1;while(to>=0&&to<planDraft.length&&planDraft[to].kind==='music')to+=step;
  if(dirBusy||!planDraft||to<0||to>=planDraft.length||planDraft.slice(Math.min(from,to),Math.max(from,to)+1).some(function(r){return r.locked}))return;
  var target=planDraft[to],moved=planDraft.splice(from,1)[0];planDraft.splice(to,0,moved);
  var at=Date.parse(target.at),neighbor=planDraft[to+(from<to?1:-1)];
  var other=neighbor?Date.parse(neighbor.at):at+(from<to?(target.duration||30)*1000:-30000);
  var when=from<to?other>at?(at+other)/2:at+1000:other<at?(at+other)/2:at-1000;
  moved.at=new Date(Math.max(Date.parse((st.broadcast_plan||{}).starts_at)||Date.now(),Date.now(),when)).toISOString();
  moved.estimated=true;moved.dirty=true;

  planSave();
}
function planSnap(at,minutes,floor){
  var parts=new Intl.DateTimeFormat('en',{timeZone:st.direction_timezone,minute:'numeric',second:'numeric'}).formatToParts(new Date(at));
  var minute=+parts.find(function(p){return p.type==='minute'}).value,second=+parts.find(function(p){return p.type==='second'}).value;
  var step=minutes*60000,remainder=minute%minutes*60000+second*1000+at%1000;
  return at-remainder+(!floor&&remainder>=step/2?step:0);
}
function planDragError(index,at){
  var r=planDraft&&planDraft[index];
  if(!r||r.locked||r.kind==='music')return 'This block cannot be moved.';
  if(!Number.isFinite(at)||at<Math.max(Date.now(),Date.parse((st.broadcast_plan||{}).starts_at)||0))return 'Choose a future time.';
  if(r.kind==='reminder'&&r.ends&&at>=Date.parse(r.ends))return 'The event is over by then; choose an earlier time.';
  var from=Date.parse(r.at);
  if(planDraft.some(function(other){var t=Date.parse(other.at);return other!==r&&other.locked&&other.kind!=='music'&&t>=Math.min(from,at)&&t<=Math.max(from,at)}))return 'A selected block is in the way; choose another time.';
  return '';
}
function planSetTime(index,at){
  if(dirBusy||!planDraft)return;
  var error=planDragError(index,at);if(error){$('direction-message').textContent=error;return;}
  var r=planDraft[index];if(Date.parse(r.at)===at)return;
  r.at=new Date(at).toISOString();r.estimated=false;r.dirty=true;
  planDraft.sort(function(a,b){return Date.parse(a.at)-Date.parse(b.at)});planSave();
}
function planClearDragTimers(){clearTimeout(planExpandTimer);clearTimeout(planFineTimer);planExpandTimer=null;planFineTimer=null;}
function planStopPointer(){
  var p=planPointer;planClearDragTimers();planDrag=null;planPointer=null;
  if(p&&p.pointerId!==undefined&&$('direction-list').hasPointerCapture(p.pointerId))$('direction-list').releasePointerCapture(p.pointerId);
  $('direction-list').querySelectorAll('.plan-drop-line,.plan-drag-lens').forEach(function(el){el.remove()});
  $('direction-list').querySelectorAll('.plan-dragging').forEach(function(el){el.classList.remove('plan-dragging')});
}
function planDragClock(at){return appearance.formatTime(new Intl.DateTimeFormat('en-GB',{timeZone:st.direction_timezone,hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(at)))}
function planPaintDrag(){
  var p=planPointer,box=$('direction-list');if(!p||p.at===null)return;
  var source=box.querySelector('[data-plan-index="'+p.index+'"]');if(source)source.classList.add('plan-dragging');
  var line=box.querySelector('.plan-drop-line');if(!line){line=document.createElement('div');line.className='plan-drop-line';line.innerHTML='<span></span>';box.appendChild(line);}
  var error=planDragError(p.index,p.at),y=Math.max(0,p.targetY-box.getBoundingClientRect().top+box.scrollTop);
  line.style.top=y+'px';line.classList.toggle('invalid',!!error);line.querySelector('span').textContent=planDragClock(p.at)+(error?' · unavailable':'');
  var lens=box.querySelector('.plan-drag-lens');if(!p.fine){if(lens)lens.remove();return;}
  if(!lens){lens=document.createElement('div');lens.className='plan-drag-lens';box.appendChild(lens);}
  lens.style.top=Math.max(0,Math.min(Math.max(0,box.offsetHeight-220),p.y-box.getBoundingClientRect().top-90))+'px';
  var html='<div class="lens-heading">Fine · 1 min</div>';
  for(var i=3;i>=-3;i--){var at=p.fine.base+i*60000,active=at===p.at;html+='<div class="lens-minute'+(active?' active':'')+(active&&error?' invalid':'')+'"><span>'+esc(planDragClock(at))+'</span>'+(active?'<span>←</span>':'')+'</div>';}
  lens.innerHTML=html;
}
function planQueueDragHover(target,key){
  var p=planPointer;if(!p)return;
  if(p.hoverKey===key&&Math.hypot(p.x-p.hoverX,p.y-p.hoverY)<6)return;
  planClearDragTimers();p.hoverKey=key;p.hoverX=p.x;p.hoverY=p.y;
  var hour=target.closest('summary')&&target.closest('[data-hour]'),row=target.closest('[data-plan-index]');
  if(hour&&!hour.open||row&&+row.dataset.planIndex!==p.index&&planDraft[+row.dataset.planIndex].edition&&!planOpen[planDraft[+row.dataset.planIndex].id]){
    planExpandTimer=setTimeout(function(){
      if(!planPointer||planPointer!==p||p.hoverKey!==key)return;
      if(hour){hour.open=true;planHours[hour.dataset.hour]=true;}
      else{planOpen[planDraft[+row.dataset.planIndex].id]=true;directionView(true);}
      planDragPreview(p.x,p.y);
    },450);
  }
  planFineTimer=setTimeout(function(){
    if(!planPointer||planPointer!==p||p.hoverKey!==key||p.at===null)return;
    p.at=planSnap(p.raw,1);p.fine={base:p.at,y:p.y};planPaintDrag();
  },750);
}
function planDragPreview(x,y){
  var p=planPointer,box=$('direction-list');if(!p||!planDraft)return;
  var previousY=p.y;p.x=x;p.y=y;
  if(p.fine&&Math.abs(y-previousY)>72){p.fine=null;p.hoverKey=null;box.querySelectorAll('.plan-drag-lens').forEach(function(el){el.remove()});}
  var bounds=box.getBoundingClientRect(),viewport=$('broadcast-timeline').getBoundingClientRect();
  if(x<bounds.left||x>bounds.right+80||y<Math.max(bounds.top,viewport.top)||y>Math.min(bounds.bottom,viewport.bottom)){
    planClearDragTimers();p.at=null;p.fine=null;p.hoverKey=null;box.querySelectorAll('.plan-drop-line,.plan-drag-lens').forEach(function(el){el.remove()});return;
  }
  if(p.fine){p.at=p.fine.base+Math.round((p.fine.y-y)/20)*60000;if(Math.abs(p.at-p.fine.base)>3*60000)p.fine={base:p.at,y:y};planPaintDrag();return;}
  var target=document.elementFromPoint(x,y);target=target&&target.closest('[data-plan-gap],[data-plan-index],[data-plan-at],summary[data-plan-first]');
  if(!target||!box.contains(target))return;
  var rect=target.getBoundingClientRect(),key,lower,upper;
  if(target.hasAttribute('data-plan-gap')){
    lower=+target.dataset.planLower;upper=+target.dataset.planUpper;
    var ratio=Math.min(1,Math.max(0,(y-rect.top+5)/10));p.raw=upper-(upper-lower)*ratio;p.targetY=rect.top;key='gap:'+target.dataset.planGap;
  }else if(target.matches('summary')){
    lower=planSnap(Date.parse(target.dataset.planAt||planDraft[+target.dataset.planFirst].at),60,true);upper=lower+3600e3;
    p.raw=upper-(upper-lower)*Math.min(1,Math.max(0,(y-rect.top)/Math.max(1,rect.height)));p.targetY=rect.top+rect.height/2;key='hour:'+target.parentNode.dataset.hour;
  }else{
    p.raw=Date.parse(target.dataset.planAt||planDraft[+target.dataset.planIndex].at);p.targetY=rect.top;key='row:'+target.dataset.planKey;
  }
  p.at=planSnap(p.raw,30);planQueueDragHover(target,key);planPaintDrag();
}
function planPointerDown(e){
  var row=e.target.closest('[data-plan-index]');
  if(e.button!==0||e.isPrimary===false||dirBusy||!planDraft||planInsertKey!==null||!row||!e.target.closest('.hd')||e.target.closest('.plan-inline,input,select,textarea'))return;
  var index=+row.dataset.planIndex,r=planDraft[index];if(r.locked||r.kind==='music')return;
  planPointer={index:index,pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,x:e.clientX,y:e.clientY,moved:false,at:null,fine:null,hoverKey:null};
}
function planPointerMove(e){
  var p=planPointer;if(!p)return;if(!p.moved&&Math.hypot(e.clientX-p.startX,e.clientY-p.startY)<6)return;
  if(!p.moved){$('direction-list').setPointerCapture(e.pointerId);planDrag=p.index;p.moved=true;var row=$('direction-list').querySelector('[data-plan-index="'+p.index+'"]');if(row)row.classList.add('plan-dragging');}
  e.preventDefault();planDragPreview(e.clientX,e.clientY);
}
function planPointerUp(e){
  if(!planPointer)return;var p=planPointer;if(p.moved)planDragPreview(e.clientX,e.clientY);planStopPointer();if(!p.moved)return;
  e.preventDefault();planSuppressClick=true;setTimeout(function(){planSuppressClick=false},0);
  if(p.at!==null)planSetTime(p.index,p.at);directionView();
}
function planPointerCancel(){
  if(!planPointer)return;
  if(planPointer.moved){planSuppressClick=true;setTimeout(function(){planSuppressClick=false},0);}
  planStopPointer();directionView();
}
$('plan-edit').onclick=function(){if(!st.broadcast_plan)return;planConfirm=null;planHours={};planOpen={};planHorizon=3;planDraft=JSON.parse(JSON.stringify(st.broadcast_plan.rows));planVersion=st.broadcast_plan.version;$('direction-message').textContent='';directionView();timeline();var box=$('broadcast-timeline');box.scrollTop+=Math.max(0,$('tl').getBoundingClientRect().top-box.getBoundingClientRect().top-box.clientHeight*.65)};
$('plan-done').onclick=function(){if(dirBusy)return;planDraft=null;planTimeId=null;planConfirm=null;planInsertKey=null;$('plan-add').hidden=true;$('direction-message').textContent='';directionView();timeline();$('broadcast-timeline').scrollTop=0};
$('plan-later').onclick=function(){planHorizon+=3;directionView()};
$('plan-undo').onclick=function(){if(!planUndo||dirBusy)return;dirBusy=true;directionView();api('/api/cmd',{cmd:'direction',action:'undo',token:planUndo}).then(function(d){if(!d||!d.ok)throw Error(d&&d.error||'Could not undo.');st.broadcast_plan=d.broadcast_plan;$('direction-message').textContent='Undone'}).catch(function(e){$('direction-message').textContent=e.message}).finally(function(){planUndo=null;dirBusy=false;planDraft=JSON.parse(JSON.stringify(st.broadcast_plan.rows));directionView()})};
$('direction-block').onchange=directionType;
$('direction-refresh').onclick=directionCatalog;
$('direction-now').onclick=function(){$('direction-at').value=localInput(null,true)};
$('direction-cancel').onclick=closePlanInsert;
$('direction-at').value=localInput();
$('direction-form').onsubmit=function(e){e.preventDefault();if(!planDraft||dirBusy)return;var b=dirBlocks[+$('direction-block').value];if(!b)return;
  planDraft=JSON.parse(JSON.stringify(st.broadcast_plan.rows));planVersion=st.broadcast_plan.version;
  planDraft.push({id:'new:'+Date.now(),kind:b.kind,title:b.title,text:b.kind==='text'?$('direction-text').value:'',at:new Date($('direction-at').value).toISOString(),duration:30,status:'PENDING',dirty:true});planDraft.sort(function(a,b){return Date.parse(a.at)-Date.parse(b.at)});planSave().then(function(saved){if(saved){closePlanInsert();$('direction-text').value=''}})
};
$('direction-list').onclick=function(e){if(planSuppressClick){planSuppressClick=false;return}var add=e.target.closest('[data-plan-add]');if(add&&!dirBusy){openPlanInsert(add);return}if(e.target.closest('[data-reroll]')){rerollNext();return}var button=e.target.closest('[data-plan-action]');if(!button||!planDraft||dirBusy)return;var i=+button.closest('[data-plan-index]').dataset.planIndex,r=planDraft[i];
  if(button.dataset.planAction==='segments'){planOpen[r.id]=!planOpen[r.id];directionView()}else if(button.dataset.planAction==='time'){planTimeId=planTimeId===r.id?null:r.id;directionView()}else if(button.dataset.planAction==='skip'){if(r.kind==='reminder'&&!r.skipped&&planConfirm!==r.id){planConfirm=r.id;directionView();return}r.skipConfirmed=r.kind==='reminder'&&planConfirm===r.id;planConfirm=null;r.skipped=!r.skipped;r.dirty=true;planSave()}else planMove(i,i+(button.dataset.planAction==='up'?1:-1));
};
$('direction-list').onchange=function(e){if(!planDraft||e.target.type!=='datetime-local'||!e.target.value)return;var block=e.target.closest('[data-plan-index]');if(!block)return;var i=+block.dataset.planIndex;planDraft[i].at=new Date(e.target.value).toISOString();planDraft[i].estimated=false;planDraft[i].dirty=true;planSave()};
$('direction-list').ondragstart=function(e){e.preventDefault()};
$('direction-list').onpointerdown=planPointerDown;
$('direction-list').onpointermove=planPointerMove;
$('direction-list').onpointerup=planPointerUp;
$('direction-list').onpointercancel=planPointerCancel;
$('direction-list').onlostpointercapture=planPointerCancel;
$('direction-list').onkeydown=function(e){if(e.key==='Escape'&&planPointer){e.preventDefault();planPointerCancel()}};
var ytBusy=false,ytLoaded=false,ytMessage=false;
function ytConnect(browser){if(ytBusy||!$('yt-form').reportValidity())return;ytBusy=true;$('yt-connect').disabled=$('yt-chrome').disabled=true;
  ytMessage=true;
  $('yt-state').textContent=browser?'reading the sign-in from Chrome… (allow access if macOS asks)':'connecting…';
  var cookie=browser?'':$('yt-cookie').value;$('yt-cookie').value='';
  api('/api/cmd',{cmd:'ytmusic_connect',cookie:cookie,browser:browser,playlist:$('yt-playlist').value}).then(function(d){
    if(!d.ok)throw Error(d.error||'Connection failed');$('yt-state').textContent=d.ytmusic.tracks+' songs connected';refresh()
  }).catch(function(e){$('yt-state').textContent=e.message}).finally(function(){ytBusy=false;$('yt-connect').disabled=$('yt-chrome').disabled=false})}
$('yt-form').onsubmit=function(e){e.preventDefault();ytConnect('')};
$('yt-chrome').onclick=function(){ytConnect('chrome')};
function rk(x){return RadioModel.replayKind(x)}
function sourceAnchor(ln,label){return '<a href="'+esc(ln.url)+'" title="'+esc(ln.url)+'" target="_blank" rel="noopener">'+esc(label)+'</a>'}
function segmentHtml(s){return s.url?sourceAnchor(s,s.text):esc(s.text)}
// one timeline entry from RadioModel.timeline(): kind, time, title, now, replay button, unfolded details
function row(x){
  var id=String(x.id),c=x.current,ex=x.expandable,o=ex&&!!open[id];
  var inner='<span class="tm">'+(c?'now':esc(appearance.formatTime(x.time)))+'</span><span class="k'+(c?' on':'')+'">'+(c?'● ':'')+x.kindLabel+'</span><span class="tx">'+esc(x.text)+'</span>'+(ex?'<span class="tm ch">'+(o?'▴':'▾')+'</span>':'');
  var h='<div data-history-id="'+esc(id)+'" class="it '+x.kind+(c?' cur'+(st.state==='PAUSED'?' paused':''):'')+'"><div class="hd">'+(ex?'<button class="tg" data-id="'+id+'" aria-expanded="'+o+'">'+inner+'</button>':'<div class="tg">'+inner+'</div>');
  if(x.kind==='music'&&x.replayable)
    h+=x.queued?'<button class="rp on" data-dq="'+id+'" title="remove from the queue" aria-label="remove from the queue">queued ✕</button>':'<button class="rp" data-q="'+id+'" title="play again at the next song break" aria-label="play again">↺</button>';
  h+='</div>';
  if(o){h+='<div class="ex">';
    RadioModel.entryDetails(x).forEach(function(d){
      if(d.title)h+='<p class="l pt">'+esc(d.title)+'</p>';
      if(d.segments)h+='<p class="sub">'+d.segments.map(segmentHtml).join('')+(d.sources.length?' · '+d.sources.map(function(s){return sourceAnchor(s,s.label)}).join(' · '):'')+'</p>';
      if(d.description)h+='<p class="sub">'+linkedText(d.description)+'</p>';
      d.links.forEach(function(ln){if(ln.label)h+='<p>'+sourceAnchor(ln,ln.label)+'</p>';if(ln.stars)h+='<p class="l">'+esc(ln.stars)+'</p>'});
      if(d.oldStars)h+='<p class="l">'+esc(RadioModel.OLD_STARS_NOTE)+'</p>'});
    h+='</div>'}
  return h+'</div>'}
function backgroundError(message){return RadioModel.backgroundError(message)}
function timeline(){
  if(rerollConfirm&&(!st.next_reroll||st.next_reroll.ref!==rerollConfirm||!st.next_reroll.available||st.next_reroll.pending))rerollConfirm=null;
  var a=document.activeElement&&document.activeElement.dataset&&document.activeElement.dataset.id;
  function trackLabels(tags){if(!tags)return '';return (tags.announced?'<span class="l announced-track"> · announced</span>':'')+(tags.set?'<span class="l"> · set</span>':'')+(tags.energy?'<span class="l"> · energy '+esc(tags.energy)+'/5'+(tags.genre?' · '+esc(tags.genre):'')+'</span>':'')}
  var up=RadioModel.upcoming(st,rerollError),next=up.next;
  $('upcoming').hidden=!next||!!planDraft;
  var rerollFocused=$('upcoming').contains(document.activeElement)&&document.activeElement.hasAttribute('data-reroll'),rerollAction=rerollFocused?document.activeElement.dataset.reroll:null;
  $('upcoming').innerHTML=next?'<div class="tg"><span class="tm">next</span><span class="tx'+(up.pending?' pend':'')+'" title="'+esc(up.title)+'">'+esc(up.title)+(up.pending?' <span class="l">· loading…</span>':trackLabels(up.tags))+'</span>'+(up.reroll?rerollButton():'')+'</div>'+(up.error?'<div class="tg"><span class="tm"></span><span class="tx up-err" role="alert">'+esc(up.error)+'</span></div>':''):'';
  if(rerollFocused){var button=$('upcoming').querySelector('[data-reroll="'+rerollAction+'"]')||$('upcoming').querySelector('[data-reroll]');if(button)button.focus({preventScroll:true})}
  var sc=$('tl').scrollTop;
  var tl=RadioModel.timeline({timeline:items,status:st},running()),aired=tl.entries,activity=tl.activity,background=tl.background;
  if(background&&(!timeline.backgroundNotice||timeline.backgroundNotice.text!==background))timeline.backgroundNotice={text:background,
    time:new Date().toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:st.direction_timezone||'Asia/Ho_Chi_Minh'})};
  if(!background)timeline.backgroundNotice=null;
  $('activity').hidden=!activity.length&&!background;
  $('activity-log').innerHTML=(background?'<div class="tg"><span class="tm" title="First observed in this tab">'+esc(appearance.formatTime(timeline.backgroundNotice.time))+'</span><span class="tx">'+esc(background)+'</span></div>':'')+activity.map(function(x){return '<div class="tg"><span class="tm">'+esc(appearance.formatTime(x.time))+'</span><span class="tx">Radio started</span></div>'}).join('');
  $('history-more').hidden=aired.length<=tl.shown;$('history-more').textContent=historyExpanded?'Show less':'Show earlier';
  var history=$('tl'),latest=aired.reduce(function(id,x){return Math.max(id,x.id)},0),previous=timeline.latest;
  timeline.latest=latest;
  var html=aired.length?(historyExpanded?aired:aired.slice(0,tl.shown)).map(row).join(''):'<p class="l">nothing has aired yet</p>';
  if(timeline.html!==html){
    timeline.html=html;
    var effect=document.documentElement.dataset.timelineEffect||'line';
    var printing=effect!=='off'&&previous!==undefined&&latest>previous&&!planDraft&&!document.hidden&&!$('tl-panel').hidden&&!matchMedia('(prefers-reduced-motion:reduce)').matches;
    var positions=new Map();
    if(printing)Array.from(history.children).forEach(function(el){positions.set(el.dataset.historyId,el.getBoundingClientRect().top)});
    history.innerHTML=html;
    if(printing)Array.from(history.children).forEach(function(el){
      var old=positions.get(el.dataset.historyId),fresh=+el.dataset.historyId>previous;
      var offset=fresh?el.getBoundingClientRect().height:old===undefined?0:old-el.getBoundingClientRect().top;
      if(effect==='fade'?fresh:offset){
        el.style.overflow='clip';
        var frames=effect==='fade'?[{opacity:0},{opacity:1}]:[{transform:'translateY('+offset+'px)',opacity:fresh?0:1},{transform:'translateY(0)',opacity:1}];
        var animation=el.firstElementChild.animate(frames,{duration:240,easing:'cubic-bezier(.16,1,.3,1)'});
        animation.onfinish=animation.oncancel=function(){el.style.removeProperty('overflow')};
      }
    });
  }
  $('tl').scrollTop=sc;
  if(a){var e=$('tl').querySelector('[data-id="'+a+'"]');if(e)e.focus({preventScroll:true})}}

$('history-more').onclick=function(){historyExpanded=!historyExpanded;timeline()};
$('activity').onkeydown=function(e){if(e.key==='Escape'){this.open=false;this.querySelector('summary').focus()}};
$('tl').ondragover=function(e){if(planDrag!==null){e.preventDefault();e.dataTransfer.dropEffect='none'}};
$('tl').ondrop=function(e){e.preventDefault();planDrag=null};
function cmd(name,extra){
  if(busy)return Promise.resolve(null);busy=true;nextBusy=name==='next';player();
  var body={cmd:name};for(var k in extra||{})body[k]=extra[k];
  return api('/api/cmd',body).then(function(r){if(r&&r.ok===false)$('er').textContent=r.error||'did not work';return r})
    .catch(function(){$('er').textContent='the radio is not answering';return null})
    .then(function(r){busy=false;nextBusy=false;player();if(name==='next'&&r&&r.ok){catchUp();setTimeout(refresh,150);setTimeout(refresh,500)}refresh();return r})}
function replay(id){
  var x=items.find(function(i){return String(i.id)===id});if(!x)return;
  if(!running()){$('er').textContent='start the radio first';return}
  cmd('replay',{kind:rk(x),ref:x.ref})}
function unqueue(id){
  var x=items.find(function(i){return String(i.id)===id});if(x)cmd('unqueue',{kind:rk(x),ref:x.ref})}
var catchupBusy=false,catchupMessage='';
function catchupView(info){
  var c=RadioModel.catchup(info,running(),catchupBusy,catchupMessage);
  $('catchup-controls').hidden=!c;
  if(!c)return;
  $('catchup-build').disabled=c.disabled;
  $('catchup-build').title=c.title;
  $('catchup-status').textContent=c.status;
}
$('catchup-build').onclick=async function(){
  catchupBusy=true;catchupMessage='';catchupView(st.catchup);
  $('catchup-status').textContent=RadioModel.CATCHUP_PREPARING;
  try{
    var result=await api('/api/cmd',{cmd:'catchup'});
    if(!result.ok)throw Error(result.error||'Could not prepare catch-up');
    st.catchup=result.catchup;catchupView(result.catchup);
  }catch(error){catchupMessage=error.message;}
  finally{catchupBusy=false;catchupView(st.catchup);}
};
function refresh(){return core.refresh()}
var shownError='';  // the poll error this page put in #er, cleared when a poll works again
function applySnapshot(d){
    if(shownError&&$('er').textContent===shownError)$('er').textContent='';
    shownError='';
    st=d.status;catchupView(st.catchup);
    if(!running()&&listening)unlisten();
    if(d.audio_preset&&document.activeElement!==$('audio-preset')){
      $('audio-preset').value=d.audio_preset.selected;
      $('audio-preset').options[1].disabled=!d.audio_preset.personal_available;
    }
    if(d.ytmusic){
      ytStatus=d.ytmusic;
      if(!ytLoaded&&document.activeElement!==$('yt-playlist')){$('yt-playlist').value=d.ytmusic.playlist;ytLoaded=true}
      if(!ytBusy&&!ytMessage&&d.ytmusic.connected)$('yt-state').textContent=d.ytmusic.tracks+' songs connected';
    }
    items=d.timeline||[];pls=d.plugins||[];stg=d.settings||[];musicChoice=d.music_source_choice||'local';player();timeline();pluginsView();settingsView();directionView();hooksView(d.hooks)
}
core.subscribe(function(core,change){
  if(change==='snapshot')applySnapshot(core.snapshot);
  else if(change==='error'){shownError=core.error;$('er').textContent=shownError}});

function selectTab(b){
  if(b.dataset.tab!=='tl'&&planDraft&&!dirBusy)$('plan-done').onclick();
  document.querySelectorAll('[data-tab]').forEach(function(t){var on=t===b;t.setAttribute('aria-selected',on);t.tabIndex=on?0:-1;$(t.getAttribute('aria-controls')).hidden=!on});
  if(b.dataset.tab==='tl')$('plan-edit').removeAttribute('data-off');else $('plan-edit').setAttribute('data-off','')}
$('tab-tl').parentNode.onclick=function(e){var b=e.target.closest('[data-tab]');if(b)selectTab(b)};
$('tab-tl').parentNode.onkeydown=function(e){
  var tabs=Array.from(this.querySelectorAll('[data-tab]')),i=tabs.indexOf(e.target);if(i<0)return;
  if(e.key==='ArrowRight')i=(i+1)%tabs.length;else if(e.key==='ArrowLeft')i=(i+tabs.length-1)%tabs.length;
  else if(e.key==='Home')i=0;else if(e.key==='End')i=tabs.length-1;else return;
  e.preventDefault();selectTab(tabs[i]);tabs[i].focus()};
$('tl').onclick=function(e){var q=e.target.closest('[data-q]');if(q){replay(q.dataset.q);return}
  var dq=e.target.closest('[data-dq]');if(dq){unqueue(dq.dataset.dq);return}
  var h=e.target.closest('[data-id]');if(h){open[h.dataset.id]=!open[h.dataset.id];timeline()}};
$('pp').onclick=function(){
  if(!running()||(!browser()&&busy))return;
  if(!browser())return cmd(st.state==='PLAYING'?'pause':'resume');
  listening?unlisten():listen()};
$('upcoming').onclick=function(e){var b=e.target.closest('[data-reroll]');if(b)rerollNext(b.dataset.reroll,b.dataset.ref)};
$('upcoming').onkeydown=function(e){if(e.key==='Escape'&&rerollConfirm){e.preventDefault();rerollNext('cancel')}};
$('music-source-select').onchange=function(){musicChoice=this.value;cmd('setting',{key:'music_source',value:this.value})};
$('nx').onclick=function(){cmd('next')};
$('sk').onclick=function(){cmd('skip-news')};
$('so').onclick=function(){if(LOCAL_CONTROL&&!busy&&st.state!=='STOPPING')cmd(running()?'stop':'play')};
// 2-second refresh: re-renders from state, so expanded rows (open) stay open
setInterval(player,250);
// the theme picker (Settings → Appearance): every theme the server has, this one selected
function themePicker(){
  var box=$('theme-select');
  box.innerHTML=core.themes().map(function(t){return '<option value="'+esc(t.name)+'"'+(t.current?' selected':'')+'>'+esc(t.title)+'</option>'}).join('');
  $('theme-note').textContent=core.themeNote}
$('theme-select').onchange=function(){core.setTheme(this.value)};
core.subscribe(function(core,change){if(change==='capabilities')themePicker()});
themePicker();
// a Details box stays open across redraws (toggle does not bubble: caught on the way down)
root.addEventListener('toggle',function(e){var d=e.target;if(d.classList&&d.classList.contains('control-help'))(helpText.open=helpText.open||{})[d.id.slice(5)]=d.open},true);
core.start();
/* ---- visualizer: audio-driven braille patterns under the title.
   Feed: radioViz.set(32 spectrum values 0..1, dw() waveform values 0..1). No feed (no sound): it settles into a flat
   muted line and the loop stops. ---- */
document.addEventListener('visibilitychange',function(){document.documentElement.dataset.pageHidden=String(document.hidden)});
document.documentElement.dataset.pageHidden=String(document.hidden);
document.documentElement.dataset.mastVisible='false';
if('IntersectionObserver' in window)new IntersectionObserver(function(entries){document.documentElement.dataset.mastVisible=String(entries[0].isIntersecting)}).observe($('station-ident'));
appearance.mount();

var radioViz=(function(){
  var CELLS=64,DW=128,ROWS=2,DH=8,N=32,spec=new Array(N).fill(0),wave=new Array(DW).fill(.5),mode='pulse',fed=0,run=false,
    el=$('viz'),BIT=[[1,2,4,64],[8,16,32,128]],MODES=['pulse','scope','bars','peaks','fall','vu','ribbon'],enabled=true,peaks=new Array(N).fill(0),history=[],
    motion=matchMedia('(prefers-reduced-motion:reduce)'),still=motion.matches;
  try{mode=localStorage.getItem('viz2')||'pulse';enabled=localStorage.getItem('vizEnabled')!=='false'&&mode!=='off'}catch(e){}
  if(MODES.indexOf(mode)<0)mode='pulse';
  // cells follow the box width (measured braille glyph), so the line never wraps or scrolls sideways
  function layout(){var cw=$('ruler').getBoundingClientRect().width/20||9.6,w=el.parentNode.clientWidth,
    c=Math.max(16,Math.min(96,Math.floor(w/cw)));if(c!==CELLS){CELLS=c;DW=c*2;wave=new Array(DW).fill(.5);draw()}}
  function braille(g){var s='';for(var cy=0;cy<ROWS;cy++){for(var cx=0;cx<CELLS;cx++){var m=0;for(var dx=0;dx<2;dx++)for(var dy=0;dy<4;dy++)if(g[cy*4+dy][cx*2+dx])m|=BIT[dx][dy];s+=String.fromCharCode(0x2800+m)}s+='\n'}return s.slice(0,-1)}
  function grid(){var g=[];for(var y=0;y<DH;y++)g.push(new Array(DW).fill(false));return g}
  function band(x){var p=x/(DW-1)*(N-1),a=Math.floor(p),b=Math.min(N-1,a+1),f=p-a;return spec[a]*(1-f)+spec[b]*f}
  function pulse(){var g=grid();for(var x=0;x<DW;x++){var h=Math.ceil(band(x)*DH/2);for(var y=0;y<DH;y++){var d=y<DH/2?DH/2-1-y:y-DH/2;if(d===0||d<h)g[y][x]=true}}return braille(g)}
  function scope(){var g=grid(),py=null;for(var x=0;x<DW;x++){var y=Math.max(0,Math.min(DH-1,Math.round((1-wave[x])*(DH-1))));g[y][x]=true;if(py!==null){for(var k=Math.min(py,y);k<=Math.max(py,y);k++)g[k][x]=true}py=y}return braille(g)}
  function bars(withPeaks){var g=grid();for(var x=0;x<DW;x++){
    var i=Math.min(N-1,Math.floor(x/DW*N));if(x%4===3)continue;
    var h=Math.round(spec[i]*DH);for(var y=DH-h;y<DH;y++)g[y][x]=true;
    if(withPeaks&&peaks[i]>.02)g[Math.max(0,DH-Math.ceil(peaks[i]*DH))][x]=true
  }return braille(g)}
  function fall(){var g=grid();for(var y=0;y<history.length;y++)for(var x=0;x<DW;x++){
    var v=history[y][Math.min(N-1,Math.floor(x/DW*N))];
    g[y][x]=v>.02&&v>((x%2)*2+y%2+1)/5
  }return braille(g)}
  function vu(){var g=grid();for(var side=0;side<2;side++){
    var start=side*Math.floor(DW/2),end=side?DW:Math.floor(DW/2),sum=0;
    for(var x=start;x<end;x++)sum+=Math.pow((wave[x]-.5)*2,2);
    var level=Math.min(1,Math.sqrt(sum/(end-start))*2.5);
    for(var x=start;x<start+Math.round(level*(end-start));x++)if((x-start)%4!==3)
      for(var y=side*4+1;y<side*4+3;y++)g[y][x]=true
  }return braille(g)}
  function ribbon(){var g=grid();for(var x=0;x<DW;x++){
    var h=Math.min(DH/2-1,Math.round(Math.abs(wave[x]-.5)*(DH-2)));
    g[DH/2-1-h][x]=true;g[DH/2+h][x]=true
  }return braille(g)}
  function quiet(){return spec.every(function(v){return v<.02})}
  function draw(){el.className=quiet()?'idle':'';el.textContent=!enabled?'':
    mode==='pulse'?pulse():mode==='scope'?scope():mode==='bars'?bars(false):
    mode==='peaks'?bars(true):mode==='fall'?fall():mode==='vu'?vu():ribbon()}
  function mark(){var b=$('vzb');b.textContent=mode;b.setAttribute('aria-pressed',String(enabled));
    b.setAttribute('aria-label',(enabled?'Disable':'Enable')+' visualizer: '+mode);b.title=enabled?'Disable visualizer':'Enable visualizer'}
  function reset(){spec.fill(0);wave.fill(.5);peaks.fill(0);history=[];fed=0}
  function change(step){mode=MODES[(MODES.indexOf(mode)+step+MODES.length)%MODES.length];
    try{localStorage.setItem('viz2',mode)}catch(x){}mark();draw()}
  $('vzb').onclick=function(){enabled=!enabled;
    try{localStorage.setItem('vizEnabled',String(enabled));localStorage.setItem('viz2',mode)}catch(x){}
    if(!enabled)reset();mark();draw()};
  $('vzprev').onclick=function(){change(-1)};
  $('vznext').onclick=function(){change(1)};
  // History and peak decay advance at 20 fps, independent of the audio feed rate.
  function tick(now){if(still||document.hidden||!enabled){reset();draw();run=false;return}
    var live=fed&&now-fed<=500;
    if(!live){for(var i=0;i<N;i++)spec[i]*=.8;for(var j=0;j<DW;j++)wave[j]+=(.5-wave[j])*.2}
    for(var i=0;i<N;i++)peaks[i]=Math.max(spec[i],peaks[i]-.035);
    history.unshift(spec.slice());history.length=Math.min(DH,history.length);
    if(!live&&quiet()&&wave.every(function(v){return Math.abs(v-.5)<.01})&&peaks.every(function(v){return v<.02})){
      reset();draw();run=false;return}
    draw();setTimeout(function(){requestAnimationFrame(tick)},50)}
  motion.addEventListener('change',function(e){still=e.matches;if(still){reset();draw()}});
  window.addEventListener('resize',layout);
  mark();layout();draw();
  return{dw:function(){return DW},
    set:function(a,w){if(still||document.hidden||!enabled)return;fed=performance.now();for(var i=0;i<N;i++)spec[i]=a[i]||0;for(var j=0;j<DW;j++)wave[j]=w[j]==null?.5:w[j];
      if(!run){run=true;requestAnimationFrame(tick)}}}})();
}
return {mount:mount};
})();
