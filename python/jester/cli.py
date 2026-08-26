"""CLI: `jester run` (mock pipeline over ingest_batch) and `jester list`."""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONFIG_DIR = str(Path(__file__).resolve().parents[2] / "config")

from jester.agents.archivist import Archivist
from jester.agents.critic import Critic, row_to_idea
from jester.agents.extractor import extract
from jester.agents.synthesizer import Synthesizer
from jester.competitors import CompetitorChecker
from jester.config import load_config
from jester.embed import select_embedding
from jester.env import load_env_once
from jester.export import default_export_dir, export_csv, prune_exports
from jester.fetchers import FixtureFetcher, MOCK_SAMPLE  # noqa: F401 (re-export)
from jester.llm import (
    PROMPT_VERSIONS,
    RUBRIC_VERSION,
    select_critic_llm,
    select_extractor_llm,
    select_synthesizer_llm,
    fallback_report,
    model_name,
)
from jester.notify import dispatch_notify
from jester import schedule as _schedule
from jester.ops import compute_floor_flags
from jester.prefilter import prefilter_comment
from jester.scoring import compute_overall
from jester.triviality import judge_trivial
from jester.store import (
    ConcurrentRunError,
    active_run,
    count_failed_batches,
    enqueue_batch,
    enqueue_new_only,
    finish_run,
    get_idea,
    get_runs,
    list_ideas,
    list_nuggets,
    mark_batch_processed,
    mark_comments_ingested,
    mark_reembedded,
    nugget_by_key,
    open_db,
    pending_batches,
    pending_reembeds,
    reap_running,
    requeue_failed,
    retention_sweep,
    start_run,
    update_idea,
    update_run_summary,
)
from jester.vector import VectorStore
from jester.worker import ingest as worker_ingest


def _vector_for(cfg, db_path):
    """§37.17/§37.24: shared server via JESTER_QDRANT_URL, else per-DB path.
    In-memory databases get an in-memory store (no :memory:.qdrant file)."""
    qdrant_url = os.environ.get("JESTER_QDRANT_URL")
    embed = select_embedding(cfg.thresholds)
    if qdrant_url:
        return VectorStore.for_url(qdrant_url, embed)
    if db_path == ":memory:":
        return VectorStore.in_memory(embed)
    db_path = os.path.abspath(db_path)
    return VectorStore.open(
        os.path.join(
            os.path.dirname(db_path),
            os.path.splitext(os.path.basename(db_path))[0] + ".qdrant",
        ),
        embed,
    )


def _idea_vector_for(cfg, db_path):
    """R44 needs its own collection: idea vectors must not pollute the nugget
    space the archivist dedups against. Returns None when no vector backend is
    reachable, which disables idea dedup rather than failing the run."""
    from jester.vector import VectorStore

    try:
        embed = select_embedding(cfg.thresholds)
        qdrant_url = os.environ.get("JESTER_QDRANT_URL")
        if qdrant_url:
            return VectorStore.for_url(qdrant_url, embed, collection="ideas")
        if db_path == ":memory:":
            return VectorStore.in_memory(embed, collection="ideas")
        base = os.path.abspath(db_path)
        path = os.path.join(
            os.path.dirname(base),
            os.path.splitext(os.path.basename(base))[0] + ".ideas.qdrant",
        )
        return VectorStore.open(path, embed, collection="ideas")
    except Exception as exc:  # noqa: BLE001 - dedup is a nice-to-have, not a gate
        print(f"idea dedup disabled (vector store unavailable: {exc})")
        return None


def _platform_counts(meta):
    """M3.2: kept comments per platform, from the post-prefilter metadata."""
    counts = {}
    for platform, _source, _thread, _fp in meta:
        key = platform or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _run_all_trivial(db, run_id):
    """True when every nugget captured in this run was low-signal (trivial=1)."""
    total, trivial = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(trivial), 0) FROM nuggets WHERE run_id=?",
        (run_id,),
    ).fetchone()
    return total > 0 and trivial == total


def _duration_fields(db, run_id, origin):
    """§37.8: actual = now - started_at; expected = rolling baseline of prior
    same-origin completed runs (None on first run — never fabricate, R29 spirit)."""
    row = db.execute(
        "SELECT started_at FROM runs WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)
    ).fetchone()
    actual = None
    if row and row[0]:
        try:
            started = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            actual = max(
                0.0, round((datetime.now(timezone.utc) - started).total_seconds(), 3)
            )
        except ValueError:
            actual = None
    expected = db.execute(
        "SELECT AVG(duration_actual_s) FROM runs "
        "WHERE origin=? AND status='completed' AND duration_actual_s IS NOT NULL AND run_id<>?",
        (origin, run_id),
    ).fetchone()[0]
    return {
        "duration_actual_s": actual,
        "duration_expected_s": round(expected, 3) if expected is not None else None,
    }


