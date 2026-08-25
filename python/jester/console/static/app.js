/* Jester operator console.
   One module, no build step — the console ships with the Python package.
   Pages load their own data on first view and on refresh, so opening the app
   costs one request set instead of seven. */
'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const num = (v, digits = 1) =>
  (v === null || v === undefined || v === '' || Number.isNaN(+v)) ? '—' : (+v).toFixed(digits);

const int = v => (v === null || v === undefined || v === '') ? '—' : String(v);

/** SQLite stamps are UTC without a zone marker; render local, never "Invalid Date". */
function when(stamp) {
  if (!stamp) return '—';
  const d = new Date(String(stamp).replace(' ', 'T') + (/[Zz+]/.test(stamp) ? '' : 'Z'));
  return Number.isNaN(d.getTime()) ? String(stamp) : d.toLocaleString();
}

function ago(stamp) {
  if (!stamp) return '';
  const d = new Date(String(stamp).replace(' ', 'T') + (/[Zz+]/.test(stamp) ? '' : 'Z'));
  if (Number.isNaN(d.getTime())) return '';
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** tone drives the colour, label is what the operator reads. */
const tag = (tone, label, extra = '') =>
  `<span class="pill ${extra} tone-${esc(String(tone || 'neutral').toLowerCase())}">` +
  `<span class="dot"></span>${esc(label)}</span>`;

const pill = (status, extra = '') => tag(status || 'neutral', status || 'unknown', extra);

const emptyRow = (cols, text) =>
  `<tr><td colspan="${cols}"><div class="empty">${esc(text)}</div></td></tr>`;

// ── transport ───────────────────────────────────────────────────────────
async function api(path, body) {
  const opts = body === undefined ? undefined : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  try {
    const r = await fetch(path, opts);
    const payload = await r.json();
    if (!r.ok && payload.ok === undefined) payload.ok = false;
    return payload;
  } catch (err) {
    // A dead fetch used to look identical to an empty result. Say which it is.
    return { ok: false, error: `console unreachable (${err.message})` };
  }
}

// ── toasts ──────────────────────────────────────────────────────────────
function toast(msg, tone = 'ok') {
  const el = document.createElement('div');
  el.className = `toast toast--${tone}`;
  el.innerHTML = `<span style="flex:1">${esc(msg)}</span><button aria-label="Dismiss">✕</button>`;
  el.querySelector('button').onclick = () => el.remove();
  $('#toasts').appendChild(el);
  // Failures stay until dismissed — an error that vanishes is an error missed.
  if (tone !== 'bad') setTimeout(() => el.remove(), 5000);
}

// ── page registry ───────────────────────────────────────────────────────
const PAGES = [
  { group: 'Pipeline', id: 'overview', label: 'Overview', icon: '◈', load: loadOverview },
  { group: 'Pipeline', id: 'runs', label: 'Runs', icon: '▷', load: loadRuns, count: () => COUNTS.runs },
  { group: 'Archive', id: 'ideas', label: 'Ideas', icon: '✦', load: loadIdeas, count: () => COUNTS.ideas },
  { group: 'Archive', id: 'nuggets', label: 'Nuggets', icon: '◦', load: loadNuggets, count: () => COUNTS.nuggets },
  { group: 'Setup', id: 'sources', label: 'Sources', icon: '⌁', load: loadSources, count: () => COUNTS.sources },
  { group: 'Setup', id: 'config', label: 'Config', icon: '⚙', load: loadConfig },
  { group: 'Health', id: 'doctor', label: 'Doctor', icon: '✚', load: loadDoctor, count: () => COUNTS.findings || null },
];

const COUNTS = {};
let CURRENT = 'overview';

function renderNav() {
  let html = '';
  let group = null;
  for (const p of PAGES) {
    if (p.group !== group) { group = p.group; html += `<li class="grp">${esc(group)}</li>`; }
    const n = p.count ? p.count() : null;
    html += `<li><button data-page="${p.id}" ${p.id === CURRENT ? 'aria-current="page"' : ''}>` +
      `<span class="ico" aria-hidden="true">${p.icon}</span>${esc(p.label)}` +
      `${n || n === 0 ? `<span class="count num">${n}</span>` : ''}</button></li>`;
  }
  $('#nav').innerHTML = html;
  $$('#nav button').forEach(b => { b.onclick = () => go(b.dataset.page); });
}

function go(id, { push = true } = {}) {
  const page = PAGES.find(p => p.id === id) || PAGES[0];
  CURRENT = page.id;
  $$('#main section').forEach(s => { s.hidden = s.dataset.page !== page.id; });
  renderNav();
  if (push && location.hash.slice(1) !== page.id) location.hash = page.id;
  document.title = `${page.label} — Jester`;
  page.load();
}

// ── busy state ──────────────────────────────────────────────────────────
async function busy(el, fn) {
  const label = el && el.innerHTML;
  if (el) { el.disabled = true; el.innerHTML = `<span class="spinner"></span>${label}`; }
  try { return await fn(); }
  finally { if (el) { el.disabled = false; el.innerHTML = label; } }
}

async function runAction(path, body, el) {
  const res = await busy(el, () => api(path, body || {}));
  const label = path.replace('/api/', '');
  if (res.ok === false) {
    // ok:false has three flavours here: a transport/handler error, a non-zero
    // CLI exit, and "the check ran and found things". Name the right one.
    const why = res.error
      || (res.findings ? `${res.findings.length} finding(s)` : '')
      || (res.code !== undefined ? `exit ${res.code}` : 'refused');
    toast(`${label}: ${why}`, 'bad');
  } else {
    const detail = [
      res.findings ? `${res.findings.length} finding(s)` : '',
      res.requeued !== undefined ? `${res.requeued} requeued` : '',
      res.fixed !== undefined ? `${res.fixed} reembedded` : '',
      res.queued_new_comments !== undefined ? `${res.queued_new_comments} new comment(s)` : '',
      res.counts && res.out_dir
        ? `${res.counts.nuggets} nugget(s) + ${res.counts.ideas} idea(s) → ${res.out_dir}` : '',
    ].filter(Boolean).join(' · ');
    toast(`${label}: ok${detail ? ' — ' + detail : ''}`, 'ok');
  }
  if (res.findings && res.findings.length) res.findings.forEach(f => toast(f, 'bad'));
  // The worker's own log is the only account of which sources it reached.
  if (res.output) {
    const lines = res.output.split('\n').map(l => l.trim()).filter(Boolean);
    const last = lines[lines.length - 1];
    lines.slice(0, -1).filter(l => /skipped|disabled|no adapter/.test(l))
      .slice(-4).forEach(l => toast(l, 'bad'));
    if (last) toast(last, res.ok === false ? 'bad' : 'ok');
  }
  await refreshCounts();
  PAGES.find(p => p.id === CURRENT).load();
  return res;
}

// ── overview ────────────────────────────────────────────────────────────
async function refreshCounts() {
  const ov = await api('/api/overview');
  if (ov.ok === false) return ov;
  Object.assign(COUNTS, {
    ideas: ov.counts.ideas,
    nuggets: ov.counts.nuggets,
    sources: ov.sources_summary ? ov.sources_summary.enabled : null,
  });
  $('#db-chip').textContent = 'db ' + String(ov.db).split(/[\\/]/).pop();
  $('#db-chip').title = ov.db;
  $('#cfg-chip').textContent = 'config ' + String(ov.config_dir || '').split(/[\\/]/).pop();
  $('#cfg-chip').title = ov.config_dir || '';
  renderNav();
  return ov;
}

async function loadOverview() {
  const [ov, infra] = await Promise.all([refreshCounts(), api('/api/infra')]);
  if (ov.ok === false) { toast(ov.error, 'bad'); return; }
  const c = ov.counts;

  $('#kpis').innerHTML = [
    ['nuggets', c.nuggets, ''],
    ['ideas', c.ideas, ''],
    ['pending batches', c.pending_batches, ''],
    ['failed batches', c.failed_batches, c.failed_batches ? 'kpi--alert' : ''],
    ['reembed backlog', c.reembed_backlog, c.reembed_backlog ? 'kpi--alert' : ''],
    ['unprocessed', c.unprocessed, ''],
    ['active runs', c.runs_active, c.runs_active ? 'kpi--live' : ''],
  ].map(([k, v, cls]) =>
    `<div class="kpi ${cls}"><b class="num">${int(v)}</b><span>${esc(k)}</span></div>`).join('');

  const banners = [];
  if (c.failed_batches) banners.push(['bad',
    `${c.failed_batches} failed ingest batch(es)`, 'Requeue them from Quick actions, or check the worker log.']);
  if (c.reembed_backlog) banners.push(['',
    `${c.reembed_backlog} nugget(s) awaiting reembed`, 'The archive is searchable but incomplete until this clears.']);
  const s = ov.sources_summary;
  if (s && s.unsupported) banners.push(['',
    `${s.unsupported} enabled source(s) have no adapter yet`,
    'The worker will report them as skipped rather than ingest them. See the Sources tab.']);
  if (s && !s.enabled) banners.push(['bad', 'No sources are enabled',
    'A live worker run has nothing to fetch. Add or enable one in the Sources tab.']);
  $('#ov-banners').innerHTML = banners.map(([tone, head, body]) =>
    `<div class="banner ${tone === 'bad' ? 'banner--bad' : ''}"><span aria-hidden="true">${tone === 'bad' ? '⚠' : 'ℹ'}</span>
     <span><b>${esc(head)}</b>${esc(body)}</span></div>`).join('');

  const lr = ov.last_run;
  $('#last-run').innerHTML = lr
    ? `${pill(lr.status)}
       <dl class="dl" style="margin-block-start:var(--s-4)">
         <dt>run</dt><dd class="mono">${esc(lr.run_id)}</dd>
         <dt>started</dt><dd>${esc(when(lr.started_at))} <span class="xs">${esc(ago(lr.started_at))}</span></dd>
         <dt>kept / ideas</dt><dd>${int(lr.n_nuggets_kept)} / ${int(lr.n_ideas)}</dd>
         <dt>duration</dt><dd>${num(lr.duration_actual_s, 1)}s <span class="xs">expected ${num(lr.duration_expected_s, 1)}s</span></dd>
       </dl>`
    : '<p class="empty">No run yet — hit ▶ run pipeline.</p>';

  const q = ov.quota;
  $('#quota').innerHTML = `<dl class="dl">
      <dt>day</dt><dd class="mono">${esc(q.day_key)}</dd>
      <dt>used today</dt><dd><b>${int(q.used_total)}</b></dd>
      ${Object.entries(q.used_by_platform).map(([k, v]) =>
        `<dt>${esc(k)}</dt><dd>${int(v)}</dd>`).join('')}
    </dl>`;

  if (infra.ok !== false) {
    $('#infra').innerHTML = [
      [infra.ollama.up, infra.ollama.up ? `Ollama · ${infra.ollama.models.length} model(s)` : 'Ollama down'],
      [infra.qdrant_6333, 'Qdrant :6333'],
      [infra.cloakserve_9222, 'cloakserve :9222'],
      [infra.worker_go_available, 'Go worker'],
    ].map(([up, label]) => tag(up ? 'done' : 'off', label, 'pill--sm')).join(' ');
    $('#btn-live-models').classList.toggle('hidden',
      !(infra.ollama.up && infra.ollama.models.some(m => /qwen|mistral|phi|llama|gemma/i.test(m))));
    $('#btn-ingest-live').classList.toggle('hidden', !infra.can_ingest_live);
    $('#btn-ingest-mock').classList.toggle('hidden', !infra.worker_go_available);
  }
}

$('#quick-actions').addEventListener('click', e => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const body = {};
  if (btn.dataset.runPrefix) body.run_id = `${btn.dataset.runPrefix}-${Date.now()}`;
  if (btn.dataset.liveModels) body.live_models = true;
  if (btn.dataset.live) body.live = true;
  runAction(btn.dataset.act, body, btn);
});

