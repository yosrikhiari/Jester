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
  { group: 'Archive', id: 'clusters', label: 'Clusters', icon: '❋', load: loadClusters, count: () => COUNTS.clusters },
  { group: 'Archive', id: 'search', label: 'Search', icon: '⌕', load: loadSearch },
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
      [infra.cloakserve_9222, 'cloakserve :9222'],
      [infra.worker_go_available, 'Go worker'],
    ].map(([up, label]) => tag(up ? 'done' : 'off', label, 'pill--sm')).join(' ')
    // Vectors get their own pill, because "the port is open" and "the app
    // writes there" are different claims and only the second one matters.
    // For a whole day this panel was green while every write went to a
    // 32-dimension local file.
    + ' ' + vectorPill(infra.vectors);
    $('#btn-live-models').classList.toggle('hidden',
      !(infra.ollama.up && infra.ollama.models.some(m => /qwen|mistral|phi|llama|gemma/i.test(m))));
    $('#btn-ingest-live').classList.toggle('hidden', !infra.can_ingest_live);
    $('#btn-ingest-mock').classList.toggle('hidden', !infra.worker_go_available);
  }
}

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
  github: 'GitHub', lemmy: 'Lemmy',
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

//: The last /api/sources response, so folding a platform open or shut is a
//: re-render rather than a round trip — and so a fold survives one.
let SOURCES_RES = { sources: [] };

async function loadSources() {
  const res = await api('/api/sources');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  SOURCES_RES = res;
  renderPlatformOptions(res.platform_kinds);
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
                  github: 4, lemmy: 5, youtube: 6, tiktok: 7 };
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
      key, depth: 1, span: 7, count: rows.length, meta: esc(meta),
      label: esc(PLATFORM_LABEL[platform] || platform),
    });
    return head + (FOLDED.has(key) ? '' : rows.map(sourceRow).join(''));
  }).join('') || emptyRow(7, 'No sources yet — add one above.');

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
    const platforms = Object.entries(r.platform_counts_obj || {});
    const signals = [
      ...platforms.map(([p, n]) => `<span class="chip">${esc(p)} ${esc(n)}</span>`),
      ...(r.floor_flags_list || []).map(f => `<span class="chip chip--bad">${esc(f)}</span>`),
      ...funnel.map(([k, v]) => `<span class="chip">${esc(k)} ${esc(v)}</span>`),
      near.length ? `<span class="chip">near ${esc(near.join(', '))}</span>` : '',
      r.error ? `<span class="chip chip--bad" title="${esc(r.error)}">error</span>` : '',
    ].filter(Boolean).join('');
    const origin = r.origin || 'manual';
    return `<tr data-id="${r.id}" style="cursor:pointer" title="Click to view details">
      <td><span class="mono">${esc(when(r.started_at))}</span>
          <div class="xs">${esc(ago(r.started_at))} · ${int(r.n_posts)} post(s)</div></td>
      <td class="mono">${esc(r.run_id)}</td>
      <td>${pill(r.status)}</td>
      <td class="num">${int(r.n_nuggets_kept)} / ${int(r.n_ideas)}</td>
      <td class="num">${num(r.duration_actual_s, 1)}s<div class="xs">exp ${num(r.duration_expected_s, 1)}s</div></td>
      <td><span class="chip${origin === 'scheduled' ? ' chip--sched' : ''}"
            >${origin === 'scheduled' ? '◷ ' : ''}${esc(origin)}</span></td>
      <td><div class="chiprow">${signals || '<span class="xs">clean</span>'}</div></td>
    </tr>`;
  }).join('') || emptyRow(7, RUNS.length
    ? `No ${esc(RUNS_ORIGIN)} runs yet.`
    : 'No runs yet — hit ▶ run pipeline on the Overview tab.');
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

// ── search ──────────────────────────────────────────────────────────────
// Every nugget has been embedded since the first run and the vector store's
// search has existed the whole time, wired only into dedup. The archive was
// searchable and there was no way to search it.

