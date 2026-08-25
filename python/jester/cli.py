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
from jester.config import load_config
from jester.embed import select_embedding
from jester.fetchers import FixtureFetcher, MOCK_SAMPLE  # noqa: F401 (re-export)
from jester.llm import (
    PROMPT_VERSIONS,
    RUBRIC_VERSION,
    select_critic_llm,
    select_extractor_llm,
    select_synthesizer_llm,
)
from jester.notify import dispatch_notify
from jester.ops import compute_floor_flags
from jester.prefilter import prefilter_comment
from jester.scoring import compute_overall
from jester.triviality import judge_trivial
from jester.store import (
    ConcurrentRunError,
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
        os.path.join(os.path.dirname(db_path), os.path.splitext(os.path.basename(db_path))[0] + ".qdrant"),
        embed,
    )


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
            started = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            actual = max(0.0, round((datetime.now(timezone.utc) - started).total_seconds(), 3))
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


def cmd_run(args):
    cfg = load_config(args.config)
    db = open_db(args.db)

    # R25: reap orphaned 'running' rows from crashed sessions, then take the lock.
    reap_running(db)
    origin = os.environ.get("JESTER_ORIGIN", "manual")
    try:
        start_run(
            db,
            args.run,
            models_used={
                "models": ["fake-llm", "fake-synth", "fake-critic"],
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
        checkpoints.append({"phase": phase, "at": datetime.now(timezone.utc).isoformat()})

    rows = pending_batches(db)
    if not rows:
        # M1.1 mock form: batches come from a Fetcher; the M1.2 skip-list
        # decides what is actually new (re-fetches yield zero new batches).
        print("no pending batches; fetching via FixtureFetcher")
        for b in FixtureFetcher().fetch():
            new = enqueue_new_only(db, b["platform"], b["source"], b["thread_id"], b["comments"])
            if new == 0:
                print(f"skip-list: thread {b['thread_id']} already ingested; 0 new comments")
        rows = pending_batches(db)

    # M1.3/R35: pre-filter before extraction; per-heuristic funnel recorded.
    raw_meta, raw_comments = [], []
    for r in rows:
        for c in json.loads(r["comments"]):
            raw_meta.append((r["platform"] or "reddit", r["source"] or "", r["thread_id"] or "", c.get("fingerprint")))
            raw_comments.append(
                {
                    "body": c.get("body", ""),
                    "fingerprint": c.get("fingerprint"),
                    "score": c.get("upvotes", 0),
                }
            )

    meta, comments = [], []
    funnel = {"kept": 0}
    used_per_thread = {}
    for i, m in enumerate(raw_meta):
        thread_id = m[2]
        v = prefilter_comment(raw_comments[i], cfg.thresholds, already_used=used_per_thread.get(thread_id, 0))
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
        n_batches=len(rows),
        n_posts=len(rows),
        prefilter_funnel=funnel,
    )

    llm = select_extractor_llm(cfg.thresholds)
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

    for r in rows:
        mark_batch_processed(db, r["id"])
        mark_comments_ingested(
            db,
            r["thread_id"],
            [c.get("fingerprint") for c in json.loads(r["comments"])],
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
    synthesizer = Synthesizer(db, cfg.thresholds)
    ideas = synthesizer.run(
        select_synthesizer_llm(cfg.thresholds),
        select_critic_llm(cfg.thresholds),
        run_id=args.run,
    )
    for idea in ideas:
        verdict = "PASS" if idea.scores.overall >= cfg.thresholds.good_idea_min else "REVIEW"
        print(f"idea #{idea.id}: {idea.title}  overall={idea.scores.overall:.1f}  [{verdict}]")
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
    competition = float(parts[3]) if len(parts) > 3 and parts[3] not in ("", "-", "unchecked") else None
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
    comp = "unchecked" if not row["competition_checked"] else f"{row['competition']:.1f}"
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
        comp = "unchecked" if not i["competition_checked"] else f"{i['competition']:.1f}"
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
        if r["prefilter_funnel"]:
            line += f"  filter={r['prefilter_funnel']}"
        if r["top_near_misses"]:
            line += f"  near={r['top_near_misses']}"
        if r["floor_flags"]:
            line += f"  flags={r['floor_flags']}"
        print(line)


def cmd_retention(args):
    """§14 TTL sweep. R31: when the write lock is held by an active run,
    skip-and-log (exit 0) rather than fighting for the lock."""
    db = open_db(args.db)
    try:
        purged = retention_sweep(db)
    except __import__("sqlite3").OperationalError as exc:
        if "locked" in str(exc).lower():
            print(f"retention skipped: write lock held ({exc})")
            return
        raise
    print(
        f"retention sweep: wiped raw_text on {purged['nuggets_raw']} nugget(s); "
        f"dropped {purged['batches']} completed batch(es)"
    )


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
        print("smoke --live requires the external stack (CloakBrowser/Qdrant/Ollama, §4); deferred")
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
    backlog = db.execute("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1").fetchone()[0]
    if backlog:
        findings.append(
            f"reembed_backlog={backlog}: nugget(s) excluded from dedup/clustering "
            "until reembedded"
        )
    orphans = 0
    for (keys_json,) in db.execute("SELECT supporting_nuggets FROM ideas").fetchall():
        for k in json.loads(keys_json or "[]"):
            row = db.execute("SELECT 1 FROM nuggets WHERE unique_key=?", (k,)).fetchone()
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
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
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
            mark_reembedded(db, key, f"emb:{abs(hash(key)) % (2 ** 63)}")
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
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
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
        print(f"resynth FAIL: raw_text NULL (retention-purged?) or missing for: {missing_raw}")
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
        print("resynth: subset did not form a cluster (lone-nugget rule R50); nothing created")
        return
    for new in created:
        print(f"resynth OK: new idea #{new.id}: {new.title}")


def cmd_migrate(args):
    """R31 concurrency contract: refuse while a run is active; otherwise apply
    idempotent schema migrations via open_db."""
    db = open_db(args.db)
    running = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
    if running:
        print(f"migrate REFUSED: {running} run(s) still active (R31)")
        sys.exit(1)
    version = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    print(f"migrate OK: schema_version={version}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="jester")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--db", default="data/jester.db")
    r.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    r.add_argument("--run", default="mock-run")
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list")
    l.add_argument("--db", default="data/jester.db")
    l.set_defaults(func=cmd_list)

    e = sub.add_parser("eval")
    e.add_argument("--good", default=str(Path(__file__).resolve().parents[2] / "eval" / "known_good.md"))
    e.add_argument("--bad", default=str(Path(__file__).resolve().parents[2] / "eval" / "known_bad.md"))
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
    rt.set_defaults(func=cmd_retention)

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
    re_.add_argument("--pending", action="store_true", help="retry all needs_reembed nuggets")
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
