"""Review commands: read-only views over the archive (F-04 split).

Pure move out of `jester/cli.py` — `cmd_eval`, `cmd_mark`, `cmd_show`,
`cmd_list`, `cmd_runs` plus their helpers. `cli.py` re-exports every name,
so `set_defaults(func=...)` wiring and `from jester.cli import ...` users
(including the console) are untouched.
"""
import json
import sys
from pathlib import Path

from jester.agents.critic import row_to_idea
from jester.config import load_config
from jester.scoring import compute_overall
from jester.store import (
    get_idea,
    get_runs,
    list_ideas,
    list_nuggets,
    nugget_by_key,
    open_db,
    update_idea,
)

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
        # encoding="utf-8": eval files are ascii today, but the default here
        # is Windows-1252 on Windows and cp437 elsewhere — the exact bug class
        # ci.yml exists because of (a requirements.txt byte pip could not
        # decode). Any non-ascii title in a future eval case would crash or
        # mis-decode depending on the host. Read explicitly, like the console
        # twin in console/api.py does.
        for line in Path(path).read_text(encoding="utf-8").splitlines():
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


_LABEL_EXAMPLE = (
    '{"unique_key": "reddit:sub:comment", "platform": "reddit", '
    '"rater": "owner", "keep": true, "gold": ["the complaint in the comment"], '
    '"predicted": [{"insight": "what the extractor said", '
    '"verdict": "supported|fabricated|unchecked"}]}'
)


def cmd_eval_extract(args):
    """S-06: extraction quality over committed JSONL labels.

    No labels is not a failure — it is the pause before the owner labels. The
    command says what to write and exits 0. Once labels exist, every aggregate
    is printed with its denominator and the published floors gate the exit.
    """
    from jester.extraction_eval import (evaluate, format_report, gate_failures,
                                        load_labels)

    path = Path(args.labels)
    if not path.exists():
        print(f"no labels yet at {path}")
        print(f"add JSONL rows, one rater verdict per line: {_LABEL_EXAMPLE}")
        return {"ok": True, "labels": 0}
    labels = load_labels(path)
    if not labels:
        print(f"no labels yet at {path} (the file is empty)")
        print(f"add JSONL rows, one rater verdict per line: {_LABEL_EXAMPLE}")
        return {"ok": True, "labels": 0}

    report = evaluate(labels)
    print(format_report(report, seed=args.seed, labels_path=path))
    failures = gate_failures(report, min_f1=args.floor,
                             max_fabrication=args.fabrication_ceiling,
                             max_nuggets_per_comment=args.max_nuggets_per_comment)
    for failure in failures:
        print(f"GATE: {failure}")
    if failures:
        print("extraction eval FAILED")
        sys.exit(1)
    print("extraction eval OK: every published floor holds")
    return {"ok": True, "report": report}


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


def _score(value) -> str:
    """One subscore for display. An unscored idea has none to format."""
    return "unscored" if value is None else f"{value:.1f}"


def cmd_show(args):
    """R26: one-idea detail view — the no-SQL half of the review loop."""
    db = open_db(args.db)
    row = get_idea(db, args.id)
    if not row:
        print(f"idea #{args.id} not found")
        sys.exit(1)
    comp = (
        "unchecked" if not row["competition_checked"] else _score(row["competition"])
    )
    print(f"IDEA #{row['id']}: {row['title']}  [{row['status']}]")
    print(
        f"  overall={_score(row['overall'])}  demand={_score(row['demand_signal'])}  "
        f"feasibility={_score(row['feasibility'])}  competition={comp}"
    )
    if row["needs_score"]:
        print("  needs_score: the critic did not answer last time; "
              "a later run re-scores it")
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
            "unchecked" if not i["competition_checked"] else _score(i["competition"])
        )
        print(
            f"IDEA #{i['id']}: {i['title']}  "
            f"overall={_score(i['overall'])}  "
            f"demand={_score(i['demand_signal'])} "
            f"feasibility={_score(i['feasibility'])} "
            f"competition={comp}  status={i['status']}"
            + ("  needs_score" if i["needs_score"] else "")
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
