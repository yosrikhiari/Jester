"""`jester evidence --run ID` writes one folder a stranger can follow (S-09).

The signals collector has had an evidence pack since it existed; the idea
pipeline produced ideas and the console showed them, but there was no way to
hand someone the question, the answer, the machine and the sources behind one
night's output. This is that folder:

    README.md       what each file answers, and how to check it yourself
    run.json        the run row: status, models, prompt versions, environment
    calls.json      every LLM call with prompt/response hashes and latency
    ideas.json      the ideas the run produced, with their scores
    nuggets.json    the nuggets the run extracted, with source URLs
    sources.json    every source URL behind those nuggets, and how many
    citations.json  idea -> nugget links, and whether each one resolves
    integrity.json  PASS/FAIL for the citation check, with the missing keys

Nothing in the README is typed in; a re-run overwrites the folder rather than
piling up versions.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jester import provenance

PREVIEW_ROWS = 10

_JSON_RUN_COLS = (
    "models_used", "env", "floor_flags", "phase_checkpoints",
    "prefilter_funnel", "top_near_misses", "platform_counts",
)


def _rows(db, sql, params=()):
    cur = db.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _decoded_run(row: dict) -> dict:
    out = dict(row)
    for col in _JSON_RUN_COLS:
        raw = out.get(col)
        if isinstance(raw, str) and raw.strip():
            try:
                out[col] = json.loads(raw)
            except ValueError:
                pass  # keep the raw cell; an unreadable one is a fact
    return out


def _safe_name(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", run_id).strip("-") or "run"


def default_out_dir(run_id: str) -> Path:
    return Path("data") / "evidence" / _safe_name(run_id)


def _table(rows, cols=None, limit=PREVIEW_ROWS) -> str:
    if not rows:
        return "_none_\n"
    cols = cols or list(rows[0].keys())
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = ""
    for row in rows[:limit]:
        cells = []
        for c in cols:
            v = row.get(c)
            text = "" if v is None else str(v)
            cells.append(text.replace("|", "\\|").replace("\n", " ")[:120])
        body += "| " + " | ".join(cells) + " |\n"
    if len(rows) > limit:
        body += f"\n_{len(rows) - limit} more row(s) in the JSON beside this file._\n"
    return head + body


def _citation_check(db, ideas):
    """Every citation an idea makes, and whether the archive holds the nugget."""
    links, missing, unsupported = [], [], []
    for idea in ideas:
        raw = idea.get("supporting_nuggets")
        try:
            keys = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            keys = None
        if not isinstance(keys, list) or not keys:
            unsupported.append({"idea_id": idea["id"],
                                "problem": "no readable supporting_nuggets"})
            continue
        for key in keys:
            found = db.execute(
                "SELECT unique_key, platform, comment_url, source_url, run_id"
                " FROM nuggets WHERE unique_key=?", (key,)).fetchone()
            if found is None:
                missing.append({"idea_id": idea["id"], "nugget_key": key})
                links.append({"idea_id": idea["id"], "nugget_key": key,
                              "exists": False})
            else:
                cols = ("unique_key", "platform", "comment_url", "source_url", "run_id")
                links.append({"idea_id": idea["id"], "nugget_key": key,
                              "exists": True, **dict(zip(cols, found))})
    return links, missing, unsupported


def _calls(db, run_id: str):
    """(calls, note). The archive may predate `llm_calls` and still be
    deliberately un-migrated (S-05); an absent table is a fact to print, not
    a reason to reshape somebody's archive."""
    try:
        return provenance.calls_for(db, run_id), None
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return [], ("llm_calls table absent (archive not migrated); "
                    "call provenance unavailable")


