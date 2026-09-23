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
/** 41,633 -> "41.6k". A run's platform mix is five of these side by side; the
 *  exact figure belongs in the tooltip, not in a column that then overflows. */
function compact(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) < 1000) return String(n);
  if (Math.abs(n) < 1e6) return (n / 1000).toFixed(n % 1000 === 0 || Math.abs(n) >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k';
  return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
}

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

// ── grouped tables ──────────────────────────────────────────────────────
// Two long lists on this page are really lists-of-lists: 91 sources across
// seven platforms, and thousands of nuggets across the communities they were
// read from. Flat, both bury the shape of the corpus — "how much of this is
// Reddit" was a question you had to answer by scrolling.

/** Which group headers are folded shut, by key. Lives for the session: a
 *  fold is a reading position, not a setting worth persisting. */
const FOLDED = new Set();

/** Bucket items by a key function, preserving first-seen order within each
 *  bucket (so a newest-first list stays newest-first inside its group). */
function groupBy(items, keyFn) {
  const out = new Map();
  for (const it of items) {
    const k = keyFn(it);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(it);
  }
  return out;
}

/** A foldable header row spanning the whole table.
 *  `depth` 1 is a platform, 2 a community inside it. */
function groupRow({ key, depth = 1, label, meta = '', count, span }) {
  const shut = FOLDED.has(key);
  return `<tr class="grp grp--${depth}${shut ? ' is-shut' : ''}" data-fold="${esc(key)}">
    <td colspan="${span}">
      <button type="button" class="grp-btn" aria-expanded="${!shut}">
        <span class="grp-caret" aria-hidden="true">${shut ? '▸' : '▾'}</span>
        <span class="grp-label">${label}</span>
        <span class="pill pill--sm tone-neutral">${esc(count.toLocaleString())}</span>
        ${meta ? `<span class="xs grp-meta">${meta}</span>` : ''}
      </button>
    </td></tr>`;
}

/** Delegated fold/unfold for any table using groupRow(). */
function bindFolding(tbodySel, rerender) {
  $(tbodySel).addEventListener('click', e => {
    const head = e.target.closest('[data-fold]');
    if (!head) return;
    const key = head.dataset.fold;
    FOLDED.has(key) ? FOLDED.delete(key) : FOLDED.add(key);
    rerender();
  });
}

/** A checkbox list of sources, grouped under a per-platform header whose own
 *  box ticks the whole platform. Two of these lists exist (the run modal and
 *  the schedule scope) and both were 91 flat rows, so "every subreddit" was
 *  eighteen clicks and "how much of this list is Discourse" was unanswerable.
 *
 *  `boxClass` is the class the page's own sync code already looks for, so the
 *  grouping is purely additive: the leaf checkboxes keep their identity.
 */
function checklistHTML(sources, { boxClass, checked, title }) {
  return [...groupBy(sources, s => s.platform || 'unknown')].map(([platform, rows]) => {
    const usable = rows.filter(s => s.supported).length;
    const head = `
      <label class="source-check-row source-grp${usable ? '' : ' is-unsupported'}">
        <input type="checkbox" class="grp-all" data-platform="${esc(platform)}"
               data-box="${esc(boxClass)}" ${usable ? '' : 'disabled'}
               aria-label="Select every ${esc(PLATFORM_LABEL[platform] || platform)} source">
        <strong>${esc(PLATFORM_LABEL[platform] || platform)}</strong>
        <span class="xs">${rows.length} source(s)${usable === rows.length ? '' : ` · ${usable} fetchable`}</span>
      </label>`;
    return head + rows.map(s => `
      <label class="source-check-row is-child${s.supported ? '' : ' is-unsupported'}"
             title="${title(s)}">
        <input type="checkbox" class="${esc(boxClass)}" data-source-name="${esc(s.name)}"
               data-platform="${esc(s.platform || 'unknown')}"
               ${s.supported && checked(s) ? 'checked' : ''} ${s.supported ? '' : 'disabled'}>
        <strong class="truncate">${esc(s.name)}</strong>
        <span class="xs">${esc(s.kind)}${s.supported ? '' : ' · no adapter'}</span>
      </label>`).join('');
  }).join('');
}

/** Reflect the leaf boxes back into their platform header, including the
 *  in-between state — a header reading "on" over a half-ticked platform is how
 *  an operator starts a run over the wrong list. */
function syncChecklistGroups(containerSel, boxClass) {
  for (const head of $$(`${containerSel} .grp-all`)) {
    const boxes = $$(`${containerSel} .${boxClass}:not([disabled])`)
      .filter(b => b.dataset.platform === head.dataset.platform);
    const on = boxes.filter(b => b.checked).length;
    head.checked = on > 0 && on === boxes.length;
    head.indeterminate = on > 0 && on < boxes.length;
  }
}

/** Ticking a platform header ticks its platform. Returns true if it handled
 *  the event, so the caller can skip its own leaf-level sync ordering. */
function handleGroupToggle(e, containerSel) {
  const head = e.target.closest('.grp-all');
  if (!head) return false;
  $$(`${containerSel} .${head.dataset.box}:not([disabled])`)
    .filter(b => b.dataset.platform === head.dataset.platform)
    .forEach(b => { b.checked = head.checked; });
  return true;
}

/** tone drives the colour, label is what the operator reads. */
const tag = (tone, label, extra = '') =>
  `<span class="pill ${extra} tone-${esc(String(tone || 'neutral').toLowerCase())}">` +
  `<span class="dot"></span>${esc(label)}</span>`;

const pill = (status, extra = '') => tag(status || 'neutral', status || 'unknown', extra);

/** Where vectors actually go, and whether that store can be written to. */
function vectorPill(v) {
  if (!v) return '';
  const bits = [v.mode === 'server' ? 'Qdrant server' : 'Qdrant local file'];
  if (v.points !== null && v.points !== undefined) bits.push(`${v.points} pts`);
  if (v.dim) bits.push(`${v.dim}d`);
  const tone = !v.usable ? 'off'
    : (v.embedding_provider || '').toLowerCase() === 'fake' ? 'warn'
    : 'done';
  const pillEl = tag(tone, bits.join(' · '), 'pill--sm');
  // The detail is the whole point when something is wrong: a width mismatch
  // or a hash-vector store reads as healthy until the next write.
  return v.detail
    ? `<span title="${esc(v.detail)}">${pillEl}</span>`
    : pillEl;
}


const emptyRow = (cols, text, next) =>
  `<tr><td colspan="${cols}"><div class="empty">${esc(text)}${next
    ? `<div><button class="btn btn--sm" data-go="${esc(next.page)}">${esc(next.label)}</button></div>` : ''}</div></td></tr>`;