function renderSearch(res) {
  const note = $('#search-note');
  if (note) {
    // Say WHICH search answered. Semantic and substring return very different
    // things, and a user who thinks they got one when they got the other will
    // draw the wrong conclusion from an empty result.
    const bits = [];
    if (res) {
      bits.push(res.mode === 'semantic' ? 'matched on meaning' : 'matched on text');
      bits.push(`${res.returned} result(s)`);
      if (res.detail) bits.push(res.detail);
    }
    note.textContent = bits.join(' · ');
    note.className = res && res.mode === 'text' ? 'xs chip chip--warn' : 'xs';
  }

  const rows = (res && res.results) || [];
  $('#search-body').innerHTML = rows.length ? rows.map(n => `
    <div class="card">
      <div class="row row-between">
        <div class="chiprow">
          <span class="chip">${esc(n.platform || '—')}</span>
          <span class="chip">${esc(n.category || 'uncategorised')}</span>
          ${n.author ? `<span class="chip">${esc(n.author)}</span>` : ''}
          ${n.created_utc ? `<span class="xs">${esc(String(n.created_utc).slice(0, 10))}</span>` : ''}
        </div>
        ${n.score !== undefined && n.score !== null
          ? `<span class="xs mono">${Number(n.score).toFixed(3)}</span>` : ''}
      </div>
      <p><strong>${esc(n.extracted_insight || '')}</strong></p>
      <p class="xs">${esc((n.raw_text || '').slice(0, 400))}</p>
      ${n.source_url ? `<a href="${esc(n.source_url)}" target="_blank" rel="noopener noreferrer" class="xs">source ↗</a>` : ''}
    </div>`).join('')
    : `<div class="empty">${esc(res ? 'Nothing matched that.' : 'Describe a problem to search for.')}</div>`;
}

async function runSearch() {
  const q = ($('#search-q').value || '').trim();
  if (!q) { renderSearch(null); return; }
  const btn = $('#search-go');
  btn.disabled = true;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}&limit=40`);
  btn.disabled = false;
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  renderSearch(res);
}

function loadSearch() { renderSearch(null); }

$('#search-go').addEventListener('click', runSearch);
$('#search-q').addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });

// ── clusters ────────────────────────────────────────────────────────────
// The archive regrouped by meaning instead of by scrape origin. Ideas drafted
// here live in `cluster_ideas` until somebody saves one — drafts are cheap and
// most get discarded, so letting them straight into the Ideas archive would
// turn it into a scratchpad.
let CLUSTERS = [];
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
  const rows = CLUSTERS.filter(c => !q ||
    `${c.label || ''} ${c.problem_statement || ''}`.toLowerCase().includes(q));

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
    return `<div class="card">
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

// ── nuggets ─────────────────────────────────────────────────────────────
let NUGGETS = [];
//: What the archive holds, as opposed to what this page received. They differ
//: only if a caller asked for a limit — but when they do differ, saying so is
//: the difference between a filter and a lie.
let NUGGET_TOTAL = 0;
let NUGGET_TRUNCATED = false;
let NUGGET_FILTER = 'all';
const NUGGET_FILTERS = ['all', 'flagged', 'trivial', 'needs reembed'];
//: Grouping by source trades the archive's global newest-first order for its
//: shape, which is the right default but not always the right view — so it is
//: a toggle, not a decision made once on the operator's behalf.
let NUGGET_GROUPED = true;

function renderNuggetFilter() {
  $('#nuggets-filter').innerHTML = NUGGET_FILTERS.map(f =>
    `<button data-f="${esc(f)}" aria-pressed="${NUGGET_FILTER === f}">${esc(f)}</button>`).join('');
}

//: `community` is the sub-source a nugget was read from — r/datascience,
//: discuss.python.org, unix. It was added after the archive already held
//: thousands of rows, so the older ones carry nothing and get a bucket that
//: says exactly that rather than being lumped in with a real community.
const NO_COMMUNITY = 'source not recorded';
//: The fixture runs' marker. Text under it was written for a test and never
//: scraped from anywhere, so it is named rather than left looking like a
//: community whose name got lost.
const FIXTURE_COMMUNITY = 'test fixture — not scraped';

/** Which bucket a nugget belongs in. Three outcomes, and they are different
 *  facts: a named community, an origin the archive never recorded, and text
 *  that was never scraped at all. */
function communityOf(g) {
  if (g.community) return g.community;
  if (String(g.source_url || '').trim().toLowerCase() === 'mock') return FIXTURE_COMMUNITY;
  return NO_COMMUNITY;
}

function nuggetRow(g) {
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
}

/** Platform, then the community inside it. Groups are ordered by size, so the
 *  platforms actually carrying the archive sit at the top; rows keep the
 *  newest-first order they arrived in. */
function groupedNuggets(rows) {
  const bySize = (a, b) => b[1].length - a[1].length;
  return [...groupBy(rows, g => g.platform || 'unknown')].sort(bySize)
    .map(([platform, inPlatform]) => {
      const pKey = `nug:${platform}`;
      const communities = [...groupBy(inPlatform, communityOf)].sort(bySize);
      const head = groupRow({
        key: pKey, depth: 1, span: 5, count: inPlatform.length,
        label: esc(PLATFORM_LABEL[platform] || platform),
        meta: esc(`${communities.length} source(s)`),
      });
      if (FOLDED.has(pKey)) return head;
      return head + communities.map(([community, items]) => {
        const cKey = `${pKey}:${community}`;
        const real = community !== NO_COMMUNITY && community !== FIXTURE_COMMUNITY;
        const sub = groupRow({
          key: cKey, depth: 2, span: 5, count: items.length,
          label: real ? esc(community) : `<span class="mute">${esc(community)}</span>`,
        });
        return sub + (FOLDED.has(cKey) ? '' : items.map(nuggetRow).join(''));
      }).join('');
    }).join('');
}