def write_pack(db, run_id: str, out_dir=None) -> dict:
    """Write the folder and return the summary the CLI prints."""
    runs = _rows(db, "SELECT * FROM runs WHERE run_id=? ORDER BY id", (run_id,))
    if not runs:
        raise LookupError(f"no run '{run_id}' in this archive")
    run = _decoded_run(runs[0])

    calls, calls_note = _calls(db, run_id)
    ideas = _rows(db, "SELECT * FROM ideas WHERE run_id=? ORDER BY id", (run_id,))
    nuggets = _rows(
        db,
        "SELECT id, unique_key, platform, category, extracted_insight, source_url,"
        " comment_url, extractor_model, trivial FROM nuggets WHERE run_id=?"
        " ORDER BY id", (run_id,))
    sources = _rows(
        db,
        "SELECT COALESCE(comment_url, source_url) AS url, platform,"
        " COUNT(*) AS nuggets FROM nuggets WHERE run_id=?"
        " AND COALESCE(comment_url, source_url) IS NOT NULL"
        " GROUP BY url, platform ORDER BY nuggets DESC, url", (run_id,))
    links, missing, unsupported = _citation_check(db, ideas)

    integrity = {
        "ok": not missing and not unsupported,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_ideas": len(ideas),
        "n_citations": len(links),
        "missing": missing,
        "unsupported": unsupported,
    }

    out = Path(out_dir) if out_dir else default_out_dir(run_id)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "run.json": run,
        "calls.json": calls,
        "ideas.json": ideas,
        "nuggets.json": nuggets,
        "sources.json": sources,
        "citations.json": links,
        "integrity.json": integrity,
    }
    for name, payload in files.items():
        (out / name).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8")

    summary = {
        "out": str(out),
        "run_id": run_id,
        "status": run.get("status"),
        "n_calls": len(calls),
        "n_ideas": len(ideas),
        "n_nuggets": len(nuggets),
        "n_sources": len(sources),
        "n_citations": len(links),
        "integrity_ok": integrity["ok"],
    }
    if calls_note:
        summary["calls_note"] = calls_note
    (out / "README.md").write_text(
        _readme(summary, run, calls, ideas, sources, integrity, calls_note),
        encoding="utf-8")
    return summary