// ── sources ─────────────────────────────────────────────────────────────
const PLATFORM_LABEL = {
  reddit: 'Reddit', hackernews: 'Hacker News', discourse: 'Discourse',
  youtube: 'YouTube', tiktok: 'TikTok',
};
// Platforms the parser accepts are served by the API, so adding an adapter
// never needs a matching edit here.
function renderPlatformOptions(platformKinds) {
  const sel = $('#src-platform');
  const keep = sel.value;
  sel.innerHTML = '<option value="auto">detect from link</option>' +
    Object.keys(platformKinds || {}).map(p =>
      `<option value="${esc(p)}">${esc(PLATFORM_LABEL[p] || p)}</option>`).join('');
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
}

async function loadSources() {
  const res = await api('/api/sources');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  const list = res.sources || [];
  COUNTS.sources = list.filter(s => s.enabled).length;
  renderNav();

  renderPlatformOptions(res.platform_kinds);
  $('#src-path').textContent = res.path || '';
  $('#src-count').textContent = `${COUNTS.sources} enabled / ${list.length} total`;

  const order = { hackernews: 0, reddit: 1, discourse: 2, youtube: 3, tiktok: 4 };
  const sorted = [...list].sort((a, b) =>
    (order[a.platform] ?? 9) - (order[b.platform] ?? 9) || a.name.localeCompare(b.name));

  $('#src-body').innerHTML = sorted.map(s => {
    const status = !s.enabled ? tag('off', 'paused', 'pill--sm')
      : s.supported ? tag('done', 'ingesting', 'pill--sm')
        : tag('warn', 'no adapter yet', 'pill--sm');
    return `<tr data-name="${esc(s.name)}">
      <td><label class="switch"><input type="checkbox" data-toggle ${s.enabled ? 'checked' : ''}
          aria-label="Enable ${esc(s.name)}"></label></td>
      <td>${esc(PLATFORM_LABEL[s.platform] || s.platform)}</td>
      <td class="mono">${esc(s.name)}</td>
      <td><span class="chip">${esc(s.kind || 'unknown')}</span></td>
      <td><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer"
             class="truncate" style="display:block;max-inline-size:34ch"
             title="${esc(s.url)}">${esc(s.url.replace(/^https?:\/\/(www\.)?/, ''))}</a>
          ${s.notes ? `<div class="xs">${esc(s.notes)}</div>` : ''}</td>
      <td>${status}</td>
      <td><button class="btn btn--danger btn--sm" data-delete aria-label="Remove ${esc(s.name)}">remove</button></td>
    </tr>`;
  }).join('') || emptyRow(7, 'No sources yet — add one above.');

  const unsupported = list.filter(s => s.enabled && !s.supported);
  $('#src-hint').innerHTML = unsupported.length
    ? `<span class="chip chip--warn">heads up</span> ${unsupported.length} enabled source(s)
       (${esc(unsupported.map(s => s.name).join(', '))}) have no worker adapter yet — the run will
       report them as skipped rather than quietly ingest nothing.`
    : 'Changes are written to <span class="mono">sources.yaml</span> immediately; the worker picks them up on its next run.';
}