/** Plain words for the counters. The table names are the pipeline's; these are the operator's. */
const LABEL = {
  nuggets: 'nuggets', ideas: 'ideas', pending_batches: 'queued batches', failed_batches: 'failed batches',
  reembed_backlog: 'not yet searchable', unprocessed: 'waiting for ideas', runs_active: 'active runs',
};
/** A 7-point sparkline as inline SVG. `values` newest last. */
function spark(values, { w = 120, h = 28 } = {}) {
  const v = (values || []).map(Number);
  if (!v.length) return '';
  const max = Math.max(1, ...v), n = v.length;
  const pts = v.map((y, i) => `${(i / (n - 1 || 1)) * w},${h - 2 - (y / max) * (h - 6)}`);
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true">
    <polygon class="area" points="0,${h} ${pts.join(' ')} ${w},${h}"/>
    <polyline points="${pts.join(' ')}"/>
    <circle cx="${w}" cy="${pts[n - 1].split(',')[1]}" r="2.5" fill="currentColor"/></svg>`;
}
/** Which platforms the worker reaches through cloakserve. Mirrors needsBrowser() in go/cmd/worker. */
const BROWSER_PLATFORMS = new Set(['reddit', 'youtube', 'tiktok', 'realestate']);
let INFRA = null;

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
  { group: 'Pipeline', id: 'queue', label: 'Queue', icon: '⧗', load: loadQueue, count: () => COUNTS.pending },
  { group: 'Archive', id: 'ideas', label: 'Ideas', icon: '✦', load: loadIdeas, count: () => COUNTS.ideas },
  { group: 'Archive', id: 'nuggets', label: 'Nuggets', icon: '◦', load: loadNuggets, count: () => COUNTS.nuggets },
  { group: 'Archive', id: 'clusters', label: 'Clusters', icon: '❋', load: loadClusters, count: () => COUNTS.clusters },
  { group: 'Archive', id: 'search', label: 'Search', icon: '⌕', load: loadSearch },
  // Its own group: this is a task deliverable with its own database, its
  // own rules and its own reviewer, not another view of Jester's archive.
  { group: 'the collector', id: 'signals', label: 'Problem signals', icon: '◎',
    load: loadSignals, count: () => COUNTS.signals },
  { group: 'Setup', id: 'sources', label: 'Sources', icon: '⌁', load: loadSources, count: () => COUNTS.sources },
  { group: 'Setup', id: 'config', label: 'Config', icon: '⚙', load: loadConfig },
  { group: 'Setup', id: 'schedule', label: 'Schedule', icon: '◷', load: loadSchedule },
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
  } else if (res.idle) {
    // A run that completed having archived nothing is not an error, but
    // calling it "ok" is what made the console feel broken: the operator got a
    // green toast every time and no new rows, forever.
    toast(`${label}: ${res.note}`, 'bad');
  } else {
    const detail = [
      res.findings ? `${res.findings.length} finding(s)` : '',
      res.requeued !== undefined ? `${res.requeued} requeued` : '',
      res.fixed !== undefined ? `${res.fixed} reembedded` : '',
      res.queued_new_comments !== undefined ? `${res.queued_new_comments} new comment(s)` : '',
      res.archived
        ? `+${res.archived.nuggets} nugget(s), +${res.archived.ideas} idea(s)` : '',
      res.counts && res.out_dir
        ? `${res.counts.nuggets} nugget(s) + ${res.counts.ideas} idea(s) → ${res.out_dir}` : '',
    ].filter(Boolean).join(' · ');
    toast(`${label}: ok${detail ? ' — ' + detail : ''}`, 'ok');
  }
  // A multi-step run reports each step's own log, not just the last one's.
  (res.steps || []).forEach(st => {
    if (st.queued_new_comments !== undefined) {
      toast(`${st.step}: ${st.queued_new_comments} new comment(s) queued`,
            st.queued_new_comments ? 'ok' : 'bad');
    }
  });
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
  const [ov, infra, tr] = await Promise.all([refreshCounts(), api('/api/infra'), api('/api/trends?days=7')]);
  if (ov.ok === false) { toast(ov.error, 'bad'); return; }
  if (infra.ok !== false) INFRA = infra;
  const c = ov.counts;
  const t = tr.ok === false ? null : tr;
  const today = k => t ? Number(t[k][t[k].length - 1] || 0) : null;
  const sum = k => t ? t[k].reduce((a, b) => a + Number(b), 0) : 0;

  // Headline: what changed today, in one sentence.
  const bits = [];
  if (t) {
    bits.push(today('nuggets') ? `+${today('nuggets').toLocaleString()} nuggets today` : 'no new nuggets today');
    bits.push(today('ideas') ? `+${today('ideas')} idea(s) today` : `no new ideas today`);
    if (today('bad_runs')) bits.push(`${today('bad_runs')} run(s) ended badly`);
  }
  $('#ov-lede').textContent = bits.length ? bits.join(' · ') + '.' : 'Archive and pipeline at a glance.';

  const tile = (key, value, series, deltaText, cls = '') =>
    `<div class="kpi ${cls}"><b class="num">${int(value)}</b><span>${esc(LABEL[key] || key)}</span>
       ${series ? spark(series) : ''}${deltaText ? `<span class="delta ${cls.includes('alert') ? 'bad' : deltaText.startsWith('+') ? 'up' : ''}">${esc(deltaText)}</span>` : ''}</div>`;
  $('#kpis').innerHTML = [
    tile('nuggets', c.nuggets, t && t.nuggets, t ? `+${today('nuggets').toLocaleString()} today · ${sum('nuggets').toLocaleString()} this week` : ''),
    tile('ideas', c.ideas, t && t.ideas, t ? (today('ideas') ? `+${today('ideas')} today` : `none this week — see Runs`) : ''),
    tile('pending_batches', c.pending_batches, t && t.queued, t ? `${today('queued').toLocaleString()} queued today` : ''),
    tile('failed_batches', c.failed_batches, t && t.bad_runs, t ? `${sum('bad_runs')} bad run(s) this week` : '', c.failed_batches ? 'kpi--alert' : ''),
  ].join('');
  $('#kpis-more').innerHTML = [
    ['reembed_backlog', c.reembed_backlog, c.reembed_backlog ? 'kpi--alert' : ''],
    ['unprocessed', c.unprocessed, ''],
    ['runs_active', c.runs_active, c.runs_active ? 'kpi--live' : ''],
  ].map(([k, v, cls]) => `<div class="kpi ${cls}"><b class="num">${int(v)}</b><span>${esc(LABEL[k])}</span></div>`).join('');

  const banners = [];
  if (c.failed_batches) banners.push(['bad',
    `${c.failed_batches} failed ingest batch(es)`, 'Requeue them, or check the worker log.', { act: '/api/requeue', label: 'requeue' }]);
  if (c.reembed_backlog) banners.push(['',
    `${c.reembed_backlog} nugget(s) not yet searchable`, 'They are archived but skipped by dedup and search until re-embedded.', { act: '/api/reembed', label: 're-embed now' }]);
  const s = ov.sources_summary;
  if (s && s.unsupported) banners.push(['',
    `${s.unsupported} enabled source(s) have no adapter yet`,
    'The worker reports them as skipped rather than ingesting them.', { page: 'sources', label: 'open Sources' }]);
  if (s && !s.enabled) banners.push(['bad', 'No sources are enabled',
    'A live worker run has nothing to fetch.', { page: 'sources', label: 'add a source' }]);
  if (INFRA && !INFRA.cloakserve_9222) banners.push(['bad', 'cloakserve is down',
    'Every Reddit, YouTube and real-estate source is skipped until it is back (docker compose --profile live up -d cloakbrowser).', { page: 'sources', label: 'see affected sources' }]);
  $('#ov-banners').innerHTML = banners.map(([tone, head, body, next]) =>
    `<div class="banner ${tone === 'bad' ? 'banner--bad' : ''}"><span aria-hidden="true">${tone === 'bad' ? '⚠' : 'ℹ'}</span>
     <span style="flex:1"><b>${esc(head)}</b>${esc(body)}</span>
     ${next ? `<button class="btn btn--sm" ${next.act ? `data-act="${next.act}"` : `data-go="${next.page}"`}>${esc(next.label)}</button>` : ''}</div>`).join('');

  const lr = ov.last_run;
  $('#last-run').innerHTML = lr
    ? `${pill(lr.status)}
       <dl class="dl" style="margin-block-start:var(--s-4)">
         <dt>run</dt><dd class="mono">${esc(lr.run_id)}</dd>
         <dt>started</dt><dd>${esc(when(lr.started_at))} <span class="xs">${esc(ago(lr.started_at))}</span></dd>
         <dt>kept / ideas</dt><dd>${int(lr.n_nuggets_kept)} / ${int(lr.n_ideas)}${zeroYieldWhy(lr)}</dd>
         <dt>duration</dt><dd>${num(lr.duration_actual_s, 1)}s <span class="xs">expected ${num(lr.duration_expected_s, 1)}s</span></dd>
       </dl>
       <p style="margin:var(--s-4) 0 0"><button class="btn btn--ghost btn--sm" data-go="runs">all runs ›</button></p>`
    : `<div class="empty">No run yet.<div><button class="btn btn--sm" data-act="/api/run">▶ run pipeline</button></div></div>`;

  // Quota: one meter, platform segments, zeros hidden behind the legend.
  const q = ov.quota;
  const used = Object.entries(q.used_by_platform).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const total = q.used_total || 0;
  const shades = ['1', '.75', '.55', '.4', '.3', '.22', '.16', '.12'];
  $('#quota').innerHTML = total
    ? `<div class="row" style="justify-content:space-between"><b class="num">${total.toLocaleString()}</b><span class="xs mono">${esc(q.day_key)}</span></div>
       <div class="meter" style="margin-block-start:var(--s-3)">${used.map(([k, v], i) =>
         `<i style="inline-size:${(v / total) * 100}%;background:var(--ink);opacity:${shades[i] || '.1'}" title="${esc(k)} ${v}"></i>`).join('')}</div>
       <div class="meter-legend">${used.map(([k, v], i) =>
         `<span><i style="background:var(--ink);opacity:${shades[i] || '.1'}"></i>${esc(k)} <span class="num">${v.toLocaleString()}</span></span>`).join('')}</div>`
    : `<p class="xs">Nothing fetched yet today (${esc(q.day_key)}). The ledger fills as the worker walks sources.</p>`;

  if (INFRA) {
    const rows = [
      [INFRA.ollama.up, 'Ollama', INFRA.ollama.up ? `${INFRA.ollama.models.length} model(s) · local models and embeddings` : 'down → live extraction and embeddings fall back to the deterministic stand-in'],
      [INFRA.cloakserve_9222, 'cloakserve :9222', INFRA.cloakserve_9222 ? 'browser scraping available' : 'down → Reddit, YouTube and real-estate sources are skipped'],
      [INFRA.vectors && INFRA.vectors.usable, 'Vectors', vectorPill(INFRA.vectors) || 'no store', true],
      [INFRA.worker_go_available, 'Go worker', INFRA.worker_go_available ? esc(INFRA.go_bin || 'found') : 'not found → nothing can be fetched'],
    ];
    $('#infra').innerHTML = rows.map(([up, name, why, raw]) =>
      `<div class="irow ${up ? '' : 'off'}"><span class="led"></span><span><b>${esc(name)}</b><span class="why">${raw ? why : esc(why)}</span></span></div>`).join('')
      + `<p class="xs" style="margin:var(--s-3) 0 0">${INFRA.cached ? 'checked within the last 30 s' : 'checked just now'} · <button class="btn btn--ghost btn--sm" data-infra-fresh>re-check</button></p>`;
    $('#btn-live-models').classList.toggle('hidden',
      !(INFRA.ollama.up && INFRA.ollama.models.some(m => /qwen|mistral|phi|llama|gemma/i.test(m))));
    $('#btn-ingest-live').classList.toggle('hidden', !INFRA.can_ingest_live);
    $('#btn-ingest-mock').classList.toggle('hidden', !INFRA.worker_go_available);
  }
}
/** Why a run kept nothing, from the flags it raised. "0 / 0" for two weeks was
 *  the biggest fact on the Runs page and nothing explained it. */
function zeroYieldWhy(r) {
  if (r.status !== 'completed' || Number(r.n_nuggets_kept) > 0) return '';
  const flags = r.floor_flags_list || (typeof r.floor_flags === 'string' ? JSON.parse(r.floor_flags || '[]') : []);
  const why = flags.includes('DEDUP_UNAVAILABLE') ? 'dedup down'
    : flags.includes('QUOTA_EXHAUSTED') ? 'quota spent'
    : flags.includes('LOW_NUGGETS') ? 'nothing new'
    : Number(r.n_comments) === 0 ? 'nothing fetched' : '';
  return why ? ` <span class="xs why" title="${esc(flags.join(', '))}">· ${esc(why)}</span>` : '';
}
$('#ov-more').onclick = () => {
  const card = $('#kpis-more-card');
  card.classList.toggle('hidden');
  $('#ov-more').setAttribute('aria-expanded', String(!card.classList.contains('hidden')));
};
$('#qa-help').onclick = () => {
  const t = $('#qa-help-text');
  t.classList.toggle('hidden');
  $('#qa-help').setAttribute('aria-expanded', String(!t.classList.contains('hidden')));
};
$('#infra').addEventListener('click', async e => {
  if (!e.target.closest('[data-infra-fresh]')) return;
  const res = await api('/api/infra?fresh=1');
  if (res.ok !== false) { INFRA = res; loadOverview(); }
});
$('#ov-banners').addEventListener('click', e => {
  const b = e.target.closest('button[data-act]');
  if (b) runAction(b.dataset.act, {}, b);
});
$('#quick-actions').addEventListener('click', e => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  // "run pipeline" is configured before it runs; every other action is
  // immediate. openPipelineModal is defined further down — this listener only
  // ever runs on a click, long after the module has finished evaluating.
  if (btn.dataset.act === '/api/run' && !btn.dataset.liveModels) {
    openPipelineModal();
    return;
  }
  const body = {};
  if (btn.dataset.runPrefix) body.run_id = `${btn.dataset.runPrefix}-${Date.now()}`;
  if (btn.dataset.liveModels) body.live_models = true;
  if (btn.dataset.live) body.live = true;
  runAction(btn.dataset.act, body, btn);
});

// ── sources ─────────────────────────────────────────────────────────────

/** When the worker last walked this source, and what it yielded. The run order
 *  is decided by this column, so it has to be readable — otherwise rotation
 *  looks like the worker picking sources at random. */
function fetchedCell(state) {
  if (!state) return '<span class="xs">never · next in line</span>';
  const yield_ = state.last_queued
    ? `+${state.last_queued} comment(s)`
    : 'nothing new';
  const total = Number(state.total_queued || 0);
  const measured = Number(state.measured_visits || 0);
  const lifetime = total ? `${total} total`
    : measured ? 'none ever' : 'lifetime not recorded';
  return `<span class="mono xs">${esc(ago(state.last_fetched_at))}</span>` +
    `<div class="xs">${esc(yield_)} · ${esc(state.visits)} visit(s) · ${esc(lifetime)}</div>`;
}
/** The verdict the server drew from source_state. `unknown` is shown as its
 *  own thing rather than folded into a bad one: a source nobody has walked yet
 *  has produced no evidence, and dressing that up as a health reading is the
 *  fastest way to park a source that was simply never tried. */
const HEALTH_TAG = {
  producing: ['done', 'producing'],
  quiet: ['', 'quiet'],
  barren: ['warn', 'nothing yet'],
  unmeasured: ['off', 'yield not recorded'],
  unknown: ['off', 'not yet visited'],
};
function healthTag(s) {
  const [cls, label] = HEALTH_TAG[s.health] || HEALTH_TAG.unknown;
  return `<span class="pill pill--sm ${cls ? 'pill--' + cls : ''}"
    title="${esc(s.health_reason || '')}">${esc(label)}</span>`;
}

/** Parking is OFFERED, never applied. A barren source may be barren because
 *  the site was down for a week, and a console that quietly switches sources
 *  off leaves the operator with a shrinking list and no idea why. */
function parkButton(s) {
  if (!s.enabled || s.health !== 'barren') return '';
  return `<button class="btn btn--sm" data-park
    title="${esc(s.health_reason || '')} — pause it until you say otherwise"
    aria-label="Park ${esc(s.name)}">park</button>`;
}

const PLATFORM_LABEL = {
  reddit: 'Reddit', hackernews: 'Hacker News', discourse: 'Discourse',
  youtube: 'YouTube', tiktok: 'TikTok', stackexchange: 'Stack Exchange',
  github: 'GitHub', lemmy: 'Lemmy', steam: 'Steam', podcast: 'Podcast',
  realestate: 'Real estate',
};
// Platforms the parser accepts are served by the API, so adding an adapter
// never needs a matching edit here.
function renderPlatformOptions(platformKinds, gaps = {}) {
  const sel = $('#src-platform');
  const keep = sel.value;
  sel.innerHTML = '<option value="auto">detect from link</option>' +
    Object.keys(platformKinds || {}).map(p => {
      // A platform the parser accepts but the worker cannot fetch stays in the
      // list — removing it would answer a pasted link with "cannot tell which
      // platform", which is false. It says why instead.
      const why = gaps[p];
      return `<option value="${esc(p)}"${why ? ` title="${esc(why)}"` : ''}>` +
        `${esc(PLATFORM_LABEL[p] || p)}${why ? ' — no adapter' : ''}</option>`;
    }).join('');
  if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
}

//: The last /api/sources response, so folding a platform open or shut is a
//: re-render rather than a round trip — and so a fold survives one.
let SOURCES_RES = { sources: [] };

async function loadSources() {
  const [res, infra] = await Promise.all([api('/api/sources'), INFRA ? Promise.resolve(INFRA) : api('/api/infra')]);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  if (infra && infra.ok !== false) INFRA = infra;
  SOURCES_RES = res;
  renderPlatformOptions(res.platform_kinds, res.platform_gaps);
  renderSources();
}

function renderSources() {
  const res = SOURCES_RES;
  const list = res.sources || [];
  COUNTS.sources = list.filter(s => s.enabled).length;
  renderNav();

  $('#src-path').textContent = res.path || '';
  $('#src-count').textContent = `${COUNTS.sources} enabled / ${list.length} total`;

  const order = { hackernews: 0, reddit: 1, discourse: 2, stackexchange: 3,
                  github: 4, lemmy: 5, steam: 6, podcast: 7, youtube: 8, tiktok: 9,
                  realestate: 10 };
  const sorted = [...list].sort((a, b) =>
    (order[a.platform] ?? 9) - (order[b.platform] ?? 9) || a.name.localeCompare(b.name));

  // Grouped by platform, because that is how the list is actually reasoned
  // about — "is Reddit still carrying this corpus" is one glance, not a scroll
  // through 91 rows. The sort above already clusters them; the headers make
  // the clusters countable and foldable.
  const byPlatform = groupBy(sorted, s => s.platform || 'unknown');
  $('#src-body').innerHTML = [...byPlatform].map(([platform, rows]) => {
    const key = `src:${platform}`;
    const on = rows.filter(r => r.enabled).length;
    const queued = rows.reduce((t, r) => t + Number(r.state?.total_queued || 0), 0);
    const barren = rows.filter(r => r.enabled && r.health === 'barren').length;
    const meta = [
      on === rows.length ? 'all enabled' : `${on} enabled`,
      queued ? `${queued.toLocaleString()} comment(s) queued` : '',
      barren ? `${barren} barren` : '',
    ].filter(Boolean).join(' · ');
    const head = groupRow({
      key, depth: 1, span: 8, count: rows.length, meta: esc(meta),
      label: esc(PLATFORM_LABEL[platform] || platform),
    });
    return head + (FOLDED.has(key) ? '' : rows.map(sourceRow).join(''));
  }).join('') || emptyRow(8, 'No sources yet — add one above.');
  /** How the worker reaches this source, and whether it can right now. A
   *  Reddit source read "quiet" for 26 days while cloakserve was down; the
   *  page never said the fetch was impossible. */
  function fetchCell(s) {
    if (!s.supported) return '<span class="xs">—</span>';
    const browser = BROWSER_PLATFORMS.has(s.platform);
    const blocked = browser && INFRA && !INFRA.cloakserve_9222;
    const noGo = INFRA && !INFRA.worker_go_available;
    return `<span class="chip" title="${browser ? 'fetched through the stealth browser (cloakserve)' : 'fetched through a public HTTP API'}">${browser ? 'browser' : 'api'}</span>`
      + (blocked ? '<div class="xs why" title="start it: docker compose --profile live up -d cloakbrowser">blocked · cloakserve down</div>'
        : noGo ? '<div class="xs why">blocked · no Go worker</div>' : '');
  }

  function sourceRow(s) {
    const status = !s.enabled ? tag('off', 'paused', 'pill--sm')
      : !s.supported ? tag('warn', 'no adapter yet', 'pill--sm')
        : healthTag(s);
    return `<tr data-name="${esc(s.name)}">
      <td><label class="switch"><input type="checkbox" data-toggle ${s.enabled ? 'checked' : ''}
          aria-label="Enable ${esc(s.name)}"></label></td>
      <td class="mono">${esc(s.name)}</td>
      <td><span class="chip">${esc(s.kind || 'unknown')}</span></td>
      <td><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer"
             class="truncate" style="display:block;max-inline-size:34ch"
             title="${esc(s.url)}">${esc(s.url.replace(/^https?:\/\/(www\.)?/, ''))}</a>
          ${s.notes ? `<div class="xs">${esc(s.notes)}</div>` : ''}</td>
      <td>${fetchCell(s)}</td>
      <td>${fetchedCell(s.state)}</td>
      <td>${status}</td>
      <td class="row-actions">${parkButton(s)}<button class="btn btn--danger btn--sm" data-delete aria-label="Remove ${esc(s.name)}">remove</button></td>
    </tr>`;
  }

  const unsupported = list.filter(s => s.enabled && !s.supported);
  const barren = list.filter(s => s.enabled && s.health === 'barren');
  const dupes = res.duplicate_names || [];
  const notes = [];
  if (dupes.length) {
    // Worth saying first: it makes every other number on this page wrong.
    notes.push(`<span class="chip chip--warn">name clash</span> ${dupes.length} name(s)
      (${esc(dupes.join(', '))}) are used twice. The worker keys a source's fetch history by
      name, so those sources share one history and one rotation slot — rename one of each.`);
  }
  if (unsupported.length) {
    notes.push(`<span class="chip chip--warn">heads up</span> ${unsupported.length} enabled source(s)
      (${esc(unsupported.map(s => s.name).join(', '))}) have no worker adapter yet — the run will
      report them as skipped rather than quietly ingest nothing.`);
  }
  if (barren.length) {
    notes.push(`<span class="chip">quiet list</span> ${barren.length} enabled source(s)
      (${esc(barren.map(s => s.name).join(', '))}) have been walked
      ${res.park_after_barren_visits || 3}+ times and never queued a comment. Each one still
      costs a rotation slot every run — “park” pauses it without removing it.`);
  }
  const gaps = res.platform_gaps || {};
  for (const [p, why] of Object.entries(gaps)) {
    notes.push(`<span class="chip">${esc(PLATFORM_LABEL[p] || p)}</span> has no adapter and is
      not planned: ${esc(why)}`);
  }
  $('#src-hint').innerHTML = notes.join('<br>')
    || 'Changes are written to <span class="mono">sources.yaml</span> immediately; the worker picks them up on its next run.';
}

bindFolding('#src-body', renderSources);

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
  const park = e.target.closest('button[data-park]');
  if (park) {
    const name = park.closest('tr').dataset.name;
    const res = await api('/api/sources/update', { name, enabled: false });
    if (res.ok === false) { toast(res.error, 'bad'); return; }
    toast(`${name} parked — re-enable it any time with the switch`, 'ok');
    loadSources();
    return;
  }
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
let RUNS = [];

// Origin is the difference between "I pressed this" and "the host did it at
// 4am", which is the first thing you want to filter on once a schedule exists.
let RUNS_ORIGIN = 'all';

function renderRunsFilter() {
  const counts = { all: RUNS.length };
  for (const r of RUNS) {
    const o = r.origin || 'manual';
    counts[o] = (counts[o] || 0) + 1;
  }
  // Only offer an origin the ledger actually contains; a "scheduled 0" tab on a
  // box with no schedule is a dead control.
  const origins = ['all', ...Object.keys(counts).filter(o => o !== 'all').sort()];
  $('#runs-filter').innerHTML = origins.map(o =>
    `<button data-origin="${esc(o)}" aria-pressed="${o === RUNS_ORIGIN}">` +
    `${esc(o)} <span class="num">${counts[o] || 0}</span></button>`).join('');
}

function renderRuns() {
  const rows = RUNS.filter(r =>
    RUNS_ORIGIN === 'all' || (r.origin || 'manual') === RUNS_ORIGIN);
  $('#runs-count').textContent = `${rows.length} of ${RUNS.length} run(s)`;
  $('#runs-body').innerHTML = rows.map(r => {
    const funnel = Object.entries(r.prefilter_funnel_obj || {});
    const near = (r.top_near_misses_list || []).map(v => (+v).toFixed(2));
    const platforms = Object.entries(r.platform_counts_obj || {}).sort((a, b) => b[1] - a[1]);
    const totalP = platforms.reduce((t, [, n]) => t + Number(n), 0) || 1;
    // One stacked bar for the platform mix, red pills only for what needs
    // eyes, the funnel and near-misses in a tooltip. The old cell rendered
    // twelve identical grey chips and the flags drowned in them.
    const mixTitle = platforms.map(([p, n]) => `${p} ${Number(n).toLocaleString()}`).join(' · ');
    const sigbar = platforms.length
      ? `<div class="sigbar" title="${esc(mixTitle)}">${platforms.map(([p, n], i) =>
          `<i style="inline-size:${(Number(n) / totalP) * 100}%;--i:${i}" title="${esc(p)} ${Number(n).toLocaleString()}"></i>`).join('')}</div>
         <span class="xs truncate" title="${esc(mixTitle)}">${esc(platforms.slice(0, 2).map(([p, n]) => `${p} ${compact(n)}`).join(' · '))}${platforms.length > 2 ? ` +${platforms.length - 2}` : ''}</span>`
      : '<span class="xs">nothing fetched</span>';
    const flags = (r.floor_flags_list || []).map(f => `<span class="chip chip--bad" title="run warning">${esc(f.toLowerCase().replace(/_/g, ' '))}</span>`).join('');
    const funnelTip = [...funnel.map(([k, v]) => `${k} ${v}`), near.length ? `near-misses ${near.join(', ')}` : ''].filter(Boolean).join(' · ');
    const errorChip = r.error ? `<span class="chip chip--bad" title="${esc(r.error)}">error</span>` : '';
    const origin = r.origin || 'manual';
    const exp = Number(r.duration_expected_s) || 0, act = Number(r.duration_actual_s) || 0;
    const ratio = exp ? act / exp : 0;
    const ratioCls = exp && ratio < .2 ? 'low' : exp && ratio > 1.5 ? 'high' : '';
    const kept = Number(r.n_nuggets_kept) || 0;
    return `<tr data-id="${r.id}" class="clickable" title="Open this run">
      <td><span>${esc(when(r.started_at))}</span>
          <div class="xs truncate" title="${esc(r.run_id)}">${esc(ago(r.started_at))} · ${compact(r.n_posts)} posts · <span class="mono">${esc(r.run_id)}</span></div></td>
      <td>${pill(r.status)}</td>
      <td class="num"><span class="${kept ? '' : 'mute'}">${int(r.n_nuggets_kept)} / ${int(r.n_ideas)}</span>${zeroYieldWhy(r)}</td>
      <td class="num">${num(r.duration_actual_s, 1)}s
          <div class="xs dur-exp">${exp ? `<span class="ratio ${ratioCls}" title="${Math.round(ratio * 100)}% of the ${num(r.duration_expected_s, 1)}s expected"><i style="inline-size:${Math.min(100, ratio * 100)}%"></i></span>` : ''}exp ${num(r.duration_expected_s, 0)}s</div></td>
      <td><span class="chip${origin === 'scheduled' ? ' chip--sched' : ''}"
            >${origin === 'scheduled' ? '◷ ' : ''}${esc(origin)}</span></td>
      <td><div class="sig" title="${esc(funnelTip)}">${sigbar}
        ${flags || errorChip ? `<div class="chiprow sig-flags">${flags}${errorChip}</div>` : ''}</div></td>
    </tr>`;
  }).join('') || emptyRow(6, RUNS.length
    ? `No ${esc(RUNS_ORIGIN)} runs yet.`
    : 'No runs yet.', RUNS.length ? null : { page: 'overview', label: '▶ run pipeline from Overview' });
}

async function loadRuns() {
  const res = await api('/api/runs');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  RUNS = res.runs || [];
  COUNTS.runs = RUNS.length;
  renderNav();
  renderRunsFilter();
  renderRuns();
}

$('#runs-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-origin]');
  if (!b) return;
  RUNS_ORIGIN = b.dataset.origin;
  renderRunsFilter();
  renderRuns();
});

$('#runs-body').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  const id = +tr.dataset.id;
  showRun(id);
});

function showRun(id) {
  const r = RUNS.find(run => run.id === id);
  if (!r) { toast('Run not found', 'bad'); return; }

  $('#drawer-title').textContent = `Run: ${r.run_id || r.id}`;

  const phases = JSON.parse(r.phase_checkpoints || '[]');
  const phasesList = phases.map(p =>
    `<div class="row xs" style="gap:var(--s-3)">
      <span class="chip">${esc(p.phase)}</span>
      <span>${esc(when(p.at))}</span>
    </div>`
  ).join('') || '<p class="xs">No phase checkpoints recorded.</p>';

  const models = r.models || {};
  const modelsList = models.models ? models.models.map(m =>
    `<span class="chip mono">${esc(m)}</span>`
  ).join(' ') : '<span class="xs">—</span>';

  const promptVers = models.prompt_versions || {};
  const promptInfo = Object.keys(promptVers).length
    ? Object.entries(promptVers).map(([k, v]) => `${k}: v${v}`).join(', ')
    : '—';

  const funnel = Object.entries(r.prefilter_funnel_obj || {});
  const funnelList = funnel.length
    ? funnel.map(([k, v]) => `<div class="row"><strong>${esc(k)}:</strong> ${esc(v)}</div>`).join('')
    : '<p class="xs">No funnel data.</p>';

  const platforms = Object.entries(r.platform_counts_obj || {});
  const platformsList = platforms.length
    ? platforms.map(([p, n]) => `<span class="chip">${esc(p)}: ${esc(n)}</span>`).join(' ')
    : '<span class="xs">—</span>';

  const flags = (r.floor_flags_list || []);
  const flagsList = flags.length
    ? flags.map(f => `<span class="chip chip--bad">${esc(f)}</span>`).join(' ')
    : '<span class="xs">None</span>';

  $('#drawer-body').innerHTML = `
    <div class="row">${pill(r.status)}</div>
    <dl class="dl">
      <dt>Started</dt><dd>${esc(when(r.started_at))}</dd>
      <dt>Finished</dt><dd>${r.finished_at ? esc(when(r.finished_at)) : '—'}</dd>
      <dt>Duration</dt><dd>${r.duration_actual_s ? num(r.duration_actual_s, 1) + 's' : '—'} ${r.duration_expected_s ? '(expected: ' + num(r.duration_expected_s, 1) + 's)' : ''}</dd>
      <dt>Origin</dt><dd>${esc(r.origin || 'manual')}</dd>
      <dt>Posts</dt><dd>${int(r.n_posts)}</dd>
      <dt>Comments</dt><dd>${int(r.n_comments)}</dd>
      <dt>Batches</dt><dd>${int(r.n_batches)}</dd>
      <dt>Nuggets kept</dt><dd>${int(r.n_nuggets_kept)}</dd>
      <dt>Discarded (trivial)</dt><dd>${r.n_discarded_trivial !== null ? int(r.n_discarded_trivial) + ' (' + num((r.trivial_share || 0) * 100, 1) + '%)' : '—'}</dd>
      <dt>Ideas</dt><dd>${int(r.n_ideas)}</dd>
    </dl>

    <h3>Models</h3>
    <div class="chiprow" style="margin-block-end:var(--s-4)">${modelsList}</div>
    <div class="xs"><strong>Prompts:</strong> ${esc(promptInfo)}</div>
    <div class="xs"><strong>Rubric:</strong> v${esc(models.rubric_version || '—')}</div>

    <h3>Platforms</h3>
    <div class="chiprow" style="margin-block-end:var(--s-4)">${platformsList}</div>

    <h3>Prefilter Funnel</h3>
    <div style="margin-block-end:var(--s-4)">${funnelList}</div>

    <h3>Floor Flags</h3>
    <div class="chiprow" style="margin-block-end:var(--s-4)">${flagsList}</div>

    ${(r.top_near_misses_list || []).length ? `
      <h3>Near Misses</h3>
      <div class="chiprow" style="margin-block-end:var(--s-4)">
        ${(r.top_near_misses_list || []).map(v => `<span class="chip">${(+v).toFixed(2)}</span>`).join(' ')}
      </div>
    ` : ''}

    <h3>Phase Checkpoints</h3>
    <div class="stack">${phasesList}</div>

    ${r.error ? `<div style="margin-block-start:var(--s-5)"><strong class="chip chip--bad">Error:</strong> ${esc(r.error)}</div>` : ''}
  `;
  openDrawer();
}

// ── ideas ───────────────────────────────────────────────────────────────
const IDEA_STATUSES = ['new', 'reviewed', 'building', 'archived'];
let IDEAS = [];
let IDEA_FILTER = 'all';
const IDEA_PAGE = 50;
const IDEA = { offset: 0, sort: 'overall', dir: 'desc', q: '' };
let IDEAS_TOTAL = 0;
let IDEA_STATUS_COUNTS = {};

function renderIdeaFilter() {
  const all = Object.values(IDEA_STATUS_COUNTS).reduce((a, b) => a + b, 0);
  $('#ideas-filter').innerHTML = ['all', ...IDEA_STATUSES].map(s =>
    `<button data-f="${s}" aria-pressed="${IDEA_FILTER === s}">${esc(s)}` +
    `${s === 'all' ? ` <span class="num">${all}</span>` : IDEA_STATUS_COUNTS[s] ? ` <span class="num">${IDEA_STATUS_COUNTS[s]}</span>` : ''}</button>`).join('');
}
/** Three scores as three small bars; a missing competition check is hatched,
 *  not "3.0". "7.0 / 6.0 / unchecked" was unreadable at scan speed. */
function scoreBars(i) {
  // Demand and feasibility are "more is better" and get the good/bad tint;
  // competition is a reading, not a verdict, so it stays neutral.
  const cell = (v, ok, tint) => v === null || v === undefined || !ok
    ? '<i class="na" title="unchecked"></i>'
    : `<i class="${tint ? (+v >= 7.5 ? 'hi' : +v < 4 ? 'lo' : '') : ''}" style="--v:${Math.max(4, Math.min(100, (+v / 10) * 100))}%" title="${num(v)}"></i>`;
  return `<span class="sbar" title="demand ${num(i.demand_signal)} · feasibility ${num(i.feasibility)} · competition ${i.competition_checked ? num(i.competition) : 'unchecked'}">
    ${cell(i.demand_signal, true, true)}${cell(i.feasibility, true, true)}${cell(i.competition, i.competition_checked, false)}</span>`;
}
function evidenceCount(i) {
  try { const a = typeof i.supporting_nuggets === 'string' ? JSON.parse(i.supporting_nuggets) : i.supporting_nuggets; return Array.isArray(a) ? a.length : 0; }
  catch { return 0; }
}
function renderIdeas() {
  $('#ideas-body').innerHTML = IDEAS.map(i => {
    const overall = +i.overall || 0;
    const tone = overall >= 7.5 ? 'score--hi' : overall >= 5 ? 'score--mid' : 'score--lo';
    return `<tr data-id="${i.id}" class="clickable">
      <td><button class="btn btn--ghost btn--sm" data-open style="max-inline-size:46ch;text-align:start;white-space:normal;padding-inline:0"><b>${esc(i.title)}</b></button>
          <div class="xs" style="max-inline-size:52ch">${esc((i.problem_statement || '').slice(0, 120))}${(i.problem_statement || '').length > 120 ? '…' : ''}</div></td>
      <td class="num"><span class="score ${tone}">${num(overall)}</span></td>
      <td>${scoreBars(i)}</td>
      <td>${pill(i.status)}</td>
      <td class="num" style="white-space:nowrap">${evidenceCount(i)} <span class="xs">nuggets · ${int(i.source_threads)} threads</span></td>
      <td class="xs">${esc(ago(i.created_at))}</td>
    </tr>`;
  }).join('') || emptyRow(6, IDEAS_TOTAL ? 'No idea matches that filter.' : 'Archive empty.',
    IDEAS_TOTAL ? null : { page: 'overview', label: '▶ run pipeline from Overview' });
  const from = IDEAS_TOTAL ? IDEA.offset + 1 : 0, to = Math.min(IDEAS_TOTAL, IDEA.offset + IDEAS.length);
  $('#ideas-shown').textContent = IDEAS_TOTAL ? `${from}–${to} of ${IDEAS_TOTAL.toLocaleString()}` : '';
  $('#ideas-pager').innerHTML = pagerHTML(IDEA.offset, IDEA_PAGE, IDEAS_TOTAL, 'idea');
  $$('#ideas-body').length && $$('[data-page="ideas"] th[data-sort]').forEach(th =>
    th.setAttribute('aria-sort', th.dataset.sort === IDEA.sort ? (IDEA.dir === 'asc' ? 'ascending' : 'descending') : 'none'));
}
/** No new ideas for a while is the page's headline fact, and it has a cause. */
function renderIdeasNote() {
  const box = $('#ideas-note');
  if (!box) return;
  const recent = RUNS.filter(r => r.status === 'completed').slice(0, 10);
  const dry = recent.length >= 3 && recent.every(r => !Number(r.n_ideas));
  if (!dry) { box.innerHTML = ''; return; }
  const flags = new Set(recent.flatMap(r => r.floor_flags_list || []));
  const why = flags.has('DEDUP_UNAVAILABLE') ? 'the dedup store was unreachable' : flags.has('LOW_NUGGETS') ? 'too few new nuggets reached the synthesizer'
    : 'synthesis produced nothing the critic would archive';
  box.innerHTML = `<div class="banner"><span aria-hidden="true">ℹ</span><span style="flex:1"><b>No new ideas in the last ${recent.length} completed runs</b>Most likely because ${esc(why)}. The nuggets stay queued and are picked up once the cause clears.</span>
    <button class="btn btn--sm" data-go="runs">open Runs</button> <button class="btn btn--sm" data-go="doctor">Doctor</button></div>`;
}
async function loadIdeas() {
  const qs = new URLSearchParams({ status: IDEA_FILTER, q: IDEA.q, sort: IDEA.sort, dir: IDEA.dir, offset: IDEA.offset, limit: IDEA_PAGE });
  const [res, runs] = await Promise.all([api('/api/ideas?' + qs), RUNS.length ? Promise.resolve(null) : api('/api/runs')]);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  if (runs && runs.ok !== false) RUNS = runs.runs || [];
  IDEAS = res.ideas || [];
  IDEAS_TOTAL = res.total || 0;
  IDEA_STATUS_COUNTS = res.status_counts || {};
  COUNTS.ideas = Object.values(IDEA_STATUS_COUNTS).reduce((a, b) => a + b, 0);
  renderNav();
  renderIdeaFilter();
  renderIdeas();
  renderIdeasNote();
}
let ideaTimer = null;
$('#ideas-q').addEventListener('input', () => {
  clearTimeout(ideaTimer);
  ideaTimer = setTimeout(() => { IDEA.q = $('#ideas-q').value.trim(); IDEA.offset = 0; loadIdeas(); }, 250);
});
$('#ideas-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-f]');
  if (!b) return;
  IDEA_FILTER = b.dataset.f; IDEA.offset = 0;
  loadIdeas();
});
$$('[data-page="ideas"] th[data-sort]').forEach(th => th.addEventListener('click', () => {
  if (IDEA.sort === th.dataset.sort) IDEA.dir = IDEA.dir === 'asc' ? 'desc' : 'asc';
  else { IDEA.sort = th.dataset.sort; IDEA.dir = th.dataset.sort === 'title' || th.dataset.sort === 'status' ? 'asc' : 'desc'; }
  IDEA.offset = 0;
  loadIdeas();
}));
$('#ideas-pager').addEventListener('click', e => {
  const b = e.target.closest('button[data-page-idea]');
  if (!b || b.disabled) return;
  IDEA.offset = Number(b.dataset.pageIdea);
  loadIdeas();
});
$('#ideas-body').addEventListener('click', e => {
  const tr = e.target.closest('tr[data-id]');
  if (!tr) return;
  showIdea(+tr.dataset.id);
});
// Status change and resynth live in the drawer now — one control per idea
// on screen, not 1 321 <select>s in a table.
$('#drawer-body').addEventListener('change', async e => {
  const sel = e.target.closest('select[data-mark]');
  if (!sel) return;
  const id = +sel.dataset.mark;
  const res = await api('/api/mark', { id, status: sel.value });
  if (res.ok === false) toast(`mark failed: ${res.error || res.code}`, 'bad');
  else toast(`idea #${id} → ${sel.value}`, 'ok');
  loadIdeas();
});
$('#drawer-body').addEventListener('click', e => {
  const b = e.target.closest('button[data-resynth]');
  if (b) runAction('/api/resynth', { id: +b.dataset.resynth }, b);
});

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
      <span class="score">${num(i.overall)}</span><span class="xs">overall</span>
      <span class="row-end row" style="gap:var(--s-2)">
        <select data-mark="${i.id}" aria-label="Set status for idea ${i.id}" style="inline-size:130px">
          ${IDEA_STATUSES.map(s => `<option ${s === i.status ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
        <button class="btn btn--sm" data-resynth="${i.id}" title="Re-run synthesis on this idea's nuggets">resynth</button>
      </span></div>
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

// ── search ──────────────────────────────────────────────────────────────
// Every nugget has been embedded since the first run and the vector store's
// search has existed the whole time, wired only into dedup. The archive was
// searchable and there was no way to search it.

const SEARCH = { platform: '', category: '' };
let SEARCH_LAST = null;
const SEARCH_EXAMPLES = ['backups fail silently', 'onboarding takes too long', 'no way to export my data'];
function recentSearches() { try { return JSON.parse(localStorage.getItem('jester-searches') || '[]'); } catch { return []; } }
function rememberSearch(q) {
  try {
    const list = [q, ...recentSearches().filter(x => x !== q)].slice(0, 8);
    localStorage.setItem('jester-searches', JSON.stringify(list));
  } catch { /* private mode */ }
}
function renderSearchChrome() {
  const recent = recentSearches();
  $('#search-recent').innerHTML = recent.length
    ? `<span class="xs">recent</span>${recent.map(q => `<button class="chip" data-q="${esc(q)}">${esc(q)}</button>`).join('')}`
    : '';
  const res = SEARCH_LAST;
  if (!res) { $('#search-facets').innerHTML = ''; return; }
  const count = (key) => { const m = new Map(); for (const r of res.results || []) { const k = r[key] || ''; if (k) m.set(k, (m.get(k) || 0) + 1); } return [...m].sort((a, b) => b[1] - a[1]); };
  const chips = (key, items) => items.map(([v, n]) =>
    `<button class="chip" data-facet="${key}" data-value="${esc(v)}" aria-pressed="${SEARCH[key] === v}" style="${SEARCH[key] === v ? 'background:var(--ink);color:var(--bg);border-color:var(--ink)' : ''}">${esc(key === 'platform' ? (PLATFORM_LABEL[v] || v) : v)} ${n}</button>`).join('');
  const active = SEARCH.platform || SEARCH.category;
  $('#search-facets').innerHTML = [
    `<span class="xs">${res.mode === 'semantic' ? 'meaning' : 'text'} match</span>`,
    chips('platform', count('platform')), chips('category', count('category')),
    active ? `<button class="btn btn--ghost btn--sm" data-facet-clear>clear</button>` : '',
  ].join(' ');
}
function highlight(text, q) {
  const words = q.toLowerCase().split(/\s+/).filter(w => w.length > 2);
  let out = esc(text);
  for (const w of words) out = out.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig'), '<mark>$1</mark>');
  return out;
}
function renderSearch(res) {
  const note = $('#search-note');
  if (note) {
    const bits = [];
    if (res) {
      bits.push(res.mode === 'semantic' ? 'matched on meaning' : 'matched on text — vector store unreachable');
      bits.push(`${res.returned} result(s)`);
      if (res.detail) bits.push(res.detail);
    }
    note.textContent = bits.join(' · ');
    note.className = res && res.mode === 'text' ? 'xs chip chip--warn' : 'xs';
  }
  SEARCH_LAST = res;
  renderSearchChrome();
  const q = ($('#search-q').value || '').trim();
  const rows = (res && res.results) || [];
  const shown = rows.filter(n => (!SEARCH.platform || n.platform === SEARCH.platform) && (!SEARCH.category || n.category === SEARCH.category));
  $('#search-body').innerHTML = shown.length ? shown.map(n => {
    const s = n.score !== undefined && n.score !== null ? Number(n.score) : null;
    const strength = s === null ? '' : s >= .8 ? 'strong' : s >= .6 ? 'fair' : 'weak';
    return `
    <div class="card">
      <div class="row row-between">
        <div class="chiprow">
          <span class="chip">${esc(PLATFORM_LABEL[n.platform] || n.platform || '—')}</span>
          <span class="chip">${esc(n.category || 'uncategorised')}</span>
          ${n.author ? `<span class="chip">${esc(n.author)}</span>` : ''}
          ${n.created_utc ? `<span class="xs">${esc(String(n.created_utc).slice(0, 10))}</span>` : ''}
        </div>
        ${s === null ? '' : `<span class="xs" title="similarity ${s.toFixed(3)}"><span class="simbar"><i style="inline-size:${Math.round(s * 100)}%"></i></span>${strength}</span>`}
      </div>
      <p><strong>${esc(n.extracted_insight || '')}</strong></p>
      <p class="xs">${res.mode === 'text' ? highlight((n.raw_text || '').slice(0, 400), q) : esc((n.raw_text || '').slice(0, 400))}</p>
      ${n.source_url ? `<a href="${esc(n.source_url)}" target="_blank" rel="noopener noreferrer" class="xs">source ↗</a>` : ''}
    </div>`;
  }).join('') : res
    ? `<div class="empty">Nothing matched${SEARCH.platform || SEARCH.category ? ' inside that filter' : ''}.</div>`
    : `<div class="empty">Ask the archive something. Try:
        <div class="row" style="justify-content:center;margin-block-start:var(--s-4)">${SEARCH_EXAMPLES.map(x => `<button class="btn btn--sm" data-q="${esc(x)}">${esc(x)}</button>`).join('')}</div></div>`;
}

async function runSearch() {
  const q = ($('#search-q').value || '').trim();
  if (!q) { renderSearch(null); return; }
  const btn = $('#search-go');
  btn.disabled = true;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}&limit=40`);
  btn.disabled = false;
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  rememberSearch(q);
  renderSearch(res);
}

