"""CLI: `jester run` (mock pipeline over ingest_batch) and `jester list`."""

import argparse
import json
import os
import re
import sqlite3
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
    FakeLLM,
    provider_for,
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
from jester.vector import (
    UnavailableVectorStore,
    VectorDimensionMismatch,
    VectorStore,
)
from jester.worker import ingest as worker_ingest


def collection_for(db_path: str, base: str = "nuggets") -> str:
    """Collection name for one database on a SHARED Qdrant server.

    Namespaced by the database file's stem, because the server has no other
    notion of which database a vector belongs to. Without this every database
    shares one collection: running a cycle against a throwaway `--db` wrote
    240 vectors straight into the production dedup space, where they stayed
    and influenced dedup for real runs. The per-database local-file mode never
    had this problem — the isolation came free with the path.

    No special case for the shipped database: a rule with an exception is how
    the next person gets surprised.
    """
    if db_path == ":memory:":
        return f"{base}__memory"
    stem = os.path.splitext(os.path.basename(os.path.abspath(db_path)))[0]
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_") or "db"
    return f"{base}__{slug}"


def _vector_for(cfg, db_path):
    """§37.17/§37.24: shared server via JESTER_QDRANT_URL, else per-DB path.
    In-memory databases get an in-memory store (no :memory:.qdrant file)."""
    qdrant_url = os.environ.get("JESTER_QDRANT_URL")
    embed = select_embedding(cfg.thresholds)
    if qdrant_url:
        return VectorStore.for_url(
            qdrant_url, embed, collection=collection_for(db_path)
        )
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
            return VectorStore.for_url(
                qdrant_url, embed, collection=collection_for(db_path, "ideas")
            )
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
    # Positional slice rather than a fixed unpack: the row grew a batch id so a
    # degraded extraction can leave its own batch queued, and this only ever
    # wanted the first field.
    for row in meta:
        key = row[0] or "unknown"
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
                    # Which batch this comment came from. Needed so a run that
                    # crosses its extractor allowance can leave the affected
                    # batches PENDING instead of marking them done with
                    # stand-in output inside.
                    r["id"],
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

    # Which extractor was ASKED for. A configured stand-in is a choice; a
    # stand-in result from a configured real model is a failure, and the two
    # must not be archived the same way.
    extractor_provider = provider_for(cfg.thresholds, "extractor")
    nuggets, degraded_batches, degraded = [], set(), 0
    # Which batch each nugget came from. The archivist works on a flat list and
    # can hand nuggets back undecided (see below); without this there is no way
    # to tell which batch to leave queued for them.
    batch_of = {}
    for (platform, source, thread_id, _fp, batch_id), c in zip(meta, comments):
        produced = extract(
            [c],
            llm,
            platform=platform,
            thread_id=thread_id,
            source_url=source,
            run_id=args.run,
        )
        for n in produced:
            if extractor_provider != "fake" and n.extractor_model == FakeLLM.name:
                # R29/R40: the model was asked and did not answer — most often
                # because a 1,000-request daily allowance ran out partway
                # through a 17,000-comment queue. Archiving the stand-in's
                # truncated body here would put a row in the corpus that looks
                # exactly like a real extraction and is not one. Synthesis has
                # refused to do this for months; extraction now does too.
                degraded += 1
                degraded_batches.add(batch_id)
                continue
            batch_of[n.unique_key] = batch_id
            nuggets.append(n)

    # R37: tag triviality pre-archive. Floor, not gate — nothing is filtered.
    for n in nuggets:
        n.trivial = judge_trivial(n.raw_text or "").trivial

    try:
        vector = _vector_for(cfg, args.db)
    except VectorDimensionMismatch:
        # NOT deferrable, and the distinction matters. A width mismatch is a
        # configuration error that every later run will hit identically, so
        # degrading to "defer everything" would turn a loud, fixable misconfig
        # into a pipeline that reports `completed` forever and archives
        # nothing. This exception is raised at open time for exactly that
        # reason; let it end the run.
        raise
    except Exception as exc:  # noqa: BLE001 - transport/backend unavailability
        # Opening the store touches the network (collection_exists), so a
        # stopped Qdrant fails HERE, before a batch is read — a different line
        # from the mid-run timeout but the same event. Hand the archivist a
        # stand-in that refuses every lookup, and the run defers all of its
        # nuggets, keeps every batch queued and finishes saying
        # DEDUP_UNAVAILABLE, instead of dying with the queue untouched.
        print(f"vector store unavailable: {exc}")
        vector = UnavailableVectorStore(exc)
    archivist = Archivist(db, vector, cfg.thresholds)
    kept = archivist.run(nuggets)

    # Nuggets the archivist could not check for duplicates — Qdrant unreachable
    # or timing out. Their batches go back in the queue rather than being marked
    # processed, so the material is re-extracted on a pass when the vector store
    # is answering, instead of being archived unverified or silently dropped.
    if archivist.deferred:
        for n, _why in archivist.deferred:
            bid = batch_of.get(n.unique_key)
            if bid is not None:
                degraded_batches.add(bid)
        print(
            f"deferred {len(archivist.deferred)} nugget(s): dedup lookup "
            f"unavailable ({archivist.deferred[0][1]})"
        )
        update_run_summary(db, args.run, n_dedup_deferred=len(archivist.deferred))

    for r, payload in readable:
        if r["id"] in degraded_batches:
            # Leave it queued, and leave its comments OUT of the skip-list:
            # marking either would make this material unreachable forever on
            # the strength of a rate limit.
            continue
        mark_batch_processed(db, r["id"])
        mark_comments_ingested(
            db,
            r["thread_id"],
            [c.get("fingerprint") for c in payload if isinstance(c, dict)],
        )
    if degraded:
        # No silent caps. "kept 812" on a run that quietly dropped 4,000
        # comments to a rate limit is the same line as a clean run.
        print(
            f"NOT archived: {degraded} comment(s) whose extraction fell back to "
            f"the deterministic stand-in; {len(degraded_batches)} batch(es) stay "
            "queued for a run with allowance left"
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
    if synthesizer.skipped_fallback:
        # "0 ideas because the model was rate-limited" and "0 ideas because
        # nothing qualified" are different nights, and only one of them is
        # worth acting on. The nuggets stay unclaimed, so a later run with a
        # working model picks them up.
        print(
            f"NOT archived: {synthesizer.skipped_fallback} group(s) whose "
            "synthesis fell back to the deterministic stand-in — those nuggets "
            "stay queued rather than becoming ideas nobody can trust"
        )
    if getattr(synthesizer, "split_groups", 0):
        # Several ideas from one thread is deliberate, not a dedup miss: the
        # thread outgrew max_nuggets_per_idea and was cut into chunks.
        print(
            f"split {synthesizer.split_groups} oversized thread group(s) into "
            f"chunks of {cfg.thresholds.max_nuggets_per_idea} nuggets "
            "(max_nuggets_per_idea)"
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
        dedup_deferred=len(archivist.deferred),
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
    _export_csvs(args, cfg, db)

    # Only a broken pipeline fails the exit code (§13.1); quality flags are advisory.
    if "FAILED_BATCHES" in flags:
        sys.exit(1)

    # What the caller needs to decide whether to continue. `jester treat`
    # reads `rate_limited` to tell "one call failed and we substituted" from
    # "the quota is gone and everything after this would be a substitution" —
    # the second must stop the drain rather than process the whole backlog
    # into mechanical output and mark it done.
    rate_limited = sum(
        int(getattr(agent, "rate_limited", 0) or 0)
        for agent in (llm, synth_llm, critic_llm)
    )
    resets = [
        float(getattr(agent, "retry_after", None) or 0)
        for agent in (llm, synth_llm, critic_llm)
        if getattr(agent, "retry_after", None)
    ]
    return {
        "ok": True,
        "run_id": args.run,
        "nuggets": len(kept),
        "ideas": len(ideas),
        "flags": flags,
        "rate_limited": rate_limited,
        # The LONGEST reset any agent saw: they share one key, so waiting out
        # the shortest would just be refused again.
        "retry_after": max(resets) if resets else None,
    }


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
        f"aged text/seller out of {purged['listings']} listing observation(s); "
        f"pruned {pruned} export dir(s)"
    )


def _record_run_crash(args, exc) -> None:
    """Best-effort: write `exc` onto whichever run row is still 'running'.

    Every failure here is swallowed. This runs while the process is already
    dying, and an error-reporting path that raises would replace the real
    traceback with its own — the exact failure it exists to prevent.
    """
    db_path = getattr(args, "db", None)
    if not db_path:
        return
    try:
        db = open_db(db_path)
        try:
            run_id = active_run(db)
            if run_id is None:
                return
            finish_run(
                db,
                run_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - never mask the original failure
        pass


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
        timeout=_ingest_timeout(args),
    )
    if res.get("output"):
        print(res["output"])
    if not res["ok"]:
        print("ingest failed: " + (res.get("error") or f"exit {res.get('code')}"))
        # A FAILED fetch is not an empty one. The worker writes
        # listing_observation as it walks, so a run killed by the ingest
        # timeout has usually already recorded thousands of listings. Exiting
        # here without exporting left them in the archive and out of the CSVs:
        # one real cycle scraped 3214 observations, hit the 3600s cap, exited
        # 1 before reaching the export, and the combined sheet went on
        # describing the previous run while the archive held 7979 rows against
        # the sheet's 4765. The exit code still reports the failure.
        _export_csvs(args)
        sys.exit(1)
    queued = res.get("queued_new_comments")
    print(
        f"queued {queued if queued is not None else 'an unreported number of'} new comment(s)"
    )

    # Export the batch that was just scraped.
    #
    # `jester run` has exported since export_after_run existed, but `run`
    # processes the QUEUE - it never touches listing_observation. Real-estate
    # rows are written by this command and by nothing else, so a scheduled
    # cycle could ingest listings every thirty minutes and the CSVs beside the
    # archive would keep describing whenever someone last ran the exporter by
    # hand. Same flag, same output directory, so a batch and its CSVs move
    # together.
    #
    # After the summary and inside a try, for the reason `run` has the same
    # shape: a scrape that succeeded must not be reported as failed because
    # writing a spreadsheet afterwards did not.
    _export_csvs(args)
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


def _ingest_timeout(args):
    """Seconds the worker may run, from config unless the caller overrides.

    Returns None when neither says anything, which `worker.ingest` reads as
    "no opinion" and answers with its own default.
    """
    override = getattr(args, "timeout", None)
    if override:
        return override
    try:
        seconds = load_config(args.config).thresholds.ingest_timeout_seconds
    except Exception:  # noqa: BLE001 - a broken config must not stop the fetch
        return None
    return seconds or None


def _export_csvs(args, cfg=None, db=None):
    """Leave the CSVs beside the archive, if the config asks for it.

    ONE implementation, called by `run`, `ingest` and `cycle`, because all
    three end by leaving a spreadsheet behind and the copies had already
    drifted: `ingest` reported listings and `run` did not, so the same export
    was described two different ways depending on which command triggered it.

    It never raises. An export is a report on work already committed to the
    archive; a spreadsheet that could not be written must not turn a finished
    scrape into a failed one.
    """
    try:
        cfg = cfg or load_config(args.config)
    except Exception as exc:  # noqa: BLE001 - config problems are reported, not fatal here
        print(f"export skipped: config unreadable ({exc})")
        return
    if not cfg.thresholds.export_after_run:
        return
    out_dir = getattr(args, "exports", None) or default_export_dir(args.db)
    try:
        manifest = export_csv(
            db or open_db(args.db),
            out_dir,
            rows_per_file=cfg.thresholds.export_rows_per_file,
        )
        c = manifest["counts"]
        # Listings are named because this line is what a nightly log shows. It
        # read "exported 3 comment(s), 0 nugget(s), 0 idea(s)" on a run that
        # had just written 4132 listings, and an operator scanning that log
        # would conclude the real-estate export had not happened. Printed only
        # when there are any, so archives without listings read as before.
        listings = c.get("listings", 0)
        extra = f", {listings} listing(s)" if listings else ""
        print(
            f"exported {c['comments']} comment(s), {c['nuggets']} nugget(s), "
            f"{c['ideas']} idea(s){extra} to {out_dir}"
        )
    except Exception as exc:  # noqa: BLE001 - reporting, not a pipeline gate
        print(f"export skipped: {exc}")


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
        timeout=_ingest_timeout(args),
    )
    if res.get("output"):
        print(res["output"])
    if not res["ok"]:
        # Processing an empty queue after a failed fetch would report success
        # for a cycle that fetched nothing.
        print("ingest failed: " + (res.get("error") or f"exit {res.get('code')}"))
        # A FAILED fetch is not an empty one. The worker writes
        # listing_observation as it walks, so a run killed by the ingest
        # timeout has usually already recorded thousands of listings. Exiting
        # here without exporting left them in the archive and out of the CSVs:
        # one real cycle scraped 3214 observations, hit the 3600s cap, exited
        # 1 before reaching the export, and the combined sheet went on
        # describing the previous run while the archive held 7979 rows against
        # the sheet's 4765. The exit code still reports the failure.
        _export_csvs(args)
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


