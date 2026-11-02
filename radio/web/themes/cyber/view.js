// Cyber: the terminal theme's behavior (radio/web/themes/terminal/view.js) in its own look, with 24 thin spectrum bars,
// and a glitch on events only (Settings → Appearance → Glitch on events; reduced motion is always Off, a tab in the
// background gets none). A new title decodes out of noise and the spectrum gathers from random heights (Reduced: the
// decode alone); a lost signal and the radio turned off or on tear the title and the spectrum into slices and flash
// blocks over the panel (Reduced: no effect, only the still state). One effect at a time: a new event ends the last.
// The words stay the core's: #ttl-sr has the title at once; the decode only draws #ttl, which screen readers skip.
var RadioTheme=(function(theme){
  var GLYPHS='#@%&$<>/\\|!?~^*+=0123456789ABCDEFXYZ',DECODE_MS=520,SLICE_MS=460,BLOCKS=8,BLOCKS_MS=420,QUIET_AFTER_POWER_MS=1500;
  // the effect an event gets: 'decode' or 'decode-still' (a new title), 'slice' (a lost signal, the radio off or on), null
  function step(text){return text.length>48?'s':text.length>30?'m':''}  // the title's size step, as in prism
  function effect(event,mode,reduced,hidden){
    if(reduced||hidden||mode==='off')return null;
    if(event==='title')return mode==='reduced'?'decode-still':'decode';
    return mode==='on'?'slice':null}
  function mount(core,root){
    theme.mount(core,root);
    var html=document.documentElement,np=root.querySelector('#np'),ttl=root.querySelector('#ttl'),motion=matchMedia('(prefers-reduced-motion:reduce)'),
      shown=ttl.textContent,timers=[],drawing=false,lost=false,wasRunning=null,poweredAt=0,observer;
    function later(fn,ms){timers.push(setTimeout(fn,ms))}
    function own(){observer.takeRecords()}  // the decode's own writes are not a new title
    function stop(){
      timers.forEach(clearTimeout);timers=[];np.classList.remove('cy-slice');np.querySelectorAll('.cy-blk').forEach(function(b){b.remove()});
      if(drawing){drawing=false;ttl.classList.remove('cy-dc');ttl.textContent=shown;own()}}
    function decode(text,still){
      drawing=true;ttl.classList.add('cy-dc');ttl.textContent='';var cells=[];
      text.split(' ').forEach(function(word,i,words){var w=document.createElement('span');w.className='w';
        Array.from(word).forEach(function(c){var b=document.createElement('i');b.textContent=c;b.dataset.c=c;w.appendChild(b);cells.push(b)});
        ttl.appendChild(w);if(i<words.length-1)ttl.appendChild(document.createTextNode(' '))});
      cells.forEach(function(b){b.style.width=b.getBoundingClientRect().width+'px'});  // each box keeps its letter's width: no reflow
      own();
      var start=performance.now(),bars=still?[]:Array.from(root.querySelectorAll('#spectrum-bars i')),ends=bars.map(function(b){return b.style.height});
      (function tick(){
        var p=Math.min(1,(performance.now()-start)/DECODE_MS);
        if(p>=1){drawing=false;ttl.classList.remove('cy-dc');ttl.textContent=text;bars.forEach(function(b,i){b.style.height=ends[i]});own();return}
        cells.forEach(function(b,k){b.textContent=k/cells.length<p-.08+Math.random()*.05?b.dataset.c:GLYPHS[Math.floor(Math.random()*GLYPHS.length)]});
        bars.forEach(function(b){b.style.height=Math.round(Math.random()*(1-p)*100)+'%'});
        own();later(tick,42)})()}
    function blocks(){
      var box=np.getBoundingClientRect(),colors=['var(--accent)','var(--alt)','var(--bad)'];
      for(var i=0;i<BLOCKS;i++)(function(i){later(function(){
        var b=document.createElement('div'),w=24+Math.random()*130,h=3+Math.random()*16;b.className='cy-blk';
        b.style.cssText='left:'+Math.random()*Math.max(0,box.width-w)+'px;top:'+Math.random()*Math.max(0,box.height-h)+'px;width:'+w+'px;height:'+h+
          'px;background:'+colors[i%3]+';opacity:'+(.5+Math.random()*.5);
        np.appendChild(b);later(function(){b.remove()},70+Math.random()*130)},Math.random()*BLOCKS_MS)})(i)}
    function fire(event){
      var fx=effect(event,appearance.glitch(),motion.matches,document.hidden);stop();
      if(fx==='slice'){ttl.dataset.t=ttl.textContent;void np.offsetWidth;np.classList.add('cy-slice');later(function(){np.classList.remove('cy-slice')},SLICE_MS);blocks()}
      else if(fx)decode(shown,fx==='decode-still')}
    var box=ttl.parentNode;
    function size(text){box.dataset.size=step(text);box.title=text}  // the whole title on hover when three lines cut it
    size(shown);
    observer=new MutationObserver(function(){
      var next=ttl.textContent;if(next===shown)return;
      var first=!shown;shown=next;size(next);
      if(!first&&Date.now()-poweredAt>QUIET_AFTER_POWER_MS)fire('title')});  // the first title, and the one a power change brings, just appear
    observer.observe(ttl,{childList:true,characterData:true,subtree:true});
    core.subscribe(function(core,change){
      if(change==='error'&&core.error&&!lost){lost=true;html.dataset.signal='lost';fire('signal');return}
      if(change!=='snapshot')return;
      if(lost){lost=false;delete html.dataset.signal}  // the signal is back: no glitch
      var running=core.running();
      if(wasRunning!==null&&running!==wasRunning){poweredAt=Date.now();fire('power')}
      wasRunning=running})}
  return {mount:mount,bars:theme.bars,effect:effect,step:step};
})(RadioBars.wrap(RadioTheme,24));