function loadSearch() { renderSearch(SEARCH_LAST); }

$('#search-go').addEventListener('click', runSearch);
$('#search-q').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });
$('[data-page="search"]').addEventListener('click', e => {
  const q = e.target.closest('[data-q]');
  if (q) { $('#search-q').value = q.dataset.q; runSearch(); return; }
  const clear = e.target.closest('[data-facet-clear]');
  if (clear) { SEARCH.platform = ''; SEARCH.category = ''; renderSearch(SEARCH_LAST); return; }
  const f = e.target.closest('[data-facet]');
  if (f) { SEARCH[f.dataset.facet] = SEARCH[f.dataset.facet] === f.dataset.value ? '' : f.dataset.value; renderSearch(SEARCH_LAST); }
});

// ── clusters ──────────────────────────────────────────────────────────────
let CLUSTERS = [];
let CLUSTER_SORT = 'size';
let CLUSTER_PLATFORM = '';
const CLUSTER_SORTS = { size: (a, b) => (b.n_nuggets || 0) - (a.n_nuggets || 0), coherence: (a, b) => (b.coherence || 0) - (a.coherence || 0), ideas: (a, b) => (b.n_ideas || 0) - (a.n_ideas || 0) };
function renderClusterControls() {
  $('#clusters-sort').innerHTML = Object.keys(CLUSTER_SORTS).map(k =>
    `<button data-csort="${k}" aria-pressed="${CLUSTER_SORT === k}">${k}</button>`).join('');
  const platforms = [...new Set(CLUSTERS.flatMap(c => c.platforms || []))].sort();
  $('#clusters-platform').innerHTML = ['', ...platforms].map(p =>
    `<button data-cplat="${esc(p)}" aria-pressed="${CLUSTER_PLATFORM === p}">${esc(p ? (PLATFORM_LABEL[p] || p) : 'all')}</button>`).join('');
}
$('#clusters-sort').addEventListener('click', e => { const b = e.target.closest('[data-csort]'); if (!b) return; CLUSTER_SORT = b.dataset.csort; renderClusterControls(); renderClusters(); });
$('#clusters-platform').addEventListener('click', e => { const b = e.target.closest('[data-cplat]'); if (!b) return; CLUSTER_PLATFORM = b.dataset.cplat; renderClusterControls(); renderClusters(); });
let CLUSTER_OPEN = null;      // id of the expanded theme
let CLUSTER_DETAIL = {};      // id -> full detail once fetched
let CLUSTER_FAKE = false;     // last pass ran on hash vectors

