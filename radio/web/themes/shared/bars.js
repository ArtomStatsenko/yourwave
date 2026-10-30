// Spectrum bars for a theme built on the terminal behavior: `count` bars in #spectrum-bars from core.audio.read(),
// down to a flat line while nothing plays; drawn on every frame by the core's spectrumLoop, which stops it with the sound.
var RadioBars={wrap:function(theme,count){
  // 32 bands -> count bars, each bar the loudest of its bands
  function bars(bands){
    var out=[];
    for(var i=0;i<count;i++){var lo=Math.floor(i*bands.length/count),hi=Math.max(lo+1,Math.floor((i+1)*bands.length/count)),m=0;
      for(var j=lo;j<hi;j++)m=Math.max(m,bands[j]);out.push(m)}
    return out}
  function mount(core,root){
    theme.mount(core,root);
    var els=Array.from(root.querySelectorAll('#spectrum-bars i'));
    function draw(levels){els.forEach(function(el,i){el.style.height=levels?Math.round(levels[i]*100)+'%':'0'})}
    RadioCore.spectrumLoop(core,function(){var heard=core.audio.read(1);draw(heard?bars(heard.bands):null)},function(){draw(null)});
  }
  return {mount:mount,bars:bars};
}};