def _batch_field(row, name):
    """One column off a batch row, or None when this database has not got it.

    `sqlite3.Row` raises IndexError for an unknown column rather than
    returning None, and a database written before `thread_meta` existed simply
    does not have it — a pipeline that crashed on those rows would refuse to
    process a queue it had already filled.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _may_seed_fixtures(args) -> bool:
    """May this run fall back to the canned fixture when the queue is empty?

    Only a caller that did NOT just fetch may say yes. `jester run` on its own
    is the M1.1 mock demo and keeps the fallback; a caller that ran a live
    fetch first sets `seed_fixtures = False`, because an empty queue after a
    real fetch is a fact about the web, not a gap to paper over.
    """
    return bool(getattr(args, "seed_fixtures", True))


def cmd_run(args):
    cfg = load_config(args.config)
    db = open_db(args.db)

    # Selected before the run row is written, because `models_used` is meant to
    # be the record that explains a score shift — it used to be the hardcoded
    # string ["fake-llm", "fake-synth", "fake-critic"], so a night run entirely
    # on live models filed itself under the fakes.
    llm = select_extractor_llm(cfg.thresholds)
    synth_llm = select_synthesizer_llm(cfg.thresholds)
    critic_llm = select_critic_llm(cfg.thresholds)

    # R25: reap orphaned 'running' rows from crashed sessions, then take the lock.
    reap_running(db)
    origin = os.environ.get("JESTER_ORIGIN", "manual")
    try:
        start_run(
            db,
            args.run,
            models_used={
                "models": [
                    model_name(llm),
                    model_name(synth_llm),
                    model_name(critic_llm),
                ],
                "prompt_versions": PROMPT_VERSIONS,
                "rubric_version": RUBRIC_VERSION,
            },
            origin=origin,
        )
    except ConcurrentRunError as exc:
        print(f"refusing to start run '{args.run}': {exc}")
        sys.exit(1)

    checkpoints = []

    def checkpoint(phase):
        checkpoints.append(
            {"phase": phase, "at": datetime.now(timezone.utc).isoformat()}
        )

    rows = pending_batches(db)
    if not rows:
        # M1.1 mock form: batches come from a Fetcher; the M1.2 skip-list
        # decides what is actually new (re-fetches yield zero new batches).
        #
        # MOCK ONLY. This fallback used to be unconditional, so a LIVE
        # scheduled cycle whose fetch came back empty — every browser source
        # skipped because the cloakserve container was down, say — quietly
        # fed the canned fixture into the production pipeline and archived it
        # as if it had been scraped. It only ever looked harmless because the
        # skip-list already held those fingerprints; against a fresh database
        # the night's archive is invented (R29). An empty queue after a live
        # fetch is a fact to report, not a gap to fill.
        if _may_seed_fixtures(args):
            print("no pending batches; fetching via FixtureFetcher")
            for b in FixtureFetcher().fetch():
                new = enqueue_new_only(
                    db,
                    b["platform"],
                    b["source"],
                    b["thread_id"],
                    b["comments"],
                    run_id=args.run,
                )
                if new == 0:
                    print(
                        f"skip-list: thread {b['thread_id']} already ingested; 0 new comments"
                    )
            rows = pending_batches(db)
        else:
            print(
                "no pending batches — the fetch queued nothing new, so there "
                "is nothing to process (see the ingest output above)"
            )

    # M1.3/R35: pre-filter before extraction; per-heuristic funnel recorded.
    raw_meta, raw_comments = [], []
    readable, unreadable = [], 0
    for r in rows:
        # The Go worker marshals an empty comment slice as JSON `null`, and a
        # truncated write leaves invalid JSON. One malformed batch used to
        # abort the whole run with `'NoneType' object is not iterable`; it is
        # now counted and skipped so the healthy batches still flow (R55).
        try:
            payload = json.loads(r["comments"] or "null")
        except (TypeError, ValueError):
            payload = None
        if not isinstance(payload, list):
            unreadable += 1
            mark_batch_processed(db, r["id"])
            continue
        # The thread/video/topic envelope the worker captured alongside the
        # comments. Absent on batches queued before the widened capture, and
        # on any adapter that could not read it — both stay {} rather than
        # being filled in with a guess.
        try:
            post_meta = json.loads(_batch_field(r, "thread_meta") or "null")
        except (TypeError, ValueError):
            post_meta = None
        if not isinstance(post_meta, dict):
            post_meta = {}

        readable.append((r, payload))
        for c in payload:
            if not isinstance(c, dict):
                continue
            raw_meta.append(
                (
                    r["platform"] or "reddit",
                    r["source"] or "",
                    r["thread_id"] or "",
                    c.get("fingerprint"),
                )
            )
            raw_comments.append(
                {
                    "body": c.get("body", ""),
                    "fingerprint": c.get("fingerprint"),
                    "score": c.get("upvotes", 0),
                    # Everything the scraper captured, carried through to the
                    # extractor. This used to stop here: the queue held the
                    # full record and the pipeline copied three keys out of
                    # it, so author, timestamp, permalink, likes and reply
                    # counts died at this line.
                    "detail": c.get("detail") or {},
                    "post": post_meta,
                }
            )

    if unreadable:
        print(f"skipped {unreadable} batch(es) with unreadable comments payload")

    meta, comments = [], []
    funnel = {"kept": 0}
    used_per_thread = {}
    for i, m in enumerate(raw_meta):
        thread_id = m[2]
        v = prefilter_comment(
            raw_comments[i],
            cfg.thresholds,
            already_used=used_per_thread.get(thread_id, 0),
        )
        if v.keep:
            used_per_thread[thread_id] = used_per_thread.get(thread_id, 0) + 1
            meta.append(m)
            comments.append(raw_comments[i])
            funnel["kept"] += 1
        else:
            funnel[v.reason] = funnel.get(v.reason, 0) + 1

    update_run_summary(
        db,
        args.run,
        n_comments=len(comments),
        # Count what the run actually processed, not what it found queued —
        # an unreadable batch is reported separately, never as a post.
        n_batches=len(readable),
        n_posts=len(readable),
        # M3.2: a multi-platform run must say what each platform contributed,
        # or one dead adapter hides behind a healthy total.
        platform_counts=_platform_counts(meta),
        prefilter_funnel=funnel,
    )

    nuggets = []
    for (platform, source, thread_id, _fp), c in zip(meta, comments):
        nuggets += extract(
            [c],
            llm,
            platform=platform,
            thread_id=thread_id,
            source_url=source,
            run_id=args.run,
        )

    # R37: tag triviality pre-archive. Floor, not gate — nothing is filtered.
    for n in nuggets:
        n.trivial = judge_trivial(n.raw_text or "").trivial

    vector = _vector_for(cfg, args.db)
    archivist = Archivist(db, vector, cfg.thresholds)
    kept = archivist.run(nuggets)

    for r, payload in readable:
        mark_batch_processed(db, r["id"])
        mark_comments_ingested(
            db,
            r["thread_id"],
            [c.get("fingerprint") for c in payload if isinstance(c, dict)],
        )

    update_run_summary(
        db,
        args.run,
        n_nuggets_kept=len(kept),
        n_discarded_trivial=sum(1 for k in kept if k.trivial),
        trivial_share=(sum(1 for k in kept if k.trivial) / len(kept)) if kept else 0.0,
        top_near_misses=json.dumps(archivist.near_misses),
    )
    checkpoint("archive")

    print(f"kept {len(kept)} nuggets from {len(comments)} pending comments")

    # M2: synthesize + score any unclaimed nuggets.
    # M2.3: the competitor web step (D-1) and R44 idea-level dedup ride on the
    # same run. Both are off unless configured, so the default path is M2.2's.
    checker = CompetitorChecker(db, cfg.thresholds)
    idea_vector = _idea_vector_for(cfg, args.db)
    synthesizer = Synthesizer(db, cfg.thresholds)
    ideas = synthesizer.run(
        synth_llm,
        critic_llm,
        run_id=args.run,
        checker=checker,
        idea_vector=idea_vector,
        max_ideas=getattr(args, "max_ideas", None),
    )
    for idea in ideas:
        verdict = (
            "PASS" if idea.scores.overall >= cfg.thresholds.good_idea_min else "REVIEW"
        )
        comp = (
            "unchecked"
            if not idea.competition_checked
            else f"{idea.scores.competition:.1f}"
        )
        print(
            f"idea #{idea.id}: {idea.title}  overall={idea.scores.overall:.1f} "
            f"comp={comp}  [{verdict}]"
        )
    # R44 merges did real work but archived nothing new — say so, or the run
    # reads as "0 ideas" on a night that grew the evidence behind several.
    for m in synthesizer.merged:
        print(
            f"merged into idea #{m.duplicate_of} (cosine {m.score:.2f}): "
            f"+{len(m.added)} nugget(s){' — re-scored' if m.grew else ''}"
        )
    # R40/R55: a provider that answered for some ideas and silently swapped in
    # canned text for the rest must say so, or the archive fills with plausible
    # rows nobody flagged.
    for line in fallback_report(llm, synth_llm, critic_llm):
        print("degraded: " + line)
    if synthesizer.stopped_early:
        # No silent caps: without this line "synthesized 3 idea(s)" reads as
        # "the archive had only 3 to give" on a run that stopped at a goal.
        print(
            f"stopped at the {args.max_ideas}-idea goal; "
            f"unclustered nuggets stay queued for the next run"
        )
    print(f"synthesized {len(ideas)} idea(s)")
    update_run_summary(db, args.run, n_ideas=len(ideas))
    checkpoint("synthesize")

    failed = count_failed_batches(db)
    flags = compute_floor_flags(
        kept=len(kept),
        ideas=len(ideas),
        all_trivial=_run_all_trivial(db, args.run),
        failed_batches=failed,
    )
    durations = _duration_fields(db, args.run, origin)
    finish_run(
        db,
        args.run,
        status="completed",
        floor_flags=flags,
        phase_checkpoints=checkpoints,
        **durations,
    )
    if flags:
        print("floor flags: " + ", ".join(flags))

    # Leave a readable copy behind. A nightly run nobody watches should not
    # require a second command before the haul is inspectable, so this happens
    # after the summary is written — an export failure must never turn a good
    # run into a failed one.
    if cfg.thresholds.export_after_run:
        out_dir = getattr(args, "exports", None) or default_export_dir(args.db)
        try:
            manifest = export_csv(
                db, out_dir, rows_per_file=cfg.thresholds.export_rows_per_file
            )
            c = manifest["counts"]
            print(
                f"exported {c['comments']} comment(s), {c['nuggets']} nugget(s), "
                f"{c['ideas']} idea(s) to {out_dir}"
            )
        except Exception as exc:  # noqa: BLE001 - reporting, not a pipeline gate
            print(f"export skipped: {exc}")

    # Only a broken pipeline fails the exit code (§13.1); quality flags are advisory.
    if "FAILED_BATCHES" in flags:
        sys.exit(1)


def _parse_eval_line(line):
    """Parse `title | demand | feasibility | [competition]`; competition optional (imputed)."""
    if line.strip().startswith("#") or not line.strip():
        return None
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 3:
        return None
    title = parts[0]
    try:
        demand = float(parts[1])
        feasibility = float(parts[2])
    except ValueError:
        return None
    competition = (
        float(parts[3])
        if len(parts) > 3 and parts[3] not in ("", "-", "unchecked")
        else None
    )
    return title, demand, feasibility, competition


def cmd_eval(args):
    cfg = load_config(args.config)
    gate = cfg.thresholds.good_idea_min
    failures = 0
    checks = 0
    for path, expect_pass in ((args.good, True), (args.bad, False)):
        if not path:
            continue
        for line in Path(path).read_text().splitlines():
            parsed = _parse_eval_line(line)
            if not parsed:
                continue
            checks += 1
            _title, demand, feasibility, competition = parsed
            overall = compute_overall(demand, feasibility, competition)
            ok = (overall >= gate) == expect_pass
            if not ok:
                failures += 1
                print(f"FAIL [{path}] {_title}: overall={overall:.1f} (gate={gate})")
    if failures:
        print(f"eval FAILED: {failures}/{checks} mismatched")
        sys.exit(1)
    print(f"eval OK: {checks} cases passed (gate={gate})")


def cmd_mark(args):
    db = open_db(args.db)
    row = get_idea(db, args.id)
    if not row:
        print(f"idea #{args.id} not found")
        sys.exit(1)
    idea = row_to_idea(row)
    idea.status = args.status
    update_idea(db, idea)
    print(f"idea #{args.id} -> status={args.status}")


def cmd_show(args):
    """R26: one-idea detail view — the no-SQL half of the review loop."""
    db = open_db(args.db)
    row = get_idea(db, args.id)
    if not row:
        print(f"idea #{args.id} not found")
        sys.exit(1)
    comp = (
        "unchecked" if not row["competition_checked"] else f"{row['competition']:.1f}"
    )
    print(f"IDEA #{row['id']}: {row['title']}  [{row['status']}]")
    print(
        f"  overall={row['overall']:.1f}  demand={row['demand_signal']:.1f}  "
        f"feasibility={row['feasibility']:.1f}  competition={comp}"
    )
    if row["competitor_notes"]:
        print(f"  competitor notes: {row['competitor_notes']}")
    print(f"  problem: {row['problem_statement'] or '-'}")
    print(f"  solution: {row['proposed_solution'] or '-'}")
    keys = json.loads(row["supporting_nuggets"] or "[]")
    print(f"  supporting nuggets ({len(keys)}):")
    for k in keys:
        n = nugget_by_key(db, k)
        if n is None:
            # Golden gate (R48) prevents this; render honestly if it ever happens.
            print(f"    MISSING citation: {k}")
            continue
        print(f"    - {k}")
        print(f"      [{n['category']}] {n['platform']}  {n['source_url']}")
        print(f"      {n['extracted_insight']}")


def cmd_list(args):
    db = open_db(args.db)
    rows = list_nuggets(db)
    for r in rows:
        print(f"[{r['category']}] {r['platform']} :: {r['unique_key']}")
        print(f"    {r['extracted_insight']}")
    print(f"total: {len(rows)} nuggets")

    ideas = list_ideas(db)
    # §3.3 comparability: warn when scores span multiple critic models.
    critic_models = {i["critic_model"] for i in ideas if i["critic_model"]}
    if len(critic_models) > 1:
        print(
            f"archive spans {len(critic_models)} critic models - "
            "scores are not directly comparable across them"
        )
    for i in ideas:
        comp = (
            "unchecked" if not i["competition_checked"] else f"{i['competition']:.1f}"
        )
        print(
            f"IDEA #{i['id']}: {i['title']}  "
            f"overall={i['overall']:.1f}  "
            f"demand={i['demand_signal']:.1f} feasibility={i['feasibility']:.1f} "
            f"competition={comp}  status={i['status']}"
        )
    print(f"total: {len(ideas)} idea(s)")


def cmd_runs(args):
    db = open_db(args.db)
    rows = get_runs(db)
    print(f"last {len(rows)} run(s):")
    for r in rows:
        # Partial rows (crashed/reaped runs) may hold NULLs — render honestly.
        act = r["duration_actual_s"]
        exp = r["duration_expected_s"]
        dur = (
            f"dur={'-' if act is None else f'{act:.1f}s'}"
            f"/exp={'-' if exp is None else f'{exp:.1f}s'}"
        )
        posts = "-" if r["n_posts"] is None else r["n_posts"]
        line = (
            f"{r['started_at']}  {r['run_id']}  {r['status']}  "
            f"kept={r['n_nuggets_kept']} ideas={r['n_ideas']}  "
            f"{dur}  origin={r['origin']}  posts={posts}"
        )
        if r["platform_counts"]:
            line += f"  platforms={r['platform_counts']}"
        if r["prefilter_funnel"]:
            line += f"  filter={r['prefilter_funnel']}"
        if r["top_near_misses"]:
            line += f"  near={r['top_near_misses']}"
        if r["floor_flags"]:
            line += f"  flags={r['floor_flags']}"
        print(line)


def cmd_retention(args):
    """§14 TTL sweep. R31: when the write lock is held by an active run,
    skip-and-log (exit 0) rather than fighting for the lock.

    `open_db` is inside the guard, not before it. Opening the database is
    itself a WRITE — it runs the schema reconcile, CREATE TABLE IF NOT EXISTS
    and the idempotent ALTERs — so an active run holds the lock against the
    OPEN, and the sweep this guarded never got as far as running. A retention
    job scheduled alongside a cycle died on a traceback instead of standing
    down, which is the one case the guard exists for.
    """
    sqlite3 = __import__("sqlite3")
    try:
        db = open_db(args.db)
        purged = retention_sweep(db)
    except sqlite3.OperationalError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            print(f"retention skipped: write lock held ({exc})")
            return
        raise
    # §14 does not stop at the database boundary: a CSV export holds the same
    # raw text, so it ages out on the same clock.
    pruned = prune_exports(
        getattr(args, "exports", None) or default_export_dir(args.db)
    )
    print(
        f"retention sweep: wiped raw_text on {purged['nuggets_raw']} nugget(s); "
        f"dropped {purged['batches']} completed batch(es); "
        f"pruned {pruned} export dir(s)"
    )


def cmd_ingest(args):
    """Fetch the curated sources with the Go worker and queue what they yield.

    The half of the loop the CLI never had. `jester run` processes the queue;
    without this there was no command-line way to fill it, so the scheduled
    nightly job fetched nothing, every night, and reported success.
    """
    res = worker_ingest(
        args.config,
        args.db,
        args.run,
        live=not args.mock,
        only=[n for n in (args.only or "").split(",") if n.strip()],
        max_comments=args.max_comments,
        max_posts=args.max_posts,
    )
    if res.get("output"):
        print(res["output"])
    if not res["ok"]:
        print("ingest failed: " + (res.get("error") or f"exit {res.get('code')}"))
        sys.exit(1)
    queued = res.get("queued_new_comments")
    print(
        f"queued {queued if queued is not None else 'an unreported number of'} new comment(s)"
    )
    return res


class _TimestampedOut:
    """Prefix every line written to it with a UTC timestamp.

    The scheduled log used to carry exactly two timestamps — the launcher's
    "starting" and "exited" lines — with the entire run flat between them. That
    is fine for a job you watch and useless for one you do not: "12 sources
    skipped" says nothing about whether that happened at the start of a 40
    second run or after a 12 minute stall, which is the only question you have
    when reading it the next morning.

    Wraps rather than replaces the stream, and only stamps at line starts, so a
    partial write (a progress dot, a prompt) does not grow a timestamp in the
    middle of a word.
    """

    def __init__(self, stream, clock=None):
        self._stream = stream
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._at_line_start = True

    def write(self, text):
        if not text:
            return 0
        stamp = self._clock().strftime("[%Y-%m-%d %H:%M:%SZ] ")
        out = []
        for piece in text.splitlines(keepends=True):
            if self._at_line_start and piece.strip():
                out.append(stamp)
            out.append(piece)
            self._at_line_start = piece.endswith("\n")
        self._stream.write("".join(out))
        return len(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        # isatty(), encoding, fileno() and friends belong to the real stream.
        return getattr(self._stream, name)


def _default_cycle_run_id() -> str:
    return "cycle-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def cmd_cycle(args):
    """One full turn of the loop: fetch, then process what was fetched.

    This is what a schedule should invoke. Scheduling `run` alone gives you a
    job that drains a queue nobody fills; scheduling `ingest` alone gives you a
    queue that grows and is never read.

    A tick that finds the previous one still working stands down. It has to
    check BEFORE fetching: discovering the clash at `start_run` would mean the
    web had already been re-scraped for a run that is about to be refused.
    """
    if not getattr(args, "run", None):
        args.run = _default_cycle_run_id()
    # A cycle is what a schedule runs, and a scheduled log is read hours later.
    # Opt out with --no-timestamps for a human running it in a terminal.
    if not getattr(args, "no_timestamps", False):
        sys.stdout = _TimestampedOut(sys.stdout)
    stale = getattr(args, "stale_after", 60) or 60
    db = open_db(args.db)
    reaped = reap_running(db, older_than_minutes=stale)
    if reaped:
        print(f"reaped {reaped} run(s) stuck 'running' for over {stale}m")
    busy = active_run(db)
    if busy:
        # Exit 0: a skipped tick is the guard working, not a failure. A
        # non-zero exit here would make every scheduler on the box start
        # emailing about a healthy system.
        print(f"skipping this tick — run '{busy}' is still in progress")
        return {"ok": True, "skipped": True, "active_run": busy}

    res = worker_ingest(
        args.config,
        args.db,
        f"{args.run}-ingest",
        live=not args.mock,
        only=[n for n in (args.only or "").split(",") if n.strip()],
        max_comments=args.max_comments,
        max_posts=args.max_posts,
    )
    if res.get("output"):
        print(res["output"])
    if not res["ok"]:
        # Processing an empty queue after a failed fetch would report success
        # for a cycle that fetched nothing.
        print("ingest failed: " + (res.get("error") or f"exit {res.get('code')}"))
        sys.exit(1)
    queued = res.get("queued_new_comments")
    print(
        f"ingest queued {queued if queued is not None else '?'} new comment(s); "
        "running the pipeline over the queue"
    )
    # A cycle has just fetched. If that came back empty, `cmd_run` must NOT
    # reach for the canned fixture: a scheduled live tick whose sources were
    # all skipped (cloakserve down, say) would otherwise archive the mock
    # sample as though it had scraped it (R29). Mock cycles keep the fallback —
    # that is the whole point of `--mock`.
    args.seed_fixtures = bool(getattr(args, "mock", False))
    return cmd_run(args)


def cmd_schedule(args):
    """D-2: register / inspect / remove the nightly job with the host scheduler."""
    if args.action == "status":
        st = _schedule.status()
        if st["installed"]:
            nxt = st.get("next_run")
            cadence = st.get("cadence")
            print(
                f"scrape job INSTALLED via {st['scheduler']}"
                + (f" — {cadence}" if cadence else "")
                + (f", next run {nxt}" if nxt else "")
            )
            # The scope is the other half of "what is scheduled"; reporting the
            # cadence alone tells you when a black box fires.
            opts = _schedule.installed_options()
            names = opts.get("only") or []
            print(
                "  sources: " + (", ".join(names) if names else "every enabled source")
            )
            depth = [
                f"{k.replace('max_', 'max ')} {v}"
                for k, v in opts.items()
                if k != "only"
            ]
            print("  depth  : " + (", ".join(depth) if depth else "no ceiling"))
        else:
            print(f"scrape job NOT installed ({st['scheduler']} has no jester entry)")
            print("  every N minutes: jester schedule install --every 30")
            print("  once a night   : jester schedule install --at 03:00")
        return
    if args.action == "install":
        scope = {
            "only": args.only,
            "max_posts": args.max_posts,
            "max_comments": args.max_comments,
            "max_ideas": args.max_ideas,
        }
        res = _schedule.install(
            at=args.at,
            db=args.db,
            config=args.config,
            every=args.every,
            extra=_schedule.scrape_flags(scope),
        )
    elif args.action == "remove":
        res = _schedule.remove()
    else:  # run
        res = _schedule.run_now()
    print(res["detail"])
    if not res["ok"] and res["action"] != "manual":
        sys.exit(1)


def cmd_export(args):
    """Write the archive out as CSV, split by content type and origin."""
    db = open_db(args.db)
    out_dir = args.out or default_export_dir(args.db)
    rows_per_file = getattr(args, "rows_per_file", None)
    if rows_per_file is None:
        try:
            rows_per_file = load_config(args.config).thresholds.export_rows_per_file
        except Exception:  # noqa: BLE001 - export must work without a config dir
            rows_per_file = None
    manifest = export_csv(db, out_dir, rows_per_file=rows_per_file)
    c = manifest["counts"]
    print(f"exported to {out_dir}")
    print(
        f"  {c['comments']} scraped comment(s), {c['nuggets']} nugget(s), "
        f"{c['ideas']} idea(s), {c['citations']} citation(s)"
    )
    if manifest.get("duplicates_dropped"):
        # Never a silent gap: the export holding fewer rows than the archive
        # must be an explained number.
        print(
            f"  {manifest['duplicates_dropped']} duplicate row(s) collapsed "
            f"— see duplicates.csv"
        )
    for name, rows in sorted(manifest["files"].items()):
        print(f"  {rows:>6}  {name}")
    if not any(manifest["files"].values()):
        # An empty archive exports empty files; say so rather than letting the
        # operator read a directory of headers as a successful haul.
        print("  (archive is empty — every file is headers only)")


def cmd_requeue(args):
    db = open_db(args.db)
    n = requeue_failed(db)
    print(f"requeue: reset {n} failed batch(es) to pending")


def cmd_notify(args):
    """R55 failure-hook runner. Best-effort: always exits 0 so it can never
    mask the nightly wrapper's real exit code."""
    reason = "hard_error"
    if getattr(args, "db", None):
        runs = get_runs(open_db(args.db), limit=1)
        if runs:
            flags = json.loads(runs[0]["floor_flags"] or "[]")
            reason = "FAILED_BATCHES" if "FAILED_BATCHES" in flags else "hard_error"
    run_id = getattr(args, "run_id", None) or "nightly"
    ok, _out = dispatch_notify(
        list(args.cmd), exit_code=args.exit_code, run_id=run_id, reason=reason
    )
    print(f"notify {'OK' if ok else 'FAILED'} ({' '.join(list(args.cmd))})")


