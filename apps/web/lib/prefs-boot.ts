export const PREFS_KEY = "sushrut.prefs";

/** Inline script for <head>: applies saved prefs before first paint (no flash of the wrong theme/size). */
export const PREFS_BOOT = `try{var p=JSON.parse(localStorage.getItem('${PREFS_KEY}')||'{}'),r=document.documentElement;
if(p.text&&p.text!=='md')r.setAttribute('data-text',p.text);if(p.theme&&p.theme!=='auto')r.setAttribute('data-theme',p.theme);
if(p.contrast==='high')r.setAttribute('data-contrast','high');if(p.motion==='reduce')r.setAttribute('data-motion','reduce');if(p.sidebar==='rail')r.setAttribute('data-sidebar','rail');
var u=p.ui||'auto',w=innerWidth,m=u!=='auto'?u:(w>=1024&&(matchMedia('(pointer: fine)').matches||w>=1280)?'desktop':'mobile');r.setAttribute('data-ui',m);if(u!=='auto')r.setAttribute('data-ui-forced',u);
if(m==='desktop'&&u==='desktop'&&(screen.width||w)<1024){r.setAttribute('data-ui-squeeze','');var v=document.querySelector('meta[name=viewport]');if(v){v.dataset.original=v.content;v.content='width=1280, viewport-fit=cover';}}}catch(e){}`;

