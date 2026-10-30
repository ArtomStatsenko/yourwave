// Spectrum bars for a theme built on the terminal behavior: `count` bars in #spectrum-bars from core.audio.read(),
// down to a flat line while nothing plays. Drawn on the core's frames; it rests (and the loop sleeps) after half a second
// without sound: not listening, a paused station, silence. Never with reduced motion.
var RadioBars={wrap:function(theme,count){
  // sound, not silence: a paused station still sends a stream, a quiet one
  function sound(bands){return bands.some(function(v){return v>.02})}
  // 32 bands -> count bars, each bar the loudest of its bands
  function bars(bands){
    var out=[];
    for(var i=0;i<count;i++){var lo=Math.floor(i*bands.length/count),hi=Math.max(lo+1,Math.floor((i+1)*bands.length/count)),m=0;
      for(var j=lo;j<hi;j++)m=Math.max(m,bands[j]);out.push(m)}
    return out}
  function mount(core,root){
    theme.mount(core,root);
    var els=Array.from(root.querySelectorAll('#spectrum-bars i')),motion=matchMedia('(prefers-reduced-motion:reduce)'),heardAt=-1e9,flat=true;
    function draw(levels){els.forEach(function(el,i){el.style.height=levels?Math.round(levels[i]*100)+'%':'0'})}
    core.frames.add(function(dt,now){
      var heard=core.audio.listening&&!motion.matches&&core.audio.read(1);
      if(heard&&sound(heard.bands)){heardAt=now;flat=false;draw(bars(heard.bands));return true}
      if(!flat){draw(null);flat=true}
      return core.audio.listening&&!motion.matches&&now-heardAt<500});
  }
  return {mount:mount,bars:bars};
}};