def evaluate_smoke(db):
    """M1.6 gates over an open connection: completed run summary WITH duration,
    and at least one non-trivial pain-point nugget (§7.2 bar via R37 tags)."""
    failures = []
    runs = get_runs(db, limit=1)
    if not runs or runs[0]["status"] != "completed":
        failures.append("no completed run summary recorded")
    elif runs[0]["duration_actual_s"] is None:
        failures.append("run summary lacks expected-vs-actual duration fields")
    nontrivial = db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE trivial=0 AND category='pain_point'"
    ).fetchone()[0]
    if nontrivial < 1:
        failures.append("fewer than 1 non-trivial nugget (§7.2 bar)")
    return failures


def cmd_smoke(args):
    """M1.6 E2E smoke in mock form. --live needs the §4 external stack."""
    if getattr(args, "live", False):
        print(
            "smoke --live requires the external stack (CloakBrowser/Qdrant/Ollama, §4); deferred"
        )
        sys.exit(1)
    db_path = args.db or os.path.join(tempfile.gettempdir(), "jester-smoke.db")
    run_id = f"smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    print(f"[smoke] mock pipeline -> {db_path}")
    cmd_run(argparse.Namespace(config=args.config, db=db_path, run=run_id))
    failures = evaluate_smoke(open_db(db_path))
    if failures:
        for f in failures:
            print(f"SMOKE FAIL: {f}")
        sys.exit(1)
    print("SMOKE PASS: run summary recorded with duration; >=1 non-trivial nugget")