$('#src-body').addEventListener('change', async e => {
  const box = e.target.closest('input[data-toggle]');
  if (!box) return;
  const name = box.closest('tr').dataset.name;
  const res = await api('/api/sources/update', { name, enabled: box.checked });
  if (res.ok === false) { toast(res.error, 'bad'); box.checked = !box.checked; return; }
  toast(`${name} ${box.checked ? 'enabled' : 'disabled'}`, 'ok');
  loadSources();
});

$('#src-body').addEventListener('click', async e => {
  const btn = e.target.closest('button[data-delete]');
  if (!btn) return;
  const name = btn.closest('tr').dataset.name;
  if (!confirm(`Remove "${name}" from the source list?`)) return;
  const res = await busy(btn, () => api('/api/sources/delete', { name }));
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  toast(`removed ${name}`, 'ok');
  loadSources();
});

$('#src-form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = $('#src-form button[type="submit"]');
  const body = {
    url: $('#src-url').value.trim(),
    platform: $('#src-platform').value,
    name: $('#src-name').value.trim() || null,
  };
  const res = await busy(btn, () => api('/api/sources/add', body));
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  toast(`added ${res.source.platform}/${res.source.name} (${res.source.kind})`, 'ok');
  $('#src-url').value = '';
  $('#src-name').value = '';
  $('#src-url').focus();
  loadSources();
});