def _readme(summary, run, calls, ideas, sources, integrity, calls_note=None) -> str:
    models = (run.get("models_used") or {}).get("models") if isinstance(
        run.get("models_used"), dict) else None
    env = run.get("env") if isinstance(run.get("env"), dict) else {}
    roles = (run.get("models_used") or {}).get("roles", {}) if isinstance(
        run.get("models_used"), dict) else {}
    verdict = "PASS" if integrity["ok"] else "FAIL"
    by_role = {}
    for call in calls:
        by_role.setdefault(call["role"], []).append(call)
    role_rows = [
        {"role": role, "calls": len(rows),
         "models": ", ".join(sorted({str(r["model"]) for r in rows}))}
        for role, rows in sorted(by_role.items())
    ]

    return f"""# Evidence pack — run `{run.get("run_id", summary["run_id"])}`

Generated {datetime.now(timezone.utc).isoformat(timespec="seconds")} from the
archive. Every number below is read from the files beside this README; nothing
here was typed in.

**Citation integrity: {verdict}** — {summary["n_citations"]} citation(s) across
{summary["n_ideas"]} idea(s), {len(integrity["missing"])} missing,
{len(integrity["unsupported"])} idea(s) citing nothing.

## What each file answers

| File | The question it answers |
|---|---|
| `run.json` | When did this run happen, how did it end, on which machine, with which role settings? |
| `calls.json` | What exactly was asked of each model, and what came back? |
| `ideas.json` | What ideas did the run produce, and how were they scored? |
| `nuggets.json` | Which comments did it distil, and where can each one be read? |
| `sources.json` | Which URLs stand behind those nuggets, and how many each? |
| `citations.json` | Which nugget supports each idea, and does that nugget still exist? |
| `integrity.json` | The citation check as one machine-readable verdict. |

## The run

| Field | Value |
|---|---|
| run_id | `{run.get("run_id", "")}` |
| status | `{run.get("status", "")}` |
| origin | `{run.get("origin", "")}` |
| started | `{run.get("started_at", "")}` |
| finished | `{run.get("finished_at") or ""}` |
| models | {", ".join(f"`{m}`" for m in (models or [])) or "_none recorded_"} |
| prompt versions | `{json.dumps((run.get("models_used") or {}).get("prompt_versions", {}))}` |
| python / machine | `{env.get("python", "?")}` / `{env.get("machine", "?")}` |

## The calls

{f"**No call record available:** {calls_note}." if calls_note else ""}
{_table(role_rows, ["role", "calls", "models"])}
`calls.json` holds one row per call. `prompt_sha256` is the SHA-256 of the exact
system+user text that was sent; `response_sha256` is the SHA-256 of the reply
(or null when the call failed). A rerun that asks the same question produces
the same prompt hash — that is the point of recording them.

The full text is **not** stored by default, because a prompt carries a scraped
comment. Set `JESTER_LLM_CALL_TEXT=1` before a run to keep it; calls are pruned
after `JESTER_LLM_CALLS_KEEP_DAYS` days (default {provenance.DEFAULT_KEEP_DAYS}).

| role | model | digest |
|---|---|---|
{chr(10).join(f"| {r} | `{v.get('model', '')}` | `{v.get('digest') or ''}` |" for r, v in sorted(roles.items())) or "| _none_ | | |"}

## The ideas

{_table(ideas, ["id", "title", "overall", "status", "needs_score"])}

## The sources

{_table(sources, ["platform", "nuggets", "url"])}

## Citation integrity

{"Every citation resolves to a nugget in the archive." if integrity["ok"] else
 "**This pack does not pass.** The following links are broken or missing:"}

{_table(integrity["missing"], ["idea_id", "nugget_key"]) if integrity["missing"] else ""}
{_table(integrity["unsupported"], ["idea_id", "problem"]) if integrity["unsupported"] else ""}

## Check it yourself

```bash
python -m jester.cli evidence --run {summary["run_id"]}
sqlite3 data/jester.db "SELECT run_id, status, started_at, finished_at FROM runs WHERE run_id='{summary["run_id"]}'"
sqlite3 data/jester.db "SELECT role, model, prompt_sha256 FROM llm_calls WHERE run_id='{summary["run_id"]}'"
```

The first command regenerates this folder from the archive.
"""


def cmd_evidence(args):
    from jester.store import open_db

    if args.db != ":memory:" and not Path(args.db).is_file():
        print(f"evidence: no archive at {args.db}")
        raise SystemExit(2)
    # A report, not a migration: this runs against the owner's archive, which
    # may be deliberately un-migrated. `migrate=False` takes read locks and
    # writes nothing (see open_db's docstring).
    db = open_db(args.db, migrate=False)
    try:
        summary = write_pack(db, args.run, args.out)
    except LookupError as exc:
        print(f"evidence: {exc}")
        raise SystemExit(2) from None
    print(f"evidence pack: {summary['out']}")
    print(
        f"  run {summary['run_id']} ({summary['status']}): "
        f"{summary['n_calls']} call(s), {summary['n_ideas']} idea(s), "
        f"{summary['n_nuggets']} nugget(s), {summary['n_sources']} source(s)")
    if not summary["integrity_ok"]:
        print("  citation integrity: FAIL — see integrity.json")
        raise SystemExit(1)
    print("  citation integrity: PASS")
    return summary


def evidence_cli(sub) -> None:
    """Register `jester evidence`; one delegation line from cli.main."""
    ev = sub.add_parser(
        "evidence",
        help="write the evidence pack for one pipeline run (S-09)",
    )
    ev.add_argument("--run", required=True, metavar="ID",
                    help="the run_id to package")
    ev.add_argument("--db", default="data/jester.db")
    ev.add_argument("--out", default=None,
                    help="output folder (default data/evidence/<run>)")
    ev.set_defaults(func=cmd_evidence)
