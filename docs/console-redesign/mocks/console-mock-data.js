/* Jester console — corporate redesign mockup fixtures (P2, 2026-10-17).
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

  /* ── Overview ─────────────────────────────────────────────────────── */
  overview: {
    banner: {
      tone: 'warn',
      title: '7 nuggets awaiting re-embed',
      body: 'Search is complete for everything else; this clears in about a minute.',
      action: 'Re-embed now',
    },
    kpis: [
      { label: 'Nuggets', value: 31818, delta: '+2,938 today', deltaTone: 'ok', spark: [22110, 23480, 24930, 26300, 27840, 28880, 31818] },
      { label: 'Ideas', value: 1321, delta: 'no new ideas in 14 cycles', deltaTone: 'mute', spark: [1180, 1204, 1241, 1265, 1288, 1302, 1321] },
      { label: 'Pending batches', value: 33614, delta: '25,067 are listings', deltaTone: 'mute', spark: [41200, 39880, 38110, 37240, 35900, 34780, 33614] },
      { label: 'Failed today', value: 0, delta: '7 in re-embed backlog', deltaTone: 'bad', spark: null },
    ],
    activity: [
      { time: '09:12', actor: 'system', action: 'cycle completed', target: 'cycle-20261017T091203Z', status: 'done' },
      { time: '08:00', actor: 'system', action: 'cycle completed', target: 'cycle-20261017T080003Z', status: 'done' },
      { time: '07:45', actor: 'console', action: 'manual run started', target: 'console-20261017T074512Z', status: 'running' },
      { time: '22:00', actor: 'system', action: 'cycle failed', target: 'cycle-20261016T220004Z', status: 'failed' },
      { time: '20:30', actor: 'system', action: 'cycle completed', target: 'cycle-20261016T203012Z', status: 'done' },
    ],
    lastRun: {
      run: 'cycle-20261017T091203Z',
      status: 'done',
      started: '09:12 · 29 min ago',
      phase: 'archive → synthesize',
      kept: 2938,
      ideas: 146,
      duration: '6 m 12 s',
      expected: '6 m 40 s',
      flags: [],
    },
    quota: {
      date: '2026-10-17',
      budget: 6000,
      total: 3049,
      platforms: [
        { platform: 'discourse', n: 1611 },
        { platform: 'hackernews', n: 1204 },
        { platform: 'lemmy', n: 118 },
        { platform: 'stackexchange', n: 86 },
        { platform: 'reddit', n: 30 },
        { platform: 'youtube', n: 0 },
        { platform: 'github', n: 0 },
      ],
    },
    infra: [
      { name: 'Ollama', note: '8 models · native', state: 'ok' },
      { name: 'cloakserve', note: ':9222 · Chrome 146', state: 'ok' },
      { name: 'Qdrant', note: '1.19 · docker', state: 'ok' },
      { name: 'Reddit API', note: 'no credentials — the browser path is used instead', state: 'off' },
      { name: 'Go worker', note: 'go 1.22', state: 'ok' },
    ],
  },

  /* ── Runs ledger ──────────────────────────────────────────────────── */
  runs: {
    shown: 6,
    total: 214,
    /* flat list kept for reference; groups drive the grouped-list view */
    rows: [
      {
        id: 'cycle-20261017T091203Z',
        started: '2026-10-17 09:12',
        status: 'done',
        kept: 2938,
        ideas: 146,
        duration: '6 m 12 s',
        expected: '6 m 40 s',
        origin: 'scheduled',
        mix: [
          { platform: 'discourse', n: 1611 },
          { platform: 'hackernews', n: 1204 },
          { platform: 'lemmy', n: 118 },
          { platform: 'stackexchange', n: 86 },
          { platform: 'reddit', n: 30 },
        ],
        warnings: [],
      },
      {
        id: 'cycle-20261017T080003Z',
        started: '2026-10-17 08:00',
        status: 'done',
        kept: 1864,
        ideas: 92,
        duration: '5 m 02 s',
        expected: '6 m 40 s',
        origin: 'scheduled',
        mix: [
          { platform: 'discourse', n: 1102 },
          { platform: 'hackernews', n: 514 },
          { platform: 'lemmy', n: 248 },
        ],
        warnings: [],
      },
      {
        id: 'console-20261017T074512Z',
        started: '2026-10-17 07:45',
        status: 'running',
        kept: 612,
        ideas: null,
        duration: 'running · 8 m',
        expected: null,
        origin: 'console',
        mix: [{ platform: 'hackernews', n: 612 }],
        warnings: [],
      },
      {
        id: 'cycle-20261016T220004Z',
        started: '2026-10-16 22:00',
        status: 'failed',
        kept: 0,
        ideas: 0,
        duration: '1 m 47 s',
        expected: '6 m 40 s',
        origin: 'scheduled',
        mix: [{ platform: 'reddit', n: 0 }],
        warnings: ['Reddit walk hit a block page after 3 retries; 2 of 5 sources skipped.'],
      },
      {
        id: 'cycle-20261016T203012Z',
        started: '2026-10-16 20:30',
        status: 'done',
        kept: 1204,
        ideas: 4,
        duration: '4 m 36 s',
        expected: '6 m 40 s',
        origin: 'scheduled',
        mix: [
          { platform: 'hackernews', n: 1204 },
          { platform: 'discourse', n: 390 },
          { platform: 'reddit', n: 220 },
          { platform: 'lemmy', n: 88 },
          { platform: 'stackexchange', n: 64 },
          { platform: 'youtube', n: 31 },
          { platform: 'github', n: 17 },
        ],
        warnings: ['LOW_NUGGETS — 4 nuggets from 1,204 comments'],
      },
      {
        id: 'cycle-20261016T181500Z',
        started: '2026-10-16 18:15',
        status: 'done',
        kept: 286,
        ideas: 11,
        duration: '2 m 58 s',
        expected: null,
        origin: 'console',
        mix: [{ platform: 'discourse', n: 286 }],
        warnings: [],
      },
    ],
    groups: {
      queued: [],
      running: [
        { id: 'console-20261017T074512Z', started: '2026-10-17 07:45', status: 'running', kept: 612, ideas: null, duration: 'running · 8 m', origin: 'console', mix: [{ platform: 'hackernews', n: 612 }], warnings: [] },
      ],
      done: [
        { id: 'cycle-20261017T091203Z', started: '2026-10-17 09:12', status: 'done', kept: 2938, ideas: 146, duration: '6 m 12 s', expected: '6 m 40 s', origin: 'scheduled', mix: [
          { platform: 'discourse', n: 1611 },
          { platform: 'hackernews', n: 1204 },
          { platform: 'lemmy', n: 118 },
          { platform: 'stackexchange', n: 86 },
          { platform: 'reddit', n: 30 },
        ], warnings: [] },
        { id: 'cycle-20261017T080003Z', started: '2026-10-17 08:00', status: 'done', kept: 1864, ideas: 92, duration: '5 m 02 s', expected: '6 m 40 s', origin: 'scheduled', mix: [
          { platform: 'discourse', n: 1102 },
          { platform: 'hackernews', n: 514 },
          { platform: 'lemmy', n: 248 },
        ], warnings: [] },
        { id: 'cycle-20261016T203012Z', started: '2026-10-16 20:30', status: 'done', kept: 1204, ideas: 4, duration: '4 m 36 s', expected: '6 m 40 s', origin: 'scheduled', mix: [
          { platform: 'hackernews', n: 1204 },
          { platform: 'discourse', n: 390 },
          { platform: 'reddit', n: 220 },
          { platform: 'lemmy', n: 88 },
          { platform: 'stackexchange', n: 64 },
          { platform: 'youtube', n: 31 },
          { platform: 'github', n: 17 },
        ], warnings: ['LOW_NUGGETS — 4 nuggets from 1,204 comments'] },
        { id: 'cycle-20261016T181500Z', started: '2026-10-16 18:15', status: 'done', kept: 286, ideas: 11, duration: '2 m 58 s', expected: null, origin: 'console', mix: [{ platform: 'discourse', n: 286 }], warnings: [] },
      ],
      failed: [
        { id: 'cycle-20261016T220004Z', started: '2026-10-16 22:00', status: 'failed', kept: 0, ideas: 0, duration: '1 m 47 s', expected: '6 m 40 s', origin: 'scheduled', mix: [{ platform: 'reddit', n: 0 }], warnings: ['Reddit walk hit a block page after 3 retries; 2 of 5 sources skipped.'] },
      ],
      requeued: [],
    },
    detail: {
      id: 'cycle-20261016T220004Z',
      started: '2026-10-16 22:00',
      status: 'failed',
      kept: 0,
      ideas: 0,
      duration: '1 m 47 s',
      origin: 'scheduled',
      phase: 'archive',
      logs: [
        { time: '22:00:01', msg: 'Starting Reddit walk for r/devops' },
        { time: '22:00:12', msg: 'Block page detected, retry 1 of 3' },
        { time: '22:01:04', msg: 'Block page detected, retry 2 of 3' },
        { time: '22:01:56', msg: 'Block page detected, retry 3 of 3 — giving up' },
        { time: '22:02:00', msg: '2 of 5 sources skipped, run marked failed' },
      ],
      errors: ['Reddit walk hit a block page after 3 retries; 2 of 5 sources skipped.'],
    },
  },

  /* ── Ideas ────────────────────────────────────────────────────────── */
  ideas: {
    shown: '1–50 of 1,321',
    rows: [
      {
        id: 'idea-20261016-001',
        title: 'Self-hosted backup runner that verifies restores on a schedule',
        overall: 82,
        scores: { demand: 78, feasibility: 64, competition: 41 },
        tags: ['backend', 'infra'],
        status: 'new',
        evidence: 23,
        created: '2026-10-16',
      },
      {
        id: 'idea-20261012-002',
        title: 'One-command Postgres migration between VPS providers',
        overall: 74,
        scores: { demand: 81, feasibility: 52, competition: 60 },
        tags: ['database', 'migration'],
        status: 'reviewed',
        evidence: 18,
        created: '2026-10-12',
      },
      {
        id: 'idea-20261008-003',
        title: 'A local-first browser extension that flags silently failing cron jobs by diffing exit codes and log tails across machines, with weekly digest emails and per-service muting',
        overall: 61,
        scores: { demand: 70, feasibility: 35, competition: 55 },
        tags: ['frontend', 'extension', 'cron'],
        status: 'reviewed',
        evidence: 14,
        created: '2026-10-08',
      },
      {
        id: 'idea-20261005-004',
        title: 'Invoice generator for one-person consultancies (bank CSV in, PDF out)',
        overall: 58,
        scores: { demand: 62, feasibility: 71, competition: 44 },
        tags: ['billing', 'saas'],
        status: 'new',
        evidence: 12,
        created: '2026-10-05',
      },
      {
        id: 'idea-20260927-005',
        title: 'Utility-meter budget alerts via manufacturer APIs',
        overall: 49,
        scores: { demand: 55, feasibility: 30, competition: 38 },
        tags: ['iot', 'alerts'],
        status: 'new',
        evidence: 5,
        created: '2026-09-27',
      },
      {
        id: 'idea-20260919-006',
        title: 'Repair-parts search index for hardware teardown archives',
        overall: 33,
        scores: { demand: 28, feasibility: 61, competition: 24 },
        tags: ['search', 'archive'],
        status: 'archived',
        evidence: 9,
        created: '2026-09-19',
      },
    ],
    detail: {
      id: 'idea-20261016-001',
      title: 'Self-hosted backup runner that verifies restores on a schedule',
      overall: 82,
      scores: { demand: 78, feasibility: 64, competition: 41 },
      tags: ['backend', 'infra'],
      status: 'new',
      evidence: 23,
      created: '2026-10-16',
      citations: [
        'r/selfhosted — "How do you verify backups automatically?" (1,204 upvotes)',
        'discourse.home-assistant.io — backup verification feature request',
      ],
      notes: [
        { time: '2026-10-16 14:00', actor: 'pm', text: 'High demand signal — worth a spike.' },
        { time: '2026-10-15 11:30', actor: 'eng', text: 'Feasibility looks good with restic backend.' },
      ],
    },
  },

  /* ── Sources ──────────────────────────────────────────────────────── */
  sources: {
    shown: '6 of 115',
    path: 'config/sources.yaml',
    rows: [
      { id: 'src-1', on: true, name: 'ExperiencedDevs', kind: 'subreddit', target: 'reddit.com/r/ExperiencedDevs', fetch: 'browser', last: '11 min ago', status: 'ok' },
      { id: 'src-2', on: true, name: 'selfhosted', kind: 'subreddit', target: 'reddit.com/r/selfhosted', fetch: 'browser', last: '31 min ago', status: 'ok' },
      { id: 'src-3', on: true, name: 'devops', kind: 'subreddit', target: 'reddit.com/r/devops', fetch: 'browser', last: '3 days ago', status: 'blocked' },
      { id: 'src-4', on: true, name: 'Hacker News front page', kind: 'site', target: 'news.ycombinator.com', fetch: 'auto', last: '2 h ago', status: 'ok' },
      { id: 'src-5', on: false, name: 'community.home-assistant.io — feature requests (2026 watch, energy dashboards)', kind: 'discourse', target: 'https://community.home-assistant.io/c/feature-requests/22?period=weekly&tags=integration%2Cenergy-dashboard', fetch: 'auto', last: '6 days ago', status: 'paused' },
      { id: 'src-6', on: true, name: 'fosdem talks', kind: 'channel', target: 'youtube.com/@fosdemtalks', fetch: 'auto', last: '4 h ago', status: 'ok' },
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
      { label: 'New since last review', value: 0, delta: 'nothing new since 2026-10-12', deltaTone: 'mute' },
      { label: 'Failed this week', value: 0, delta: 'last failure 2026-10-16', deltaTone: 'mute' },
    ],
    loading: 'Loading blocks are flat now — the shimmer gradient is gone.',
    longRow: {
      title:
        'A local-first browser extension that flags silently failing cron jobs by diffing exit codes and log tails across machines, with weekly digest emails and per-service muting',
      source: 'community.home-assistant.io — feature requests (2026 watch, energy dashboards)',
    },
  },
};