function clusterQuality(c) {
  // The numbers that decide whether a theme means anything, said plainly.
  // n_authors is the strongest of them: five chunks from one prolific
  // commenter cluster beautifully and signify nothing.
  const bits = [];
  bits.push(`${c.n_nuggets} nugget${c.n_nuggets === 1 ? '' : 's'}`);
  if (c.n_authors > 0) bits.push(`${c.n_authors} author${c.n_authors === 1 ? '' : 's'}`);
  else if (c.authors_unknown > 0) bits.push('authors not captured');
  if (c.n_threads > 0) bits.push(`${c.n_threads} thread${c.n_threads === 1 ? '' : 's'}`);
  bits.push(`coherence ${Number(c.coherence || 0).toFixed(2)}`);
  return bits.join(' · ');
}

function clusterFlags(c) {
  const out = [];
  // A theme confined to one thread is exactly what the old thread-grouping
  // already found — worth saying, because it is not new information.
  if (c.single_thread) out.push('<span class="chip chip--warn">one thread</span>');
  if (c.single_author) out.push('<span class="chip chip--warn">one author</span>');
  if ((c.embedding_model || '').startsWith('fake'))
    out.push('<span class="chip chip--bad">hash vectors — not meaningful</span>');
  if (c.n_ideas > 0) out.push(`<span class="chip">${c.n_ideas} draft idea(s)</span>`);
  return out.join('');
}

function renderClusterIdea(i) {
  const saved = i.promoted_idea_id
    ? `<span class="chip">saved as idea #${i.promoted_idea_id}</span>` : '';
  const scores = [
    ['demand', i.demand_signal], ['feasibility', i.feasibility],
    // competition stays null when the critic did not verify it (R29) — shown
    // as "unchecked", never as a zero.
    ['competition', i.competition === null || i.competition === undefined ? null : i.competition],
    ['overall', i.overall],
  ].map(([k, v]) => `${k} ${v === null ? '—' : Number(v).toFixed(1)}`).join(' · ');
  return `<div class="card" style="margin-block-start:var(--jester-s-4)">
    <div class="row row-between">
      <strong>${esc(i.title || 'untitled')}</strong>
      <div class="chiprow">${saved}</div>
    </div>
    <p class="xs">${esc(scores)}${i.synthesis_model ? ' · ' + esc(i.synthesis_model) : ''}</p>
    <p>${esc(i.problem_statement || '')}</p>
    <p>${esc(i.proposed_solution || '')}</p>
    ${i.promoted_idea_id ? '' : `<div class="row">
      <button data-save-idea="${i.id}" class="primary">✓ save to Ideas</button>
      <button data-discard-idea="${i.id}">discard</button>
    </div>`}
  </div>`;
}

