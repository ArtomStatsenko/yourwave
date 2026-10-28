// Yourwave page core: talks to the radio's API and keeps the shared state. No look and no DOM: a theme
// (radio/web/themes/<name>/view.js) renders from it. Contract: docs/ui-themes.md.
var RadioCore=(function(){
  var POLL_MS=2000;

  function create(options){
    options=options||{};
    var token=options.token||'',fetchImpl=options.fetch||function(url,init){return fetch(url,init)};
    var listeners=[],sequence=0,applied=0,timer=null;
    var core={
      localControl:!!options.localControl,  // false on the phone port: play and stop belong to the Mac
      snapshot:null,      // the last /api/status answer: {status, timeline, plugins, settings, ...}
      error:'',           // why the last poll failed ('' when the last one worked)
      capabilities:null,  // /api/capabilities: operations, reads, themes
      commands:{}         // one function per operation in capabilities, e.g. core.commands.next()
    };

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

    // fn(core, change): change is 'snapshot' (a new answer), 'error' (a poll failed) or 'capabilities'.
    core.subscribe=function(fn){listeners.push(fn);return function(){listeners=listeners.filter(function(l){return l!==fn})}};
    function emit(change){listeners.slice().forEach(function(fn){fn(core,change)})}

    // One poll. A late answer never replaces a newer one; a failed poll keeps the last good snapshot.
    core.refresh=function(){
      var mine=++sequence;
      return core.api('/api/status').then(function(d){
        if(mine<applied)return;
        if(!d.status)throw Error(d.error||'the page cannot reach the radio');
        applied=mine;core.error='';core.snapshot=d;emit('snapshot')})
      .catch(function(error){if(mine>=applied){core.error=error.message||'the page cannot reach the radio';emit('error')}})};

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

  return {create:create};
})();