// ── runs ────────────────────────────────────────────────────────────────
async function loadRuns() {
  const res = await api('/api/runs');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  COUNTS.runs = res.runs.length;
  renderNav();

  $('#runs-body').innerHTML = res.runs.map(r => {
    const funnel = Object.entries(r.prefilter_funnel_obj || {});
    const near = (r.top_near_misses_list || []).map(v => (+v).toFixed(2));
    const platforms = Object.entries(r.platform_counts_obj || {});
    const signals = [
      ...platforms.map(([p, n]) => `<span class="chip">${esc(p)} ${esc(n)}</span>`),
      ...(r.floor_flags_list || []).map(f => `<span class="chip chip--bad">${esc(f)}</span>`),
      ...funnel.map(([k, v]) => `<span class="chip">${esc(k)} ${esc(v)}</span>`),
      near.length ? `<span class="chip">near ${esc(near.join(', '))}</span>` : '',
      r.error ? `<span class="chip chip--bad" title="${esc(r.error)}">error</span>` : '',
    ].filter(Boolean).join('');
    return `<tr>
      <td>${esc(when(r.started_at))}<div class="xs">${esc(ago(r.started_at))} · ${int(r.n_posts)} post(s)</div></td>
      <td class="mono">${esc(r.run_id)}</td>
      <td>${pill(r.status)}</td>
      <td class="num">${int(r.n_nuggets_kept)} / ${int(r.n_ideas)}</td>
      <td class="num">${num(r.duration_actual_s, 1)}s<div class="xs">exp ${num(r.duration_expected_s, 1)}s</div></td>
      <td><span class="chip">${esc(r.origin || 'manual')}</span></td>
      <td><div class="chiprow">${signals || '<span class="xs">clean</span>'}</div></td>
    </tr>`;
  }).join('') || emptyRow(7, 'No runs yet — hit ▶ run pipeline on the Overview tab.');
}

// ── ideas ───────────────────────────────────────────────────────────────
const IDEA_STATUSES = ['new', 'reviewed', 'building', 'archived'];
let IDEAS = [];
let IDEA_FILTER = 'all';

function renderIdeaFilter() {
  const counts = IDEAS.reduce((acc, i) => (acc[i.status] = (acc[i.status] || 0) + 1, acc), {});
  $('#ideas-filter').innerHTML = ['all', ...IDEA_STATUSES].map(s =>
    `<button data-f="${s}" aria-pressed="${IDEA_FILTER === s}">${esc(s)}` +
    `${s === 'all' ? ` ${IDEAS.length}` : counts[s] ? ` ${counts[s]}` : ''}</button>`).join('');
}