function renderClusters() {
  const q = ($('#clusters-q').value || '').trim().toLowerCase();
  const rows = CLUSTERS.filter(c => (!q ||
    `${c.label || ''} ${c.problem_statement || ''}`.toLowerCase().includes(q))
    && (!CLUSTER_PLATFORM || (c.platforms || []).includes(CLUSTER_PLATFORM)))
    .sort(CLUSTER_SORTS[CLUSTER_SORT] || CLUSTER_SORTS.size);

  const warn = $('#clusters-warn');
  if (warn) {
    warn.innerHTML = CLUSTER_FAKE
      ? `<div class="card card--bad"><strong>These groups are not meaningful.</strong>
         <p class="xs">They were built from 32-dimension hash vectors, which carry no
         semantic information — two comments about the same problem are as far apart
         as two unrelated ones. Set <code>embedding_provider: ollama</code> in
         config/thresholds.yaml, make sure Ollama is reachable, then regroup.</p></div>`
      : '';
  }

  $('#clusters-body').innerHTML = rows.length ? rows.map(c => {
    const open = CLUSTER_OPEN === c.id;
    const detail = CLUSTER_DETAIL[c.id];
    return `<div class="card${open ? ' card--wide' : ''}">
      <div class="row row-between">
        <div>
          <h3 style="margin:0">${esc(c.label || 'unnamed theme')}</h3>
          <p class="xs">${esc(clusterQuality(c))}</p>
        </div>
        <div class="chiprow">${clusterFlags(c)}</div>
      </div>
      <p>${esc(c.problem_statement || '')}</p>
      <div class="chiprow">${(c.platforms || [])
        .map(p => `<span class="chip">${esc(p)}</span>`).join('')}</div>
      <div class="row">
        <button data-open-cluster="${c.id}">${open ? '▾ hide' : '▸ show'} ${c.size} chunk(s)</button>
        <button data-gen-idea="${c.id}" class="primary">✦ generate idea</button>
      </div>
      ${open && detail ? `
        <div class="stack" style="margin-block-start:var(--jester-s-4)">
          ${(detail.members || []).map(m => `<div class="card card--flush" style="padding:var(--jester-s-3)">
            <p class="xs">${esc(m.author || 'unknown author')} · ${esc(m.platform || '')}
              · similarity ${Number(m.similarity || 0).toFixed(2)}
              ${m.source_url ? `· <a href="${esc(m.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>` : ''}</p>
            <p>${esc(m.chunk_text || '')}</p>
          </div>`).join('')}
          ${(detail.ideas || []).map(renderClusterIdea).join('')}
        </div>` : ''}
    </div>`;
  }).join('') : `<div class="empty">${esc(CLUSTERS.length
    ? 'No theme matches that filter.'
    : 'No themes yet — press “regroup” to cluster the archive.')}</div>`;

  const note = $('#clusters-count');
  if (note) {
    const parts = [`${rows.length} theme(s) shown`];
    if (rows.length !== CLUSTERS.length) parts.push(`${CLUSTERS.length} total`);
    note.textContent = parts.join(' · ');
  }
}

async function loadClusters() {
  const res = await api('/api/clusters');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  CLUSTERS = res.clusters || [];
  CLUSTER_FAKE = !!res.fake_embeddings;
  COUNTS.clusters = res.total ?? CLUSTERS.length;
  renderNav();
  renderClusterControls();
  renderClusters();
}

async function openCluster(id) {
  if (CLUSTER_OPEN === id) { CLUSTER_OPEN = null; renderClusters(); return; }
  const res = await api(`/api/cluster/${id}`);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  CLUSTER_DETAIL[id] = res.cluster;
  CLUSTER_OPEN = id;
  renderClusters();
}

$('#clusters-q').addEventListener('input', renderClusters);

/** Poll a background job to completion, reporting what it says as it goes. */
async function followJob(jobId, onDone) {
  const btn = $('#clusters-run');
  const restore = btn ? btn.textContent : '';
  if (btn) btn.disabled = true;
  let last = '';
  // 2s: the work runs for minutes, so a tighter poll buys nothing and just
  // adds requests. Ten minutes of ceiling covers the measured 577s pass with
  // room, and stops a hung job polling until the tab closes.
  for (let i = 0; i < 300; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await api(`/api/job/${jobId}`);
    if (res.ok === false) { toast(res.error, 'bad'); break; }
    const j = res.job;
    if (j.progress && j.progress !== last) {
      last = j.progress;
      if (btn) btn.textContent = `⟳ ${j.progress}`;
    }
    if (j.status === 'done') {
      if (btn) { btn.disabled = false; btn.textContent = restore; }
      onDone(j.result || {});
      return;
    }
    if (j.status === 'error') {
      if (btn) { btn.disabled = false; btn.textContent = restore; }
      // The first line is the message; the rest is a traceback nobody wants
      // in a toast but which is on the job row for whoever needs it.
      toast(String(j.error || 'job failed').split(String.fromCharCode(10))[0], 'bad');
      return;
    }
  }
  if (btn) { btn.disabled = false; btn.textContent = restore; }
  toast('still running — check the Jobs list', 'warn');
}

$('#clusters-run').addEventListener('click', async () => {
  // Starts a background job and returns at once: the pass takes minutes and
  // used to hold one request open for all of it, so the browser gave up long
  // before the work finished.
  const res = await api('/api/cluster/run', {});
  if (res.ok === false) {
    // A refusal carries the knob that fixes it; showing only "failed" would
    // send someone hunting.
    toast(res.detail ? `${res.error} — ${res.detail}` : res.error, 'bad');
    return;
  }
  toast('regrouping in the background — this takes a few minutes');
  followJob(res.job_id, async out => {
    toast(`${out.clusters} theme(s) from ${out.nuggets} nugget(s); ` +
          `${out.ungrouped_nuggets} did not group`, 'ok');
    await loadClusters();
  });
});

$('#clusters-body').addEventListener('click', async e => {
  const open = e.target.closest('button[data-open-cluster]');
  if (open) { await openCluster(Number(open.dataset.openCluster)); return; }

  const gen = e.target.closest('button[data-gen-idea]');
  if (gen) {
    const id = Number(gen.dataset.genIdea);
    gen.disabled = true; gen.textContent = '… thinking';
    const res = await api('/api/cluster/idea', { cluster_id: id });
    gen.disabled = false; gen.textContent = '✦ generate idea';
    if (res.ok === false) { toast(res.error, 'bad'); return; }
    toast(res.detail || 'draft created', 'ok');
    CLUSTER_OPEN = null;            // force a refetch so the draft shows
    await openCluster(id);
    await loadClusters();
    return;
  }

  const save = e.target.closest('button[data-save-idea]');
  if (save) {
    const res = await api('/api/cluster/idea/save', { draft_id: Number(save.dataset.saveIdea) });
    if (res.ok === false) { toast(res.error, 'bad'); return; }
    toast(`saved as idea #${res.idea_id}`, 'ok');
    const id = CLUSTER_OPEN; CLUSTER_OPEN = null;
    if (id) await openCluster(id);
    await loadClusters();
    return;
  }

  const drop = e.target.closest('button[data-discard-idea]');
  if (drop) {
    const res = await api('/api/cluster/idea/discard', { draft_id: Number(drop.dataset.discardIdea) });
    if (res.ok === false) { toast(res.error, 'bad'); return; }
    toast('draft discarded');
    const id = CLUSTER_OPEN; CLUSTER_OPEN = null;
    if (id) await openCluster(id);
    await loadClusters();
  }
});

// ── queue ───────────────────────────────────────────────────────────────
// What has been scraped and not yet treated. This was visible only as a single
// number on the Overview page, which is thin for something that legitimately
// holds tens of thousands of comments for days: ingestion and treatment run on
// separate clocks, so the gap between them is a standing state, not a moment.

let QUEUE_STATUS = 'pending';
const QUEUE_STATUSES = ['pending', 'failed', 'done'];
let QUEUE_OPEN = null;
let QUEUE_KIND = 'comment';
const QUEUE_KIND_LABEL = { comment: 'comments', listing: 'listings' };
let QUEUE_BY_KIND = {};
function renderQueueKinds() {
  $('#queue-kind').innerHTML = Object.keys(QUEUE_KIND_LABEL).map(k => {
    const n = (QUEUE_BY_KIND[k] || {}).batches || 0;
    return `<button data-kind="${k}" aria-pressed="${QUEUE_KIND === k}">${QUEUE_KIND_LABEL[k]} <span class="num">${n.toLocaleString()}</span></button>`;
  }).join('');
}

function renderQueueStatuses() {
  $('#queue-status').innerHTML = QUEUE_STATUSES.map(st =>
    `<button data-qs="${esc(st)}" aria-pressed="${QUEUE_STATUS === st}">${esc(st)}</button>`).join('');
}

/** Whether treatment can run at all, and when it next can. */
function renderTreatment(t) {
  const box = $('#queue-treatment');
  if (!t) { box.innerHTML = ''; return; }
  if (!t.blocked) {
    box.innerHTML = `<div class="card"><span class="chip chip--ok">clear</span>
      ${esc(t.provider || 'the provider')} is not rate-limited &mdash; treatment drains this
      queue on its own schedule.</div>`;
    return;
  }
  box.innerHTML = `<div class="card">
    <span class="chip chip--warn">treatment paused</span>
    <b>${esc(t.human)}</b> until ${esc(when(t.blocked_until))}.
    ${t.reason ? `<div class="xs">${esc(t.reason)}</div>` : ''}
    <div class="xs">Nothing is lost while it waits &mdash; the queue is the buffer, and
      <span class="mono">treat</span> exits in a second and a half rather than
      grinding against an exhausted quota.</div>
  </div>`;
}

/** Where the waiting work came from, as a share of the whole. */
function renderQueueMix(rows, totalComments) {
  const box = $('#queue-mix');
  if (!rows || !rows.length) {
    box.innerHTML = '<p class="xs">Nothing queued.</p>';
    return;
  }
  box.innerHTML = rows.map(r => {
    const share = totalComments ? Math.round((r.comments / totalComments) * 100) : 0;
    // The bar is the point: a dominant platform should be obvious without
    // reading the numbers.
    return `<div class="qmix">
      <span class="chip">${esc(PLATFORM_LABEL[r.platform] || r.platform)}</span>
      <div class="qbar" aria-hidden="true"><i style="inline-size:${share}%"></i></div>
      <span class="xs mono">${r.comments.toLocaleString()} comment(s) · ${r.batches} batch(es) · ${share}%</span>
    </div>`;
  }).join('');
}

function queueRow(b) {
  const tone = b.status === 'failed' ? 'bad' : b.status === 'done' ? 'done' : 'warn';
  // A NULL count means the batch's JSON would not parse — which is a defect,
  // not an empty batch, so it must not render as 0.
  const n = b.n_comments === null || b.n_comments === undefined
    ? '<span class="chip chip--bad">unreadable</span>'
    : `<span class="mono">${Number(b.n_comments).toLocaleString()}</span>`;
  return `<tr data-batch="${b.id}">
    <td class="mono xs">${b.id}</td>
    <td><span class="chip">${esc(PLATFORM_LABEL[b.platform] || b.platform || '—')}</span>
        <div class="xs truncate" style="max-inline-size:38ch" title="${esc(b.source || '')}">${esc(b.thread_id || b.source || '')}</div></td>
    <td>${n}</td>
    <td class="xs">${esc(ago(b.created_at))}</td>
    <td class="mono xs">${esc(b.run_id || '')}</td>
    <td>${tag(tone, b.status, 'pill--sm')}${b.attempts ? `<div class="xs">${b.attempts} attempt(s)</div>` : ''}
        ${b.error ? `<div class="xs" title="${esc(b.error)}">${esc(String(b.error).slice(0, 60))}</div>` : ''}</td>
    <td><button class="btn btn--sm" data-open-batch="${b.id}">open</button></td>
  </tr>`;
}

function renderQueue(res) {
  const batches = res.batches || [];
  QUEUE_BY_KIND = res.by_kind || {};
  renderQueueKinds();
  $('#queue-body').innerHTML = batches.map(queueRow).join('')
    || emptyRow(7, `No ${esc(QUEUE_KIND_LABEL[QUEUE_KIND])} ${esc(res.status)} in the queue.`,
      res.status === 'pending' ? { page: 'overview', label: 'fetch some — ingest from Overview' } : null);
  const parts = [`${(res.total_batches || 0).toLocaleString()} batch(es)`,
                 `${(res.total_comments || 0).toLocaleString()} ${QUEUE_KIND === 'listing' ? 'listing(s)' : 'comment(s)'}`];
  if (res.truncated) parts.push(`showing the oldest ${res.shown}`);
  $('#queue-shown').textContent = parts.join(' · ');
  const t = res.totals || {};
  $('#queue-count').textContent = QUEUE_STATUSES
    .filter(st => t[st])
    .map(st => `${t[st].batches.toLocaleString()} ${st}`)
    .join(' · ') || 'the queue is empty';
  renderTreatment(res.treatment);
  renderQueueMix(res.by_platform, res.total_comments);
}

async function loadQueue() {
  const res = await api(`/api/queue?status=${encodeURIComponent(QUEUE_STATUS)}&kind=${encodeURIComponent(QUEUE_KIND)}`);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  COUNTS.pending = (res.by_kind && res.by_kind.comment) ? res.by_kind.comment.batches : 0;
  renderNav();
  renderQueueStatuses();
  renderQueue(res);
}

/** One batch's comments, in the drawer — the table stays where it was. It
 *  used to render under a 300-row table, out of sight. */
async function openBatch(id) {
  const res = await api(`/api/queue/batch/${id}`);
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  QUEUE_OPEN = id;
  const b = res.batch, post = res.post || {};
  const rows = (res.comments || []).map(c => {
    const d = c.detail || {};
    const meta = [d.author, d.created_at ? when(d.created_at) : '',
                  d.upvotes !== undefined && d.upvotes !== null ? `${d.upvotes} up` : '',
                  d.depth ? `depth ${d.depth}` : '']
      .filter(Boolean).join(' · ');
    return `<div class="evidence">${esc(c.body || d.body || '')}
        ${meta ? `<div class="xs" style="margin-block-start:4px">${esc(meta)}</div>` : ''}</div>`;
  }).join('') || '<p class="xs">This batch carries no comments.</p>';
  $('#drawer-title').textContent = `Batch #${b.id}`;
  $('#drawer-body').innerHTML = `
    <div class="row"><span class="chip">${esc(PLATFORM_LABEL[b.platform] || b.platform)}</span>${tag(b.status === 'failed' ? 'bad' : b.status === 'done' ? 'done' : 'warn', b.status, 'pill--sm')}
      <span class="xs">${esc(ago(b.created_at))}</span></div>
    ${post.title ? `<div><b>${esc(post.title)}</b></div>` : ''}
    <p class="xs">${post.community ? esc(post.community) + ' · ' : ''}<a href="${esc(b.source || '')}" target="_blank" rel="noopener noreferrer">${esc(b.source || '')}</a></p>
    ${b.error ? `<div class="banner banner--bad"><span aria-hidden="true">⚠</span><span>${esc(b.error)}</span></div>` : ''}
    <h3>${(res.comments || []).length} comment(s)</h3>
    ${rows}`;
  openDrawer();
}
$('#queue-status').addEventListener('click', e => {
  const b = e.target.closest('button[data-qs]');
  if (!b) return;
  QUEUE_STATUS = b.dataset.qs;
  loadQueue();
});
$('#queue-kind').addEventListener('click', e => {
  const b = e.target.closest('button[data-kind]');
  if (!b) return;
  QUEUE_KIND = b.dataset.kind;
  loadQueue();
});
$('#queue-refresh').onclick = loadQueue;
$('#queue-body').addEventListener('click', e => {
  const b = e.target.closest('button[data-open-batch]');
  if (b) openBatch(Number(b.dataset.openBatch));
});

// ── nuggets ─────────────────────────────────────────────────────────────
let NUGGETS = [];
let NUGGET_TOTAL = 0;
//: How many the archive holds regardless of the filters — the number that
//: decides whether an empty list means "nothing here" or "too narrow".
let NUGGET_ARCHIVE_TOTAL = 0;
const NUGGET_PAGE = 100;
const NUG = { offset: 0, platform: '', community: '', category: '', flag: '', q: '' };
const NUGGET_FLAG_LABEL = { flagged: 'flagged', trivial: 'trivial', reembed: 'not yet searchable', unclustered: 'waiting for ideas' };
let NUGGET_FACETS = { platform: [], community: [], category: [], flags: {} };
let NUGGET_FACET_MORE = { community: false, category: false };

function renderNuggetFilter() {
  const f = NUGGET_FACETS.flags || {};
  $('#nuggets-filter').innerHTML = [['', 'all'], ...Object.entries(NUGGET_FLAG_LABEL)].map(([k, label]) =>
    `<button data-f="${esc(k)}" aria-pressed="${NUG.flag === k}">${esc(label)}${k && f[k] !== undefined ? ` <span class="num">${Number(f[k]).toLocaleString()}</span>` : ''}</button>`).join('');
}
function nuggetRow(g) {
  const flags = [
    g.trivial ? '<span class="chip chip--warn">trivial</span>' : '',
    g.needs_reembed ? '<span class="chip chip--bad">not yet searchable</span>' : '',
    g.synthesized_at ? '' : '<span class="chip" title="not yet part of an idea">waiting for ideas</span>',
  ].filter(Boolean).join('');
  return `<tr>
    <td class="nug-insight">${esc(g.extracted_insight || '')}
      <div class="xs mono" style="margin-block-start:4px">${esc(g.unique_key)}${g.source_url
        ? ` · <a href="${esc(g.source_url)}" target="_blank" rel="noopener noreferrer">source ↗</a>` : ''}</div></td>
    <td class="nug-from"><b>${esc(PLATFORM_LABEL[g.platform] || g.platform || '—')}</b><span class="xs">${esc(g.community || '')}</span></td>
    <td><span class="chip">${esc(g.category || 'uncategorised')}</span></td>
    <td><div class="chiprow">${flags || '<span class="xs">—</span>'}</div></td>
  </tr>`;
}
function facetBlock(title, key, items, limit = 12) {
  const list = NUGGET_FACET_MORE[key] ? items : items.slice(0, limit);
  return `<div class="facet"><h4>${esc(title)}</h4>
    ${list.map(it => `<button data-facet="${key}" data-value="${esc(it.value)}" aria-pressed="${NUG[key] === it.value}">
       <span class="truncate">${esc(key === 'platform' ? (PLATFORM_LABEL[it.value] || it.value || '—') : (it.value || 'not recorded'))}</span><span class="n">${Number(it.n).toLocaleString()}</span></button>`).join('')}
    ${items.length > limit ? `<button class="more" data-facet-more="${key}">${NUGGET_FACET_MORE[key] ? 'show fewer' : `+${items.length - limit} more`}</button>` : ''}
  </div>`;
}
/** The rail's open/shut state. Remembered per browser: whether you like the
 *  filters in view is a preference, not something to re-decide every visit.
 *  Default follows the width - there is no room for a 230px rail on a phone. */
let FACETS_OPEN = true;
try {
  const saved = localStorage.getItem('jester-facets');
  FACETS_OPEN = saved === null ? window.innerWidth >= 900 : saved === 'open';
} catch { /* private mode */ }

function applyFacetsOpen() {
  const layout = $('#nuggets-layout');
  const btn = $('#facets-toggle');
  if (!layout || !btn) return;
  layout.classList.toggle('is-collapsed', !FACETS_OPEN);
  btn.setAttribute('aria-expanded', String(FACETS_OPEN));
  $('#facets-toggle-label').textContent = FACETS_OPEN ? 'hide filters' : 'filters';
  try { localStorage.setItem('jester-facets', FACETS_OPEN ? 'open' : 'shut'); } catch { /* private mode */ }
}

/** What is narrowing the list right now, as removable chips. These stay on
 *  screen when the rail is shut, so a hidden filter can never silently shape
 *  what you are reading - which is the one real risk of collapsing it. */
function renderActiveFacets() {
  const label = { platform: 'platform', community: 'community', category: 'category', flag: 'flag', q: 'text' };
  const chips = ['platform', 'community', 'category', 'flag', 'q']
    .filter(k => NUG[k])
    .map(k => `<span class="fchip"><b>${label[k]}</b> ${esc(k === 'flag' ? (NUGGET_FLAG_LABEL[NUG[k]] || NUG[k]) : NUG[k])}
        <button data-facet-drop="${k}" aria-label="Remove ${label[k]} filter">\u2715</button></span>`).join('');
  $('#nuggets-active').innerHTML = chips
    ? chips + '<button class="btn btn--ghost btn--sm" data-facet-clear>clear all</button>'
    : '<span class="xs">showing the whole archive</span>';
}

function renderNuggetFacets() {
  const f = NUGGET_FACETS;
  $('#nuggets-facets').innerHTML =
    facetBlock('Platform', 'platform', f.platform || [], 12)
    + facetBlock('Community', 'community', f.community || [], 10)
    + facetBlock('Category', 'category', f.category || [], 8);
  renderActiveFacets();
  applyFacetsOpen();
}

$('#facets-toggle').onclick = () => { FACETS_OPEN = !FACETS_OPEN; applyFacetsOpen(); };
$('#nuggets-active').addEventListener('click', e => {
  const drop = e.target.closest('[data-facet-drop]');
  if (drop) {
    NUG[drop.dataset.facetDrop] = '';
    if (drop.dataset.facetDrop === 'q') $('#nuggets-q').value = '';
    NUG.offset = 0;
    loadNuggets();
    return;
  }
  if (e.target.closest('[data-facet-clear]')) {
    Object.assign(NUG, { offset: 0, platform: '', community: '', category: '', flag: '', q: '' });
    $('#nuggets-q').value = '';
    loadNuggets();
  }
});
function renderNuggets() {
  // NUGGET_TOTAL is the count under the current filters, so it is 0 whenever
  // the filters match nothing — including on a full archive. Deciding the
  // empty state from it told the operator to run the pipeline when what he
  // actually had to do was drop a filter.
  const filtered = !!(NUG.platform || NUG.community || NUG.category || NUG.flag || NUG.q);
  $('#nuggets-body').innerHTML = NUGGETS.map(nuggetRow).join('')
    || (NUGGET_ARCHIVE_TOTAL
      ? emptyRow(4, filtered
          ? `No nugget matches these filters — ${NUGGET_ARCHIVE_TOTAL.toLocaleString()} in the archive.`
          : 'Nothing to show.')
      : emptyRow(4, 'No nuggets archived yet.', { page: 'overview', label: '▶ run pipeline from Overview' }));
  if (!NUGGETS.length && filtered && NUGGET_ARCHIVE_TOTAL) {
    $('#nuggets-body .empty').insertAdjacentHTML('beforeend',
      '<div><button class="btn btn--sm" data-facet-clear>clear the filters</button></div>');
  }
  const from = NUGGET_TOTAL ? NUG.offset + 1 : 0, to = Math.min(NUGGET_TOTAL, NUG.offset + NUGGETS.length);
  $('#nuggets-shown').textContent = NUGGET_TOTAL ? `${from.toLocaleString()}–${to.toLocaleString()} of ${NUGGET_TOTAL.toLocaleString()}` : '';
  $('#nuggets-pager').innerHTML = pagerHTML(NUG.offset, NUGGET_PAGE, NUGGET_TOTAL, 'nug');
}
function pagerHTML(offset, size, total, key) {
  if (total <= size) return '';
  const page = Math.floor(offset / size) + 1, pages = Math.ceil(total / size);
  return `<button class="btn btn--ghost btn--sm" data-page-${key}="${Math.max(0, offset - size)}" ${offset === 0 ? 'disabled' : ''}>‹</button>
    <span>page ${page} of ${pages}</span>
    <button class="btn btn--ghost btn--sm" data-page-${key}="${offset + size}" ${offset + size >= total ? 'disabled' : ''}>›</button>`;
}
async function loadNuggets() {
  const qs = new URLSearchParams({ offset: NUG.offset, limit: NUGGET_PAGE, platform: NUG.platform,
    community: NUG.community, category: NUG.category, flag: NUG.flag, q: NUG.q });
  const res = await api('/api/nuggets/page?' + qs.toString());
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  NUGGETS = res.nuggets || [];
  NUGGET_TOTAL = res.total || 0;
  NUGGET_FACETS = res.facets || NUGGET_FACETS;
  NUGGET_ARCHIVE_TOTAL = res.archive_total ?? NUGGET_TOTAL;
  COUNTS.nuggets = NUGGET_ARCHIVE_TOTAL;
  renderNav();
  renderNuggetFilter();
  renderNuggetFacets();
  renderNuggets();
  const note = $('#nuggets-count');
  if (note) {
    const parts = [`${NUGGET_TOTAL.toLocaleString()} match`];
    if (res.archive_total && res.archive_total !== NUGGET_TOTAL) parts.push(`${res.archive_total.toLocaleString()} in archive`);
    note.textContent = parts.join(' · ');
  }
}
let nugTimer = null;
$('#nuggets-q').addEventListener('input', () => {
  clearTimeout(nugTimer);
  nugTimer = setTimeout(() => { NUG.q = $('#nuggets-q').value.trim(); NUG.offset = 0; loadNuggets(); }, 250);
});
$('#nuggets-filter').addEventListener('click', e => {
  const b = e.target.closest('button[data-f]');
  if (!b) return;
  NUG.flag = b.dataset.f; NUG.offset = 0;
  loadNuggets();
});
$('#nuggets-body').addEventListener('click', e => {
  if (!e.target.closest('[data-facet-clear]')) return;
  Object.assign(NUG, { offset: 0, platform: '', community: '', category: '', flag: '', q: '' });
  $('#nuggets-q').value = '';
  loadNuggets();
});
$('#nuggets-facets').addEventListener('click', e => {
  // "clear" lives on the bar above with the active chips, not in the rail —
  // the rail can be shut, and a clear button you cannot reach is not one.
  const more = e.target.closest('[data-facet-more]');
  if (more) { NUGGET_FACET_MORE[more.dataset.facetMore] = !NUGGET_FACET_MORE[more.dataset.facetMore]; renderNuggetFacets(); return; }
  const b = e.target.closest('button[data-facet]');
  if (!b) return;
  const k = b.dataset.facet;
  NUG[k] = NUG[k] === b.dataset.value ? '' : b.dataset.value;
  if (k === 'platform') NUG.community = '';
  NUG.offset = 0;
  loadNuggets();
});
$('#nuggets-pager').addEventListener('click', e => {
  const b = e.target.closest('button[data-page-nug]');
  if (!b || b.disabled) return;
  NUG.offset = Number(b.dataset.pageNug);
  loadNuggets();
  $('#nuggets-body').closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
});

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
  llm_provider: 'default backend for the chat roles — fake (deterministic), ollama (local daemon) or groq (hosted). Override per role under Agent models.',
  embedding_provider: 'fake (deterministic) or ollama (local daemon)',
};
// Per-key choices now arrive on each knob as `meta.choices`, because this
// list was a copy of a vocabulary that lives in config.py and it went stale
// the moment a third llm_provider existed: the console kept offering two.
// Kept only as the fallback for a server that predates the field.
let CFG_BASE = {};
/** Knobs by pipeline stage. A flat grid of 26 mixed one prefilter char cap
 *  with a Groq budget; the operator thinks in stages, so the page does too. */
