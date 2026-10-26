// Three throwaway UI variants, switched with ?variant=A|B|C. Only local sample state changes.
const variants = ['A', 'B', 'C'];
const names = ['Ultraviolet', 'Darkroom', 'Ribbon'];
const descriptions = ['Плеер слева, эфир справа', 'Плотная тёмная консоль', 'Спокойная единая колонка'];
const query = new URLSearchParams(location.search);
let variant = variants.includes(query.get('variant')) ? query.get('variant') : 'A';
let screen = ['timeline', 'settings', 'integrations', 'experiments'].includes(query.get('screen')) ? query.get('screen') : 'timeline';
let editing = false;
let track = 0;
let listening = false;
const tracks = [
 {title:'Everything In Its Right Place',artist:'Radiohead',album:'Kid A',year:'2000'},
 {title:'A Walk',artist:'Tycho',album:'Dive',year:'2011'},
 {title:'Two Thousand and Seventeen',artist:'Four Tet',album:'New Energy',year:'2017'}
];
function updateURL() {
 const url = new URL(location.href);
 url.searchParams.set('variant', variant);
 url.searchParams.set('screen', screen);
 history.replaceState(null, '', url);
}
function showVariant(next) {
 variant = next;
 document.documentElement.dataset.variant = variant;
 document.querySelectorAll('[data-variant-panel]').forEach(el => el.hidden = el.dataset.variantPanel !== variant);
 document.querySelectorAll('[data-pick]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.pick === variant)));
 const i = variants.indexOf(variant);
 document.querySelector('#variant-name').textContent = variant + ' / ' + names[i];
 document.querySelector('#variant-description').textContent = descriptions[i];
 document.title = 'Yourwave / ' + names[i] + ' / design prototype';
 updateURL();
}
function showScreen(next) {
 screen = next;
 document.querySelectorAll('[data-screen]').forEach(el => el.hidden = el.dataset.screen !== screen);
 document.querySelectorAll('[data-nav]').forEach(el => el.setAttribute('aria-pressed', String(el.dataset.nav === screen)));
 updateURL();
}
document.addEventListener('click', event => {
 const button = event.target.closest('button');
 if (!button) return;
 if (button.dataset.pick) showVariant(button.dataset.pick);
 if (button.dataset.cycle) showVariant(variants[(variants.indexOf(variant) + Number(button.dataset.cycle) + variants.length) % variants.length]);
 if (button.dataset.nav) showScreen(button.dataset.nav);
 if (button.hasAttribute('data-listen')) {
  listening = !listening;
  document.querySelectorAll('[data-listen]').forEach(el => {el.setAttribute('aria-pressed', String(listening));el.querySelector('span').textContent = listening ? 'Pause' : 'Listen';});
  document.querySelectorAll('[data-preview-feedback]').forEach(el => el.textContent = listening ? 'Listening state preview. No audio is connected.' : 'Sample broadcast. No audio is connected.');
 }
 if (button.hasAttribute('data-next')) {
  track = (track + 1) % tracks.length;
  const t = tracks[track];
  for (const key of ['title','artist','album','year']) document.querySelectorAll('[data-track-'+key+']').forEach(el => el.textContent = t[key]);
 }
 if (button.hasAttribute('data-edit')) {
  editing = !editing;
  document.querySelectorAll('[data-future]').forEach(el => el.hidden = !editing);
  document.querySelectorAll('[data-up-next]').forEach(el => el.hidden = editing);
  document.querySelectorAll('[data-edit]').forEach(el => {el.textContent = editing ? 'Done' : 'Edit program';el.setAttribute('aria-pressed', String(editing));});
  document.querySelectorAll('[data-schedule-hint]').forEach(el => el.textContent = editing ? 'Sample program. Later blocks above, history below.' : 'Next above, recent broadcasts below.');
 }
 if (button.hasAttribute('data-toggle')) {
  const on = button.getAttribute('aria-pressed') !== 'true';
  button.setAttribute('aria-pressed', String(on));button.textContent = on ? 'On' : 'Off';
 }
 if (button.hasAttribute('data-more')) {
  const panel = button.closest('[data-screen]');
  const row = panel.querySelector('[data-earlier]');
  row.hidden = !row.hidden;button.textContent = row.hidden ? 'Show earlier' : 'Show less';
 }
 if (button.hasAttribute('data-insert')) {
  button.closest('[data-screen]').querySelector('[data-screen-feedback]').textContent = 'Insertion preview only. The broadcast program has not changed.';
 }
 if (button.hasAttribute('data-move')) {
  const row = button.closest('.timeline-row');
  const time = row.querySelector('time');time.textContent = time.textContent === '21:30' ? '21:45' : '21:30';
  button.closest('[data-screen]').querySelector('[data-screen-feedback]').textContent = 'Sample time changed. The broadcast program has not changed.';
 }
});
document.addEventListener('input',event=>{
 if(event.target.matches('[data-volume]')) event.target.closest('.volume').querySelector('output').textContent=event.target.value+'%';
});
document.addEventListener('change',event=>{
 if(event.target.matches('select')) event.target.closest('[data-screen]').querySelector('[data-screen-feedback]').textContent='Preview changed locally. Station settings have not changed.';
});
document.addEventListener('keydown',event=>{
 if(event.target.closest('input,select,textarea,summary,[contenteditable=true],button:not([data-cycle]):not([data-pick])')) return;
 if(event.key==='ArrowLeft'||event.key==='ArrowRight'){
  event.preventDefault();showVariant(variants[(variants.indexOf(variant)+(event.key==='ArrowLeft'?-1:1)+variants.length)%variants.length]);
 }
});
showVariant(variant);showScreen(screen);