function renderIdeas() {
  const q = $('#ideas-q').value.trim().toLowerCase();
  const rows = IDEAS.filter(i =>
    (IDEA_FILTER === 'all' || i.status === IDEA_FILTER) &&
    (!q || `${i.title} ${i.problem_statement || ''}`.toLowerCase().includes(q)));

  $('#ideas-body').innerHTML = rows.map(i => {
    const overall = +i.overall || 0;
    const tone = overall >= 7.5 ? 'score--hi' : overall >= 5 ? 'score--mid' : 'score--lo';
    const comp = i.competition_checked ? num(i.competition) : '<span class="xs">unchecked</span>';
    return `<tr data-id="${i.id}">
      <td class="num mono">${i.id}</td>
      <td><button class="btn btn--ghost btn--sm" data-open style="max-inline-size:42ch;text-align:start;white-space:normal">${esc(i.title)}</button></td>
      <td class="num"><span class="score ${tone}">${num(overall)}</span></td>
      <td class="num">${num(i.demand_signal)} / ${num(i.feasibility)} / ${comp}</td>
      <td>${pill(i.status)}</td>
      <td class="mono xs">${esc(i.critic_model || '—')}</td>
      <td><div class="row" style="gap:var(--s-2);flex-wrap:nowrap">
        <select data-mark aria-label="Set status for idea ${i.id}" style="inline-size:118px">
          ${IDEA_STATUSES.map(s => `<option ${s === i.status ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
        <button class="btn btn--ghost btn--sm" data-resynth>resynth</button>
      </div></td>
    </tr>`;
  }).join('') || emptyRow(7, IDEAS.length
    ? 'No idea matches that filter.'
    : 'Archive empty — run the pipeline to synthesize ideas.');
}

async function loadIdeas() {
  const res = await api('/api/ideas');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  IDEAS = res.ideas || [];
  COUNTS.ideas = IDEAS.length;
  renderNav();
  renderIdeaFilter();
  renderIdeas();
}

$('#ideas-q').addEventListener('input', renderIdeas);
$('#ideas-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-f]');
  if (!b) return;
  IDEA_FILTER = b.dataset.f;
  renderIdeaFilter();
  renderIdeas();
});
$('#ideas-body').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const id = +tr.dataset.id;
  if (e.target.closest('[data-open]')) showIdea(id);
  else if (e.target.closest('[data-resynth]')) runAction('/api/resynth', { id }, e.target.closest('button'));
});
$('#ideas-body').addEventListener('change', async e => {
  const sel = e.target.closest('select[data-mark]');
  if (!sel) return;
  const id = +sel.closest('tr').dataset.id;
  const res = await api('/api/mark', { id, status: sel.value });
  if (res.ok === false) toast(`mark failed: ${res.error || res.code}`, 'bad');
  else toast(`idea #${id} → ${sel.value}`, 'ok');
  loadIdeas();
});

// ── idea drawer ─────────────────────────────────────────────────────────
function openDrawer() {
  $('#drawer').classList.add('on');
  $('#drawer').setAttribute('aria-hidden', 'false');
  $('#scrim').classList.add('on');
  $('#drawer-close').focus();
}
function closeDrawer() {
  $('#drawer').classList.remove('on');
  $('#drawer').setAttribute('aria-hidden', 'true');
  $('#scrim').classList.remove('on');
}
$('#drawer-close').onclick = closeDrawer;
$('#scrim').onclick = closeDrawer;

async function showIdea(id) {
  const res = await api('/api/idea/' + id);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  const i = res.idea;
  $('#drawer-title').textContent = `#${i.id} — ${i.title}`;
  const cites = i.citations || [];
  const missing = cites.filter(c => !c.found).length;
  $('#drawer-body').innerHTML = `
    <div class="row">${pill(i.status)}
      <span class="score">${num(i.overall)}</span><span class="xs">overall</span></div>
    <dl class="dl">
      <dt>demand</dt><dd>${num(i.demand_signal)}</dd>
      <dt>feasibility</dt><dd>${num(i.feasibility)}</dd>
      <dt>competition</dt><dd>${i.competition_checked ? num(i.competition) : 'unchecked'}</dd>
      <dt>last scored</dt><dd>${esc(when(i.last_scored_at))}</dd>
      <dt>critic</dt><dd class="mono">${esc(i.critic_model || '—')}</dd>
      <dt>synthesis</dt><dd class="mono">${esc(i.synthesis_model || '—')}</dd>
    </dl>
    <div><b>Problem.</b> ${esc(i.problem_statement || '—')}</div>
    <div><b>Solution.</b> ${esc(i.proposed_solution || '—')}</div>
    ${i.competitor_notes ? `<div><b>Competitors.</b> ${esc(i.competitor_notes)}</div>` : ''}
    <h3>Evidence <span class="xs">${cites.length} citation(s)${missing ? `, ${missing} missing` : ''}</span></h3>
    ${cites.map(c => c.found
      ? `<div class="evidence">
           <div class="mono xs">${esc(c.key)}</div>
           <div class="xs">[${esc(c.detail.category || '')}]
             <a href="${esc(c.detail.source_url || '#')}" target="_blank" rel="noopener noreferrer">${esc(c.detail.source_url || '')}</a></div>
           <div style="margin-block-start:6px">${esc(c.detail.extracted_insight || '')}</div>
         </div>`
      : `<div class="evidence evidence--missing">
           <b>Missing citation.</b> <span class="mono">${esc(c.key)}</span> is referenced but not in the archive.
         </div>`).join('') || '<p class="xs">No citations recorded.</p>'}`;
  openDrawer();
}

// ── nuggets ─────────────────────────────────────────────────────────────
let NUGGETS = [];
let NUGGET_FILTER = 'all';
const NUGGET_FILTERS = ['all', 'flagged', 'trivial', 'needs reembed'];

function renderNuggetFilter() {
  $('#nuggets-filter').innerHTML = NUGGET_FILTERS.map(f =>
    `<button data-f="${esc(f)}" aria-pressed="${NUGGET_FILTER === f}">${esc(f)}</button>`).join('');
}

function renderNuggets() {
  const q = $('#nuggets-q').value.trim().toLowerCase();
  const rows = NUGGETS.filter(g => {
    if (NUGGET_FILTER === 'trivial' && !g.trivial) return false;
    if (NUGGET_FILTER === 'needs reembed' && !g.needs_reembed) return false;
    if (NUGGET_FILTER === 'flagged' && !g.trivial && !g.needs_reembed) return false;
    if (!q) return true;
    return `${g.unique_key} ${g.category || ''} ${g.extracted_insight || ''}`.toLowerCase().includes(q);
  });

  $('#nuggets-body').innerHTML = rows.map(g => {
    const flags = [
      g.trivial ? '<span class="chip chip--warn">trivial</span>' : '',
      g.needs_reembed ? '<span class="chip chip--bad">needs reembed</span>' : '',
      g.synthesized_at ? '' : '<span class="chip">unprocessed</span>',
    ].filter(Boolean).join('');
    return `<tr>
      <td class="mono xs">${esc(g.unique_key)}${g.source_url
        ? `<div><a href="${esc(g.source_url)}" target="_blank" rel="noopener noreferrer" class="xs">source ↗</a></div>` : ''}</td>
      <td>${esc(g.platform || '—')}</td>
      <td><span class="chip">${esc(g.category || 'uncategorised')}</span></td>
      <td style="max-inline-size:52ch">${esc(g.extracted_insight || '')}</td>
      <td><div class="chiprow">${flags || '<span class="xs">—</span>'}</div></td>
    </tr>`;
  }).join('') || emptyRow(5, NUGGETS.length
    ? 'No nugget matches that filter.'
    : 'No nuggets archived yet.');
}

async function loadNuggets() {
  const res = await api('/api/nuggets');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  NUGGETS = res.nuggets || [];
  COUNTS.nuggets = NUGGETS.length;
  renderNav();
  renderNuggetFilter();
  renderNuggets();
}

$('#nuggets-q').addEventListener('input', renderNuggets);
$('#nuggets-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-f]');
  if (!b) return;
  NUGGET_FILTER = b.dataset.f;
  renderNuggetFilter();
  renderNuggets();
});

