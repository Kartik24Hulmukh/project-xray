'use strict';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const list=document.querySelector('#list'),detail=document.querySelector('#detail');
async function json(url){const r=await fetch(url,{headers:{Accept:'application/json'}});if(!r.ok)throw new Error(`Request failed (${r.status})`);return r.json()}
function errorBox(message){return `<div class="error" role="alert"><strong>Could not load data.</strong><p>${esc(message)}</p></div>`}
async function loadProjects(){
 list.setAttribute('aria-busy','true');list.innerHTML='<p class="muted">Loading projects…</p>';
 try{const d=await json('/api/projects');if(!d.projects.length){list.innerHTML='<p class="muted">No reviewed public dossiers are available yet.</p>';return}
 list.innerHTML=d.projects.map(p=>`<article class="card"><span class="badge ${p.synthetic?'warn':''}">${p.synthetic?'SYNTHETIC':'PUBLISHED'}</span><h3>${esc(p.title)}</h3><p class="meta">${esc(p.authority)}${p.location?' · '+esc(p.location):''}</p><p>${esc(p.summary)}</p><button class="open-project" type="button" data-project="${esc(p.id)}">View dossier</button></article>`).join('');
 list.querySelectorAll('.open-project').forEach(b=>b.addEventListener('click',()=>openProject(b.dataset.project)));
 }catch(e){list.innerHTML=errorBox(e.message)}finally{list.removeAttribute('aria-busy')}
}
async function openProject(id){
 detail.classList.remove('hidden');detail.setAttribute('aria-busy','true');detail.innerHTML='<p class="muted">Loading dossier…</p>';
 try{const d=await json('/api/projects/'+encodeURIComponent(id)),p=d.project;
 detail.innerHTML=`<p class="eyebrow">PROJECT DOSSIER</p><h2>${esc(p.title)}</h2>${p.synthetic?'<div class="notice"><strong>Synthetic fixture:</strong> not a real case.</div>':''}<p class="meta">${esc(p.authority)}${p.location?' · '+esc(p.location):''}</p><p>${esc(p.summary)}</p><div class="actions"><a class="action" href="/api/projects/${esc(id)}/report">Evidence report</a><a class="action" href="/api/projects/${esc(id)}/claims.csv">Claims CSV (accessible export)</a><a class="action" href="/api/projects/${esc(id)}/rti">Draft RTI</a><a class="action" href="/api/projects/${esc(id)}/capsule">Evidence capsule</a></div><h3>Published claims</h3>${d.claims.length?d.claims.map(c=>`<article class="claim"><div><span class="badge">${esc(c.claim_type)}</span> <span class="badge state-${esc(c.publication_state)}">${esc(c.publication_state)}</span></div><p>${esc(c.text)}</p><small>Source: <a href="${esc(c.source_url)}" target="_blank" rel="noopener noreferrer">${esc(c.publisher||c.source_url)}</a>${c.page_ref?' · '+esc(c.page_ref):''}<br>Retrieved ${esc(c.retrieved_at)} · SHA-256 <code>${esc(c.source_sha256)}</code></small></article>`).join(''):'<p class="muted">No public claims.</p>'}<h3>Records not located</h3>${d.gaps.length?d.gaps.map(g=>`<div class="gap"><strong>${esc(g.document_name)}</strong><br><small>Searched: ${esc(g.search_scope)} · ${esc(g.searched_at)}</small></div>`).join(''):'<p class="muted">No evidence gaps recorded.</p>'}<h3>Responses</h3>${d.responses.length?d.responses.map(x=>`<article class="claim"><strong>${esc(x.responder)}</strong><p>${esc(x.text)}</p></article>`).join(''):'<p class="muted">No authority or subject response recorded.</p>'}`;
 detail.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});detail.focus({preventScroll:true});
 }catch(e){detail.innerHTML=errorBox(e.message)}finally{detail.removeAttribute('aria-busy')}
}
document.querySelector('#refresh').addEventListener('click',loadProjects);loadProjects();