function renderNuggets() {
  const q = $('#nuggets-q').value.trim().toLowerCase();
  const rows = NUGGETS.filter(g => {
    if (NUGGET_FILTER === 'trivial' && !g.trivial) return false;
    if (NUGGET_FILTER === 'needs reembed' && !g.needs_reembed) return false;
    if (NUGGET_FILTER === 'flagged' && !g.trivial && !g.needs_reembed) return false;
    if (!q) return true;
    return `${g.unique_key} ${g.category || ''} ${g.community || ''} ${g.extracted_insight || ''}`
      .toLowerCase().includes(q);
  });

  $('#nuggets-body').innerHTML =
    (NUGGET_GROUPED ? groupedNuggets(rows) : rows.map(nuggetRow).join(''))
    || emptyRow(5, NUGGETS.length
      ? 'No nugget matches that filter.'
      : 'No nuggets archived yet.');

  // Always say what is on screen versus what exists. A row count that only
  // ever describes itself cannot tell you something is missing.
  const note = $('#nuggets-count');
  if (note) {
    const shown = rows.length;
    const parts = [`${shown.toLocaleString()} shown`];
    if (NUGGET_GROUPED && shown) {
      const platforms = new Set(rows.map(g => g.platform || 'unknown'));
      const communities = new Set(rows.map(g => `${g.platform}/${communityOf(g)}`));
      parts.push(`${platforms.size} platform(s), ${communities.size} source(s)`);
    }
    if (shown !== NUGGETS.length) parts.push(`${NUGGETS.length.toLocaleString()} loaded`);
    if (NUGGET_TRUNCATED) parts.push(`${NUGGET_TOTAL.toLocaleString()} in archive — response was capped`);
    else if (NUGGET_TOTAL !== NUGGETS.length) parts.push(`${NUGGET_TOTAL.toLocaleString()} in archive`);
    note.textContent = parts.join(' · ');
    note.className = NUGGET_TRUNCATED ? 'xs chip chip--warn' : 'xs';
  }
}

async function loadNuggets() {
  const res = await api('/api/nuggets');
  if (res.ok === false) { toast(res.error, 'bad'); return; }
  NUGGETS = res.nuggets || [];
  // The ARCHIVE's count, not this page's. These were the same line before,
  // so a capped response reported its own length as the total and the nav
  // badge read "500" over an archive of 1,761.
  NUGGET_TOTAL = typeof res.total === 'number' ? res.total : NUGGETS.length;
  NUGGET_TRUNCATED = !!res.truncated;
  COUNTS.nuggets = NUGGET_TOTAL;
  renderNav();
  renderNuggetFilter();
  renderNuggetGrouping();
  renderNuggets();
}

bindFolding('#nuggets-body', renderNuggets);

function renderNuggetGrouping() {
  $('#nuggets-grouping').innerHTML = [['grouped', true], ['flat', false]].map(([label, on]) =>
    `<button data-grouped="${on}" aria-pressed="${NUGGET_GROUPED === on}">${label}</button>`).join('');
}

$('#nuggets-grouping').addEventListener('click', e => {
  const b = e.target.closest('button[data-grouped]');
  if (!b) return;
  NUGGET_GROUPED = b.dataset.grouped === 'true';
  renderNuggetGrouping();
  renderNuggets();
});

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
  llm_provider: 'default backend for the chat roles — fake (deterministic), ollama (local daemon) or groq (hosted). Override per role under Agent models.',
  embedding_provider: 'fake (deterministic) or ollama (local daemon)',
};
// Per-key choices now arrive on each knob as `meta.choices`, because this
// list was a copy of a vocabulary that lives in config.py and it went stale
// the moment a third llm_provider existed: the console kept offering two.
// Kept only as the fallback for a server that predates the field.
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
    const choices = meta.choices;
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

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if ($('#pipeline-modal').open) closeModal();
    else closeDrawer();
  }
  // e.target is `document` when nothing is focused — it has no .matches().
  const t = e.target;
  if ((t instanceof Element && t.matches('input, select, textarea, [contenteditable]'))
      || e.metaKey || e.ctrlKey || e.altKey) return;
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
go(location.hash.slice(1) || 'overview', { push: false });