const KNOB_GROUPS = [
  ['Fetch', 'how much the worker walks and how politely', ['max_threads_per_source', 'max_threads_per_platform', 'max_comments_per_thread', 'min_comments_per_thread', 'max_reviews_per_app', 'realestate_detail_per_page', 'request_delay_ms', 'ingest_timeout_seconds']],
  ['Pre-filter', 'what is dropped before any model sees it', ['prefilter_min_chars', 'prefilter_max_chars', 'prefilter_min_words', 'prefilter_max_emoji', 'prefilter_max_mentions', 'min_upvotes']],
  ['Archive & synthesis', 'dedup and how ideas are formed', ['dedup_threshold', 'max_nuggets_per_idea', 'good_idea_min']],
  ['Critic web step', 'competition checks and their budget', ['competitor_search_provider', 'competitor_ttl_days', 'critic_web_rate_limit', 'critic_web_daily_budget']],
  ['Providers & output', 'backends and what a run leaves behind', ['llm_provider', 'embedding_provider', 'export_after_run', 'export_rows_per_file']],
];
function mapKnobValue(key) {
  const out = {};
  for (const row of $$(`[data-knob-map="${key}"] tr[data-map-row]`)) {
    const k = row.querySelector('[data-map-key]').value.trim();
    const v = row.querySelector('[data-map-val]').value.trim();
    if (k && v !== '') out[k] = +v;
  }
  return out;
}
function cfgDirty() {
  const out = {};
  for (const [key, base] of Object.entries(CFG_BASE)) {
    if (base && typeof base === 'object') {
      const box = $(`[data-knob-map="${key}"]`);
      if (!box) continue;
      const cur = mapKnobValue(key);
      const changed = JSON.stringify(cur) !== JSON.stringify(base);
      box.classList.toggle('dirty', changed);
      if (changed) out[key] = cur;
      continue;
    }
    const el = $(`[data-knob="${key}"]`);
    if (!el) continue;
    const raw = el.value.trim();
    const changed = typeof base === 'number' ? +raw !== base
      : typeof base === 'boolean' ? (raw === 'true') !== base : raw !== String(base);
    el.classList.toggle('dirty', changed);
    if (changed) out[key] = typeof base === 'number' ? +raw : typeof base === 'boolean' ? raw === 'true' : raw;
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
  const editable = { ...res.editable };
  delete editable.embedding_model;
  const knob = (key, meta) => {
    CFG_BASE[key] = meta.value;
    const range = meta.range ? `${meta.range[0]} – ${meta.range[1]}` : '';
    const choices = meta.choices;
    let control;
    if (meta.value && typeof meta.value === 'object') {
      // A platform → number map. It rendered as "[object Object]" before.
      const rows = Object.entries(meta.value);
      control = `<div data-knob-map="${key}"><table class="mapknob">
        <tbody>${rows.map(([k, v]) => `<tr data-map-row><td><input data-map-key value="${esc(k)}" placeholder="platform" style="inline-size:120px"></td>
          <td><input data-map-val type="number" min="1" max="200" step="1" value="${esc(v)}" style="inline-size:80px"></td>
          <td><button class="btn btn--ghost btn--sm" data-map-del aria-label="remove">✕</button></td></tr>`).join('')}</tbody></table>
        <button class="btn btn--ghost btn--sm" data-map-add>+ platform</button></div>`;
    } else if (typeof meta.value === 'boolean') {
      control = `<select id="knob-${key}" data-knob="${key}"><option value="true" ${meta.value ? 'selected' : ''}>on</option><option value="false" ${meta.value ? '' : 'selected'}>off</option></select>`;
    } else if (choices) {
      control = `<select id="knob-${key}" data-knob="${key}">${choices.map(p =>
        `<option ${p === meta.value ? 'selected' : ''}>${p}</option>`).join('')}</select>`;
    } else {
      control = `<input id="knob-${key}" data-knob="${key}" value="${esc(meta.value)}"
           ${meta.type === 'int' ? 'type="number" step="1"' : meta.type === 'float' ? 'type="number" step="0.01"' : ''}
           ${meta.range ? `min="${meta.range[0]}" max="${meta.range[1]}"` : ''}>`;
    }
    return `<div class="knob">
      <label for="knob-${key}">${esc(key.replace(/_/g, ' '))}</label>
      ${control}
      <span class="hint">${esc(KNOB_HELP[key] || '')}${range ? ` · ${range}` : ''}</span>
    </div>`;
  };
  const placed = new Set();
  const sections = KNOB_GROUPS.map(([title, sub, keys]) => {
    const here = keys.filter(k => editable[k]);
    here.forEach(k => placed.add(k));
    if (!here.length) return '';
    return `<details class="cfg-group" open><summary><h3 style="display:inline">${esc(title)}</h3> <span class="xs">${esc(sub)} · ${here.length} knob(s)</span></summary>
      <div class="knobgrid" style="margin-block-start:var(--s-5)">${here.map(k => knob(k, editable[k])).join('')}</div></details>`;
  });
  const rest = Object.keys(editable).filter(k => !placed.has(k));
  if (rest.length) sections.push(`<details class="cfg-group" open><summary><h3 style="display:inline">Other</h3> <span class="xs">${rest.length} knob(s)</span></summary>
      <div class="knobgrid" style="margin-block-start:var(--s-5)">${rest.map(k => knob(k, editable[k])).join('')}</div></details>`);
  $('#cfg-knobs').innerHTML = sections.join('');
  $('#cfg-knobs').classList.remove('knobgrid');
  $$('#cfg-knobs [data-knob], #cfg-knobs [data-map-key], #cfg-knobs [data-map-val]').forEach(el => { el.oninput = syncSavebar; el.onchange = syncSavebar; });
  syncSavebar();
  $('#cfg-scraper').innerHTML = Object.entries(res.scraper).map(([k, v]) => {
    const shown = /license|proxy|secret|key/i.test(k) ? (v ? '•••• set' : 'not set') : (v || '—');
    return `<dt>${esc(k)}</dt><dd class="mono">${esc(shown)}</dd>`;
  }).join('');
  $('#cfg-env').innerHTML = Object.entries(res.env).map(([k, v]) =>
    `<dt class="mono xs">${esc(k)}</dt><dd>${v === true ? tag('done', 'set', 'pill--sm') : v === false ? tag('off', 'not set', 'pill--sm') : `<span class="mono">${esc(v || '—')}</span>`}</dd>`
  ).join('');
  loadModels();
}
$('#cfg-knobs').addEventListener('click', e => {
  const add = e.target.closest('[data-map-add]');
  if (add) {
    const tbody = add.parentElement.querySelector('tbody');
    tbody.insertAdjacentHTML('beforeend', `<tr data-map-row><td><input data-map-key placeholder="platform" style="inline-size:120px"></td>
      <td><input data-map-val type="number" min="1" max="200" step="1" value="3" style="inline-size:80px"></td>
      <td><button class="btn btn--ghost btn--sm" data-map-del aria-label="remove">✕</button></td></tr>`);
    $$('[data-map-key], [data-map-val]', tbody).forEach(el => { el.oninput = syncSavebar; el.onchange = syncSavebar; });
    syncSavebar();
    return;
  }
  const del = e.target.closest('[data-map-del]');
  if (del) { del.closest('tr').remove(); syncSavebar(); }
});

const ROLE_HELP = {
  extractor: 'turns one comment into a nugget — one call per comment, so this is the throughput role',
  archivist: 'dedups and files nuggets into the archive',
  synthesizer: 'writes the problem and the proposed solution — one call per idea cluster',
  critic: 'scores an idea and checks competitors',
  embedding_model: 'vectorises nuggets for dedup and search (Ollama only)',
};
// Roles served by a chat provider, and therefore overridable per role. The
// embedding role is not one: Groq serves no embedding model.
const CHAT_ROLES = ['extractor', 'synthesizer', 'critic'];
const LLM_PROVIDER_OPTIONS = ['fake', 'ollama', 'groq'];
let PROVIDERS_BASE = {};
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

/** Per-role provider overrides, which live in thresholds.yaml rather than the
 *  `models:` block and so save through a different endpoint. */
function providersDirty() {
  const out = {};
  for (const [role, base] of Object.entries(PROVIDERS_BASE)) {
    const sel = $(`[data-provider="${role}"]`);
    if (!sel) continue;
    const changed = sel.value !== base;
    sel.classList.toggle('dirty', changed);
    if (changed) out[`${role}_provider`] = sel.value;
  }
  return out;
}

function syncModelsBar() {
  const n = Object.keys(modelsDirty()).length + Object.keys(providersDirty()).length;
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
    // Which backend serves THIS role. The embedding role has no override —
    // Groq serves no embedding model, so there is nothing to choose between.
    const provider = CHAT_ROLES.includes(role)
      ? (res.role_providers[role] || res.llm_provider) : res.embedding_provider;
    if (CHAT_ROLES.includes(role)) PROVIDERS_BASE[role] = provider;
    // A role served by Groq picks from Groq's catalogue; an Ollama role picks
    // from what is pulled. Offering the wrong list is offering a choice that
    // can only fail at run time.
    const pool = provider === 'groq' ? (res.groq_choices || [])
      : provider === 'ollama' ? (res.ollama_choices || []) : [];
    // The configured model always appears, even when it is not available —
    // the console must show what the file says, then flag the gap.
    const options = [...new Set([...pool, value].filter(Boolean))].sort();
    const known = options.includes(value);
    // Only an Ollama-served role can be "not pulled"; Groq has nothing to pull.
    const missing = (res.missing || []).includes(value);
    const unreachable = provider === 'groq' && res.groq_ready === false;
    return `<div class="knob">
      <label for="model-${role}">${esc(role.replace('_model', '').replace(/_/g, ' '))}
        ${missing ? '<span class="chip chip--warn">not pulled</span>' : ''}
        ${unreachable ? '<span class="chip chip--bad">groq unreachable</span>' : ''}</label>
      ${CHAT_ROLES.includes(role) ? `<select data-provider="${role}" aria-label="Provider for ${role}">
        ${LLM_PROVIDER_OPTIONS.map(o => `<option value="${esc(o)}" ${o === provider ? 'selected' : ''}>${esc(o)}</option>`).join('')}
      </select>` : ''}
      <select id="model-${role}" data-model="${role}">
        ${options.map(o => `<option value="${esc(o)}" ${o === value ? 'selected' : ''}>${esc(o)}</option>`).join('')}
        <option value="${CUSTOM}" ${known ? '' : 'selected'}>custom…</option>
      </select>
      <input data-model-custom="${role}" class="${known ? 'hidden' : ''}"
             value="${esc(known ? '' : value)}" placeholder="model name" autocomplete="off">
      <span class="hint">${esc(ROLE_HELP[role] || '')}</span>
    </div>`;
  }).join('');

  // Changing a role's provider changes which models are valid for it, so the
  // grid is rebuilt from the server rather than re-filtered in place — the
  // server owns which models each provider actually serves.
  $$('#models-grid [data-provider]').forEach(sel => {
    sel.onchange = () => { sel.classList.add('dirty'); syncModelsBar(); };
  });

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
  const providers = providersDirty();
  if (!Object.keys(patch).length && !Object.keys(providers).length) return;
  const res = await busy(e.currentTarget, async () => {
    // Providers first: saving a Groq model name under an Ollama-served role
    // would flag it "not pulled" for the moment between the two writes.
    if (Object.keys(providers).length) {
      const pr = await api('/api/thresholds', { patch: providers, profile: MODELS_PROFILE });
      if (pr.ok === false) return pr;
    }
    return Object.keys(patch).length
      ? api('/api/models', { patch, profile: MODELS_PROFILE })
      : { ok: true, applied: {}, profile: MODELS_PROFILE };
  });
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  const n = Object.keys(res.applied || {}).length + Object.keys(providers).length;
  toast(`saved ${n} setting(s) to ${res.profile || MODELS_PROFILE}`, 'ok');
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