// ── config ──────────────────────────────────────────────────────────────
const KNOB_HELP = {
  max_comments_per_thread: 'cap on comments taken from one thread',
  max_threads_per_source: 'threads / videos / topics the live worker walks per source, per run',
  min_comments_per_thread: 'skip quiet threads (Hacker News + Discourse only: they get a count before fetching)',
  min_upvotes: 'below this a comment is dropped before extraction',
  dedup_threshold: 'cosine similarity at which two nuggets are the same',
  prefilter_min_chars: 'shorter than this is noise',
  prefilter_max_chars: 'longer than this is an essay, not a pain signal',
  prefilter_max_emoji: 'emoji count that marks a comment as low-signal',
  prefilter_max_mentions: '@-mention count that marks a comment as low-signal',
  prefilter_min_words: 'fewer words than this is noise',
  request_delay_ms: 'pacing between navigations (§36 #7)',
  good_idea_min: 'overall score at which an idea counts as good',
  competitor_ttl_days: 're-run the competitor check when older than this',
  critic_web_rate_limit: 'critic web calls per minute',
  critic_web_daily_budget: 'critic web calls per day',
  embedding_model: 'model name passed to the embedding provider',
  competitor_search_provider: 'verifies competition before scoring (M2.3/D-1); none keeps it unchecked',
  llm_provider: 'fake (deterministic) or ollama (local daemon)',
  embedding_provider: 'fake (deterministic) or ollama (local daemon)',
};
// Per-key choices: not every *_provider takes the same values, and offering
// the llm choices for the competitor search would write an invalid config.
const PROVIDER_CHOICES = {
  llm_provider: ['fake', 'ollama'],
  embedding_provider: ['fake', 'ollama'],
  competitor_search_provider: ['none', 'fake', 'duckduckgo'],
};
let CFG_BASE = {};

function cfgDirty() {
  const out = {};
  for (const [key, base] of Object.entries(CFG_BASE)) {
    const el = $(`[data-knob="${key}"]`);
    if (!el) continue;
    const raw = el.value.trim();
    const changed = typeof base === 'number' ? +raw !== base : raw !== String(base);
    el.classList.toggle('dirty', changed);
    if (changed) out[key] = typeof base === 'number' ? +raw : raw;
  }
  return out;
}

function syncSavebar() {
  const n = Object.keys(cfgDirty()).length;
  $('#cfg-savebar').classList.toggle('hidden', n === 0);
  $('#cfg-dirty').textContent = `${n} unsaved change${n === 1 ? '' : 's'}`;
}