def cmd_cluster(args):
    """Regroup the archive into semantic themes (chunk -> embed -> cluster).

    The counterpart to `run`, which groups nuggets by the thread they were
    scraped from. This groups them by what they SAY, so a complaint on Hacker
    News and the same complaint on Reddit land together.
    """
    from jester import clustering
    from jester.clustering import FakeEmbeddingRefused, cluster_archive

    cfg = load_config(args.config)
    db = open_db(args.db)
    # argparse defaults are None so an unset flag is distinguishable from one
    # set to the same value the module already uses; resolve here rather than
    # writing the numbers down twice.
    threshold = (
        clustering.DEFAULT_THRESHOLD if args.threshold is None else args.threshold
    )
    neighbours = (
        clustering.DEFAULT_NEIGHBOURS if args.neighbours is None else args.neighbours
    )
    min_size = (
        clustering.DEFAULT_MIN_SIZE if args.min_size is None else args.min_size
    )
    try:
        res = cluster_archive(
            db,
            cfg.thresholds,
            run_id=args.run or "cluster",
            threshold=threshold,
            neighbours=neighbours,
            min_size=min_size,
            limit=args.limit,
        )
    except FakeEmbeddingRefused as exc:
        # Not a crash — a refusal, and the operator needs to know which knob
        # fixes it rather than a stack trace.
        print(f"cluster refused: {exc}")
        sys.exit(2)

    if not res.get("clusters"):
        print(
            f"no clusters formed from {res.get('nuggets', 0)} nugget(s) — "
            f"nothing reached {min_size} members at threshold "
            f"{threshold}. Lower --threshold or --min-size to group more "
            "loosely."
        )
        return res
    print(
        f"{res['clusters']} cluster(s) from {res['nuggets']} nugget(s) "
        f"[{res['embedding_model']}]"
    )
    # R55: say what did NOT group. A pass that clusters 8% of the archive has
    # told you the threshold is too tight, and reporting only the clusters
    # hides it.
    print(
        f"  grouped {res['grouped_nuggets']}, "
        f"ungrouped {res['ungrouped_nuggets']} "
        f"(threshold {res['threshold']}, min size {res['min_size']})"
    )
    return res