// ── schedule ────────────────────────────────────────────────────────────
let SCHED = null;

/** The scope of a schedule in one readable phrase, for the summary row. */
function describeScope(opts, total) {
  const names = (opts && opts.only) || [];
  const where = names.length
    ? `${names.length} of ${total} source${total === 1 ? '' : 's'}`
    : 'every enabled source';
  const depth = [];
  if (opts && opts.max_posts) depth.push(`${opts.max_posts} post(s)`);
  if (opts && opts.max_comments) depth.push(`${opts.max_comments} comment(s)`);
  if (opts && opts.max_ideas) depth.push(`${opts.max_ideas} idea(s)`);
  return where + ' · ' + (depth.length ? `max ${depth.join(', ')}` : 'no depth ceiling');
}

function renderSchedule(res) {
  SCHED = res;
  const on = res.installed;
  const opts = res.options || {};
  const sources = res.sources || [];

  $('#sched-banner').innerHTML = on
    ? `<div class="banner banner--ok"><span aria-hidden="true">✓</span><span>
         <b>Scheduled</b>${esc(res.cadence || 'registered')} via ${esc(res.scheduler)},
         scraping ${esc(describeScope(opts, sources.length))}.</span></div>`
    : `<div class="banner"><span aria-hidden="true">◷</span><span>
         <b>Nothing scheduled</b>Choose what to scrape and how often; the host scheduler
         will run the full loop for you, with this console closed.</span></div>`;

  $('#sched-state').innerHTML = [
    ['status', on ? pill('installed') : pill('off')],
    ['scheduler', esc(res.scheduler || '—')],
    ['cadence', esc(res.cadence || '—')],
    ['next run', esc(res.next_run || '—')],
    // What it will FETCH, read back from the launcher the scheduler runs —
    // not from this form, which may hold edits nobody has saved.
    ['scrapes', on ? esc(describeScope(opts, sources.length)) : '—'],
    ['task name', `<span class="mono">${esc(res.task_name)}</span>`],
    ['runs', `<span class="mono">${esc(res.command)}</span>`],
  ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');

  renderScheduleScope(res);
  renderScheduleActivity(res);

  $('#sched-presets').innerHTML = (res.presets || []).map(m =>
    `<button class="btn btn--ghost btn--sm" data-preset="${m}">${labelMinutes(m)}</button>`).join('');

  $('#sched-logpath').textContent = res.log_path || '';
  $('#sched-log').textContent = res.log_tail
    ? res.log_tail
    : 'no scheduled run has written a log yet';

  $('#sched-remove').disabled = !on;
  $('#sched-runnow').disabled = !on;
  // POSIX cannot be installed for you — jester will not edit a crontab behind
  // your back, so the buttons print the line instead of pretending to work.
  const manual = !res.windows;
  $('#sched-install-every').textContent = manual ? 'show crontab line' : 'set interval';
  $('#sched-install-daily').textContent = manual ? 'show daily line' : 'set daily';
}

function labelMinutes(m) {
  if (m % 60 === 0) return `${m / 60}h`;
  return `${m}m`;
}


/** Fill the scope form from what is REGISTERED, so opening the page shows the
 *  live schedule rather than whatever the form last happened to hold. */

/** Ran-vs-should-have-run for one window. The count alone cannot tell a healthy
 *  schedule from a dead one; the pair can. */
function renderScheduleActivity(res) {
  const act = res.activity || {};
  const w = act.windows || {};
  const tile = (label, value, sub, cls) =>
    `<div class="kpi ${cls || ''}"><b>${value}</b><span>${esc(label)}</span>
       ${sub ? `<span class="kpi-sub xs">${sub}</span>` : ''}</div>`;

  const bucket = (hours, label) => {
    const b = w[hours];
    if (!b) return '';
    const exp = b.expected;
    // Only colour the comparison once there is one to make. No cadence (a
    // hand-made trigger) or no runs yet means no verdict, not a red tile.
    let cls = '';
    if (exp) {
      if (b.runs >= exp) cls = 'kpi--ok';
      else if (b.runs >= Math.ceil(exp * 0.6)) cls = 'kpi--live';
      else cls = 'kpi--alert';
    }
    const sub = exp
      ? `of ${exp} expected${b.partial ? ' so far' : ''}`
      : 'no cadence to compare';
    return tile(label, b.runs, sub, cls);
  };

  const last = act.last;
  $('#sched-kpis').innerHTML = [
    bucket('1', 'runs · last hour'),
    bucket('24', 'runs · last 24h'),
    tile('runs all time', act.total || 0,
         act.first_run_at ? `since ${esc(when(act.first_run_at))}` : 'never run'),
    tile('last tick', last ? esc(ago(last.started_at)) : '—',
         last ? esc(when(last.started_at)) : 'no scheduled run yet'),
    tile('archived · last 24h', `${(w['24'] || {}).nuggets || 0}`,
         `${(w['24'] || {}).ideas || 0} idea(s)`),
  ].join('');

  const ticks = act.recent || [];
  $('#sched-ticks').innerHTML = ticks.map(t => `
    <tr>
      <td class="mono">${esc(when(t.started_at))}<div class="xs">${esc(ago(t.started_at))}</div></td>
      <td class="mono">${esc(t.run_id)}</td>
      <td>${pill(t.status)}</td>
      <td class="num">${int(t.n_comments)}</td>
      <td class="num">${int(t.n_nuggets_kept)}</td>
      <td class="num">${int(t.n_ideas)}</td>
    </tr>`).join('')
    || emptyRow(6, res.installed
      ? 'Registered, but it has not fired yet — use “run it now” to prove it works.'
      : 'Nothing scheduled yet.');
}

function renderScheduleScope(res) {
  const opts = res.options || {};
  const chosen = new Set(opts.only || []);
  // An empty selection means "every enabled source", so that is what the
  // boxes must show — an all-unticked list would read as "scrape nothing".
  const all = !chosen.size;
  const sources = res.sources || [];

  $('#sched-sources').innerHTML = checklistHTML(sources, {
    boxClass: 'sched-src',
    checked: s => (all ? s.enabled : chosen.has(s.name)),
    title: s => s.supported ? esc(s.url)
      : 'no worker adapter for ' + esc(s.platform) + '/' + esc(s.kind) + ' yet',
  });

  const off = sources.length - sources.filter(s => s.supported).length;
  $('#sched-sources-hint').textContent = off
    ? `${sources.length - off} of ${sources.length} can be fetched; `
      + `${off} ${off === 1 ? 'has' : 'have'} no worker adapter yet.`
    : `${sources.length} source(s) available.`;

  $('#sched-max-posts').value = opts.max_posts || '';
  $('#sched-max-comments').value = opts.max_comments || '';
  $('#sched-max-ideas').value = opts.max_ideas || '';
  syncSchedSelectAll();
}

function syncSchedSelectAll() {
  const boxes = $$('.sched-src:not([disabled])');
  const on = boxes.filter(b => b.checked).length;
  syncChecklistGroups('#sched-sources', 'sched-src');
  const all = $('#sched-select-all');
  all.checked = on > 0 && on === boxes.length;
  all.indeterminate = on > 0 && on < boxes.length;
  $('#sched-selected-count').textContent =
    on === boxes.length ? 'all sources' : `${on} selected`;
  // A schedule that fetches nothing is not a schedule.
  $('#sched-install-every').disabled = on === 0;
  $('#sched-install-daily').disabled = on === 0;
}

/** The scope the form is currently describing. Ticking every box posts an
 *  empty list, which the worker reads as "walk the enabled list" — that keeps
 *  a schedule following the Sources tab instead of freezing today's names. */
function scheduleScope() {
  const boxes = $$('.sched-src:not([disabled])');
  const picked = boxes.filter(b => b.checked).map(b => b.dataset.sourceName);
  const num = id => {
    const v = parseInt($(id).value, 10);
    return Number.isFinite(v) && v > 0 ? v : null;
  };
  return {
    only: picked.length === boxes.length ? [] : picked,
    max_posts: num('#sched-max-posts'),
    max_comments: num('#sched-max-comments'),
    max_ideas: num('#sched-max-ideas'),
  };
}

$('#sched-select-all').addEventListener('change', e => {
  $$('.sched-src:not([disabled])').forEach(cb => { cb.checked = e.target.checked; });
  syncSchedSelectAll();
});
$('#sched-sources').addEventListener('change', e => {
  handleGroupToggle(e, '#sched-sources');
  syncSchedSelectAll();
});

async function loadSchedule() {
  const res = await api('/api/schedule');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  renderSchedule(res);
}

$('#sched-presets').addEventListener('click', e => {
  const b = e.target.closest('button[data-preset]');
  if (!b) return;
  $('#sched-every').value = b.dataset.preset;
  $('#sched-install-every').click();
});

async function installSchedule(body, el) {
  const res = await busy(el, () => api('/api/schedule/install', body));
  // A POSIX install is `ok:false` with the crontab line as its detail — that
  // is an instruction, not a failure, so it must not render as an error.
  const manual = res.action === 'manual';
  toast(res.detail || (res.ok ? 'scheduled' : 'could not schedule'),
        res.ok ? 'ok' : (manual ? 'bad' : 'bad'));
  if (res.schedule) renderSchedule(res.schedule); else loadSchedule();
}

$('#sched-install-every').onclick = e => {
  const every = parseInt($('#sched-every').value, 10);
  if (!Number.isFinite(every) || every < 1 || every > 1439) {
    toast('Interval must be a whole number of minutes between 1 and 1439', 'bad');
    return;
  }
  installSchedule({ every, options: scheduleScope() }, e.currentTarget);
};

$('#sched-install-daily').onclick = e => {
  const at = $('#sched-at').value.trim();
  if (!/^\d{1,2}:\d{2}$/.test(at)) { toast('Time must be HH:MM', 'bad'); return; }
  installSchedule({ at, options: scheduleScope() }, e.currentTarget);
};

$('#sched-remove').onclick = async e => {
  const res = await busy(e.currentTarget, () => api('/api/schedule/remove', {}));
  toast(res.detail || (res.ok ? 'removed' : 'could not remove'), res.ok ? 'ok' : 'bad');
  if (res.schedule) renderSchedule(res.schedule); else loadSchedule();
};

$('#sched-runnow').onclick = async e => {
  const res = await busy(e.currentTarget, () => api('/api/schedule/run', {}));
  toast(res.detail || (res.ok ? 'task triggered' : 'could not trigger'), res.ok ? 'ok' : 'bad');
  // The task runs detached; give it a moment, then show what it wrote.
  setTimeout(loadSchedule, 4000);
};

// ── doctor ──────────────────────────────────────────────────────────────
/** A finding is prose from the guards; the fix is one of a few known moves. */
const DOCTOR_FIXES = [
  [/stuck in 'running'/i, 'warn', { page: 'runs', label: 'open Runs' }, 'A run row says running but no process owns it (crashed session). It clears itself on the next successful run.'],
  [/reembed_backlog/i, 'warn', { act: '/api/reembed', label: 're-embed now' }, 'Those nuggets are archived but invisible to dedup and search until re-embedded.'],
  [/failed/i, 'bad', { act: '/api/requeue', label: 'requeue failed' }, 'Failed batches hold comments that were fetched and never treated.'],
  [/cloakserve|9222/i, 'bad', { page: 'sources', label: 'see affected sources' }, 'Browser-fetched sources are skipped until cloakserve is back.'],
  [/ollama/i, 'bad', { page: 'config', label: 'open Config' }, 'Live models fall back to the deterministic stand-in.'],
  [/quota|rate.?limit|groq/i, 'warn', { page: 'queue', label: 'open Queue' }, 'Treatment pauses; the queue is the buffer.'],
  [/source/i, 'warn', { page: 'sources', label: 'open Sources' }, ''],
];
async function loadDoctor() {
  const doc = await api('/api/doctor');
  if (doc.error) { toast(doc.error, 'bad'); return; }
  const findings = doc.findings || [];
  COUNTS.findings = findings.length;
  renderNav();
  $('#doctor-card').innerHTML = findings.length === 0
    ? '<div class="banner banner--ok"><span aria-hidden="true">✓</span><span><b>Doctor clean</b>No silent-degradation guard tripped.</span></div>'
    : `<div class="stack">${findings.map(f => {
        const fix = DOCTOR_FIXES.find(([re]) => re.test(f)) || [null, 'warn', { page: 'runs', label: 'open Runs' }, ''];
        const [, sev, next, why] = fix;
        return `<div class="fix ${sev}"><span class="sevdot"></span>
          <span><b>${esc(f)}</b>${why ? `<span class="xs">${esc(why)}</span>` : ''}</span>
          <button class="btn btn--sm" ${next.act ? `data-act="${next.act}"` : `data-go="${next.page}"`}>${esc(next.label)}</button></div>`;
      }).join('')}</div>`;
  loadEval();
}
$('#doctor-card').addEventListener('click', e => {
  const b = e.target.closest('button[data-act]');
  if (b) runAction(b.dataset.act, {}, b);
});

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

// ── pipeline configuration modal ────────────────────────────────────────
let SOURCES_LIST = [];

function openModal() {
  $('#pipeline-modal').showModal();
  // `hidden` is the scrim's resting state in the markup so it cannot flash
  // before this file runs; the class drives the fade. Both have to move, or
  // whichever one is left behind wins and the page never dims.
  $('#modal-scrim').hidden = false;
  $('#modal-scrim').classList.add('on');
}

function closeModal() {
  $('#pipeline-modal').close();
  $('#modal-scrim').classList.remove('on');
  $('#modal-scrim').hidden = true;
}

// A source is identified by its NAME everywhere else in this file and in
// sources.yaml — there is no `id` field on the payload, so the old
// data-source-id="${s.id}" put the string "undefined" on all sixteen boxes.
async function loadSourcesForModal() {
  const res = await api('/api/sources');
  if (res.ok === false) { toast('Failed to load sources', 'bad'); return; }
  SOURCES_LIST = res.sources || [];

  const selectable = SOURCES_LIST.filter(s => s.supported);
  $('#sources-checklist').innerHTML = checklistHTML(SOURCES_LIST, {
    boxClass: 'source-check',
    checked: s => s.enabled,
    title: s => s.supported ? esc(s.url)
      : 'no worker adapter for ' + esc(s.platform) + '/' + esc(s.kind) + ' yet',
  });

  syncSelectAll();
  const off = SOURCES_LIST.length - selectable.length;
  $('#sources-hint').textContent = off
    ? `${selectable.length} of ${SOURCES_LIST.length} can be fetched; `
      + `${off} ${off === 1 ? 'has' : 'have'} no worker adapter yet.`
    : `${SOURCES_LIST.length} source(s) available.`;
}

// The header box reflects the boxes below it in both directions, including the
// in-between state — "select all" that silently means "none of the 4 I ticked"
// is how an operator starts a run over the wrong list.
function syncSelectAll() {
  const boxes = $$('.source-check:not([disabled])');
  const on = boxes.filter(b => b.checked).length;
  syncChecklistGroups('#sources-checklist', 'source-check');
  const all = $('#select-all-sources');
  all.checked = on > 0 && on === boxes.length;
  all.indeterminate = on > 0 && on < boxes.length;
  $('#modal-run').disabled = on === 0;
  $('#selected-count').textContent = `${on} selected`;
}

$('#select-all-sources').addEventListener('change', e => {
  const checked = e.target.checked;
  $$('.source-check:not([disabled])').forEach(cb => { cb.checked = checked; });
  syncSelectAll();
});
$('#sources-checklist').addEventListener('change', e => {
  handleGroupToggle(e, '#sources-checklist');
  syncSelectAll();
});

$('#goal-type').addEventListener('change', e => {
  const showValue = e.target.value !== 'none';
  $('#goal-value-field').style.display = showValue ? 'block' : 'none';
});

$('#modal-close').onclick = closeModal;
$('#modal-cancel').onclick = closeModal;
$('#modal-scrim').onclick = closeModal;

function openPipelineModal() {
  loadSourcesForModal();
  openModal();
}

$('#modal-run').onclick = async () => {
  // `source_names`, not `source_ids`: the server matches these against
  // sources.yaml by name and refuses the run if one is unknown, so a typo can
  // never quietly become a zero-source fetch.
  const selectedSources = $$('.source-check:checked').map(cb => cb.dataset.sourceName);
  if (selectedSources.length === 0) {
    toast('Select at least one source', 'bad');
    return;
  }

  const goalType = $('#goal-type').value;
  const body = {
    run_id: $('#run-id-input').value.trim() || `console-${Date.now()}`,
    source_names: selectedSources,
  };

  if (goalType !== 'none') {
    const goalValue = parseInt($('#goal-value').value, 10);
    if (!Number.isFinite(goalValue) || goalValue < 1) {
      toast('The run goal needs a whole number of 1 or more', 'bad');
      return;
    }
    body.goal = { type: goalType, value: goalValue };
  }

  closeModal();
  await runAction('/api/run', body, $('button[data-act="/api/run"]'));
};

// The modal interception lives INSIDE the single #quick-actions listener
// above, not in a second one. A second listener on the same node cannot
// cancel the first — stopPropagation does not stop other handlers on the same
// element, and the first was registered first anyway — so the earlier
// arrangement fired a real run and then opened the modal to configure the run
// it had just started.

// ── run strip: what is running now, on every page ────────────────────────
async function pollRunning() {
  const res = await api('/api/running');
  const strip = $('#runstrip');
  if (!strip) return;
  const runs = (res.ok !== false && res.running) || [];
  if (!runs.length) { strip.classList.add('hidden'); strip.innerHTML = ''; return; }
  const r = runs[0];
  strip.classList.remove('hidden');
  strip.innerHTML = `<span class="dot"></span><span class="mono">${esc(r.run_id)}</span>
    <span>${esc(r.phase)}</span><span class="xs">${esc(ago(r.started_at))}${r.n_comments ? ` · ${Number(r.n_comments).toLocaleString()} comment(s)` : ''}</span>
    ${runs.length > 1 ? `<span class="xs">+${runs.length - 1} more</span>` : ''}`;
  strip.title = `${r.origin} run · click for Runs`;
}
$('#runstrip').addEventListener('click', () => go('runs'));
pollRunning();
setInterval(pollRunning, 15000);

// ── help sheet + delegated navigation ────────────────────────────────────
function openHelp() { $('#help-modal').showModal(); }
function closeHelp() { $('#help-modal').close(); }
$('#help-btn').onclick = openHelp;
$('#help-close').onclick = closeHelp;
document.addEventListener('click', e => {
  const b = e.target.closest('[data-go]');
  if (b && b.dataset.go) go(b.dataset.go);
});
// Close the "more" menu when clicking elsewhere.
document.addEventListener('click', e => {
  $$('details.menu[open]').forEach(d => { if (!d.contains(e.target)) d.open = false; });
});

// ── keyboard ─────────────────────────────────────────────────────────────
const PAGE_KEYS = { o: 'overview', r: 'runs', q: 'queue', i: 'ideas', n: 'nuggets', c: 'clusters', s: 'sources', d: 'doctor' };
let chord = null;
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#pipeline-modal').open) closeModal();
    else if ($('#help-modal').open) closeHelp();
    else closeDrawer();
    return;
  }
  const t = e.target;
  if ((t instanceof Element && t.matches('input, select, textarea, [contenteditable]'))
      || e.metaKey || e.ctrlKey || e.altKey) return;
  if (chord === 'g') {
    chord = null;
    if (PAGE_KEYS[e.key]) { e.preventDefault(); go(PAGE_KEYS[e.key]); }
    return;
  }
  if (e.key === 'g') { chord = 'g'; setTimeout(() => { chord = null; }, 1200); return; }
  if (e.key === '?') { e.preventDefault(); openHelp(); return; }
  if (e.key === '/') {
    const box = $(`[data-page="${CURRENT}"] input[type="text"], [data-page="${CURRENT}"] input:not([type])`);
    if (box) { e.preventDefault(); box.focus(); box.select(); }
    else { e.preventDefault(); go('search'); setTimeout(() => $('#search-q').focus(), 50); }
    return;
  }
  if (e.key === 'r') $('#refresh-btn').click();
  const idx = '12345678'.indexOf(e.key);
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