async function loadConfig() {
  const res = await api('/api/config');
  if (res.ok === false) { toast(res.error, 'bad'); return; }

  CFG_BASE = {};
  // embedding_model lives in the Agent models card, where the Ollama list is.
  $('#cfg-knobs').innerHTML = Object.entries(res.editable)
    .filter(([key]) => key !== 'embedding_model')
    .map(([key, meta]) => {
    CFG_BASE[key] = meta.value;
    const range = meta.range ? `${meta.range[0]} – ${meta.range[1]}` : '';
    const choices = PROVIDER_CHOICES[key];
    const control = choices
      ? `<select id="knob-${key}" data-knob="${key}">${choices.map(p =>
          `<option ${p === meta.value ? 'selected' : ''}>${p}</option>`).join('')}</select>`
      : `<input id="knob-${key}" data-knob="${key}" value="${esc(meta.value)}"
           ${meta.type === 'int' ? 'type="number" step="1"' : meta.type === 'float' ? 'type="number" step="0.01"' : ''}
           ${meta.range ? `min="${meta.range[0]}" max="${meta.range[1]}"` : ''}>`;
    return `<div class="knob">
      <label for="knob-${key}">${esc(key.replace(/_/g, ' '))}</label>
      ${control}
      <span class="hint">${esc(KNOB_HELP[key] || '')}${range ? ` · ${range}` : ''}</span>
    </div>`;
  }).join('');
  $$('#cfg-knobs [data-knob]').forEach(el => { el.oninput = syncSavebar; el.onchange = syncSavebar; });
  syncSavebar();

  $('#cfg-scraper').innerHTML = Object.entries(res.scraper).map(([k, v]) => {
    const shown = /license|proxy/i.test(k) ? (v ? '•••• set' : 'not set') : (v || '—');
    return `<dt>${esc(k)}</dt><dd class="mono">${esc(shown)}</dd>`;
  }).join('');

  $('#cfg-env').innerHTML = Object.entries(res.env).map(([k, v]) =>
    `<dt class="mono xs">${esc(k)}</dt><dd class="mono">${v === true ? 'set' : v === false ? 'not set' : esc(v || '—')}</dd>`
  ).join('');

  loadModels();
}

// ── agent models ────────────────────────────────────────────────────────
const ROLE_HELP = {
  extractor: 'turns one comment into a nugget',
  archivist: 'dedups and files nuggets into the archive',
  synthesizer: 'clusters nuggets into candidate ideas',
  critic: 'scores an idea and checks competitors',
  embedding_model: 'vectorises nuggets for dedup and search',
};
const CUSTOM = '__custom__';
let MODELS_BASE = {};
let MODELS_PROFILE = null;

function modelsDirty() {
  const out = {};
  for (const [role, base] of Object.entries(MODELS_BASE)) {
    const sel = $(`[data-model="${role}"]`);
    const txt = $(`[data-model-custom="${role}"]`);
    if (!sel) continue;
    const value = (sel.value === CUSTOM ? txt.value : sel.value).trim();
    const changed = value !== base;
    sel.classList.toggle('dirty', changed);
    if (txt) txt.classList.toggle('dirty', changed);
    if (changed && value) out[role] = value;
  }
  return out;
}

function syncModelsBar() {
  const n = Object.keys(modelsDirty()).length;
  $('#models-savebar').classList.toggle('hidden', n === 0);
  $('#models-dirty').textContent = `${n} unsaved change${n === 1 ? '' : 's'}`;
}

