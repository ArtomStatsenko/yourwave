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

  return {KINDS:KINDS,num:num,entryTitle:entryTitle,backgroundError:backgroundError,hostHistory:hostHistory,
    isCurrent:isCurrent,isQueued:isQueued,replayKind:replayKind,entryText:entryText,entryParts:entryParts,
    expandable:expandable,sourceLabel:sourceLabel,newsSegments:newsSegments,starsText:starsText,
    OLD_STARS_NOTE:OLD_STARS_NOTE,entryDetails:entryDetails,entry:entry,timeline:timeline,HISTORY_SHOWN:HISTORY_SHOWN,upcoming:upcoming,trackTags:trackTags};
})();
