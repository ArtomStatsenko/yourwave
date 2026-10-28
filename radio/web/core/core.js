// Yourwave page core: talks to the radio's API and keeps the shared state. No look and no DOM: a theme
// (radio/web/themes/<name>/view.js) renders from it. Contract: docs/ui-themes.md.
var RadioCore=(function(){
  var POLL_MS=2000;
  // What each kind of air entry is called, the same in every theme (and in the phone's Media Session).
  var KINDS={music:'song',news:'host edition',host:'host',id:'jingle',reminder:'reminder',weather:'weather',session:'on air'};

  // Listening on this device: the station's live stream (/stream, same origin so Web Audio can read it) through
  // an analyser (the spectrum) and a gain (the volume). Pause and volume are this page's own; next / stop /
  // replay are station commands for every listener.
  function createAudio(core,emit){
    var au=null,ac=null,an=null,gn=null,fb=null,tb=null,lastT=-1,stuck=0,mtitle=null,volume=1;
    try{var saved=localStorage.getItem('vol');if(saved!==null)volume=+saved}catch(e){}
    var audio={listening:false,volume:volume,
      onnext:null};  // the phone's "next track" button; a theme may route it through its own busy state
    function connect(){au.src='/stream?'+Date.now();au.play().catch(function(){});lastT=-1;stuck=0}
    audio.listen=function(){
      var was=audio.listening;audio.listening=true;
      if(!au){au=new Audio();ac=new (window.AudioContext||window.webkitAudioContext)();
        var src=ac.createMediaElementSource(au);an=ac.createAnalyser();an.fftSize=2048;an.smoothingTimeConstant=.6;an.maxDecibels=-10;
        gn=ac.createGain();src.connect(an);an.connect(gn);gn.connect(ac.destination);fb=new Uint8Array(an.frequencyBinCount);tb=new Uint8Array(an.fftSize);
        gn.gain.value=audio.volume}
      ac.resume();
      connect();
      if(!was)emit('listening')};
    audio.unlisten=function(){audio.listening=false;au.pause();au.removeAttribute('src');au.load();emit('listening')};
    audio.setVolume=function(v){audio.volume=v;if(gn)gn.gain.value=v;try{localStorage.setItem('vol',v)}catch(e){}};
    // Seconds of stream buffered ahead of what is heard: the page shows the air that late.
    audio.lag=function(){if(!audio.listening||!au||!au.buffered.length)return 0;return Math.max(0,au.buffered.end(au.buffered.length-1)-au.currentTime)};
    // After "next": jump to the live edge, keeping a small playable reserve instead of reopening the stream.
    audio.catchUp=function(){
      if(!audio.listening||!au||!au.buffered.length)return;
      var end=au.buffered.end(au.buffered.length-1);
      if(end-au.currentTime>.5){try{au.currentTime=end-.25}catch(e){}}};
    // A stream that stopped moving (station restarted, network hiccup) is reopened: it rejoins the live air.
    audio.watchdog=function(){if(!audio.listening||!core.running())return;
      if(au.currentTime===lastT){if(++stuck>=2)connect()}else{lastT=au.currentTime;stuck=0}};
    // What is heard now: 32 log-spaced bands, 40 Hz .. 16 kHz, and a waveform of waveLength points, all 0..1;
    // null while nothing plays.
    audio.read=function(waveLength){
      if(!audio.listening||au.paused||au.readyState<3)return null;
      an.getByteFrequencyData(fb);var a=[],ny=ac.sampleRate/2,n=fb.length;
      for(var i=0;i<32;i++){var lo=Math.floor(40*Math.pow(400,i/32)/ny*n),hi=Math.max(lo+1,Math.floor(40*Math.pow(400,(i+1)/32)/ny*n)),m=0;
        for(var j=lo;j<hi&&j<n;j++)m=Math.max(m,fb[j]);a.push(m/255)}
      an.getByteTimeDomainData(tb);var w=[];
      for(var x=0;x<waveLength;x++)w.push(tb[Math.floor(x/waveLength*tb.length)]/255);
      return {bands:a,wave:w}};
    // The phone's lock screen and headphones: the title on air, play / pause / stop / next.
    audio.media=function(ds){
      if(!('mediaSession' in navigator))return;
      var t=core.running()?(ds.current_title||''):'';if(t===mtitle)return;mtitle=t;
      var p=t.split(' - '),music=ds.current_kind==='music'&&p.length>1;
      navigator.mediaSession.metadata=new MediaMetadata({title:music?p.slice(1).join(' - '):t||'Yourwave',
        artist:music?p[0]:KINDS[ds.current_kind]||'',album:'Yourwave'})};
    if(typeof navigator!=='undefined'&&'mediaSession' in navigator){var ms=navigator.mediaSession;
      ms.setActionHandler('play',function(){if(!audio.listening&&core.running())audio.listen()});
      ms.setActionHandler('pause',function(){if(audio.listening)audio.unlisten()});
      try{ms.setActionHandler('stop',function(){if(audio.listening)audio.unlisten()});
        ms.setActionHandler('nexttrack',function(){audio.onnext?audio.onnext():core.command('next')})}catch(e){}}
    return audio;
  }

  function create(options){
    options=options||{};
    var token=options.token||'',fetchImpl=options.fetch||function(url,init){return fetch(url,init)};
    var listeners=[],sequence=0,applied=0,timer=null,history=[];  // history: recent statuses, about 30 s
    var core={
      localControl:!!options.localControl,  // false on the phone port: play and stop belong to the Mac
      snapshot:null,      // the last /api/status answer: {status, timeline, plugins, settings, ...}
      error:'',           // why the last poll failed ('' when the last one worked)
      capabilities:null,  // /api/capabilities: operations, reads, themes
      commands:{},        // one function per operation in capabilities, e.g. core.commands.next()
      kinds:KINDS
    };
    // The station is on air (playing or paused), by the last snapshot.
    core.running=function(){var s=core.snapshot&&core.snapshot.status;return !!s&&(s.state==='PLAYING'||s.state==='PAUSED')};

    // A request with the page token (e.g. an audio preview, which is not JSON).
    core.fetch=function(path,init){
      init=init||{};var headers={'X-Radio-Token':token};for(var k in init.headers||{})headers[k]=init.headers[k];
      var merged={};for(var j in init)merged[j]=init[j];merged.headers=headers;
      return fetchImpl(path,merged)};

    // One JSON request. Resolves with the parsed body, also for an error status.
    core.api=function(path,body){
      return core.fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},
        body:body?JSON.stringify(body):undefined}).then(function(r){return r.json()})};

    // A station command: {cmd: name, ...fields}. Resolves with the radio's answer ({ok, error, ...}).
    core.command=function(name,fields){
      var body={cmd:name};for(var k in fields||{})body[k]=fields[k];
      return core.api('/api/cmd',body)};

    // fn(core, change): change is 'snapshot' (a new answer), 'error' (a poll failed), 'capabilities', or
    // 'listening' (this device started or stopped listening).
    core.subscribe=function(fn){listeners.push(fn);return function(){listeners=listeners.filter(function(l){return l!==fn})}};
    function emit(change){listeners.slice().forEach(function(fn){fn(core,change)})}

    // One poll. A late answer never replaces a newer one; a failed poll keeps the last good snapshot.
    core.refresh=function(){
      var mine=++sequence;
      return core.api('/api/status').then(function(d){
        if(mine<applied)return;
        if(!d.status)throw Error(d.error||'the page cannot reach the radio');
        applied=mine;core.error='';core.snapshot=d;
        history.push({t:Date.now(),s:d.status});while(history.length>2&&history[1].t<Date.now()-30000)history.shift();
        core.audio.watchdog();emit('snapshot')})
      .catch(function(error){if(mine>=applied){core.error=error.message||'the page cannot reach the radio';emit('error')}})};

    core.audio=createAudio(core,emit);
    // What this device hears now, {t: when it was the air, s: the status then}: a listener's buffer is a few
    // seconds behind the station, so the page shows the air that late. fallback: the status before any poll.
    core.heard=function(fallback){var now=Date.now(),lg=core.audio.lag(),h=history.filter(function(e){return e.t<=now-lg*1000});
      return h[h.length-1]||history[0]||{t:now,s:fallback||(core.snapshot&&core.snapshot.status)||{state:'STOPPED'}}};

    core.loadCapabilities=function(){
      return core.api('/api/capabilities').then(function(c){
        if(!c||!c.operations)return;
        core.capabilities=c;core.commands={};
        Object.keys(c.operations).forEach(function(name){core.commands[name]=function(fields){return core.command(name,fields)}});
        emit('capabilities')}).catch(function(){})};

    // Polls every 2 s from now on; the first answer comes at once.
    core.start=function(){if(!timer){timer=setInterval(core.refresh,POLL_MS);core.refresh();core.loadCapabilities()}return core};
    core.stop=function(){clearInterval(timer);timer=null};
    return core;
  }

  return {create:create,KINDS:KINDS};
})();