def _qdrant_point_count(url, collection="nuggets"):
    """§33 #4 parity probe; overridable in tests via monkeypatch."""
    from jester.vector import VectorStore

    return VectorStore.count_points_at(url, collection)


def evaluate_doctor(db):
    """v3.8 #4 / §30 #2 health checks over SQLite. Every finding here is a
    silent-degradation risk (R40/R55): stuck runs, failed batches, a growing
    reembed_backlog, orphaned idea citations, and — when a shared Qdrant is
    configured (JESTER_QDRANT_URL) — point-per-row parity with the archive."""
    findings = []
    stale = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
    if stale:
        findings.append(f"{stale} run(s) stuck in 'running' (crashed session?)")
    failed = db.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE status='failed'"
    ).fetchone()[0]
    if failed:
        findings.append(f"{failed} failed batch(es); run `jester requeue` to reset")
    backlog = db.execute(
        "SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1"
    ).fetchone()[0]
    if backlog:
        findings.append(
            f"reembed_backlog={backlog}: nugget(s) excluded from dedup/clustering "
            "until reembedded"
        )
    orphans = 0
    for (keys_json,) in db.execute("SELECT supporting_nuggets FROM ideas").fetchall():
        for k in json.loads(keys_json or "[]"):
            row = db.execute(
                "SELECT 1 FROM nuggets WHERE unique_key=?", (k,)
            ).fetchone()
            if row is None:
                orphans += 1
    if orphans:
        findings.append(f"{orphans} orphaned idea citation(s) point at missing nuggets")
    qdrant_url = os.environ.get("JESTER_QDRANT_URL")
    if qdrant_url:
        rows = db.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0]
        try:
            points = _qdrant_point_count(qdrant_url)
        except Exception as exc:
            findings.append(f"qdrant parity check failed: {exc}")
        else:
            if points != rows:
                findings.append(
                    f"qdrant parity mismatch: {rows} nugget row(s) vs {points} point(s)"
                )
    return findings