def cmd_identify(args):
    """Recover the community a nugget came from, where the row already says.

    Dry by default: this rewrites archived provenance, so the operator sees
    what it would claim before it claims anything.
    """
    from jester.identify import identify

    db = open_db(args.db)
    report = identify(db, apply=args.apply)
    for line in report.lines():
        print(line)
    if not args.apply and report.n_identified:
        print("\nnothing was written — re-run with --apply")
    return {
        "ok": True,
        "identified": report.n_identified,
        "unidentified": report.n_unidentified,
        "applied": report.applied,
    }


def cmd_backup(args):
    """Snapshot the archive, verify the snapshot, prune old ones.

    Verification is not optional here: a backup nobody has opened is a hope,
    not a backup, and a mid-write filesystem copy produces a file that opens
    fine and is corrupt.
    """
    from jester.backup import backup_db, prune_backups, verify_backup

    if args.verify:
        got = verify_backup(args.verify)
        if not got["ok"]:
            print(f"backup UNUSABLE: {got['error']}")
            sys.exit(1)
        counts = ", ".join(f"{k}={v}" for k, v in got["counts"].items())
        print(f"backup OK (integrity {got['integrity']}) — {counts}")
        return got

    res = backup_db(args.db, args.out or "", compress=not args.no_compress)
    if not res["ok"]:
        print(f"backup failed: {res['error']}")
        sys.exit(1)
    ratio = ""
    if res.get("source_bytes"):
        ratio = f" ({res['bytes'] * 100 // res['source_bytes']}% of source)"
    print(f"wrote {res['path']} — {res['bytes']:,} bytes{ratio}")

    check = verify_backup(res["path"])
    if not check["ok"]:
        # Written but unreadable is worse than not written: it looks like
        # protection and is not.
        print(f"  VERIFY FAILED: {check['error']}")
        sys.exit(1)
    counts = ", ".join(f"{k}={v}" for k, v in check["counts"].items())
    print(f"  verified: integrity {check['integrity']}, {counts}")
    print(f"  note: {res['detail']}")

    removed = prune_backups(
        args.out or str(Path(args.db).parent / "backups"),
        keep=args.keep,
        stem=Path(args.db).stem,
    )
    if removed:
        print(f"  pruned {len(removed)} old backup(s), keeping {args.keep}")
    return res


def cmd_treat(args):
    """Process everything the scrapers have queued, then stop.

    THE SPLIT THIS COMPLETES. Scraping is cheap, polite and bounded by how
    often we are willing to knock on someone's door. Treatment is one LLM call
    per comment, bounded by a quota that resets on someone else's clock.
    `cycle` ran both together, which meant the expensive half set the pace for
    the cheap half — and a quota-exhausted tick still walked every source and
    then had nothing to process it with.

    So: `ingest` scrapes on its own cadence and the queue grows. `treat` drains
    the queue whenever the quota allows, and the queue is the buffer between
    them.

    Refuses to run degraded. Every Groq agent falls back to a deterministic
    stand-in on failure, which is right for one bad call and catastrophic under
    an exhausted quota: the whole backlog would be processed into mechanical
    output and MARKED DONE, with no second chance. So the run stops the moment
    the limit is hit and leaves the rest queued.
    """
    from jester import llm_quota

    db = open_db(args.db)
    # The console's Queue page reads the block back under this same key.
    provider = llm_quota.TREATMENT_PROVIDER

    pending = len(pending_batches(db))
    if not pending:
        print("nothing to treat: the queue is empty")
        return {"ok": True, "treated": 0, "pending": 0}

    # Ask the recorded state BEFORE spending a request. This is what makes it
    # safe to schedule `treat` frequently: when the quota is known to be gone,
    # it costs one SQLite read and exits.
    waiting = llm_quota.blocked_for(db, provider)
    if waiting > 0 and not args.force:
        st = llm_quota.status(db, provider)
        print(
            f"{provider} quota exhausted for another {llm_quota.human(waiting)} "
            f"(until {st['blocked_until']})"
        )
        print(f"  {pending} batch(es) waiting; they keep until it resets")
        if st["reason"]:
            print(f"  reason: {st['reason'][:160]}")
        return {"ok": True, "treated": 0, "pending": pending, "blocked": True}

    print(f"treating {pending} pending batch(es)")
    if not getattr(args, "run", None):
        args.run = "treat-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result = cmd_run(args)

    # cmd_run stamps the agents it used onto the result so the caller can see
    # whether the work was really done or quietly substituted.
    limited = (result or {}).get("rate_limited") or 0
    retry_after = (result or {}).get("retry_after")
    left = len(pending_batches(db))

    if limited:
        stamp = llm_quota.record_block(
            db, provider,
            retry_after if retry_after is not None else 3600,
            reason=f"{limited} call(s) lost to the rate limit during {args.run}",
        )
        print(
            f"{provider} rate limit reached after {limited} call(s); "
            f"not treating the remaining {left} batch(es)"
        )
        print(f"  will retry after {stamp}")
        # Exit 0: stopping on an exhausted quota is the design working, not a
        # failure. A non-zero exit here would make every scheduler on the box
        # start emailing about a healthy system.
        return {"ok": True, "treated": pending - left, "pending": left, "blocked": True}

    llm_quota.clear_block(db, provider)
    print(f"treated {pending - left} batch(es); {left} still pending")
    return {"ok": True, "treated": pending - left, "pending": left, "blocked": False}


