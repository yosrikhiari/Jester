/* Jester console — corporate redesign mockup fixtures (flat/dense direction, 2026-10-17).
 *
 * EVERY VALUE IN THIS FILE IS FABRICATED FOR DESIGN REVIEW. There is no
 * archive content, no credential, no real user data and no network access
 * anywhere in it. The mockup page (../2026-10-17-corporate-redesign-mockup.html)
 * renders these objects and nothing else.
 *
 * Shapes mirror what the console's /api/* endpoints return closely enough
 * that the P2–P5 repaint needs no data-flow change — but this file is never
 * loaded by the console. It exists beside the mockup, nothing else imports it.
 */
window.JESTER_MOCK = {
  meta: {
    generated: '2026-10-17T09:41:00Z',
    note: 'Fabricated fixture data for the corporate redesign mockup — no API calls, no real archive content.',
  },

  /* ── Runs ledger ────────────────────────────────────────────────────
   * st: running | queued | done | failed | requeued
   * ideas may be null (phase not reached). dur '—' until finished.
   * err: null or a one-line failure reason. log: [time, message] pairs.
   */
  runs: {
    total: 214,
    pages: 31,
    rows: [
      { id: 'cycle-20261017T040002Z', at: '17 Oct, 04:00', st: 'running', kept: 41, ideas: null, dur: '—', org: 'scheduled', err: null,
        log: [['04:00:02', 'archive started'], ['04:03:40', '41 documents kept']] },
      { id: 'cycle-20261017T100000Z', at: '17 Oct, 10:00', st: 'queued', kept: 0, ideas: null, dur: '—', org: 'scheduled', err: null,
        log: [['—', 'waiting for the 04:00 cycle to finish']] },
      { id: 'cycle-20261016T220004Z', at: '16 Oct, 22:00', st: 'done', kept: 212, ideas: 58, dur: '11m 02s', org: 'scheduled', err: null,
        log: [['22:00:04', 'archive started'], ['22:06:30', '212 documents kept'], ['22:11:06', '58 ideas synthesized']] },
      { id: 'cycle-20261016T160001Z', at: '16 Oct, 16:00', st: 'done', kept: 187, ideas: 44, dur: '9m 48s', org: 'manual', err: null,
        log: [['16:00:01', 'archive started'], ['16:09:49', '44 ideas synthesized']] },
      { id: 'cycle-20261016T100003Z', at: '16 Oct, 10:00', st: 'done', kept: 203, ideas: 51, dur: '10m 15s', org: 'scheduled', err: null,
        log: [['10:00:03', 'archive started'], ['10:10:18', '51 ideas synthesized']] },
      { id: 'cycle-20261016T040004Z', at: '16 Oct, 04:00', st: 'failed', kept: 9, ideas: null, dur: '4m 12s', org: 'retry',
        err: 'Synthesis stopped: the model endpoint timed out after 3 attempts.',
        log: [['04:00:04', 'archive started'], ['04:02:51', '9 documents kept'], ['04:04:16', 'synthesize failed (timeout)']] },
      { id: 'cycle-20261015T220002Z', at: '15 Oct, 22:00', st: 'failed', kept: 0, ideas: null, dur: '0m 31s', org: 'scheduled',
        err: 'Archive stopped: 3 sources returned HTTP 429.',
        log: [['22:00:02', 'archive started'], ['22:00:33', 'rate limited by 3 sources']] },
      { id: 'cycle-20261015T100005Z', at: '15 Oct, 10:00', st: 'requeued', kept: 0, ideas: null, dur: '—', org: 'manual', err: null,
        log: [['—', 'requeued by operator after a partial archive walk']] },
    ],
    /* stat strip shown above the runs table */
    stats: [
      { label: 'Runs this week', value: '38', note: '+12% vs last week', tone: 'ok' },
      { label: 'Ideas kept', value: '1,321', note: '+86 today', tone: 'ok' },
      { label: 'Sources healthy', value: '112 / 115', note: '3 stale', tone: '' },
      { label: 'Failed', value: '2', note: '1 needs a requeue', tone: 'bd' },
    ],
  },

  /* ── Overview ─────────────────────────────────────────────────────── */
  overview: {
    banner: {
      tone: 'wn',
      title: '7 nuggets awaiting re-embed',
      body: 'Search is complete for everything else; this clears in about a minute.',
      action: 'Re-embed now',
    },
    stats: [
      { label: 'Nuggets', value: '31,818', note: '+2,938 today', tone: 'ok' },
      { label: 'Ideas', value: '1,321', note: 'no new in 14 cycles', tone: '' },
      { label: 'Pending batches', value: '33,614', note: '25,067 are listings', tone: '' },
      { label: 'Failed today', value: '0', note: '7 in re-embed backlog', tone: 'bd' },
    ],
    /* ids into runs.rows — newest five */
    recentRunIds: [
      'cycle-20261017T100000Z',
      'cycle-20261017T040002Z',
      'cycle-20261016T220004Z',
      'cycle-20261016T160001Z',
      'cycle-20261016T100003Z',
    ],
    activity: [
      { t: '10:00', text: 'run queued for the next slot', run: 'cycle-20261017T100000Z', st: 'queued' },
      { t: '04:03', text: 'archive phase finished — 41 documents kept', run: 'cycle-20261017T040002Z', st: 'running' },
      { t: '22:11', text: 'cycle completed — 58 ideas synthesized', run: 'cycle-20261016T220004Z', st: 'done' },
      { t: '04:04', text: 'synthesize failed (endpoint timeout)', run: 'cycle-20261016T040004Z', st: 'failed' },
      { t: '22:00', text: 'rate limited by 3 sources', run: 'cycle-20261015T220002Z', st: 'failed' },
    ],
  },

  /* ── Ideas ──────────────────────────────────────────────────────────
   * st: new | reviewed | archived. Each row carries its own citations
   * and notes so the detail panel renders from the row alone.
   */
  ideas: {
    total: 1321,
    pages: 27,
    rows: [
      { id: 'idea-20261016-001', title: 'Self-hosted backup runner that verifies restores on a schedule',
        overall: 82, demand: 78, feas: 64, comp: 41, st: 'new', tags: ['backend', 'infra'], ev: 23, created: '16 Oct',
        citations: [
          'r/selfhosted — “How do you verify backups automatically?” (1,204 upvotes)',
          'discourse.home-assistant.io — backup verification feature request',
        ],
        notes: [
          { t: '16 Oct 14:00', text: 'High demand signal — worth a spike.' },
          { t: '15 Oct 11:30', text: 'Feasibility looks good with restic backend.' },
        ] },
      { id: 'idea-20261012-002', title: 'One-command Postgres migration between VPS providers',
        overall: 74, demand: 81, feas: 52, comp: 60, st: 'reviewed', tags: ['database', 'migration'], ev: 18, created: '12 Oct',
        citations: ['discourse.pgadmins.org — “migrate across VPS providers” thread'],
        notes: [{ t: '13 Oct 09:10', text: 'Scope is big; parked until the backup runner lands.' }] },
      { id: 'idea-20261008-003', title: 'A local-first browser extension that flags silently failing cron jobs by diffing exit codes and log tails across machines, with weekly digest emails and per-service muting',
        overall: 61, demand: 70, feas: 35, comp: 55, st: 'reviewed', tags: ['frontend', 'extension'], ev: 14, created: '08 Oct',
        citations: ['community.home-assistant.io — feature request thread'],
        notes: [] },
      { id: 'idea-20261005-004', title: 'Invoice generator for one-person consultancies (bank CSV in, PDF out)',
        overall: 58, demand: 62, feas: 71, comp: 44, st: 'new', tags: ['billing', 'saas'], ev: 12, created: '05 Oct',
        citations: ['r/consulting — invoice tooling complaints (412 upvotes)'],
        notes: [] },
      { id: 'idea-20260927-005', title: 'Utility-meter budget alerts via manufacturer APIs',
        overall: 49, demand: 55, feas: 30, comp: 38, st: 'new', tags: ['iot'], ev: 5, created: '27 Sep',
        citations: [],
        notes: [{ t: '28 Sep 10:00', text: 'Manufacturer API access is the open risk.' }] },
      { id: 'idea-20260919-006', title: 'Repair-parts search index for hardware teardown archives',
        overall: 33, demand: 28, feas: 61, comp: 24, st: 'archived', tags: ['search'], ev: 9, created: '19 Sep',
        citations: [],
        notes: [] },
    ],
  },

  /* ── Sources ────────────────────────────────────────────────────────
   * st: ok | blocked | paused (health dot in the table).
   */
  sources: {
    total: 115,
    path: 'config/sources.yaml',
    rows: [
      { name: 'ExperiencedDevs', kind: 'subreddit', target: 'reddit.com/r/ExperiencedDevs', fetch: 'browser', last: '11 min ago', st: 'ok' },
      { name: 'selfhosted', kind: 'subreddit', target: 'reddit.com/r/selfhosted', fetch: 'browser', last: '31 min ago', st: 'ok' },
      { name: 'devops', kind: 'subreddit', target: 'reddit.com/r/devops', fetch: 'browser', last: '3 days ago', st: 'blocked' },
      { name: 'Hacker News front page', kind: 'site', target: 'news.ycombinator.com', fetch: 'auto', last: '2 h ago', st: 'ok' },
      { name: 'home-assistant feature requests', kind: 'discourse', target: 'community.home-assistant.io/c/feature-requests', fetch: 'auto', last: '6 days ago', st: 'paused' },
      { name: 'fosdem talks', kind: 'channel', target: 'youtube.com/@fosdemtalks', fetch: 'auto', last: '4 h ago', st: 'ok' },
    ],
  },

  /* ── Edge-case states (mockup-only page) ──────────────────────────── */
  states: {
    empty: {
      message: 'No runs match this filter.',
      action: 'Clear filters',
    },
    error: {
      title: 'Ingest failed',
      body: 'Reddit walk hit a block page after 3 retries; 2 of 5 sources were skipped. Nothing was queued from the failed walk.',
      action: 'Retry ingest',
    },
    longText: {
      evidence:
        'The cron job dies silently at 03:00 every night and nobody notices until the reports are stale. We only found out when the finance team asked why Monday numbers were still Friday numbers — the exit code was 0 the whole time because the wrapper swallowed the error.',
      url: 'https://community.home-assistant.io/t/bug-energy-dashboard-statistics-not-clearing-after-integration-reload-when-using-utility-meter-helpers-with-fixed-start-dates/1183429/47',
      token: 'cycle-20261016T220004Z-ingest-reddit-r-devops-blockpage-retry3',
    },
    missingEvidence: {
      title: 'Citation missing',
      body: 'The source comment was deleted before archiving, so this idea cannot be verified against the archive.',
    },
    zero: [
      { label: 'New since last review', value: '0', note: 'nothing new since 12 Oct', tone: '' },
      { label: 'Failed this week', value: '0', note: 'last failure 16 Oct', tone: '' },
    ],
    loading: 'Loading is instant here — there is no shimmer, pulse or skeleton sweep in this direction.',
  },
};
