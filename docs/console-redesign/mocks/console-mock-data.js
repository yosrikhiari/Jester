/* Jester console — corporate redesign mockup fixtures (flat/dense direction, 2026-10-17).
 *
 * EVERY VALUE IN THIS FILE IS FABRICATED FOR DESIGN REVIEW. There is no
 * archive content, no credential, no real user data and no network access
 * anywhere in it. The mockup page (../2026-10-17-corporate-redesign-mockup.html)
 * renders these objects and nothing else.
 *
 * Field names mirror what the console's /api/* endpoints return closely enough
 * that the P2–P5 repaint needs no data-flow change — but this file is never
 * loaded by the console. It exists beside the mockup, nothing else imports it.
 */
window.JESTER_MOCK = {
  meta: {
    generated: '2026-10-17T09:41:00Z',
    now: '2026-10-17T10:12:00Z',
    note: 'Fabricated fixture data for the corporate redesign mockup — no API calls, no real archive content.',
  },

  /* Sidebar badge counts — the numbers renderNav() reads from COUNTS. */
  counts: {
    runs: 214, pending: 33, ideas: 1321, posts: 402, nuggets: 31818,
    clusters: 12, signals: 96, sources: 7, findings: 3,
  },

  /* /api/trends?days=7 — one value per day, oldest first, last = today. */
  trends: {
    nuggets: [4120, 3890, 4410, 4210, 4560, 4320, 2938],
    ideas: [2, 0, 2, 1, 0, 0, 0],
    queued: [812, 940, 776, 1010, 884, 918, 33],
    bad_runs: [0, 1, 0, 0, 2, 0, 0],
  },

  /* ── Overview: /api/overview + /api/infra ─────────────────────────── */
  overview: {
    banners: [
      { tone: 'wn', head: '7 nugget(s) not yet searchable',
        body: 'They are archived but skipped by dedup and search until re-embedded.',
        next: { label: 're-embed now' } },
      { tone: 'bad', head: 'cloakserve is down',
        body: 'Every Reddit, YouTube and real-estate source is skipped until it is back (docker compose --profile live up -d cloakbrowser).',
        next: { label: 'see affected sources', go: 'sources' } },
      { tone: '', head: '1 enabled source(s) have no adapter yet',
        body: 'The worker reports them as skipped rather than ingesting them.',
        next: { label: 'open Sources', go: 'sources' } },
    ],
    last_run: {
      status: 'completed', run_id: 'cycle-20261016T220004Z',
      started_at: '2026-10-16T22:00:04Z',
      n_posts: 212, n_comments: 4011,
      n_nuggets_kept: 212, n_ideas: 58,
      duration_actual_s: 662.0, duration_expected_s: 700.0,
      floor_flags_list: [],
    },
    quota: {
      day_key: '2026-10-17',
      used_total: 1842,
      used_by_platform: { reddit: 1240, hackernews: 380, discourse: 180, youtube: 42 },
    },
    infra: {
      cached: false,
      ollama: { up: true, models: ['qwen2.5:7b', 'llama3.1:8b', 'phi3:mini', 'nomic-embed-text'] },
      cloakserve_9222: false,
      vectors: { usable: true, store: 'sqlite-vec', dim: 384 },
      worker_go_available: true, go_bin: 'bin/jester-worker.exe',
      can_ingest_live: false,
    },
  },

  /* ── Runs: /api/runs (flat ledger, origin filter) ───────────────────
   * status: running | queued | completed | failed
   * origin: scheduled | manual | console
   */
  runs: {
    rows: [
      { run_id: 'cycle-20261017T040002Z', status: 'running', started_at: '2026-10-17T04:00:02Z',
        n_posts: 118, n_comments: 0, n_nuggets_kept: 41, n_ideas: 0,
        duration_actual_s: null, duration_expected_s: 700.0, origin: 'scheduled',
        platform_counts_obj: { reddit: 28, hackernews: 13 }, floor_flags_list: [],
        phases: [['fetch', 'ok', '2.1s'], ['prefilter', 'ok', '0.4s'], ['extract', 'running', '—'],
                 ['dedup', 'waiting', '—'], ['synthesize', 'waiting', '—'], ['export', 'waiting', '—']],
        prefilter_funnel_obj: [['seen', 1540], ['dropped: short', 214], ['dropped: low-signal', 96], ['kept', 1230]],
        models: { extractor: 'qwen2.5:7b', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v7' } },
      { run_id: 'cycle-20261017T100000Z', status: 'queued', started_at: '2026-10-17T10:00:00Z',
        n_posts: 0, n_comments: 0, n_nuggets_kept: 0, n_ideas: 0,
        duration_actual_s: null, duration_expected_s: 700.0, origin: 'scheduled',
        platform_counts_obj: {}, floor_flags_list: [], phases: [], prefilter_funnel_obj: [],
        models: {}, prompt_versions: {} },
      { run_id: 'cycle-20261016T220004Z', status: 'completed', started_at: '2026-10-16T22:00:04Z',
        n_posts: 212, n_comments: 4011, n_nuggets_kept: 212, n_ideas: 58,
        duration_actual_s: 662.0, duration_expected_s: 700.0, origin: 'scheduled',
        platform_counts_obj: { reddit: 141, hackernews: 48, discourse: 23 },
        floor_flags_list: [],
        phases: [['fetch', 'ok', '2.3s'], ['prefilter', 'ok', '0.4s'], ['extract', 'ok', '489.2s'],
                 ['dedup', 'ok', '1.2s'], ['synthesize', 'ok', '158.0s'], ['export', 'ok', '0.3s']],
        prefilter_funnel_obj: [['seen', 4011], ['dropped: short', 611], ['dropped: emoji', 214],
                               ['dropped: mentions', 96], ['dropped: below upvotes', 877], ['kept', 2202]],
        models: { extractor: 'qwen2.5:7b', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v7' } },
      { run_id: 'cycle-20261016T160001Z', status: 'completed', started_at: '2026-10-16T16:00:01Z',
        n_posts: 187, n_comments: 3480, n_nuggets_kept: 187, n_ideas: 44,
        duration_actual_s: 588.0, duration_expected_s: 700.0, origin: 'manual',
        platform_counts_obj: { reddit: 122, hackernews: 51, discourse: 14 },
        floor_flags_list: [],
        phases: [['fetch', 'ok', '2.0s'], ['prefilter', 'ok', '0.4s'], ['extract', 'ok', '431.8s'],
                 ['dedup', 'ok', '1.1s'], ['synthesize', 'ok', '144.2s'], ['export', 'ok', '0.3s']],
        prefilter_funnel_obj: [['seen', 3480], ['dropped: short', 502], ['dropped: below upvotes', 704], ['kept', 2274]],
        models: { extractor: 'qwen2.5:7b', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v7' } },
      { run_id: 'cycle-20261016T100003Z', status: 'completed', started_at: '2026-10-16T10:00:03Z',
        n_posts: 203, n_comments: 3745, n_nuggets_kept: 203, n_ideas: 51,
        duration_actual_s: 615.0, duration_expected_s: 700.0, origin: 'scheduled',
        platform_counts_obj: { reddit: 130, hackernews: 55, discourse: 18 },
        floor_flags_list: [],
        phases: [['fetch', 'ok', '2.2s'], ['prefilter', 'ok', '0.4s'], ['extract', 'ok', '452.0s'],
                 ['dedup', 'ok', '1.2s'], ['synthesize', 'ok', '150.1s'], ['export', 'ok', '0.3s']],
        prefilter_funnel_obj: [['seen', 3745], ['dropped: short', 545], ['dropped: emoji', 190], ['kept', 3010]],
        models: { extractor: 'qwen2.5:7b', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v7' } },
      { run_id: 'cycle-20261016T040004Z', status: 'failed', started_at: '2026-10-16T04:00:04Z',
        n_posts: 9, n_comments: 214, n_nuggets_kept: 9, n_ideas: 0,
        duration_actual_s: 252.0, duration_expected_s: 700.0, origin: 'manual',
        platform_counts_obj: { reddit: 9 },
        floor_flags_list: [],
        error: 'Synthesis stopped: the model endpoint timed out after 3 attempts.',
        phases: [['fetch', 'ok', '1.8s'], ['prefilter', 'ok', '0.3s'], ['extract', 'ok', '238.4s'],
                 ['dedup', 'ok', '0.9s'], ['synthesize', 'failed', '11.6s'], ['export', 'skipped', '—']],
        prefilter_funnel_obj: [['seen', 214], ['dropped: short', 41], ['kept', 173]],
        models: { extractor: 'qwen2.5:7b', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v7' },
        top_near_misses_list: [0.48, 0.46, 0.41] },
      { run_id: 'cycle-20261015T220002Z', status: 'failed', started_at: '2026-10-15T22:00:02Z',
        n_posts: 0, n_comments: 0, n_nuggets_kept: 0, n_ideas: 0,
        duration_actual_s: 31.0, duration_expected_s: 700.0, origin: 'scheduled',
        platform_counts_obj: {}, floor_flags_list: [],
        error: 'Archive stopped: 3 sources returned HTTP 429.',
        phases: [['fetch', 'failed', '30.2s'], ['prefilter', 'skipped', '—'], ['extract', 'skipped', '—'],
                 ['dedup', 'skipped', '—'], ['synthesize', 'skipped', '—'], ['export', 'skipped', '—']],
        prefilter_funnel_obj: [], models: {}, prompt_versions: {} },
      { run_id: 'cycle-20261015T100005Z', status: 'completed', started_at: '2026-10-15T10:00:05Z',
        n_posts: 61, n_comments: 902, n_nuggets_kept: 0, n_ideas: 0,
        duration_actual_s: 402.0, duration_expected_s: 700.0, origin: 'console',
        platform_counts_obj: { hackernews: 61 }, floor_flags_list: ['LOW_NUGGETS'],
        phases: [['fetch', 'ok', '1.9s'], ['prefilter', 'ok', '0.3s'], ['extract', 'ok', '301.0s'],
                 ['dedup', 'ok', '1.0s'], ['synthesize', 'ok', '96.4s'], ['export', 'ok', '0.2s']],
        prefilter_funnel_obj: [['seen', 902], ['dropped: short', 118], ['kept', 784]],
        models: { extractor: 'fake-llm', critic: 'fake-llm', embedding: 'nomic-embed-text' },
        prompt_versions: { extractor: 'v6' } },
    ],
  },

  /* ── Ideas: /api/ideas ──────────────────────────────────────────────
   * status: new | reviewed | building | archived. Rows carry their own
   * citations (ok true/false) and notes so the detail renders standalone.
   */
  ideas: {
    total: 1321, page: 50, offset: 0, sort: 'overall', dir: 'desc',
    status_counts: { new: 412, reviewed: 588, building: 77, archived: 244 },
    note: {
      head: 'No new ideas in the last 3 completed runs',
      body: 'The 15 Oct 10:00 run kept 0 nuggets (floor flag: nothing new); dedup is folding the rest into existing clusters. Not a bug — a quiet archive week.',
    },
    rows: [
      { id: 'idea-20261016-001', title: 'Self-hosted backup runner that verifies restores on a schedule',
        overall: 82, demand: 78, feas: 64, comp: 41, status: 'new',
        ev: 23, source_threads: 9, created_at: '2026-10-16T22:11:06Z',
        tags: ['backup', 'infra'],
        citations: [
          { ok: true, text: 'r/selfhosted — “How do you verify backups automatically?” (1,204 upvotes)' },
          { ok: true, text: 'discourse.home-assistant.io — backup verification feature request' },
        ],
        notes: [
          { t: '16 Oct 14:00', text: 'High demand signal — worth a spike.' },
          { t: '15 Oct 11:30', text: 'Feasibility looks good with restic backend.' },
        ] },
      { id: 'idea-20261012-002', title: 'One-command Postgres migration between VPS providers',
        overall: 74, demand: 81, feas: 52, comp: 60, status: 'building',
        ev: 18, source_threads: 6, created_at: '2026-10-12T09:02:00Z',
        tags: ['database', 'migration'],
        citations: [{ ok: true, text: 'discourse.pgadmins.org — “migrate across VPS providers” thread' }],
        notes: [{ t: '13 Oct 09:10', text: 'Scope is big; parked until the backup runner lands.' }] },
      { id: 'idea-20261008-003', title: 'A local-first browser extension that flags silently failing cron jobs by diffing exit codes and log tails across machines, with weekly digest emails and per-service muting',
        overall: 61, demand: 70, feas: 35, comp: 55, status: 'reviewed',
        ev: 14, source_threads: 4, created_at: '2026-10-08T18:41:12Z',
        tags: ['frontend', 'extension'],
        citations: [
          { ok: true, text: 'community.home-assistant.io — feature request thread' },
          { ok: false, text: 'comment deleted before archiving — cannot be verified' },
        ],
        notes: [] },
      { id: 'idea-20261005-004', title: 'Invoice generator for one-person consultancies (bank CSV in, PDF out)',
        overall: 58, demand: 62, feas: 71, comp: 44, status: 'new',
        ev: 12, source_threads: 3, created_at: '2026-10-05T20:15:44Z',
        tags: ['billing', 'saas'],
        citations: [{ ok: true, text: 'r/consulting — invoice tooling complaints (412 upvotes)' }],
        notes: [] },
      { id: 'idea-20260927-005', title: 'Utility-meter budget alerts via manufacturer APIs',
        overall: 49, demand: 55, feas: 30, comp: 38, status: 'new',
        ev: 5, source_threads: 2, created_at: '2026-09-27T13:06:30Z',
        tags: ['iot'],
        citations: [],
        notes: [{ t: '28 Sep 10:00', text: 'Manufacturer API access is the open risk.' }] },
      { id: 'idea-20260919-006', title: 'Repair-parts search index for hardware teardown archives',
        overall: 33, demand: 28, feas: 61, comp: 24, status: 'archived',
        ev: 9, source_threads: 3, created_at: '2026-09-19T08:22:51Z',
        tags: ['search'],
        citations: [], notes: [] },
    ],
  },

  /* ── Queue: /api/queue?status&kind ──────────────────────────────────
   * kind: comment | listing · status: pending | failed | done
   * n_comments === null means the batch JSON would not parse — shown as
   * "unreadable", never as 0.
   */
  queue: {
    kind: 'comment', status: 'pending',
    by_kind: { comment: { batches: 31 }, listing: { batches: 2 } },
    totals: { pending: 31, failed: 4, done: 812 },
    total_batches: 33, total_comments: 33614, truncated: true, shown: 60,
    treatment: {
      blocked: true,
      blocks: [{ provider: 'groq/llama-3.3-70b', human: '2 h 14 m',
                 blocked_until: '2026-10-17T12:26:00Z',
                 reason: '429 rate limit — local Ollama jobs are unaffected' }],
      note: 'Only the jobs using a blocked model wait; jobs on other models, and on local Ollama, keep running. Nothing is lost while they wait — the queue is the buffer.',
    },
    mix: [
      { platform: 'reddit', comments: 22140, batches: 22 },
      { platform: 'hackernews', comments: 8086, batches: 8 },
      { platform: 'discourse', comments: 3388, batches: 3 },
    ],
    batches: [
      { id: 'batch-8841', kind: 'comment', platform: 'reddit', source: 'r/selfhosted', thread_id: 't3_1o9xk2',
        n_comments: 214, created_at: '2026-10-17T04:03:00Z', run_id: 'cycle-20261017T040002Z',
        status: 'pending', attempts: 0, error: null },
      { id: 'batch-8840', kind: 'comment', platform: 'hackernews', source: 'Hacker News front page', thread_id: 'item-42118977',
        n_comments: 512, created_at: '2026-10-17T04:02:10Z', run_id: 'cycle-20261017T040002Z',
        status: 'pending', attempts: 0, error: null },
      { id: 'batch-8839', kind: 'comment', platform: 'reddit', source: 'r/ExperiencedDevs', thread_id: 't3_1o98hc',
        n_comments: 187, created_at: '2026-10-17T04:01:44Z', run_id: 'cycle-20261017T040002Z',
        status: 'pending', attempts: 0, error: null },
      { id: 'batch-8838', kind: 'comment', platform: 'discourse', source: 'home-assistant feature requests',
        thread_id: 'topic-1183429', n_comments: null, created_at: '2026-10-17T04:01:02Z',
        run_id: 'cycle-20261017T040002Z', status: 'failed', attempts: 2,
        error: 'batch JSON did not parse (truncated response)' },
      { id: 'batch-8837', kind: 'comment', platform: 'hackernews', source: 'whoishiring', thread_id: 'item-42099112',
        n_comments: 96, created_at: '2026-10-16T22:06:30Z', run_id: 'cycle-20261016T220004Z',
        status: 'done', attempts: 1, error: null },
    ],
    comments: {
      'batch-8841': [
        { author: 'k8sGPT-fan', created_at: '2026-10-16T23:41:00Z', category: 'pain_point', depth: 0, trivial: false,
          text: 'Every backup tool I tried has a restore path nobody has tested. I want a cron that does a real restore into a scratch VM and diffs the result.' },
        { author: 'zelkova_rs', created_at: '2026-10-16T23:58:00Z', category: 'workaround', depth: 1, trivial: false,
          text: 'We do this with restic + a shell script and it works, but the diff step is hand-rolled and breaks monthly.' },
        { author: 'throwaway_ops', created_at: '2026-10-17T00:12:00Z', category: 'noise', depth: 0, trivial: true,
          text: 'lol same' },
      ],
      'batch-8838': [],
    },
  },

  /* ── Posts: /api/posts ──────────────────────────────────────────────
   * views: signal | trivial | all · sorts: top | oldest | newest
   */
  posts: {
    total: 402, shown: 60,
    rows: [
      { id: 't3_1o9xk2', title: 'How do you actually verify that your backups restore?', community: 'r/selfhosted',
        platform: 'reddit', author: 'u/nervous-sysadmin', created_at: '2026-10-16T21:04:00Z',
        score: 1204, upvote_ratio: 0.97, views: 41200, comments_advertised: 186, held: 173, signal: 122, trivial: 51, coverage: 0.93,
        url: 'https://reddit.com/r/selfhosted/comments/1o9xk2/' },
      { id: 'item-42118977', title: 'Ask HN: What broke silently for you this quarter?', community: 'Hacker News',
        platform: 'hackernews', author: '', created_at: '2026-10-15T14:00:00Z',
        score: 341, upvote_ratio: null, views: null, comments_advertised: 512, held: 512, signal: 288, trivial: 224, coverage: 1,
        url: 'https://news.ycombinator.com/item?id=42118977' },
      { id: 'topic-1183429', title: 'Energy dashboard statistics not clearing after integration reload', community: 'home-assistant',
        platform: 'discourse', author: 'david.t', created_at: '2026-10-12T09:22:00Z',
        score: null, upvote_ratio: null, views: 880, comments_advertised: null, held: 14, signal: 9, trivial: 5, coverage: null,
        url: 'https://community.home-assistant.io/t/1183429' },
      { id: 't3_1o8hfe', title: 'Migrated to a new VPS in one command — writeup', community: 'r/devops',
        platform: 'reddit', author: 'u/pg_hacker', created_at: '2026-10-11T11:30:00Z',
        score: 412, upvote_ratio: 0.94, views: null, comments_advertised: 96, held: 96, signal: 71, trivial: 25, coverage: 1,
        url: 'https://reddit.com/r/devops/comments/1o8hfe/' },
      { id: 'item-42099112', title: 'Who is hiring? (October 2026)', community: 'Hacker News',
        platform: 'hackernews', author: '', created_at: '2026-10-01T14:00:00Z',
        score: 88, upvote_ratio: null, views: null, comments_advertised: 96, held: 96, signal: 96, trivial: 0, coverage: 1,
        url: 'https://news.ycombinator.com/item?id=42099112' },
      { id: 'topic-1178821', title: 'Feature request: per-helper mute window', community: 'home-assistant',
        platform: 'discourse', author: 'maya.k', created_at: '2026-10-03T16:45:00Z',
        score: null, upvote_ratio: null, views: 402, comments_advertised: null, held: 0, signal: 0, trivial: 0, coverage: null,
        url: 'https://community.home-assistant.io/t/1178821' },
    ],
    comments: {
      't3_1o9xk2': [
        { author: 'k8sGPT-fan', created_at: '2026-10-16T21:20:00Z', category: 'pain_point', depth: 0, trivial: false,
          text: 'The backup is the easy half. Nobody tests the restore until the day they need it, and by then the diff step they improvised in a panic is the only evidence anything worked.' },
        { author: 'zelkova_rs', created_at: '2026-10-16T21:41:00Z', category: 'workaround', depth: 1, trivial: false,
          text: 'We restore into a scratch VM weekly and diff the file trees. It caught a silent rsync exclusion last month that would have nuked the postgres volume.' },
        { author: 'vex', created_at: '2026-10-16T22:02:00Z', category: 'noise', depth: 0, trivial: true,
          text: '3-2-1 and pray, that’s the whole industry' },
        { author: 'amara_sys', created_at: '2026-10-16T22:30:00Z', category: 'feature_request', depth: 0, trivial: false,
          text: 'I would pay for a tool that schedules the restore test and emails me a diff instead of a green checkmark.' },
      ],
      'item-42118977': [
        { author: 'throwaway1337', created_at: '2026-10-15T14:22:00Z', category: 'pain_point', depth: 0, trivial: false,
          text: 'Our nightly cron had exit code 0 for six weeks while the actual job failed inside a wrapper that swallowed stderr. Only found out when finance asked why Monday numbers were Friday numbers.' },
        { author: 'pg_hacker', created_at: '2026-10-15T15:01:00Z', category: 'workaround', depth: 0, trivial: false,
          text: 'Delta between the two machines’ log tails is the poor man’s watchdog. Works until the clock drifts.' },
      ],
    },
  },

  /* ── Nuggets: /api/nuggets/page ───────────────────────────────────── */
  nuggets: {
    total: 31818, shown: 60,
    facets: {
      platform: { Reddit: 18420, 'Hacker News': 9201, Discourse: 3116, YouTube: 1081 },
      community: { 'r/selfhosted': 6204, 'r/devops': 4118, 'Ask HN': 3902,
                   'home-assistant': 3116, 'r/ExperiencedDevs': 2874 },
      category: { backup: 5022, onboarding: 3417, billing: 2881, monitoring: 2410, export: 1908 },
    },
    active: [],
    rows: [
      { id: 'nug-918241', insight: 'Restore verification is the missing half of every backup tool — people improvise a diff step in a panic.',
        platform: 'reddit', community: 'r/selfhosted', author: 'u/k8sGPT-fan',
        category: 'backup', flags: [], created_at: '2026-10-16T22:06:30Z' },
      { id: 'nug-918240', insight: 'Nightly cron reported exit 0 for six weeks while the job failed inside a wrapper that swallowed stderr.',
        platform: 'hackernews', community: 'Ask HN', author: 'throwaway1337',
        category: 'monitoring', flags: [], created_at: '2026-10-15T15:01:00Z' },
      { id: 'nug-918239', insight: 'A scratch-VM restore test with a file-tree diff caught a silent rsync exclusion that would have destroyed the Postgres volume.',
        platform: 'reddit', community: 'r/selfhosted', author: 'u/zelkova_rs',
        category: 'backup', flags: [], created_at: '2026-10-16T22:41:00Z' },
      { id: 'nug-918238', insight: 'The cron job dies silently at 03:00 every night and nobody notices until the reports are stale. We only found out when the finance team asked why Monday numbers were still Friday numbers — the exit code was 0 the whole time because the wrapper swallowed the error, and the dashboard kept rendering last Friday’s aggregates as if they were current.',
        platform: 'hackernews', community: 'Ask HN', author: 'throwaway1337',
        category: 'monitoring', flags: [], created_at: '2026-10-15T15:04:00Z' },
      { id: 'nug-918237', insight: 'lol same', platform: 'reddit', community: 'r/selfhosted',
        author: 'u/vex', category: 'noise', flags: ['trivial'], created_at: '2026-10-16T23:02:00Z' },
      { id: 'nug-918236', insight: 'Would pay for a tool that schedules the restore test and emails a diff instead of a green checkmark.',
        platform: 'reddit', community: 'r/selfhosted', author: 'u/amara_sys',
        category: 'backup', flags: ['buyer'], created_at: '2026-10-16T23:30:00Z' },
    ],
  },

  /* ── Clusters: /api/clusters ────────────────────────────────────────
   * tone: recurring | a few threads | one conversation
   */
  clusters: {
    rows: [
      { id: 'cl-01', theme: 'Backup restore verification', size: 34, tone: 'recurring',
        single_author: false, embedding_model: 'nomic-embed-text',
        idea_status: 'drafted', idea_title: 'Self-hosted backup runner that verifies restores on a schedule',
        problem: 'Every backup tool has a restore path nobody has tested; people improvise a diff step in a panic and trust it anyway.',
        solution: 'A runner that schedules real restores into a scratch VM, diffs the result, and emails a report instead of a green checkmark.',
        scores: { overall: 82, demand: 78, feas: 64, comp: 41 },
        quotes: [
          { author: 'u/k8sGPT-fan', text: 'The backup is the easy half. Nobody tests the restore until the day they need it.' },
          { author: 'u/zelkova_rs', text: 'We restore into a scratch VM weekly and diff the file trees.' },
          { author: 'u/amara_sys', text: 'I would pay for a tool that schedules the restore test and emails me a diff.' },
        ] },
      { id: 'cl-02', theme: 'Silently failing cron jobs', size: 21, tone: 'recurring',
        single_author: false, embedding_model: 'nomic-embed-text',
        idea_status: null, idea_title: null,
        problem: 'Nightly jobs die with exit 0 because wrappers swallow stderr; dashboards render stale aggregates as current.',
        solution: 'Diff exit codes and log tails across machines; flag services whose last-known-good drifted.',
        scores: { overall: 61, demand: 70, feas: 35, comp: 55 },
        quotes: [
          { author: 'throwaway1337', text: 'Exit code was 0 the whole time because the wrapper swallowed the error.' },
          { author: 'pg_hacker', text: 'Delta between two machines’ log tails is the poor man’s watchdog.' },
        ] },
      { id: 'cl-03', theme: 'VPS-to-VPS Postgres migration', size: 14, tone: 'a few threads',
        single_author: false, embedding_model: 'nomic-embed-text',
        idea_status: null, idea_title: null,
        problem: 'Moving a live database between providers means dumps, DNS, and version pinning done from memory.',
        solution: 'One command that orchestrates dump → transfer → restore → cutover with a rollback path.',
        scores: { overall: 74, demand: 81, feas: 52, comp: 60 },
        quotes: [{ author: 'u/pg_hacker', text: 'Migrated in one command; the writeup is the top result for the query now.' }] },
      { id: 'cl-04', theme: 'Invoice tooling for solo consultants', size: 9, tone: 'a few threads',
        single_author: false, embedding_model: 'nomic-embed-text',
        idea_status: null, idea_title: null,
        problem: 'Bank CSV exports and invoice PDFs are glued by hand every month.',
        solution: 'Bank CSV in, categorized invoice PDF out; one file per client.',
        scores: { overall: 58, demand: 62, feas: 71, comp: 44 },
        quotes: [{ author: 'r/consulting', text: 'Freshbooks is 30 dollars a month for what is a mail merge.' }] },
      { id: 'cl-05', theme: 'Smart-meter budget alerts', size: 6, tone: 'one conversation',
        single_author: true, embedding_model: 'nomic-embed-text',
        idea_status: null, idea_title: null,
        problem: 'Manufacturer APIs expose consumption but no budget alerting.',
        solution: 'Poll the API, alert on rate-of-change thresholds.',
        scores: { overall: 49, demand: 55, feas: 30, comp: 38 },
        quotes: [{ author: 'maya.k', text: 'The API is there; the alerting simply is not.' }] },
      { id: 'cl-06', theme: 'Teardown parts cross-reference', size: 11, tone: 'a few threads',
        single_author: false, embedding_model: 'fake-embed',
        idea_status: null, idea_title: null,
        problem: 'Finding a replacement part means reading teardown threads by hand.',
        solution: 'Index teardown images + part numbers into a searchable cross-reference.',
        scores: { overall: 33, demand: 28, feas: 61, comp: 24 },
        quotes: [] },
    ],
  },

  /* ── Search: /api/search?q= ───────────────────────────────────────── */
  search: {
    q: 'backups fail silently',
    examples: ['backups fail silently', 'onboarding takes too long', 'no way to export my data'],
    recents: ['cron exits 0', 'restore diff', 'invoice csv'],
    facets: { platform: { Reddit: 2, 'Hacker News': 1 }, category: { backup: 2, monitoring: 1 } },
    results: [
      { id: 'nug-918241', platform: 'reddit', category: 'backup', author: 'u/k8sGPT-fan',
        created_utc: '2026-10-16T22:06:30Z', similarity: 0.82, strength: 'strong',
        insight: 'Restore verification is the missing half of every backup tool.',
        raw_text: 'Every backup tool I tried has a restore path nobody has tested. I want a cron that does a real restore into a scratch VM and diffs the result against the source, then emails me a report instead of a green checkmark. I have lost two weekends to this already.',
        source_url: 'https://reddit.com/r/selfhosted/comments/1o9xk2/' },
      { id: 'nug-918239', platform: 'reddit', category: 'backup', author: 'u/zelkova_rs',
        created_utc: '2026-10-16T22:41:00Z', similarity: 0.74, strength: 'related',
        insight: 'A scratch-VM restore test caught a silent rsync exclusion.',
        raw_text: 'We restore into a scratch VM weekly and diff the file trees. It caught a silent rsync exclusion last month that would have nuked the postgres volume.',
        source_url: 'https://reddit.com/r/selfhosted/comments/1o9xk2/' },
      { id: 'nug-918240', platform: 'hackernews', category: 'monitoring', author: 'throwaway1337',
        created_utc: '2026-10-15T15:01:00Z', similarity: 0.61, strength: 'marginal',
        insight: 'Nightly cron reported exit 0 while the job failed inside a wrapper.',
        raw_text: 'Our nightly cron had exit code 0 for six weeks while the actual job failed inside a wrapper that swallowed stderr.',
        source_url: 'https://news.ycombinator.com/item?id=42118977' },
    ],
  },

  /* ── Signals (the collector): /api/signals + /api/signals/runs ────── */
  signals: {
    path: 'archive/signals.db',
    counts: {
      live: { unique_collected: 96, buyer: 31, practitioner: 41, errors: 7 },
      synthetic: { unique_collected: 12, buyer: 4, practitioner: 6, errors: 1 },
    },
    scraper: {
      command: 'jester scrape-jobs --since 24h',
      mode: 'daily', at: '06:30',
      state: 'last run 2 h ago · next 06:30',
      drift: null,
    },
    runs: [
      { started_at: '2026-10-17T06:30:00Z', kind: 'jobs', source: 'hackernews whoishiring',
        collected: 118, new: 42, buyer: 11, errors: 2, status: 'completed' },
      { started_at: '2026-10-16T06:30:00Z', kind: 'jobs', source: 'hackernews whoishiring',
        collected: 96, new: 18, buyer: 7, errors: 0, status: 'completed' },
      { started_at: '2026-10-15T06:30:00Z', kind: 'jobs', source: 'hackernews whoishiring',
        collected: 0, new: 0, buyer: 0, errors: 3, status: 'failed' },
      { started_at: '2026-10-14T06:30:00Z', kind: 'jobs', source: 'hackernews whoishiring',
        collected: 104, new: 21, buyer: 9, errors: 1, status: 'completed' },
      { started_at: '2026-10-13T06:30:00Z', kind: 'jobs', source: 'hackernews whoishiring',
        collected: 88, new: 14, buyer: 4, errors: 1, status: 'completed' },
    ],
    facets: {
      audience: { buyer: 31, practitioner: 41, curious: 24 },
      community: { 'Ask HN': 62, 'r/ExperiencedDevs': 18, 'r/devops': 16 },
      mode: { live: 96, synthetic: 12 },
      outcome: { kept: 89, skipped: 7 },
    },
    total: 108, shown: 60,
    records: [
      { record: '“I would pay $20/mo for a restore-verification cron”', audience: 'buyer', conf: 0.91,
        why: 'explicit price + explicit product shape' },
      { record: '“We restored into a scratch VM weekly; it caught an rsync exclusion”', audience: 'practitioner', conf: 0.84,
        why: 'named workflow with a failure it caught' },
      { record: '“Finance asked why Monday numbers were Friday numbers”', audience: 'buyer', conf: 0.77,
        why: 'cost of the failure, stated by the buyer’s team' },
      { record: '“Delta between two machines’ log tails is the poor man’s watchdog”', audience: 'practitioner', conf: 0.72,
        why: 'workaround, implies demand for the real thing' },
      { record: '“lol same”', audience: 'curious', conf: 0.10,
        why: 'no content — kept only as an example of trivial' },
      { record: '“Manufacturer API is there; alerting is not”', audience: 'practitioner', conf: 0.69,
        why: 'gap statement about a real product' },
    ],
  },

  /* ── Sources: /api/sources (+ /api/infra for fetch feasibility) ──────
   * health: producing | quiet | barren | unmeasured | unknown
   */
  sources: {
    path: 'config/sources.yaml',
    park_after_barren_visits: 3,
    duplicate_names: [],
    platform_gaps: { tiktok: 'not planned: ToS forbids scraping' },
    groups: [
      { platform: 'hackernews', rows: [
        { name: 'hackernews-frontpage', kind: 'site', url: 'https://news.ycombinator.com/', notes: '',
          fetch: 'api', supported: true, enabled: true, health: 'producing', health_reason: '',
          state: { last_fetched_at: '2026-10-17T04:02:10Z', last_queued: 512, total_queued: 20411, visits: 214, measured_visits: 214 } },
        { name: 'whoishiring', kind: 'thread', url: 'https://news.ycombinator.com/submitted?id=whoishiring', notes: '',
          fetch: 'api', supported: true, enabled: true, health: 'quiet', health_reason: '',
          state: { last_fetched_at: '2026-10-16T22:06:30Z', last_queued: 96, total_queued: 4118, visits: 32, measured_visits: 32 } },
      ] },
      { platform: 'reddit', rows: [
        { name: 'selfhosted', kind: 'subreddit', url: 'https://reddit.com/r/selfhosted/', notes: '',
          fetch: 'browser', supported: true, enabled: true, health: 'producing', health_reason: '',
          state: { last_fetched_at: '2026-10-17T04:03:00Z', last_queued: 214, total_queued: 31102, visits: 208, measured_visits: 208 } },
        { name: 'ExperiencedDevs', kind: 'subreddit', url: 'https://reddit.com/r/ExperiencedDevs/', notes: '',
          fetch: 'browser', supported: true, enabled: true, health: 'quiet', health_reason: '',
          state: { last_fetched_at: '2026-10-17T04:01:44Z', last_queued: 187, total_queued: 9214, visits: 187, measured_visits: 187 } },
        { name: 'devops', kind: 'subreddit', url: 'https://reddit.com/r/devops/', notes: '',
          fetch: 'browser', supported: true, enabled: true, health: 'barren',
          health_reason: 'walked 4 times since 12 Oct, never queued a comment — blocked · cloakserve down',
          state: { last_fetched_at: '2026-10-13T04:00:00Z', last_queued: 0, total_queued: 512, visits: 96, measured_visits: 96 } },
      ] },
      { platform: 'discourse', rows: [
        { name: 'home-assistant-features', kind: 'discourse', url: 'https://community.home-assistant.io/c/feature-requests', notes: '',
          fetch: 'api', supported: true, enabled: false, health: 'unknown', health_reason: '',
          state: null },
      ] },
      { platform: 'youtube', rows: [
        { name: 'fosdem-talks', kind: 'channel', url: 'https://youtube.com/@fosdemtalks', notes: '',
          fetch: 'browser', supported: true, enabled: true, health: 'barren',
          health_reason: 'blocked · cloakserve down since 14 Oct',
          state: { last_fetched_at: '2026-10-14T06:00:00Z', last_queued: 0, total_queued: 1204, visits: 41, measured_visits: 41 } },
      ] },
      { platform: 'tiktok', rows: [
        { name: 'tiktok-trends', kind: 'tag', url: 'https://tiktok.com/tag/saas', notes: 'pasted by hand, never worked',
          fetch: 'api', supported: false, enabled: true, health: 'unknown',
          health_reason: 'no worker adapter for tiktok/tag yet',
          state: null },
      ] },
    ],
  },

  /* ── Config: /api/config + /api/models ────────────────────────────── */
  config: {
    dirty: ['max_threads_per_source', 'competitor_ttl_days'],
    groups: [
      { stage: 'Fetch', blurb: 'how much the worker walks and how politely', knobs: [
        { key: 'max_threads_per_source', label: 'max threads / source', value: 12, range: [1, 50], help: 'threads / videos / topics the live worker walks per source, per run' },
        { key: 'max_threads_per_platform', label: 'max threads / platform', map: { reddit: 10, hackernews: 15 }, help: 'a platform → number map: caps each platform across all its sources' },
        { key: 'max_comments_per_thread', label: 'max comments / thread', value: 80, range: [1, 200], help: 'cap on comments taken from one thread' },
        { key: 'min_comments_per_thread', label: 'min comments / thread', value: 3, help: 'skip quiet threads (Hacker News + Discourse only: they get a count before fetching)' },
        { key: 'request_delay_ms', label: 'request delay (ms)', value: 1500, help: 'pacing between navigations (§36 #7)' },
      ] },
      { stage: 'Pre-filter', blurb: 'what is dropped before any model sees it', knobs: [
        { key: 'prefilter_min_chars', label: 'min chars', value: 40, help: 'shorter than this is noise' },
        { key: 'prefilter_max_chars', label: 'max chars', value: 2000, help: 'longer than this is an essay, not a pain signal' },
        { key: 'prefilter_min_words', label: 'min words', value: 8, help: 'fewer words than this is noise' },
        { key: 'prefilter_max_emoji', label: 'max emoji', value: 3, help: 'emoji count that marks a comment as low-signal' },
        { key: 'min_upvotes', label: 'min upvotes', value: 2, help: 'below this a comment is dropped before extraction' },
      ] },
      { stage: 'Archive & synthesis', blurb: 'dedup and how ideas are formed', knobs: [
        { key: 'dedup_threshold', label: 'dedup threshold', value: 0.92, range: [0.5, 1], help: 'cosine similarity at which two nuggets are the same' },
        { key: 'good_idea_min', label: 'good idea min', value: 70, help: 'overall score at which an idea counts as good' },
        { key: 'extractor_batch_size', label: 'extractor batch size', value: 24, help: 'comments per extractor model call; 1 = one call each (legacy)' },
      ] },
      { stage: 'Critic web step', blurb: 'competition checks and their budget', knobs: [
        { key: 'competitor_search_provider', label: 'search provider', value: 'duckduckgo',
          choices: ['none', 'duckduckgo', 'brave'], help: 'verifies competition before scoring; none keeps it unchecked' },
        { key: 'competitor_ttl_days', label: 'competitor ttl (days)', value: 30, help: 're-run the competitor check when older than this' },
        { key: 'critic_web_rate_limit', label: 'rate limit / min', value: 6, help: 'critic web calls per minute' },
        { key: 'critic_web_daily_budget', label: 'daily budget', value: 500, help: 'critic web calls per day' },
      ] },
      { stage: 'Providers & output', blurb: 'backends and what a run leaves behind', knobs: [
        { key: 'llm_provider', label: 'llm provider', value: 'ollama',
          choices: ['fake', 'ollama', 'groq'], help: 'default backend for the chat roles — override per role under Agent models' },
        { key: 'embedding_provider', label: 'embedding provider', value: 'ollama',
          choices: ['fake', 'ollama'], help: 'fake (deterministic) or ollama (local daemon)' },
        { key: 'export_after_run', label: 'export after run', value: 'true',
          choices: ['true', 'false'], help: 'write the export bundle when a run finishes' },
        { key: 'export_rows_per_file', label: 'rows / file', value: 5000, help: 'export bundle chunk size' },
      ] },
    ],
    models: {
      roles: [
        { role: 'extractor', value: 'qwen2.5:7b', choices: ['fake-llm', 'qwen2.5:7b', 'llama3.1:8b', 'phi3:mini'] },
        { role: 'critic', value: 'fake-llm', choices: ['fake-llm', 'qwen2.5:7b', 'llama3.1:8b'] },
        { role: 'chat', value: 'qwen2.5:7b', choices: ['fake-llm', 'qwen2.5:7b', 'llama3.1:8b', 'phi3:mini'] },
        { role: 'embedding', value: 'nomic-embed-text', choices: ['fake-embed', 'nomic-embed-text', 'bge-small'] },
      ],
      profile: 'balanced', profiles: ['fast', 'balanced', 'thorough'],
      ollama: { up: true, models: ['qwen2.5:7b', 'llama3.1:8b', 'phi3:mini', 'nomic-embed-text'] },
    },
    scraper: { mode: 'daily', at: '06:30' },
    env: [
      { key: 'JESTER_DB', set: true, note: 'archive/jester.db' },
      { key: 'OLLAMA_HOST', set: true, note: 'http://127.0.0.1:11434' },
      { key: 'CLOAKSERVE_URL', set: true, note: 'http://127.0.0.1:9222' },
      { key: 'GROQ_API_KEY', set: false, note: 'not set — groq roles fall back to fake' },
    ],
  },

  /* ── Schedule: /api/schedule ──────────────────────────────────────── */
  schedule: {
    installed: true,
    scheduler: 'Windows Task Scheduler',
    cadence: 'every 360 min',
    next_run: '17 Oct, 16:00 (in 5 h 48 m)',
    fetch_scope: 'every enabled source, capped at 40 posts / 120 comments per run',
    options: { max_posts: 40, max_comments: 120, max_ideas: 8, only: [] },
    presets: [['30', '30 min'], ['60', '1 h'], ['120', '2 h'], ['360', '6 h'], ['720', '12 h'], ['1440', 'daily']],
    sources: [
      { platform: 'hackernews', name: 'hackernews-frontpage', supported: true, enabled: true, url: 'https://news.ycombinator.com/' },
      { platform: 'hackernews', name: 'whoishiring', supported: true, enabled: true, url: 'https://news.ycombinator.com/submitted?id=whoishiring' },
      { platform: 'reddit', name: 'selfhosted', supported: true, enabled: true, url: 'https://reddit.com/r/selfhosted/' },
      { platform: 'reddit', name: 'ExperiencedDevs', supported: true, enabled: true, url: 'https://reddit.com/r/ExperiencedDevs/' },
      { platform: 'reddit', name: 'devops', supported: true, enabled: true, url: 'https://reddit.com/r/devops/' },
      { platform: 'youtube', name: 'fosdem-talks', supported: true, enabled: true, url: 'https://youtube.com/@fosdemtalks' },
      { platform: 'tiktok', name: 'tiktok-trends', supported: false, enabled: true, url: 'https://tiktok.com/tag/saas' },
    ],
    sources_hint: '6 of 7 can be fetched; 1 has no worker adapter yet.',
    activity: {
      windows: { '1': { runs: 0, expected: 1, partial: true },
                 '24': { runs: 4, expected: 4, nuggets: 7812, ideas: 153 } },
      total: 214, first_run_at: '2026-08-02T04:00:00Z',
      last: { started_at: '2026-10-17T04:00:02Z' },
      recent: [
        { started_at: '2026-10-17T04:00:02Z', run_id: 'cycle-20261017T040002Z', status: 'running', n_comments: 1540, n_nuggets_kept: 41, n_ideas: 0 },
        { started_at: '2026-10-16T22:00:04Z', run_id: 'cycle-20261016T220004Z', status: 'completed', n_comments: 4011, n_nuggets_kept: 212, n_ideas: 58 },
        { started_at: '2026-10-16T16:00:01Z', run_id: 'cycle-20261016T160001Z', status: 'completed', n_comments: 3480, n_nuggets_kept: 187, n_ideas: 44 },
        { started_at: '2026-10-16T10:00:03Z', run_id: 'cycle-20261016T100003Z', status: 'completed', n_comments: 3745, n_nuggets_kept: 203, n_ideas: 51 },
        { started_at: '2026-10-16T04:00:04Z', run_id: 'cycle-20261016T040004Z', status: 'failed', n_comments: 214, n_nuggets_kept: 9, n_ideas: 0 },
      ],
    },
    log: [
      '2026-10-17T04:00:02Z run cycle-20261017T040002Z started (scheduled, every 360 min)',
      '2026-10-17T04:03:40Z fetch ok — 212 posts, 1540 comments seen, 1230 kept by prefilter',
      '2026-10-17T04:03:41Z extract running — batch 24 comments/call, qwen2.5:7b',
      '2026-10-16T22:11:06Z run cycle-20261016T220004Z completed — 212 nuggets, 58 ideas in 662s',
      '2026-10-16T16:09:49Z run cycle-20261016T160001Z completed — 187 nuggets, 44 ideas in 588s',
      '2026-10-16T04:04:16Z run cycle-20261016T040004Z failed — synthesize: endpoint timeout (attempt 3/3)',
    ],
  },

  /* ── Doctor: /api/doctor + /api/eval ──────────────────────────────── */
  doctor: {
    findings: [
      { sev: 'bad', text: 'cloakserve is down — Reddit, YouTube and real-estate sources are skipped',
        why: 'start it: docker compose --profile live up -d cloakbrowser',
        fix: { label: 'see affected sources', go: 'sources' } },
      { sev: 'warn', text: '7 nugget(s) are archived but not yet searchable (re-embed backlog)',
        why: 'they are skipped by dedup and search until re-embedded',
        fix: { label: 're-embed now' } },
      { sev: 'warn', text: 'embedding model changed (nomic-embed-text → bge-small in config)',
        why: 'old vectors and new ones are not comparable — re-embed before trusting search',
        fix: { label: 'open Config', go: 'config' } },
    ],
    eval: { ok: false, gate: 70, checks: 12, failures: 2,
      details: [
        { ok: false, title: 'backup verification idea', overall: 61, expect_pass: true },
        { ok: false, title: 'invoice generator idea', overall: 58, expect_pass: true },
        { ok: true, title: 'vps migration idea', overall: 74, expect_pass: true },
        { ok: true, title: 'meter alerts idea (expected fail)', overall: 49, expect_pass: false },
      ] },
  },

  /* ── Chat: /api/chat/sessions + /api/chat/session ─────────────────── */
  chat: {
    sessions: [
      { session_id: 'chat-001', title: 'why did the 16 Oct 04:00 run fail?', messages: 8, created_utc: '2026-10-17T09:30:00Z' },
      { session_id: 'chat-002', title: 're-embed the stale nuggets', messages: 6, created_utc: '2026-10-17T08:02:00Z' },
      { session_id: 'chat-003', title: 'add r/ExperiencedDevs to the schedule', messages: 3, created_utc: '2026-10-16T15:44:00Z' },
    ],
    open: 'chat-001',
    messages: [
      { role: 'you', content: 'why did the 16 Oct 04:00 run fail?' },
      { role: 'model', content: 'Synthesis stopped: the model endpoint timed out after 3 attempts. 9 documents were kept; the run is marked failed. The batch it was on (batch-8838) also has an unreadable JSON payload.' },
      { role: 'you', content: 'requeue the run' },
      { role: 'model', content: 'I have prepared a requeue proposal for cycle-20261016T040004Z. It re-runs synthesize only — fetch, prefilter, extract and dedup results are kept.' },
    ],
    pending: [
      { proposal_id: 'prop-041', edit_kind: 'requeue_run', actor: 'model', created_utc: '2026-10-17T09:40:12Z',
        diff_json: { run_id: 'cycle-20261016T040004Z', phases: ['synthesize'], attempts: 1 } },
    ],
    edits: [
      { id: 'edit-037', edit_kind: 'update_config', actor: 'you', created_utc: '2026-10-17T08:05:00Z',
        outcome: 'applied', reverts_edit: null,
        after_json: { key: 'max_threads_per_source', from: 10, to: 12 } },
    ],
    state: 'the model proposes only; apply/reject below run as YOU. chat_max_rounds bounds every turn; a proposal expires after chat_proposal_ttl_hours.',
  },

  /* ── Edge-case states (mockup-only page) ──────────────────────────── */
  states: {
    empty: { message: 'No runs match this filter.', action: 'Clear filters' },
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