def cmd_schedule(args):
    """D-2: register / inspect / remove the nightly job with the host scheduler."""
    # Every branch here has to resolve the task the same way. `status` and
    # `run` used to ignore --command and always act on the cycle task, which
    # was invisible while cycle was the only one anybody scheduled and became
    # wrong the moment a second task existed: `schedule run --command signals`
    # cheerfully started the nightly pipeline instead.
    _cmd = getattr(args, "command", None) or "cycle"
    _task = _schedule.TASK_FOR_COMMAND.get(_cmd, _schedule.TASK_NAME)
    if args.action == "status":
        st = _schedule.status(_task)
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
        command = getattr(args, "command", None) or "cycle"
        task = _schedule.TASK_FOR_COMMAND.get(command, _schedule.TASK_NAME)
        # `treat` takes none of the scrape ceilings — it drains whatever the
        # scrape half queued, and a --max-posts flag on it would be accepted
        # and ignored.
        flags = () if command == "treat" else _schedule.scrape_flags(scope)
        res = _schedule.install(
            at=args.at,
            db=args.db,
            config=args.config,
            task_name=task,
            every=args.every,
            extra=flags,
            command=command,
        )
    elif args.action == "remove":
        res = _schedule.remove(_task)
    else:  # run
        res = _schedule.run_now(_task)
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


def cmd_property_alerts(args):
    """Evaluate property alert criteria against recent listings."""
    from jester.agents.property_alerts import AlertCriteria, evaluate_alerts, format_alert_digest

    criteria = AlertCriteria(
        governorate=args.governorate,
        city=args.city,
        price_min=args.price_min,
        price_max=args.price_max,
        rooms_min=args.rooms_min,
        rooms_max=args.rooms_max,
        surface_min=args.surface_min,
        surface_max=args.surface_max,
        property_type=args.property_type,
        transaction_type=args.transaction_type,
    )

    db = open_db(args.db)
    alerts = evaluate_alerts(db, criteria, lookback_hours=args.lookback_hours)

    if args.format == "json":
        import json
        print(json.dumps([vars(a) for a in alerts], indent=2, default=str))
    else:
        print(format_alert_digest(alerts))


def cmd_property_analytics(args):
    """Run property market analytics."""
    from jester.agents.property_analytics import run_full_analytics

    results = run_full_analytics(args.db)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Analytics written to {args.output}")
    else:
        print(json.dumps(results, indent=2, default=str))


def cmd_property_dedup(args):
    """Check cross-portal duplicates for a specific listing."""
    from jester.agents.property_dedup import check_duplicate
    from jester.vector import VectorStore
    from jester.embed import select_embedding
    from jester.config import load_config
    import os

    cfg = load_config(args.config)
    embed = select_embedding(cfg.thresholds)

    qdrant_url = os.environ.get("JESTER_QDRANT_URL")
    if qdrant_url:
        vec_store = VectorStore.for_url(
            qdrant_url, embed, collection="properties__jester"
        )
    else:
        vec_store = VectorStore.open(
            os.path.join(os.path.dirname(args.db), "jester.properties.qdrant"),
            embed,
        )

    db = open_db(args.db)
    result = check_duplicate(db, vec_store, args.portal, args.listing_id)

    print(json.dumps(vars(result), indent=2, default=str))


def _db_path_of(db) -> str:
    """The file a sqlite connection is attached to.

    Needed because `evaluate_doctor` is handed a connection, not a path, and
    the collection name is derived from the path. Asking the connection avoids
    changing a signature every caller and test already depends on.
    """
    try:
        for _seq, name, file in db.execute("PRAGMA database_list"):
            if name == "main":
                return file or ":memory:"
    except Exception:  # noqa: BLE001
        pass
    return ":memory:"


def _qdrant_point_count(url, collection=None):
    """§33 #4 parity probe; overridable in tests via monkeypatch."""
    from jester.vector import VectorStore

    return VectorStore.count_points_at(url, collection or "nuggets")


#: How long a scheduled run may be absent before that is itself a finding.
#: Twenty-six rather than twenty-four so a run that drifts an hour, or starts
#: late because the machine was asleep, does not cry wolf every morning.
SCHEDULE_SILENCE_HOURS = 26


