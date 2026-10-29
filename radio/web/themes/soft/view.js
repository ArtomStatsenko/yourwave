// Soft: the terminal theme's behavior (radio/web/themes/terminal/view.js) in its own look, with 16 soft spectrum bars,
// and sliders filled up to their knob (--value, for theme.css), kept in step as they move and after each redraw.
var RadioTheme=(function(theme){
  function fill(el){var min=parseFloat(el.min||0),max=parseFloat(el.max||100);
    el.style.setProperty('--value',(max>min?(el.value-min)/(max-min)*100:0)+'%')}
  function mount(core,root){
    theme.mount(core,root);
    function all(){root.querySelectorAll('input[type=range]').forEach(fill)}
    root.addEventListener('input',function(e){if(e.target.type==='range')fill(e.target)});
    core.subscribe(all);all()}
  return {mount:mount,bars:theme.bars,fill:fill};
})(RadioBars.wrap(RadioTheme,16));