def cmd_doctor(args):
    db = open_db(args.db)  # SchemaVersionError propagates loud — that is a finding
    checks = 4 + (1 if os.environ.get("JESTER_QDRANT_URL") else 0)
    findings = evaluate_doctor(db)
    if findings:
        for f in findings:
            print(f"doctor FAIL: {f}")
        sys.exit(1)
    print(f"doctor OK: {checks} check(s) clean")


def cmd_reembed(args):
    """§3.2 v3.5 #4 recovery: re-attempt vector persistence for flagged nuggets."""
    db = open_db(args.db)
    # §33 #1 spirit: repair must not race an active run. Refuse, don't reap.
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[
        0
    ]
    if running:
        print(f"refusing reembed: {running} run(s) still active")
        sys.exit(1)

    rows = pending_reembeds(db)
    if not rows:
        print("reembed OK: backlog empty")
        return

    cfg = load_config(args.config)
    vector = _vector_for(cfg, args.db)
    ok = failed = 0
    for row in rows:
        key = row["unique_key"]
        try:
            vector.upsert(key, row["raw_text"] or "", {})
            mark_reembedded(db, key, f"emb:{abs(hash(key)) % (2**63)}")
            ok += 1
        except Exception as exc:
            failed += 1
            print(f"reembed FAIL {key}: {exc}")
    print(f"reembedded {ok} nugget(s); {failed} still failing")
    if failed:
        sys.exit(1)


