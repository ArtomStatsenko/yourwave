// Yourwave page model: what a theme shows, computed from the core's snapshot, with the words it uses.
// No markup and no DOM: every theme says the same thing in its own look. Contract: docs/ui-themes.md.
var RadioModel=(function(){
  var KINDS=RadioCore.KINDS;
  var HISTORY_SHOWN=15;  // timeline entries before "Show earlier"

  function num(n){return n>=1000?(n/1000).toFixed(n>=10000?0:1).replace(/\.0$/,'')+'k':String(n)}

  // ---- timeline: what aired, newest first

  // An entry's title: a Russian title of a spoken insert is not shown, its kind is.
  function entryTitle(kind,title){if(kind==='news'||kind==='host')return 'Host edition';return kind!=='music'&&/[А-Яа-яЁё]/.test(title)?({reminder:'Reminder',weather:'Local forecast',id:'Station ID',jingle:'Jingle'}[kind]||title):title}
  // A station error the listener did not cause (it shows under Activity, not as a warning).
  function backgroundError(message){return /^(?:render |assembly |content:|reminders?:|VoiceStudio unavailable)/i.test(message||'')}
  // A host edition and its teaser, or editions back to back, are one entry with several parts.
  function hostHistory(events){
    var rows=[];
    events.slice().reverse().forEach(function(event){
      var previous=rows[rows.length-1];
      if(previous&&((event.type==='MUSIC_TEASER'&&['PACKAGE_STARTED','REPLAY'].includes(previous.type))
          ||event.type==='PACKAGE_STARTED'&&previous.type==='PACKAGE_STARTED')){
        rows[rows.length-1]=Object.assign({},previous,{parts:(previous.parts||[]).concat(event.parts||[]),
          kind:previous.kind==='news'||event.kind==='news'?'news':'host',event_ids:(previous.event_ids||[previous.id]).concat(event.id)});
      }else rows.push(event);
    });
    return rows.reverse();
  }
  // The entry on air now: the newest one (or a part of it) while the station runs.
  function isCurrent(x,items,running){var current=items.find(function(i){return i.kind!=='session'});return running&&(x===current||!!current&&(x.event_ids||[]).includes(current.id))}
  function replayKind(x){return 'music'}
  // A replay waiting in the station's real queue (it survives a reload; one that went on air drops out).
  function isQueued(x,status){return (status.replay_queue||[]).some(function(q){return q.kind===replayKind(x)&&q.ref===x.ref})}
  function entryText(x){return entryTitle(x.kind,x.title)+(x.replay?' · replay':'')+(x.outcome==='SKIPPED'?' · skipped':'')}
  // The parts an entry unfolds into: a host edition's stories, or a reminder's own text.
  function entryParts(x){return x.parts||(x.kind==='reminder'?[{text:x.title,description:x.description,links:x.links||[]}]:[])}
  function expandable(x){return entryParts(x).some(function(p){return p.text||p.description||(p.links||[]).length})}

  // How a source link is named: its title, a Reddit post's slug, or the site.
  function sourceLabel(ln){
    var label=ln.title,address=ln.url.replace(/^https?:\/\/(?:www\.)?/,''),post=address.match(/^reddit\.com\/r\/[^/]+\/comments\/[^/]+\/([^/?#]+)/);
    if(post&&(!label||/^Reddit:/i.test(label)))label='Reddit · '+post[1].replace(/_/g,' ');
    return !label||label.replace(/^https?:\/\/(?:www\.)?/,'')===address?'Source · '+address.split(/[/?#]/)[0]:label;
  }
  // A story's text in pieces, in order: {text} or {text, link} where the words name one of its sources
  // ("MLX-Serve" -> the Reddit post about it). used: urls already linked in this entry, one link per source.
  function newsSegments(p,used){
    var text=p.text||'',spans=[],words=Array.from(text.matchAll(/[\p{L}\p{N}]+(?:[-.][\p{L}\p{N}]+)*/gu));
    var normalize=s=>s.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu,'');
    (p.links||[]).forEach(function(ln){
      if(!/^https?:\/\//.test(ln.url))return;
      var url;try{url=new URL(ln.url)}catch(_){return}
      var slug=url.pathname.split('/').filter(Boolean).pop()||'';
      var names=[ln.title||'',url.hostname.replace(/^www\./,'')];
      if(/(^|\.)reddit\.com$/.test(url.hostname))names.push(slug.replace(/_/g,' '));
      if(url.hostname==='github.com')names.push(url.pathname.split('/')[2]||'');
      var best=null;
      names.forEach(function(name){
        var tokens=(name.match(/[\p{L}\p{N}]+(?:[-.][\p{L}\p{N}]+)*/gu)||[]).map(normalize);
        for(var n=tokens.length;n>0;n--){
          if(n===1&&(tokens[0].length<3||/^(the|new|how|why|news|reddit|github|новости|проект|обсуждение)$/.test(tokens[0])))continue;
          for(var i=0;i<=words.length-n;i++){
            if(!tokens.slice(0,n).every((token,k)=>token===normalize(words[i+k][0])))continue;
            var start=words[i].index,end=words[i+n-1].index+words[i+n-1][0].length;
            if(spans.some(s=>start<s.end&&end>s.start))continue;
            if(!best||n>best.size)best={start,end,size:n,link:ln};
          }
        }
      });
      if(best){spans.push(best);used.add(ln.url)}
    });
    var at=0,out=[];
    spans.sort((a,b)=>a.start-b.start).forEach(function(s){out.push({text:text.slice(at,s.start)},{text:text.slice(s.start,s.end),link:s.link});at=s.end});
    out.push({text:text.slice(at)});
    return out;
  }
  // "★ 12.3k (+45 today) · original description" for a GitHub source; '' without numbers.
  function starsText(ln){return ln.stars_total?'★ '+num(ln.stars_total)+(ln.stars_today?' (+'+num(ln.stars_today)+' today)':'')+(ln.original?' · '+ln.original:''):''}
  var OLD_STARS_NOTE='stars and description were not saved for older bulletins';

  // The unfolded details of an entry, part by part, in reading order:
  // {title, segments (the text, news mentions linked), sources (links shown after the text), description,
  //  links (the remaining links, each with its stars text), oldStars (an old GitHub bulletin without numbers)}.
  function entryDetails(x){
    var P=entryParts(x),used=new Set(),news=x.kind==='news';
    var segments=P.map(function(p){return news?newsSegments(p,used):[{text:p.text||''}]});
    return P.map(function(p,k){
      var d={title:P.length>1&&p.title&&!['news','host'].includes(x.kind)?(k+1)+'. '+p.title:'',segments:p.text?segments[k]:null,
        sources:[],description:p.description||'',links:[],oldStars:false};
      if(p.text&&news)(p.links||[]).forEach(function(ln){
        if(/^https?:\/\//.test(ln.url)&&!used.has(ln.url)){d.sources.push({url:ln.url,label:sourceLabel(ln)});used.add(ln.url)}});
      (p.links||[]).forEach(function(ln){
        if(!/^https?:\/\//.test(ln.url))return;
        var link={url:ln.url,label:'',stars:starsText(ln)};
        if(!used.has(ln.url)){link.label=sourceLabel(ln);used.add(ln.url)}
        if(link.label||link.stars)d.links.push(link);
        if(!ln.stars_total&&p.source==='github_trending')d.oldStars=true;
      });
      return d;
    });
  }

  // One aired event as a timeline entry: the event's own fields plus current (on air now), queued (a replay
  // waiting), kindLabel, text (the title line) and expandable (it has details to unfold).
  function entry(x,items,running,status){
    return Object.assign({},x,{current:isCurrent(x,items,running),queued:isQueued(x,status||{}),kindLabel:KINDS[x.kind],
      text:entryText(x),expandable:expandable(x)})}
  // The timeline for a snapshot: entries (newest first; the theme shows the first HISTORY_SHOWN until asked for
  // more), the radio's own starts (Activity), and a background error worth showing there.
  function timeline(snapshot,running){
    var items=snapshot.timeline||[],status=snapshot.status||{};
    var aired=hostHistory(items).filter(function(x){return x.kind!=='session'}).map(function(x){return entry(x,items,running,status)});
    return {entries:aired,shown:HISTORY_SHOWN,activity:items.filter(function(x){return x.kind==='session'}),
      background:backgroundError(status.last_error)?status.last_error:''};
  }

  // ---- next: what airs at the next boundary

  // The next entry (a queued replay, the planned next block, or the next song), its title, the song's tags,
  // and the reroll state: pending (a picked song still loading) and the last reroll error.
  // localError: an error this page got from its own reroll request.
  function upcoming(status,localError){
    var tracks=status.upcoming_tracks||[],list=status.upcoming||[];
    var next=list.find(function(x){return x.stage==='queued'})||list.find(function(x){return x.stage==='next'})||list.find(function(x){return x.stage==='music'});
    var nr=status.next_reroll||{},pend=nr.pending;
    return {next:next||null,title:pend?pend.title:entryTitle(next&&next.kind,next&&next.title),pending:!!pend,
      tags:next?trackTags(next,status,tracks.length&&tracks[0].ref===next.ref&&tracks[0].set):null,
      reroll:!!(next&&next.kind==='music'&&status.next_reroll&&next.ref===status.next_reroll.ref),
      error:localError||nr.error||''};
  }
  // A song's tags in the plan: announced by the host, part of a set, its energy and genre.
  // announced: refs still unclaimed this render (a song announced once is tagged once).
  function trackTags(x,status,isSet,announced){
    if(!x||x.kind&&x.kind!=='music')return null;
    announced=announced||(status.announced_tracks||[]).slice();
    var i=announced.indexOf(x.ref),profile=(status.upcoming_tracks||[]).find(function(t){return t.ref===x.ref});
    if(i>=0)announced.splice(i,1);
    return {announced:i>=0,set:!!isSet,energy:profile&&profile.energy?profile.energy:null,genre:profile&&profile.energy&&profile.genre?profile.genre:''};
  }

  // ---- the plan (Air direction): what is scheduled for the next hours

  // How far a planned block is: optional, error, queued, ready, voice pending, text pending ('' when nothing to say).
  function planReadiness(r){
    if(r.optional)return 'optional';
    if(r.skipped||r.kind==='music'||r.preparation_started===false)return '';
    if(r.status==='ERROR')return 'error';
    if(r.status==='QUEUED')return 'queued';
    if(r.voice_ready)return 'ready';
    if(r.text_ready)return 'voice pending';
    return typeof r.text_ready==='boolean'?'text pending':'';
  }
  // "Now" for the plan, in ms: the moment of the pause while paused (the plan does not run on without the air).
  function planNow(status){return status.state==='PAUSED'&&status.paused_at?status.paused_at*1000:Date.now()}
  // The plan's blocks and the coming songs within horizonHours, grouped by hour of the station's time zone, newest
  // hour first: [{key ('YYYY-MM-DD, HH' or 'pending' for blocks without a time), current (this hour),
  // entries: [{row, index (in rows; null for a song)}] newest first}]. A song announced on air is marked announced.
  function planGroups(rows,status,horizonHours){
    var first=planNow(status),end=first+horizonHours*3600e3,groups={};
    var format=new Intl.DateTimeFormat('en-CA',{timeZone:status.direction_timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}),current=format.format(new Date(first));
    var entries=rows.flatMap(function(r,i){return r.kind==='music'?[]:[{row:r,index:i}]}),announced=(status.announced_tracks||[]).slice();
    (status.upcoming_tracks||[]).forEach(function(r,i){var mark=announced.indexOf(r.ref);if(mark>=0)announced.splice(mark,1);
      entries.push({row:Object.assign({},r,{id:'track:'+i,kind:'music',announced:mark>=0}),index:null})});
    entries.forEach(function(e){var at=Date.parse(e.row.at);if(at>=end)return;var hour=Number.isFinite(at)?format.format(new Date(at)):'pending';
      (groups[hour]||(groups[hour]=[])).push(e)});
    return Object.keys(groups).map(function(k){return {key:k,current:k===current,entries:groups[k].sort(function(a,b){return k==='pending'?b.row.id.slice(6)-a.row.id.slice(6):Date.parse(b.row.at)-Date.parse(a.row.at)})}}).sort(function(a,b){return a.key==='pending'?-1:b.key==='pending'?1:Date.parse(b.entries[0].row.at)-Date.parse(a.entries[0].row.at)});
  }
  // A time for a block inserted before rows[index] (rows newest first): halfway to the next newer one, never in the
  // past; null when there is no room.
  function planGapTime(rows,index){
    var lower=index<rows.length?Date.parse(rows[index].at):Date.now(),upper=index?Date.parse(rows[index-1].at):lower+60000;
    lower=Math.max(lower,Date.now());if(upper<=lower)return null;
    return new Date(lower+(upper-lower)/2).toISOString();
  }
  // One planned block as shown: title, readiness ('skipped', 'scheduled', ...), detail (what the block holds),
  // expectedAt (when it will likely air, if a minute or more from its planned time), and what may be done with it.
  function planRow(r){
    var music=r.kind==='music';
    var expected=!music&&r.expected_at&&Math.abs(Date.parse(r.expected_at)-Date.parse(r.at))>=60000?r.expected_at:null;
    return {title:r.edition?'Host edition':r.title||r.kind,readiness:r.skipped?'skipped':planReadiness(r)||(music?'':'scheduled'),
      detail:r.edition||music?'':r.content_title||'',expectedAt:expected,
      editable:!r.locked&&!music,skippable:['news','weather','reminder'].includes(r.kind)};
  }

  // ---- settings: the station's adjustable values, the AI provider and the voice

  // A setting's value as read: its label on a stepped scale, else the number with its unit.
  function settingValue(s,value){return s.value_labels?s.value_labels[+value]:value+(s.unit?' '+s.unit:'')}
  // What control a setting needs: switch, select, text, stepped (a slider with named steps) or slider.
  function settingType(s){return s.key==='music_shuffle'?'switch':s.options?'select':s.step===null?'text':s.value_labels?'stepped':'slider'}
  // The Settings tab: the plain settings in order, each with type, valueText, defaultText and changed (it differs
  // from config.toml and can be reset); the AI provider, its model row and the voice row come out as their own
  // blocks (ai, voice); source lists (multiline) are edited with their sources.
  function settings(rows){
    var provider=rows.find(function(s){return s.connection}),model=rows.find(function(s){return /_model$/.test(s.key)}),voice=rows.find(function(s){return s.voice_state});
    return {provider:provider||null,model:model||null,voice:voice||null,
      rows:rows.filter(function(s){return !s.multiline&&s!==model&&s!==voice}).map(function(s){
        return s===provider?{key:s.key,type:'ai'}:Object.assign({},s,{type:settingType(s),valueText:settingValue(s,s.value),
          defaultText:settingValue(s,s.default),changed:s.value!==s.default})})};
  }

  // What to do next when the AI provider is not usable, by connection state.
  var AI_TODO={no_key:{key:'add your API key below',sign:'sign in below'},bad_key:'replace the API key',unreachable:'check the connection',no_model:'choose another model below'};
  var STATE_MARK={ok:['ok','●'],wait:['wait','○'],bad:['bad','▲']};  // tone (ok/wait/bad) and its glyph
  // The AI block's summary: provider · model, the connection in a few words and what to do next.
  function ai(provider){
    var c=provider.connection,state=c.state,known=state==='ok'||state==='no_model',parts=c.detail.split(' · ');
    var todo=AI_TODO[state];if(state==='no_key')todo=provider.key_field?todo.key:todo.sign;
    return {name:provider.active.provider_title+(known?' · '+(state==='no_model'?'no model':provider.active.model):''),
      tone:STATE_MARK[state==='ok'?'ok':state==='checking'?'wait':'bad'],status:parts[0],
      note:state==='ok'?parts[1]||'':todo||'',state:state,
      providerLabel:function(o){return provider.labels[o]||o},
      modelHint:state==='no_model'?c.detail+'. Choose another model.':''};
  }
  // Feedback under the API key field once the provider was asked about a just-saved key.
  function keyChecked(state){return state==='bad_key'?{cls:'bad',text:'The provider rejected the API key. Copy it again from your account.',invalid:true}:{cls:'ok',text:'Key saved.'}}
  function keySaved(key){return key?{cls:'ok',text:'Key saved. Checking it now…',checking:true}:{cls:'',text:'Key removed.'}}
  var KEY_EMPTY={cls:'bad',text:'Enter an API key.',invalid:true};

  // The Voice block's summary: the engine, its server state, and the sample's feedback.
  // pending: the engine was just switched (its server is starting); sample: {st: idle|busy|playing|error, err, t0}.
  function voice(row,pending,sample){
    var vs=pending?{state:'starting',detail:'Starting…'}:row.voice_state,parts=vs.detail.split(' · ');
    return {name:row.labels[row.value]||row.value,tone:STATE_MARK[vs.state==='ready'?'ok':vs.state==='starting'?'wait':'bad'],
      status:parts[0],note:parts[1]||'',label:function(o){return row.labels[o]||o},
      sampleLabel:sample.st==='busy'?'Preparing…':sample.st==='playing'?'■ Stop':'Play a sample',feedback:sampleFeedback(sample,row)};
  }
  // [tone, text] under the sample button.
  function sampleFeedback(sample,row){
    if(sample.st==='busy'){var s=Math.floor((Date.now()-sample.t0)/1000);
      return ['wait','Preparing sample · '+s+' s'+(s>20||(row&&row.voice_state.state!=='ready')?' (startup can take about a minute)':'')]}
    if(sample.st==='playing')return ['ok','Playing the sample…'];
    if(sample.st==='error')return ['bad','Could not make a sample: '+sample.err+'. Try again in a minute.'];
    return ['','']}

  return {KINDS:KINDS,num:num,settingValue:settingValue,settingType:settingType,settings:settings,ai:ai,keyChecked:keyChecked,
    keySaved:keySaved,KEY_EMPTY:KEY_EMPTY,voice:voice,sampleFeedback:sampleFeedback,planReadiness:planReadiness,planNow:planNow,planGroups:planGroups,planGapTime:planGapTime,planRow:planRow,entryTitle:entryTitle,backgroundError:backgroundError,hostHistory:hostHistory,
    isCurrent:isCurrent,isQueued:isQueued,replayKind:replayKind,entryText:entryText,entryParts:entryParts,
    expandable:expandable,sourceLabel:sourceLabel,newsSegments:newsSegments,starsText:starsText,
    OLD_STARS_NOTE:OLD_STARS_NOTE,entryDetails:entryDetails,entry:entry,timeline:timeline,HISTORY_SHOWN:HISTORY_SHOWN,upcoming:upcoming,trackTags:trackTags};
})();