async function loadModels(profile) {
  const res = await api('/api/models' + (profile ? `?profile=${encodeURIComponent(profile)}` : ''));
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  MODELS_PROFILE = res.profile;
  MODELS_BASE = {};

  $('#models-profile').innerHTML = (res.profiles || []).map(p =>
    `<button data-profile="${esc(p.name)}" aria-pressed="${p.name === res.profile}"
       title="llm_provider: ${esc(p.llm_provider)}">${esc(p.name)}</button>`).join('');

  const badge = $('#models-ollama');
  badge.textContent = res.ollama_up ? `Ollama · ${res.installed.length} pulled` : 'Ollama down';
  badge.className = 'pill pill--sm ' + (res.ollama_up ? 'tone-done' : 'tone-off');

  $('#models-grid').innerHTML = res.roles.map(role => {
    const value = res.models[role] || '';
    MODELS_BASE[role] = value;
    // The configured model always appears, even when it is not pulled — the
    // console must show what the file says, then flag the gap.
    const options = [...new Set([...(res.installed || []), value].filter(Boolean))].sort();
    const known = options.includes(value);
    // The server decides what counts as pulled (`name` == `name:latest`).
    const missing = res.ollama_up && (res.missing || []).includes(value);
    return `<div class="knob">
      <label for="model-${role}">${esc(role.replace('_model', '').replace(/_/g, ' '))}
        ${missing ? '<span class="chip chip--warn">not pulled</span>' : ''}</label>
      <select id="model-${role}" data-model="${role}">
        ${options.map(o => `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('')}
        <option value="${CUSTOM}" ${known ? '' : 'selected'}>custom…</option>
      </select>
      <input data-model-custom="${role}" class="${known ? 'hidden' : ''}"
             value="${esc(known ? '' : value)}" placeholder="model name" autocomplete="off">
      <span class="hint">${esc(ROLE_HELP[role] || '')}</span>
    </div>`;
  }).join('');

  $$('#models-grid [data-model]').forEach(sel => {
    sel.onchange = () => {
      const txt = $(`[data-model-custom="${sel.dataset.model}"]`);
      txt.classList.toggle('hidden', sel.value !== CUSTOM);
      if (sel.value === CUSTOM) txt.focus();
      syncModelsBar();
    };
  });
  $$('#models-grid [data-model-custom]').forEach(el => { el.oninput = syncModelsBar; });
  syncModelsBar();
}

$('#models-profile').addEventListener('click', e => {
  const b = e.target.closest('button[data-profile]');
  if (!b || b.dataset.profile === MODELS_PROFILE) return;
  loadModels(b.dataset.profile);
});
$('#models-save').onclick = async e => {
  const patch = modelsDirty();
  if (!Object.keys(patch).length) return;
  const res = await busy(e.currentTarget, () => api('/api/models', { patch, profile: MODELS_PROFILE }));
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  toast(`saved ${Object.keys(res.applied).length} model(s) to ${res.profile}`, 'ok');
  loadModels(MODELS_PROFILE);
};
$('#models-reset').onclick = () => loadModels(MODELS_PROFILE);

$('#cfg-save').onclick = async e => {
  const patch = cfgDirty();
  if (!Object.keys(patch).length) return;
  const res = await busy(e.currentTarget, () => api('/api/thresholds', { patch }));
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  toast(`saved ${Object.keys(res.applied || patch).length} threshold(s)`, 'ok');
  loadConfig();
};
$('#cfg-reset').onclick = () => loadConfig();

// ── doctor ──────────────────────────────────────────────────────────────
async function loadDoctor() {
  const doc = await api('/api/doctor');
  // ok:false here means "found problems", not "the call failed" — only an
  // explicit error field is a transport failure.
  if (doc.error) { toast(doc.error, 'bad'); return; }
  const findings = doc.findings || [];
  COUNTS.findings = findings.length;
  renderNav();
  $('#doctor-card').innerHTML = findings.length === 0
    ? '<div class="banner banner--ok"><span aria-hidden="true">✓</span><span><b>Doctor clean</b>No silent-degradation guard tripped.</span></div>'
    : findings.map(f => `<div class="banner banner--bad" style="margin-block-end:var(--s-3)">
        <span aria-hidden="true">⚠</span><span>${esc(f)}</span></div>`).join('');
  loadEval();
}

async function loadEval() {
  const res = await api('/api/eval');
  const badge = $('#eval-badge');
  if (res.ok === false && res.error) {
    badge.textContent = 'error';
    badge.className = 'pill pill--sm tone-failed';
    $('#eval-info').innerHTML = `<p class="xs">${esc(res.error)}</p>`;
    return;
  }
  badge.textContent = res.ok ? 'PASS' : 'FAIL';
  badge.className = 'pill pill--sm ' + (res.ok ? 'tone-done' : 'tone-failed');
  $('#eval-info').innerHTML = `
    <dl class="dl"><dt>gate</dt><dd>${num(res.gate)}</dd>
      <dt>checks</dt><dd>${int(res.checks)}</dd>
      <dt>failures</dt><dd>${int(res.failures)}</dd></dl>
    ${(res.details || []).length ? `<div class="tablewrap" style="margin-block-start:var(--s-4)">
      <table><thead><tr><th></th><th>case</th><th class="num">overall</th><th>expected</th></tr></thead>
      <tbody>${res.details.map(d => `<tr>
        <td>${d.ok ? '✓' : '<span style="color:var(--failed)">✗</span>'}</td>
        <td>${esc(d.title)}</td><td class="num">${num(d.overall)}</td>
        <td class="xs">${d.expect_pass ? '≥ gate' : '< gate'}</td></tr>`).join('')}</tbody></table>
    </div>` : ''}`;
}
$('#eval-rerun').onclick = e => busy(e.currentTarget, loadEval);

// ── chrome ──────────────────────────────────────────────────────────────
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('jester-theme', theme); } catch { /* private mode */ }
}
$('#theme-btn').onclick = () =>
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

$('#refresh-btn').onclick = e => busy(e.currentTarget, async () => {
  await refreshCounts();
  await PAGES.find(p => p.id === CURRENT).load();
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeDrawer();
  // e.target is `document` when nothing is focused — it has no .matches().
  const t = e.target;
  if ((t instanceof Element && t.matches('input, select, textarea, [contenteditable]'))
      || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === 'r') $('#refresh-btn').click();
  const idx = '1234567'.indexOf(e.key);
  if (idx >= 0 && PAGES[idx]) go(PAGES[idx].id);
});

window.addEventListener('hashchange', () => {
  const id = location.hash.slice(1) || 'overview';
  if (id !== CURRENT) go(id, { push: false });
});

// boot
let saved = null;
try { saved = localStorage.getItem('jester-theme'); } catch { /* private mode */ }
applyTheme(saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
go(location.hash.slice(1) || 'overview', { push: false });