def cmd_resynth(args):
    """D-16/R56 escape hatch: re-derive a NEW idea from an existing idea's
    evidence. Original idea untouched; nuggets not reassigned."""
    db = open_db(args.db)
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[
        0
    ]
    if running:
        print(f"refusing resynth: {running} run(s) still active")
        sys.exit(1)

    idea = get_idea(db, args.id)
    if not idea:
        print(f"idea #{args.id} not found")
        sys.exit(1)

    keys = json.loads(idea["supporting_nuggets"] or "[]")
    rows = []
    missing_raw = []
    for k in keys:
        row = nugget_by_key(db, k)
        if row is None:
            missing_raw.append(k)  # vanished nugget: also fatal for resynthesis
            continue
        if row["raw_text"] is None:
            missing_raw.append(k)  # v3.7 #6: retention purged the forensic text
    if missing_raw:
        print(
            f"resynth FAIL: raw_text NULL (retention-purged?) or missing for: {missing_raw}"
        )
        sys.exit(1)

    cfg = load_config(args.config)
    synth = Synthesizer(db, cfg.thresholds)
    created = synth.run(
        select_synthesizer_llm(cfg.thresholds),
        select_critic_llm(cfg.thresholds),
        run_id=f"resynth-{args.id}",
        rows=rows or [nugget_by_key(db, k) for k in keys],
    )
    if not created:
        print(
            "resynth: subset did not form a cluster (lone-nugget rule R50); nothing created"
        )
        return
    for new in created:
        print(f"resynth OK: new idea #{new.id}: {new.title}")


