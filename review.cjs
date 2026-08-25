const fs=require('fs');
const s=fs.readFileSync('jester-design-system.html','utf8');
const nav=[...s.matchAll(/data-sec="([a-z]+)"><span class="n">(\d+)</g)].map(m=>({id:m[1],n:+m[2]}));
const secs=[...s.matchAll(/<section class=.sec. id=.([a-z]+)./g)].map(m=>m[1]);
const chips=[...s.matchAll(/class=.chipnum.>(\d+)</g)].map(m=>+m[1]);
console.log('nav:',nav.length,'secs:',secs.length,'chips:',chips.length);
console.log('nav->missing sec:',JSON.stringify(nav.filter(x=>!secs.includes(x.id))));
const misN=nav.map(x=>x.n).filter((v,i)=>v!==i);
const misC=chips.filter((v,i)=>v!==i);
console.log('.n sequence bad idx:',JSON.stringify(misN),'| chipnum bad idx:',JSON.stringify(misC));
console.log('order match:',JSON.stringify(nav.map(x=>x.id))===JSON.stringify(secs));
const ids={};for(const m of s.matchAll(/ id="([^"]+)"/g)){ids[m[1]]=(ids[m[1]]||0)+1;}
console.log('dup ids:',JSON.stringify(Object.entries(ids).filter(([,c])=>c>1)));
const miss=new Set();
for(const m of s.matchAll(/href="#([A-Za-z0-9_-]+)"/g)){const t=m[1];if(t.startsWith('i-'))continue;if(!ids[t])miss.add(t);}
console.log('dead anchors:',JSON.stringify([...miss]));
// defined-but-unused classes (rough)
const style=s.slice(0,s.indexOf('</style>'));
const defs=new Set();
for(const m of style.matchAll(/\.([a-zA-Z][a-zA-Z0-9_-]*)/g))defs.add(m[1]);
const body=s.slice(s.indexOf('<body'));
const unused=[...defs].filter(c=>!new RegExp('(^|[ "\\x27])'+c+'($|[ " ])').test(body));
console.log('unused classes:',JSON.stringify(unused));