/* ── the collector problem signals ─────────────────────────────────────────────────
 * A page of its own, reading a database of its own. The nugget archive and
 * the problem-signal archive answer to different people: one is Jester's, the
 * other is a task deliverable whose every row has to declare whether it is
 * live or fixture. Showing them in one table would make that distinction a
 * column nobody reads.
 *
 * What this page is for is narrow, and the layout says so: the counts, then
 * the runs that produced them, then the records. A reader's first question is
 * "did the collector run and what did it find", not "show me row 1".
 */
const SIG = { audience: '', community: '', kind: '', mode: '', q: '', offset: 0, limit: 50 };
let SIG_FACETS = {};
let SIG_TOTAL = 0;
let SIG_EXISTS = true;
let SFACETS_OPEN = FACETS_OPEN;

const AUDIENCE_TONE = { buyer: 'tone-ok', practitioner: 'tone-info', none: 'tone-neutral' };

function applySignalFacetsOpen() {
  const layout = $('#signals-layout'); const btn = $('#sfacets-toggle');
  if (!layout || !btn) return;
  layout.classList.toggle('is-collapsed', !SFACETS_OPEN);
  btn.setAttribute('aria-expanded', String(SFACETS_OPEN));
  $('#sfacets-toggle-label').textContent = SFACETS_OPEN ? 'hide filters' : 'filters';
}

function signalFacetBlock(title, key, items, limit) {
  if (!items || !items.length) return '';
  return '<div class="facet"><h4>' + esc(title) + '</h4>'
    + items.slice(0, limit).map(it =>
      '<button data-sfacet="' + key + '" data-value="' + esc(it.value) + '"'
      + ' aria-pressed="' + (SIG[key] === it.value) + '">'
      + '<span class="truncate">' + esc(it.value || 'not recorded') + '</span>'
      + '<span class="n">' + Number(it.n).toLocaleString() + '</span></button>').join('')
    + '</div>';
}

function renderSignalActive() {
  const label = { audience: 'audience', community: 'source', kind: 'kind', mode: 'mode', q: 'text' };
  const chips = Object.keys(label).filter(k => SIG[k]).map(k =>
    '<span class="fchip"><b>' + label[k] + '</b> ' + esc(SIG[k])
    + '<button data-sfacet-drop="' + k + '" aria-label="Remove ' + label[k] + ' filter">✕</button></span>'
  ).join('');
  $('#signals-active').innerHTML = chips
    ? chips + '<button class="btn btn--ghost btn--sm" data-sfacet-clear>clear all</button>'
    : '<span class="xs">showing every record, kept and rejected</span>';
}

function renderSignalTiles(counts) {
  /* Split by mode, never summed. A fixture row counted as a live one is the
   * first item on this task's fail list, so the tiles never add them up. */
  const modes = Object.keys(counts || {});
  if (!modes.length) { $('#signals-tiles').innerHTML = ''; return; }
  const live = counts.live || { unique_collected: 0, buyer: 0, practitioner: 0, errors: 0 };
  const fixture = counts.fixture;
  const tiles = [
    ['collected (live)', live.unique_collected, 'unique records, deduplicated'],
    ['buyer signals', live.buyer, 'what outreach can act on'],
    ['practitioner', live.practitioner, 'content material, not leads'],
    ['failed fetches', live.errors, live.errors ? 'stored as rows, not lost' : 'none in this archive'],
  ];
  $('#signals-tiles').innerHTML = tiles.map(t =>
    '<div class="kpi' + (t[0] === 'failed fetches' && t[1] ? ' kpi--alert' : '') + '">'
    + '<span>' + esc(t[0]) + '</span><b class="num">' + Number(t[1] || 0).toLocaleString() + '</b>'
    + '<div class="kpi-sub">' + esc(t[2]) + '</div></div>').join('')
    + (fixture
      ? '<div class="kpi"><span>fixture rows</span><b class="num">'
        + Number(fixture.unique_collected).toLocaleString()
        + '</b><div class="kpi-sub">never counted as live</div></div>'
      : '');
}

function renderSignalRuns(runs, sources) {
  const body = $('#signal-runs-body');
  if (!runs || !runs.length) {
    const hint = sources && sources.length
      ? ' Collect with <span class="mono">jester signals run --source ' + esc(sources[0].name) + '</span>.'
      : '';
    body.innerHTML = '<tr><td colspan="8" class="empty">No run recorded yet.' + hint + '</td></tr>';
    return;
  }
  const tone = { ok: 'tone-ok', partial: 'tone-warn', failed: 'tone-bad' };
  body.innerHTML = runs.map(r =>
    '<tr><td class="mono xs">' + esc((r.started_utc || '').slice(0, 19).replace('T', ' ')) + '</td>'
    + '<td>' + esc(r.kind) + '</td><td class="mono xs">' + esc(r.source) + '</td>'
    + '<td class="num">' + Number(r.collected).toLocaleString() + '</td>'
    + '<td class="num">' + Number(r.new).toLocaleString() + '</td>'
    + '<td class="num">' + Number(r.buyer).toLocaleString() + '</td>'
    + '<td class="num">' + Number(r.errors).toLocaleString() + '</td>'
    + '<td><span class="pill pill--sm ' + (tone[r.status] || 'tone-neutral') + '">' + esc(r.status) + '</span>'
    + (r.note ? '<span class="xs"> ' + esc(r.note) + '</span>' : '') + '</td></tr>').join('');
}

function renderSignals(rows) {
  const body = $('#signals-body');
  if (!rows.length) {
    /* The empty state has to know why it is empty. "Nothing here" on a
     * filtered view of a full archive sends people to re-run a collector that
     * is working fine. */
    const filtered = ['audience', 'community', 'kind', 'mode', 'q'].some(k => SIG[k]);
    const msg = !SIG_EXISTS
      ? 'No signal archive yet — run <span class="mono">jester signals run</span> to create one.'
      : filtered
        ? 'Nothing matches these filters. <button class="btn btn--ghost btn--sm" data-sfacet-clear>clear the filters</button>'
        : 'The archive is empty. <span class="mono">jester signals run</span> collects into it.';
    body.innerHTML = '<tr><td colspan="4" class="empty">' + msg + '</td></tr>';
    return;
  }
  body.innerHTML = rows.map(r => {
    const what = (r.title || r.excerpt || '').trim();
    const flags = [];
    if (r.run_status !== 'ok') flags.push('<span class="pill pill--sm tone-bad">fetch failed</span>');
    if (r.edited_utc) flags.push('<span class="pill pill--sm tone-warn">edited x' + r.revisions + '</span>');
    if (r.removed_utc) flags.push('<span class="pill pill--sm tone-warn">removed at source</span>');
    if (/ambiguous/.test(r.match_reason || '')) flags.push('<span class="pill pill--sm tone-neutral">ambiguous</span>');
    if (/inherited/.test(r.match_reason || '')) flags.push('<span class="pill pill--sm tone-neutral">voice inherited</span>');
    // A hiring record names its own company and quotes its engagement line;
    // a forum record leaves both "unknown" and shows neither.
    const named = r.company && r.company !== 'unknown' ? esc(r.company) : '';
    const intent = r.buyer_intent && r.buyer_intent !== 'unknown'
      ? '<span class="pill pill--sm tone-info">' + esc(r.buyer_intent) + '</span>' : '';
    return '<tr><td><a href="' + esc(r.source_url) + '" target="_blank" rel="noopener">'
      + (named || esc(what.slice(0, 110)) || '(no title)') + '</a>'
      + (named ? '<div class="xs">' + esc(what.slice(0, 100)) + '</div>' : '')
      + '<div class="xs">' + esc(r.community) + ' &middot; ' + esc(r.kind)
      + ' &middot; found by <span class="mono">' + esc(r.query) + '</span> ' + intent + '</div>'
      + (flags.length
        ? '<div class="row" style="gap:var(--s-2);margin-block-start:var(--s-2)">' + flags.join('') + '</div>'
        : '')
      + '</td><td><span class="pill pill--sm ' + (AUDIENCE_TONE[r.audience] || 'tone-neutral') + '">'
      + esc(r.audience) + '</span></td>'
      + '<td class="num">' + Number(r.match_confidence).toFixed(2) + '</td>'
      + '<td class="xs">' + esc(r.match_reason || '') + '</td></tr>';
  }).join('');
}

function renderSignalPager() {
  const from = SIG_TOTAL ? SIG.offset + 1 : 0;
  const to = Math.min(SIG.offset + SIG.limit, SIG_TOTAL);
  $('#signals-shown').textContent = SIG_TOTAL
    ? from.toLocaleString() + '–' + to.toLocaleString() + ' of ' + SIG_TOTAL.toLocaleString() : '';
  $('#signals-pager').innerHTML =
    '<button class="btn btn--ghost btn--sm" data-spage="prev" ' + (SIG.offset ? '' : 'disabled') + '>&lsaquo; prev</button>'
    + '<button class="btn btn--ghost btn--sm" data-spage="next" ' + (to < SIG_TOTAL ? '' : 'disabled') + '>next &rsaquo;</button>';
}

async function loadSignals() {
  const qs = new URLSearchParams(
    Object.entries(SIG).filter(e => e[1] !== '' && e[1] !== null)).toString();
  const pair = await Promise.all([
    api('/api/signals?' + qs).catch(() => null),
    api('/api/signals/runs').catch(() => null),
  ]);
  const d = pair[0]; const runs = pair[1];
  if (!d) return;
  SIG_EXISTS = d.exists !== false;
  SIG_TOTAL = d.total || 0;
  SIG_FACETS = d.facets || {};
  const live = (d.counts || {}).live || {};
  $('#signals-count').textContent = SIG_EXISTS
    ? (live.unique_collected || 0).toLocaleString() + ' live record(s) in ' + d.path
    : 'no archive at ' + d.path + ' yet';
  renderSignalTiles(d.counts || {});
  renderSignalRuns((runs || {}).runs || [], (runs || {}).sources || []);
  $('#signals-facets').innerHTML =
    signalFacetBlock('Audience', 'audience', SIG_FACETS.audience || [], 6)
    + signalFacetBlock('Source', 'community', SIG_FACETS.community || [], 10)
    + signalFacetBlock('Kind', 'kind', SIG_FACETS.kind || [], 4)
    + signalFacetBlock('Mode', 'mode', SIG_FACETS.mode || [], 4);
  renderSignalActive();
  applySignalFacetsOpen();
  renderSignals(d.rows || []);
  renderSignalPager();
  // The nav badge counts what this page is FOR. Total records would make the
  // badge read 455 on an archive holding three things anyone can act on.
  COUNTS.signals = (live.buyer || 0) || null;
  renderNav();
}

$('#sfacets-toggle').onclick = () => { SFACETS_OPEN = !SFACETS_OPEN; applySignalFacetsOpen(); };
$('#signals-facets').addEventListener('click', e => {
  const b = e.target.closest('[data-sfacet]');
  if (!b) return;
  const k = b.dataset.sfacet;
  SIG[k] = SIG[k] === b.dataset.value ? '' : b.dataset.value;
  SIG.offset = 0;
  loadSignals();
});
function clearSignalFilters() {
  Object.assign(SIG, { audience: '', community: '', kind: '', mode: '', q: '', offset: 0 });
  $('#signals-q').value = '';
  loadSignals();
}
$('#signals-active').addEventListener('click', e => {
  const drop = e.target.closest('[data-sfacet-drop]');
  if (drop) {
    SIG[drop.dataset.sfacetDrop] = '';
    if (drop.dataset.sfacetDrop === 'q') $('#signals-q').value = '';
    SIG.offset = 0;
    loadSignals();
    return;
  }
  if (e.target.closest('[data-sfacet-clear]')) clearSignalFilters();
});
$('#signals-body').addEventListener('click', e => {
  if (e.target.closest('[data-sfacet-clear]')) clearSignalFilters();
});
$('#signals-pager').addEventListener('click', e => {
  const b = e.target.closest('[data-spage]');
  if (!b) return;
  SIG.offset = Math.max(0, SIG.offset + (b.dataset.spage === 'next' ? SIG.limit : -SIG.limit));
  loadSignals();
});
let sigTimer;
$('#signals-q').addEventListener('input', () => {
  clearTimeout(sigTimer);
  sigTimer = setTimeout(() => {
    SIG.q = $('#signals-q').value.trim();
    SIG.offset = 0;
    loadSignals();
  }, 250);
});

// The topbar chips and the nav badges come from /api/overview, which until
// now was only fetched by the Overview page's own loader. Deep-linking to
// any other page - which is what a bookmark, a shared link and every
// screenshot do - left the chips reading "db -" and the nav counts blank.
// Fetch the counts on boot regardless of which page is opening.
if ((location.hash.slice(1) || 'overview') !== 'overview') refreshCounts();
go(location.hash.slice(1) || 'overview', { push: false });