def cmd_migrate(args):
    """R31 concurrency contract: refuse while a run is active; otherwise apply
    idempotent schema migrations via open_db."""
    db = open_db(args.db)
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[
        0
    ]
    if running:
        print(f"migrate REFUSED: {running} run(s) still active (R31)")
        sys.exit(1)
    version = db.execute(
        "SELECT value FROM meta WHERE key='schema_version'"
    ).fetchone()[0]
    print(f"migrate OK: schema_version={version}")


def main(argv=None):
    # Secrets live in .env, not in thresholds.yaml — the config files are
    # committed and the console writes to them. Loading here rather than at
    # import time keeps `import jester.cli` free of side effects.
    load_env_once()
    # Idea titles and worker output carry non-ASCII (smart quotes, non-breaking
    # hyphens, CJK). On a Windows console the default cp1252 stream raises
    # UnicodeEncodeError and kills the whole cycle. Force UTF-8 so a single
    # odd glyph never aborts a nightly run. No-op on the already-UTF-8 container.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    p = argparse.ArgumentParser(prog="jester")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--db", default="data/jester.db")
    r.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    r.add_argument(
        "--exports",
        default=None,
        help="where export_after_run writes (default: exports/ beside the db)",
    )
    r.add_argument("--run", default="mock-run")
    r.add_argument(
        "--max-ideas",
        type=int,
        default=None,
        help="stop synthesizing after this many new ideas; "
        "unclustered nuggets stay queued for the next run",
    )
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list")
    l.add_argument("--db", default="data/jester.db")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("eval")
    e.add_argument(
        "--good",
        default=str(Path(__file__).resolve().parents[2] / "eval" / "known_good.md"),
    )
    e.add_argument(
        "--bad",
        default=str(Path(__file__).resolve().parents[2] / "eval" / "known_bad.md"),
    )
    e.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    e.set_defaults(func=cmd_eval)

    m = sub.add_parser("mark")
    m.add_argument("--db", default="data/jester.db")
    m.add_argument("--id", type=int, required=True)
    m.add_argument("--status", required=True)
    m.set_defaults(func=cmd_mark)

    sh = sub.add_parser("show")
    sh.add_argument("--db", default="data/jester.db")
    sh.add_argument("--id", type=int, required=True)
    sh.set_defaults(func=cmd_show)

    rs = sub.add_parser("runs")
    rs.add_argument("--db", default="data/jester.db")
    rs.set_defaults(func=cmd_runs)

    rt = sub.add_parser("retention")
    rt.add_argument("--db", default="data/jester.db")
    rt.add_argument(
        "--exports",
        default=None,
        help="export dir to prune (default: exports/ beside the db)",
    )
    rt.set_defaults(func=cmd_retention)

    def _worker_flags(parser):
        parser.add_argument(
            "--only",
            default="",
            help="comma-separated source names; empty walks every enabled source",
        )
        parser.add_argument(
            "--max-comments",
            type=int,
            default=None,
            help="stop once this many comments are queued (0/None = no cap)",
        )
        parser.add_argument(
            "--max-posts",
            type=int,
            default=None,
            help="stop once this many threads/videos/topics are fetched",
        )
        parser.add_argument(
            "--mock",
            action="store_true",
            help="run the worker against the fixture instead of the live web",
        )

    ing = sub.add_parser("ingest", help="fetch the curated sources with the Go worker")
    ing.add_argument("--db", default="data/jester.db")
    ing.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    ing.add_argument("--run", default="cli-ingest")
    _worker_flags(ing)
    ing.set_defaults(func=cmd_ingest)

    cy = sub.add_parser(
        "cycle", help="ingest, then run the pipeline over what was queued"
    )
    cy.add_argument("--db", default="data/jester.db")
    cy.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    # A fixed default would stamp every tick of a 30-minute schedule with the
    # same run_id: the ledger becomes N identical rows and the per-run ingest
    # id collides too. The timestamp is resolved at parse time, once per tick.
    cy.add_argument(
        "--run", default=None, help="run id (default: cycle-<UTC timestamp>)"
    )
    cy.add_argument("--exports", default=None)
    cy.add_argument(
        "--max-ideas",
        type=int,
        default=None,
        help="stop synthesizing after this many new ideas",
    )
    cy.add_argument(
        "--no-timestamps",
        action="store_true",
        help="do not prefix each output line with a UTC timestamp",
    )
    cy.add_argument(
        "--stale-after",
        type=int,
        default=60,
        metavar="MINUTES",
        help="a run still 'running' after this long is treated as "
        "crashed and reaped (default 60)",
    )
    _worker_flags(cy)
    cy.set_defaults(func=cmd_cycle)

    sc = sub.add_parser(
        "schedule",
        help="register the scrape+process cycle with the host scheduler (D-2)",
    )
    sc.add_argument(
        "action",
        choices=["status", "install", "remove", "run"],
        nargs="?",
        default="status",
    )
    sc.add_argument(
        "--at",
        default=_schedule.DEFAULT_TIME,
        help="HH:MM local time for a daily schedule",
    )
    sc.add_argument(
        "--every",
        type=int,
        default=None,
        metavar="MINUTES",
        help="run every N minutes instead of daily (1-1439)",
    )
    # What each tick fetches and how deep. Without these a scheduled run always
    # means "every enabled source, no ceiling", which is the last thing you
    # want firing every half hour.
    sc.add_argument(
        "--only", default="", help="comma-separated source names each tick should fetch"
    )
    sc.add_argument(
        "--max-posts", type=int, default=None, help="threads/videos/topics per tick"
    )
    sc.add_argument("--max-comments", type=int, default=None, help="comments per tick")
    sc.add_argument(
        "--max-ideas", type=int, default=None, help="new ideas synthesized per tick"
    )
    sc.add_argument("--db", default="data/jester.db")
    sc.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    sc.set_defaults(func=cmd_schedule)

    ex = sub.add_parser(
        "export", help="write the archive as CSV by content type + origin"
    )
    ex.add_argument("--db", default="data/jester.db")
    ex.add_argument(
        "--out", default=None, help="output directory (default: exports/ beside the db)"
    )
    ex.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    ex.add_argument(
        "--rows-per-file",
        type=int,
        default=None,
        dest="rows_per_file",
        help="rows per CSV shard (default: export_rows_per_file)",
    )
    ex.set_defaults(func=cmd_export)

    rq = sub.add_parser("requeue")
    rq.add_argument("--db", default="data/jester.db")
    rq.set_defaults(func=cmd_requeue)

    nt = sub.add_parser("notify")
    nt.add_argument("--db", default=None)
    nt.add_argument("--run-id", default=None, dest="run_id")
    nt.add_argument("--exit-code", type=int, required=True, dest="exit_code")
    nt.add_argument("cmd", nargs=argparse.REMAINDER)
    nt.set_defaults(func=cmd_notify)

    sk = sub.add_parser("smoke")
    sk.add_argument("--db", default=None)
    sk.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    sk.add_argument("--live", action="store_true")
    sk.set_defaults(func=cmd_smoke)

    dc = sub.add_parser("doctor")
    dc.add_argument("--db", default="data/jester.db")
    dc.set_defaults(func=cmd_doctor)

    re_ = sub.add_parser("reembed")
    re_.add_argument("--db", default="data/jester.db")
    re_.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    re_.add_argument(
        "--pending", action="store_true", help="retry all needs_reembed nuggets"
    )
    re_.set_defaults(func=cmd_reembed)

    rs_ = sub.add_parser("resynth")
    rs_.add_argument("--db", default="data/jester.db")
    rs_.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    rs_.add_argument("--id", type=int, required=True)
    rs_.set_defaults(func=cmd_resynth)

    mg = sub.add_parser("migrate")
    mg.add_argument("--db", default="data/jester.db")
    mg.set_defaults(func=cmd_migrate)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