def _schedule_silence(db):
    """A dead man's switch for the schedule.

    Every other check here reacts to something that went WRONG. This one reacts
    to nothing happening at all, which is a different failure and the one the
    notifier cannot see: `JESTER_NOTIFY_CMD` fires when a run fails, and a run
    that never started never fails.

    That is the exact shape Task Scheduler produces on a laptop. Its defaults
    refuse to start on battery and never retry a slot missed while asleep, so
    the symptom is a quiet gap: no error, no row, no notification. Nothing in
    the archive could tell that from a genuinely quiet week.
    """
    from datetime import datetime, timedelta, timezone

    row = db.execute(
        "SELECT run_id, started_at FROM runs WHERE run_id NOT LIKE 'mock%' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    # Indexed, not keyed: this connection hands back plain tuples, which is
    # why every other check in this function reads `.fetchone()[0]`.
    if row is None or not row[1]:
        # Never run at all is a real state, but it is what a fresh checkout
        # looks like, and saying "the schedule is silent" to someone who has
        # not installed one yet is noise.
        return []
    raw = str(row[1]).replace(" ", "T")
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return []
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    gap = datetime.now(timezone.utc) - last
    if gap < timedelta(hours=SCHEDULE_SILENCE_HOURS):
        return []
    hours = int(gap.total_seconds() // 3600)
    return [
        f"no run in {hours}h (last: {row[0]}). A scheduled run that "
        "never starts never fails, so nothing else here would say so — check "
        "`jester schedule status`, and that the machine was awake and not on "
        "battery."
    ]


def evaluate_doctor(db):
    """v3.8 #4 / §30 #2 health checks over SQLite. Every finding here is a
    silent-degradation risk (R40/R55): stuck runs, failed batches, a growing
    reembed_backlog, orphaned idea citations, and — when a shared Qdrant is
    configured (JESTER_QDRANT_URL) — point-per-row parity with the archive."""
    findings = []
    stale = db.execute("SELECT COUNT(*) FROM runs WHERE status='running'").fetchone()[0]
    if stale:
        findings.append(f"{stale} run(s) stuck in 'running' (crashed session?)")
    findings += _schedule_silence(db)
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
        # The collection is namespaced per database, so the parity probe has
        # to ask about THIS database's collection. Probing the bare "nuggets"
        # name reported 0 points against a healthy 1,771-point store and
        # raised a mismatch finding for a system that was fine.
        collection = collection_for(_db_path_of(db))
        try:
            points = _qdrant_point_count(qdrant_url, collection)
        except Exception as exc:
            findings.append(f"qdrant parity check failed: {exc}")
        else:
            if points != rows:
                findings.append(
                    f"qdrant parity mismatch in {collection}: "
                    f"{rows} nugget row(s) vs {points} point(s)"
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

    # --all re-embeds the WHOLE archive, not just the failure backlog. Needed
    # whenever the embedding backend changes: vectors written by a different
    # model are not comparable with new ones, and a backlog drain would leave
    # the archive half in one space and half in another — which reads as a
    # working store and returns nonsense neighbours.
    if getattr(args, "all", False):
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT unique_key, raw_text FROM nuggets ORDER BY id"
        ).fetchall()
        print(f"re-embedding all {len(rows)} nugget(s)")
    else:
        rows = pending_reembeds(db)
    if not rows:
        print("reembed OK: backlog empty")
        return

    cfg = load_config(args.config)
    vector = _vector_for(cfg, args.db)
    # Which backend is about to write, said out loud: re-embedding with the
    # wrong one is silent and only shows up later as nonsense neighbours.
    # getattr throughout — a test double is a vector store without an embedder.
    _emb = getattr(vector, "embed", None)
    if _emb is not None:
        print(f"embedding backend: {type(_emb).__name__} "
              f"(dim {getattr(_emb, 'dim', '?')})")
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


def cmd_signals(args):
    """the collector: the problem-signal collector.

    Sub-actions, each one command with an exit code, because every milestone
    is graded as "this check passes" rather than "this work happened":

        export    seed the labeled fixtures, upsert, write CSV + manifest
        check     run every fixture case through the classifier (non-zero on a miss)
        scope     write the scope/access document the owner signs
        rules     print the loaded rule set
        field-map print the field map
        load      copy SQLite -> ClickHouse and reconcile the counts (M2)
        queries   run the saved SQL and print each answer
        evidence  load, reconcile, run every query, write the review pack

    There is no live action. Live collection needs approved commercial Reddit
    API access, which is somebody else's decision, and a path that cannot
    run cannot misfire.
    """
    from jester import signals as sig
    from jester.signals import filters as sigf
    from jester.signals import scope as sigscope

    rules_path = getattr(args, "rules", None)
    try:
        rules = sigf.load_rules(rules_path)
    except sigf.RulesError as exc:
        print(f"signal rules: {exc}")
        sys.exit(1)

    out_dir = Path(args.out or (Path(args.db).resolve().parent / "exports" / "signals"))
    action = args.action or "export"

    if action == "field-map":
        print(sig.field_map_markdown())
        return

    if action == "rules":
        # This printed a v1 rule set: flat `problem`/`negative` phrase lists
        # and single `relevant_at`/`ambiguous_at` thresholds. None of those
        # survived the move to families and two audiences, so the command
        # crashed on its first line. Nothing caught it because printing the
        # rules is the one action no test asserted on.
        by_role = {}
        for fam in rules.families:
            by_role.setdefault(fam.role, []).append(fam)
        print(f"config_version {rules.config_version} · {len(rules.families)} families · "
              f"{len(rules.hard_reject)} hard reject · {len(rules.frame_phrases)} frame phrase(s)")
        for role in ("buyer_required", "buyer_boost", "pain_marker", "negative"):
            fams = by_role.get(role, [])
            if not fams:
                continue
            print(f"  {role}:")
            for fam in fams:
                print(f"    {fam.name:<16}{fam.weight:+.2f}  {len(fam.phrases):>3} phrase(s)")
        print(f"thresholds: buyer at {rules.buyer_at}, practitioner at "
              f"{rules.practitioner_at}; within {rules.ambiguous_margin} below either "
              "is kept and flagged ambiguous")
        print(f"inherited voice: weight {rules.inherited_weight}, only for replies "
              f"under {rules.inherited_max_chars} chars")
        buyers = rules.communities_for("buyer")
        content = rules.communities_for("practitioner")
        print(f"scope: {'APPROVED' if rules.approved else 'PROVISIONAL'} · "
              f"{len(buyers)} buyer + {len(content)} content community(ies) · "
              f"{len(rules.queries)} queries")
        for name in buyers:
            print(f"  buyer   {name}")
        for name in content:
            print(f"  content {name}")
        return

    if action == "scope":
        path = sigscope.write_scope(out_dir, rules)
        status = "APPROVED" if rules.approved else "PROVISIONAL — awaiting the scope owner"
        print(f"scope: {status}")
        # The 3-5 applies to the BUYER list, which is the decision being
        # signed off. Counting the content sources into that total made the
        # line read "7 (task asks 3-5)" on a scope that was inside the range.
        buyers = rules.communities_for("buyer")
        content = rules.communities_for("practitioner")
        print(f"communities: {len(buyers)} buyer (asks 3-5) + {len(content)} content "
              f"· queries: {len(rules.queries)}")
        print(f"written: {path}")
        return

    if action == "check":
        from jester.signals import fixtures as sigfx
        try:
            res = sigfx.check(rules, getattr(args, "cases", None) or None)
        except sigfx.FixtureError as exc:
            print(f"fixture set: {exc}")
            sys.exit(1)
        for c in res["cases"]:
            mark = "PASS" if c["pass"] else "FAIL"
            conf = f"{c['confidence']:.2f}" if c["confidence"] is not None else "  - "
            print(f"{mark}  {c['id']:<40}{c['category']:<11}{conf}  {c['got']}")
            if c["misses"]:
                print(f"        want {c['want']}")
                print(f"        {'; '.join(c['misses'])}")
            elif c.get("detail"):
                print(f"        {c['detail'][:110]}")
        print(f"\n{res['passed']}/{res['total']} case(s) passed · "
              f"categories: {', '.join(res['categories'])}")
        # Coverage is part of the gate: a set that quietly lost its failure
        # cases still reads 100%, which is the failure this guards against.
        if res["missing_categories"]:
            print(f"MISSING CATEGORIES: {', '.join(res['missing_categories'])}")
        if res["short_by"]:
            print(f"SHORT BY {res['short_by']} case(s) of the "
                  f"{sigfx.REQUIRED_TOTAL} the gate asks for")
        if res["failed"] or res["missing_categories"] or res["short_by"]:
            if res["failed"]:
                print(f"FAILED: {', '.join(res['failed'])}")
            sys.exit(1)
        return

    if action == "sources":
        from jester.signals import sources as sigsrc
        print("Collectors that exist, and the authority they run on:\n")
        for s in sigsrc.available():
            print(f"  {s['name']} ({s['platform']})")
            print(f"    access: {s['access']}")
            print(f"    terms:  {s['terms_url']}")
            print(f"    pace:   {s['rate']}\n")
        # Named rather than left as an absence. A missing collector that is
        # missing on purpose is a decision; a missing collector nobody
        # mentions is an oversight someone will 'fix'.
        print("  reddit — NOT BUILT ON PURPOSE. The free Data API is "
              "non-commercial and this work is commercial, so the Reddit path "
              "needs approved commercial access, which is somebody else's "
              "decision. A path that cannot legally run "
              "must not exist as code that can be called by accident.")
        return

    if action in ("digest", "reclassify"):
        from jester.signals import digest as sigdigest
        from jester.signals import run as sigrun

        db = sig.open_signals(args.db)
        try:
            if action == "reclassify":
                baseline = sigf.load_rules(args.baseline) if args.baseline else None
                res = sigrun.reclassify(db, rules, baseline=baseline,
                                        mode=args.only_mode, dry_run=not args.apply)
                print(f"{res['records']} record(s) re-scored against rule set "
                      f"v{res['config_version']}")
                if not res["comparable"]:
                    # Saying so, because the number below looks like an A/B and
                    # is not: the stored verdict was computed from the whole
                    # post and only a 600-char excerpt survives.
                    print("NOTE: compared against STORED verdicts, which were "
                          "computed from the full post. Only the excerpt is "
                          "kept, so part of any difference is truncation, not "
                          "the rules. Pass --baseline <rules.yaml> for a clean "
                          "comparison.")
                print(f"before {res['before']}")
                print(f"after  {res['after']}")
                print(f"changed {res['changed']}")
                for c in res["changes"][:25]:
                    print(f"  {c['from']:>12} -> {c['to']:<12} [{c['confidence']}] {c['title']}")
                if len(res["changes"]) > 25:
                    print(f"  … {len(res['changes']) - 25} more")
                print("applied" if res["applied"] else "dry run — pass --apply to write")
                return

            data = sigdigest.write_digest(db, out_dir, since=args.digest_since,
                                          until=args.digest_until,
                                          mode=args.only_mode or "live", rules=rules)
            print(f"digest {data['window']['from'][:10]} to {data['window']['to'][:10]} "
                  f"(mode {data['window']['mode']})")
            print(f"collected {data['collected']} · buyer {data['buyer']} · "
                  f"practitioner {data['practitioner']} · rejected {data['rejected']}")
            print(f"repeated needs: {len(data['repeated_needs'])} · "
                  f"mentioned once: {len(data['single_mentions'])} · "
                  f"runs in window: {len(data['runs'])}")
            print(f"written: {data['path']}")
            return
        finally:
            db.close()

    if action in ("run", "recover", "runs"):
        from jester.signals import run as sigrun
        from jester.signals import sources as sigsrc

        db = sig.open_signals(args.db)
        try:
            if action == "runs":
                rows = sigrun.runs(db, limit=args.limit, mode=args.only_mode)
                if not rows:
                    print("no runs recorded yet")
                    return
                print(f"{'started':<21}{'kind':<11}{'source':<13}{'coll':>5}"
                      f"{'new':>5}{'buyer':>7}{'pract':>7}{'err':>5}  status")
                for r in rows:
                    print(f"{r['started_utc'][:19]:<21}{r['kind']:<11}{r['source']:<13}"
                          f"{r['collected']:>5}{r['new']:>5}{r['buyer']:>7}"
                          f"{r['practitioner']:>7}{r['errors']:>5}  {r['status']}"
                          + (f"  ({r['note']})" if r['note'] else ""))
                return

            source = sigsrc.get_source(args.source)
            fn = sigrun.collect if action == "run" else sigrun.recover
            kwargs = dict(source_name=args.source, rules=rules, since=args.since,
                          limit_per_query=args.limit, mode="live", source=source)
            if action == "run":
                kwargs["queries"] = args.query or None
            row = fn(db, **kwargs)

            print(f"run {row['run_id']} ({row['kind']}) · {row['source']} · since {row['since']}")
            print(f"queries: {len(json.loads(row['queries']))} · "
                  f"collected {row['collected']} · new {row['new']} · "
                  f"seen again {row['seen_again']} · edited {row['edited']}")
            print(f"buyer {row['buyer']} · practitioner {row['practitioner']} · "
                  f"relevant {row['relevant']} · errors {row['errors']}")
            if row["note"]:
                print(row["note"])
            # A run that collected nothing is a valid run. Saying so out loud
            # stops the next person reading an empty day as a broken collector.
            if row["collected"] == 0:
                print("zero results — a valid run; every query was read and "
                      "returned nothing")
            print(f"status: {row['status']}")
            if row["status"] == "failed":
                sys.exit(1)
            return
        except sigsrc.SourceError as exc:
            print(f"source: {exc}")
            sys.exit(1)
        finally:
            db.close()

    if action in ("load", "queries", "evidence"):
        from jester.signals import clickhouse as ch
        from jester.signals import evidence as ev

        client = ch.Client()
        db = sig.open_signals(args.db)
        try:
            if action == "queries":
                # Read-only: answers the questions without touching the
                # archive, so it is safe to run while a load is in flight.
                failed = False
                for q in ch.run_saved_queries(client):
                    print(f"\n== {q['name']} — {q['title']}")
                    if not q["ok"]:
                        print(f"   FAILED: {q['error']}")
                        failed = True
                        continue
                    if not q["rows"]:
                        print("   (no rows)")
                        continue
                    cols = list(q["rows"][0].keys())
                    print("   " + " | ".join(cols))
                    for r in q["rows"][:15]:
                        print("   " + " | ".join(
                            str(r.get(c, ""))[:40] for c in cols))
                    if len(q["rows"]) > 15:
                        print(f"   … {len(q['rows']) - 15} more row(s)")
                if failed:
                    sys.exit(1)
                return

            if action == "load":
                res = ch.load(db, client, mode=args.only_mode)
                rec = ch.reconcile(db, client, mode=args.only_mode)
                print(f"loaded {res['rows_sent']} row(s) in {res['requests']} request(s) "
                      f"into {res['database']} at {res['url']}")
                print(f"sqlite {rec['sqlite_rows']} · clickhouse {rec['clickhouse_rows_final']} "
                      f"(final) · distinct ids {rec['clickhouse_unique_ids']}")
                # The M2 check in one line. Duplicates are the failure the
                # milestone names; un-merged parts are not a failure at all.
                print(f"duplicates: {rec['duplicates']} · "
                      f"un-merged parts: {rec['unmerged_parts']} (harmless)")
                if not rec["reconciles"]:
                    print("MISMATCH: the two stores do not agree")
                    sys.exit(1)
                if rec["sqlite_rows"] == 0:
                    # 0 == 0 reconciles, and saying so would read as a check
                    # that passed. Nothing was loaded and nothing was verified,
                    # and that is what the line has to say.
                    print(f"nothing matched mode={args.only_mode or 'all'}: "
                          "no rows loaded, nothing checked")
                    return
                print("reconciles: yes")
                return

            pack = out_dir / "evidence"
            summary = ev.write_evidence(db, pack, client=client, mode=args.only_mode)
            r = summary["reconcile"]
            print(f"sqlite {r['sqlite_rows']} · clickhouse {r['clickhouse_rows_final']} · "
                  f"duplicates {r['duplicates']} · csv {summary['csv']['rows_in_csv']}")
            bad = [q["name"] for q in summary["queries"] if not q["ok"]]
            if bad:
                print(f"QUERIES FAILED: {', '.join(bad)}")
            print(f"pack: {pack / 'README.md'}")
            if not summary["passes"]:
                sys.exit(1)
            print("evidence: PASS")
            return
        except ch.ClickHouseError as exc:
            print(f"clickhouse: {exc}")
            sys.exit(1)
        finally:
            db.close()

    if args.mode != "fixture":
        print("only --mode fixture exists today: live collection needs approved "
              "Reddit API access, which is somebody else's decision. "
              "Nothing here fakes a live run.")
        sys.exit(1)

    manifest = sig.run_fixture_export(args.db, out_dir, run_id=args.run, rules=rules)
    up = manifest["upsert"]
    print(f"upserted: {up['new']} new, {up['seen_again']} seen again, {up['edited']} edited")
    for mode, c in sorted(manifest["counts_by_mode"].items()):
        print(f"{mode}: {c['unique_collected']} unique, {c['relevant']} relevant, "
              f"{c['removed']} removed, {c['errors']} error(s)")
    xposts = manifest.get("crossposts") or []
    if xposts:
        # Reported, never merged: the same question in two communities is two
        # real records, and collapsing them would make the counts lie.
        print(f"crossposts: {len(xposts)} text(s) seen in more than one record "
              f"({', '.join(x['communities'][0] + '…' for x in xposts[:3])})")
    print(f"csv: {out_dir / manifest['csv']} ({manifest['rows_in_csv']} row(s), "
          f"reconciles={manifest['reconciles']})")
    print(f"also written: field-map.md, schema.clickhouse.sql, manifest.json in {out_dir}")


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

    sg = sub.add_parser(
        "signals",
        help="the collector problem-signal collector: fixtures -> classifier -> SQLite -> CSV",
    )
    sg.add_argument("action", nargs="?", default="export",
                    choices=["export", "check", "scope", "rules", "field-map",
                             "load", "queries", "evidence",
                             "run", "recover", "runs", "sources",
                             "digest", "reclassify"],
                    help="export (default) | check fixtures | write the scope doc | "
                         "print rules | print the field map | load into ClickHouse | "
                         "run the saved SQL | write the evidence pack | "
                         "run a live collection | recover failed queries | "
                         "list runs | list sources | write the weekly digest | "
                         "re-score the archive against the rules")
    sg.add_argument("--db", default="data/jester.db")
    sg.add_argument("--out", default=None,
                    help="output directory (default: exports/signals beside the db)")
    sg.add_argument("--rules", default=None,
                    help="rule set (default: config/signal_rules.yaml)")
    # `export` still only knows how to write fixtures. Live collection is the
    # `run` action against a named approved source, and it labels its rows
    # live — a fixture row must never be countable as a live one, so the two
    # do not share a code path.
    sg.add_argument("--mode", default="fixture", choices=["fixture"],
                    help="fixture only, for `export`; live collection is `signals run`")
    sg.add_argument("--source", default="hackernews",
                    help="which approved collector `run`/`recover` uses")
    sg.add_argument("--since", default="7d",
                    help="how far back to search: 7d, 24h, or an ISO date")
    sg.add_argument("--query", action="append", default=[],
                    help="override the configured phrases; repeatable")
    sg.add_argument("--limit", type=int, default=100,
                    help="max records per query (also the row limit for `runs`)")
    sg.add_argument("--digest-since", default="",
                    help="digest window start (ISO); default: seven days back")
    sg.add_argument("--digest-until", default="",
                    help="digest window end (ISO); default: now")
    sg.add_argument("--baseline", default=None,
                    help="reclassify: another rules file to compare against, "
                         "so the delta is the rules and not the truncation")
    sg.add_argument("--cases", default=None,
                    help="directory of labelled fixture cases for `check` "
                         "(default: the problem-signal set). A second rule set "
                         "needs a second set of cases, or it has no gate.")
    sg.add_argument("--apply", action="store_true",
                    help="reclassify: write the new verdicts (default: dry run)")
    sg.add_argument("--run", default="signals-fixture", help="run id stamped on the records")
    # Separate from --mode, which says what this command may collect. This one
    # narrows what is loaded or exported, and defaults to everything: leaving
    # rows out of an archive is how a count stops reconciling.
    sg.add_argument("--only-mode", default="", choices=["", "live", "fixture"],
                    help="load/export only rows of this mode (default: all)")
    sg.set_defaults(func=cmd_signals)

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
            "--timeout",
            type=int,
            default=None,
            help="seconds the worker may run before it is killed "
                 "(default: thresholds.ingest_timeout_seconds)",
        )
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
    # cmd_ingest has always read args.exports - it is the command that writes
    # listing_observation, so it is the one whose CSVs matter - but only `run`
    # and `cycle` declared the flag. The getattr could therefore never be
    # satisfied from the command line: `ingest --exports ...` was a usage
    # error, and the export silently went beside the db instead.
    ing.add_argument(
        "--exports",
        default=None,
        help="where export_after_run writes (default: exports/ beside the db)",
    )
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
    sc.add_argument(
        "--command",
        choices=("cycle", "ingest", "treat", "signals"),
        default="cycle",
        help="which half to schedule. `ingest` scrapes only (no LLM); `treat` "
             "drains the queue while the quota lasts; `cycle` welds both "
             "together as before; `signals` runs the problem-signal collector "
             "against its own database. Each gets its own task, so scraping "
             "can run often and cheaply while treatment waits for a quota "
             "reset.",
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
    re_.add_argument(
        "--all",
        action="store_true",
        help="re-embed EVERY nugget, not just the failure backlog — use after "
             "changing embedding_provider, since vectors from two models are "
             "not comparable",
    )
    re_.set_defaults(func=cmd_reembed)

    cl = sub.add_parser(
        "cluster",
        help="regroup the archive into semantic themes using embeddings",
    )
    cl.add_argument("--db", default="data/jester.db")
    cl.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    cl.add_argument("--run", default=None, help="run id to file the pass under")
    cl.add_argument(
        "--threshold", type=float, default=None,
        help="cosine similarity two chunks must reach to be linked "
             "(higher = tighter, fewer, cleaner groups)",
    )
    cl.add_argument(
        "--neighbours", type=int, default=None,
        help="how many neighbours each chunk may link to",
    )
    cl.add_argument(
        "--min-size", dest="min_size", type=int, default=None,
        help="drop groups smaller than this as noise",
    )
    cl.add_argument(
        "--limit", type=int, default=None,
        help="cluster only the N newest nuggets (default: the whole archive)",
    )
    cl.set_defaults(func=cmd_cluster)

    tr = sub.add_parser(
        "treat",
        help="process everything the scrapers queued, stopping if the LLM "
             "quota runs out",
    )
    tr.add_argument("--db", default="data/jester.db")
    tr.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    tr.add_argument("--run", default=None, help="run id (default: treat-<UTC>)")
    tr.add_argument("--exports", default=None)
    tr.add_argument("--max-ideas", dest="max_ideas", type=int, default=None)
    tr.add_argument(
        "--force",
        action="store_true",
        help="attempt even when the quota is recorded as exhausted",
    )
    tr.set_defaults(func=cmd_treat)

    bk = sub.add_parser(
        "backup", help="snapshot the archive and verify the snapshot"
    )
    bk.add_argument("--db", default="data/jester.db")
    bk.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    bk.add_argument("--out", default=None, help="directory (default: data/backups)")
    bk.add_argument(
        "--keep", type=int, default=14,
        help="how many backups to retain (default 14)",
    )
    bk.add_argument("--no-compress", action="store_true")
    bk.add_argument(
        "--verify", default=None, metavar="PATH",
        help="verify an existing backup instead of taking a new one",
    )
    bk.set_defaults(func=cmd_backup)

    idf = sub.add_parser(
        "identify-sources",
        help="recover the community a nugget came from, from its own source_url",
    )
    idf.add_argument("--db", default="data/jester.db")
    idf.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    idf.add_argument(
        "--apply", action="store_true",
        help="write the recovered values (default: report only)",
    )
    idf.set_defaults(func=cmd_identify)

    rs_ = sub.add_parser("resynth")
    rs_.add_argument("--db", default="data/jester.db")
    rs_.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    rs_.add_argument("--id", type=int, required=True)
    rs_.set_defaults(func=cmd_resynth)

    # Property commands (real-estate intelligence)
    pa = sub.add_parser(
        "property-alerts",
        help="evaluate property alert criteria against recent listings",
    )
    pa.add_argument("--db", default="data/jester.db")
    pa.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    pa.add_argument("--governorate", help="filter by governorate (e.g., Tunis)")
    pa.add_argument("--city", help="filter by city")
    pa.add_argument("--price-min", type=int, help="minimum price")
    pa.add_argument("--price-max", type=int, help="maximum price")
    pa.add_argument("--rooms-min", type=int, help="minimum rooms")
    pa.add_argument("--rooms-max", type=int, help="maximum rooms")
    pa.add_argument("--surface-min", type=float, help="minimum surface (m²)")
    pa.add_argument("--surface-max", type=float, help="maximum surface (m²)")
    pa.add_argument("--property-type", help="filter by property type (e.g., Appartement)")
    pa.add_argument("--transaction-type", help="sale or rent")
    pa.add_argument("--lookback-hours", type=int, default=24, help="hours to look back")
    pa.add_argument("--format", choices=["text", "json"], default="text", help="output format")
    pa.set_defaults(func=cmd_property_alerts)

    pan = sub.add_parser(
        "property-analytics",
        help="run property market analytics (price trends, duplicates, heatmap)",
    )
    pan.add_argument("--db", default="data/jester.db")
    pan.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    pan.add_argument("--output", help="output JSON file (default: stdout)")
    pan.set_defaults(func=cmd_property_analytics)

    pd = sub.add_parser(
        "property-dedup",
        help="check cross-portal duplicates for a specific listing",
    )
    pd.add_argument("--db", default="data/jester.db")
    pd.add_argument("--config", default=DEFAULT_CONFIG_DIR)
    pd.add_argument("--portal", required=True, help="portal name (e.g., tayara)")
    pd.add_argument("--listing-id", required=True, help="listing ID to check")
    pd.set_defaults(func=cmd_property_dedup)

    mg = sub.add_parser("migrate")
    mg.add_argument("--db", default="data/jester.db")
    mg.set_defaults(func=cmd_migrate)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        # A command that owns a run row must not die silently. Until now the
        # only thing that ever wrote a terminal status was `reap_running`, from
        # a LATER process — so the run that actually crashed recorded nothing,
        # and the console showed 'aborted' with a NULL error while the cause
        # sat in a log file. Stamp the reason on the row on the way out, then
        # re-raise: the traceback and the exit code are unchanged.
        _record_run_crash(args, exc)
        raise


if __name__ == "__main__":
    main(sys.argv[1:])
