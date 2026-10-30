// Prism: the terminal theme's behavior (radio/web/themes/terminal/view.js) in its own look, with 32 white spectrum bars
// (shared/bars.js) and the colour split moving:
// with the sound (--ab on the theme root), a rising title on a new track, a settle on a new tab or switch, and a still
// red split on an error. Reduced motion keeps the still split; nothing moves.
var RadioTheme=(function(theme){
  // the split width for a smoothed level of the low bands (0..1): 1.5px still, up to 4.3px loud
  function width(level){return (1.5+level*2.8).toFixed(2)+'px'}
  function low(bands){var n=Math.max(1,Math.floor(bands.length/4)),s=0;for(var i=0;i<n;i++)s+=bands[i];return s/n}
  // the title's size step (theme.css): up to 30 characters as designed, 31–48 smaller, longer smaller still
  function step(text){return text.length>48?'s':text.length>30?'m':''}
  function mount(core,root){
    theme.mount(core,root);
    var html=document.documentElement,motion=matchMedia('(prefers-reduced-motion:reduce)'),level=0,running=false;
    function moving(){return core.audio.listening&&!motion.matches&&!document.hidden&&!core.error&&!(core.snapshot&&core.snapshot.state==='PAUSED')}
    function frame(){
      if(!moving()){running=false;level=0;root.style.removeProperty('--ab');return}
      var heard=core.audio.read(1);level+=((heard?low(heard.bands):0)-level)*.25;
      root.style.setProperty('--ab',width(level));requestAnimationFrame(frame)}
    function start(){if(!running&&moving()){running=true;requestAnimationFrame(frame)}}
    core.subscribe(function(core,change){
      html.classList.toggle('prism-error',!!core.error);   // the words are the core's, shown where terminal shows them
      if(change==='listening'||change==='snapshot')start()});
    motion.addEventListener('change',start);document.addEventListener('visibilitychange',start);

    // a new track: the size step is set first; the box takes the new title's height for the whole motion; the old
    // title leaves upward, the text is swapped at 180 ms, the new one rises into place
    var ttl=root.querySelector('#ttl'),box=root.querySelector('.title'),shown=ttl.textContent,swapping=null;
    function size(text){box.dataset.size=step(text)}
    size(shown);
    new MutationObserver(function(){
      var next=ttl.textContent;if(next===shown||swapping!==null&&next===swapping)return;
      size(next);
      if(!shown||motion.matches){shown=next;return}
      swapping=next;box.style.height='';var h=box.offsetHeight;ttl.textContent=shown;box.style.height=h+'px';
      box.classList.remove('swap');void box.offsetWidth;box.classList.add('swap');
      setTimeout(function(){shown=swapping;swapping=null;ttl.textContent=shown},180)
    }).observe(ttl,{childList:true,characterData:true,subtree:true});
    box.addEventListener('animationend',function(){box.classList.remove('swap');box.style.height=''});

    // a new tab or a switch turned on: its split opens and settles back
    function settle(el){if(!el||motion.matches)return;el.classList.remove('settle');void el.offsetWidth;el.classList.add('settle');
      setTimeout(function(){el.classList.remove('settle')},600)}
    new MutationObserver(function(list){list.forEach(function(m){if(m.target.getAttribute('aria-selected')==='true')settle(m.target)})})
      .observe(root.querySelector('.tabs'),{attributes:true,attributeFilter:['aria-selected'],subtree:true});
    root.addEventListener('click',function(e){var pl=e.target.closest('.pl');if(pl){var name=pl.dataset.pl;
      setTimeout(function(){var now=root.querySelector('.pl[data-pl="'+name+'"]');if(now&&now.getAttribute('aria-pressed')==='true')settle(now)})}});  // the row is drawn anew
    root.addEventListener('change',function(e){if(e.target.matches('input[role=switch]')&&e.target.checked)settle(e.target)});
    start()}
  return {mount:mount,width:width,low:low,step:step,bars:theme.bars};
})(RadioBars.wrap(RadioTheme,32));
