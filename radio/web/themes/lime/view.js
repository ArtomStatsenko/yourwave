// Lime: the terminal theme's behavior (radio/web/themes/terminal/view.js, loaded first) in its own look.
// This adds the spectrum strip: 20 bars from core.audio.read(), down to a flat line while nothing plays; the
// loop runs only while this device listens, and never with reduced motion.
var RadioTheme=(function(terminal){
  var BARS=20;
  // 32 bands -> 20 bars, each bar the loudest of its bands
  function bars(bands){
    var out=[];
    for(var i=0;i<BARS;i++){var lo=Math.floor(i*bands.length/BARS),hi=Math.max(lo+1,Math.floor((i+1)*bands.length/BARS)),m=0;
      for(var j=lo;j<hi;j++)m=Math.max(m,bands[j]);out.push(m)}
    return out}
  function mount(core,root){
    terminal.mount(core,root);
    var els=Array.from(root.querySelectorAll('#lime-bars i')),motion=matchMedia('(prefers-reduced-motion:reduce)'),running=false;
    function draw(levels){els.forEach(function(el,i){el.style.height=levels?Math.round(levels[i]*100)+'%':'0'})}
    function frame(){
      if(!core.audio.listening||motion.matches){running=false;draw(null);return}
      var heard=core.audio.read(1);draw(heard?bars(heard.bands):null);
      requestAnimationFrame(frame)}
    function start(){if(!running&&core.audio.listening&&!motion.matches){running=true;requestAnimationFrame(frame)}}
    core.subscribe(function(core,change){if(change==='listening')start()});
    motion.addEventListener('change',start);
  }
  return {mount:mount,bars:bars};
})(RadioTheme);
