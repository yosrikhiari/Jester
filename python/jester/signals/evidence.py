"""The evidence pack — one folder a reviewer can read in under an hour.

The archive has to be checkable by someone who did not build it, in under an
hour, and that hour is the whole design constraint. Nobody reads a codebase in
an hour, so the pack is arranged so the first page answers the questions a
reviewer actually has, in this order:

1. Do the two stores agree? (one table, pass or fail)
2. Did a repeat import create duplicates? (one number, must be zero)
3. What is in the archive, split live from fixture?
4. What failed, and is anything failing silently?
5. Where do I check a claim myself? (the saved SQL, the CSV, the field map)

Everything in `README.md` is generated from the run that produced the folder.
Nothing is typed in, so the pack cannot claim a number the data does not hold —
and a re-run overwrites it rather than accumulating versions someone has to
choose between.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import TABLE, clickhouse_ddl, export_csv, field_map_markdown
from .clickhouse import Client, load, reconcile, run_saved_queries
from .fixtures import REQUIRED_TOTAL, check as check_fixtures

#: How many rows of a query's output the README shows inline. Enough to see
#: the shape; the full result is beside it as JSON, so nothing is hidden by
#: the summary.
PREVIEW_ROWS = 12


def _table(rows: list[dict], limit: int = PREVIEW_ROWS) -> str:
    """A list of dicts as a Markdown table. Empty is stated, not left blank —
    "no rows" is an answer, and a missing table reads like a missing check."""
    if not rows:
        return "_no rows_\n"
    cols = list(rows[0].keys())
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = ""
    for r in rows[:limit]:
        cells = []
        for c in cols:
            v = r.get(c)
            text = "" if v is None else str(v)
            cells.append(text.replace("|", "\\|").replace("\n", " ")[:120])
        body += "| " + " | ".join(cells) + " |\n"
    if len(rows) > limit:
        body += f"\n_{len(rows) - limit} more row(s) in the JSON beside this file._\n"
    return head + body


def write_evidence(db: sqlite3.Connection, out_dir: str | Path, *,
                   client: Client | None = None, mode: str = "",
                   do_load: bool = True) -> dict:
    """Load, reconcile, run the saved queries, and write the pack.

    Returns the same summary the CLI prints, so the exit code and the document
    are decided by one set of numbers rather than two.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = client or Client()

    loaded = load(db, client, mode=mode) if do_load else {"rows_sent": 0, "requests": 0,
                                                          "mode_filter": mode or "all",
                                                          "database": client.database,
                                                          "url": client.url}
    rec = reconcile(db, client, mode=mode)
    queries = run_saved_queries(client)
    csv_manifest = export_csv(db, out, mode=mode)

    # The fixture gate, re-run here. Edits, removals and failed fetches are
    # behaviour across runs, so a live archive that happens to contain none of
    # them proves nothing about how they are handled — the twenty labeled cases
    # do, against a real table, and a reviewer should not have to take that on
    # trust from another document.
    gate = check_fixtures()

    # Copies of the things a reviewer needs in hand: the schema they are
    # querying and the meaning of each column. Generated from the field map,
    # so they describe the table that actually exists.
    (out / "schema.clickhouse.sql").write_text(clickhouse_ddl(), encoding="utf-8")
    (out / "field-map.md").write_text(field_map_markdown(), encoding="utf-8")

    qdir = out / "queries"
    qdir.mkdir(exist_ok=True)
    for q in queries:
        (qdir / f"{q['name']}.sql").write_text(q["sql"] + "\n", encoding="utf-8")
        (qdir / f"{q['name']}.json").write_text(
            json.dumps(q["rows"], indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "load": loaded,
        "reconcile": rec,
        "csv": csv_manifest,
        "queries": [{"name": q["name"], "ok": q["ok"], "rows": len(q["rows"]),
                     "error": q.get("error", "")} for q in queries],
        "fixture_gate": {"passed": gate["passed"], "total": gate["total"],
                         "required": REQUIRED_TOTAL, "failed": gate["failed"],
                         "categories": gate["categories"],
                         "missing_categories": gate["missing_categories"]},
        # One boolean the CLI exits on. Both stores agreeing is not enough:
        # a query that failed to run means a check did not happen, and a check
        # that did not happen must not read as a check that passed.
        "passes": bool(rec["reconciles"] and csv_manifest["reconciles"]
                       and all(q["ok"] for q in queries)
                       and not gate["failed"] and not gate["missing_categories"]
                       and not gate["short_by"]),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out / "fixture-gate.json").write_text(
        json.dumps(gate["cases"], indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "README.md").write_text(_readme(summary, queries, client), encoding="utf-8")
    return summary


def _readme(summary: dict, queries: list[dict], client: Client) -> str:
    rec, csvm, ld = summary["reconcile"], summary["csv"], summary["load"]
    g = summary["fixture_gate"]
    verdict = "PASS" if summary["passes"] else "FAIL"
    by_name = {q["name"]: q for q in queries}

    lines = [
        "# the collector — problem-signal archive, evidence pack",
        "",
        f"> Generated {summary['written_at']} from the archive itself. "
        "Every number below is a query result, not a claim typed into a document. "
        "Re-running `jester signals evidence` overwrites this folder.",
        "",
        f"**Verdict: {verdict}.** "
        + ("The two stores agree, the CSV matches the table, and every saved query ran."
           if summary["passes"] else
           "Something below does not reconcile — the failing line says which."),
        "",
        "---",
        "",
        "## 1. Do the two stores agree?",
        "",
        "| check | value | verdict |",
        "|---|---|---|",
        f"| rows in SQLite | {rec['sqlite_rows']} | — |",
        f"| rows in ClickHouse (`FINAL`) | {rec['clickhouse_rows_final']} | "
        f"{'match' if rec['clickhouse_rows_final'] == rec['sqlite_rows'] else 'MISMATCH'} |",
        f"| distinct `record_id` | {rec['clickhouse_unique_ids']} | "
        f"{'match' if rec['clickhouse_unique_ids'] == rec['clickhouse_rows_final'] else 'MISMATCH'} |",
        f"| **duplicates after repeat import** | **{rec['duplicates']}** | "
        f"{'none' if rec['duplicates'] == 0 else 'DUPLICATES PRESENT'} |",
        f"| rows in CSV | {csvm['rows_in_csv']} | "
        f"{'match' if csvm['reconciles'] else 'MISMATCH'} |",
        f"| un-merged parts (harmless) | {rec['unmerged_parts']} | informational |",
        "",
        "**Why duplicates cannot happen.** The table is a `ReplacingMergeTree` "
        "ordered on `record_id` alone, so importing the same record twice "
        "collapses to one row. The sort key deliberately excludes community and "
        "platform: including them would produce two rows the day a post is "
        "crossposted or a community is renamed. Because the collapse happens at "
        "merge time, every saved query says `FINAL` — a count without it counts "
        "parts, not records.",
        "",
        f"Loaded this run: **{ld['rows_sent']} row(s)** in {ld['requests']} request(s) "
        f"into `{ld['database']}` at `{ld['url']}` (mode filter: {ld['mode_filter']}).",
        "",
        "---",
        "",
        "## 2. What is in the archive",
        "",
        "Split by mode, because a fixture row must never be read as a live one. "
        "`mode` is a column on every record, so this is a `GROUP BY`, not a promise.",
        "",
        _table(by_name["02-counts-by-mode"]["rows"]),
        "",
        "### By collection day",
        "",
        "Dated on when *we* collected it, not when the author posted. A missing "
        "day is a day the collector did not run.",
        "",
        _table(by_name["03-daily-collection"]["rows"]),
        "",
        "### By community",
        "",
        "The source-quality table. `buyer_rate` is what decides whether a "
        "community stays in scope.",
        "",
        _table(by_name["04-audience-by-community"]["rows"]),
        "",
        "---",
        "",
        "## 3. What failed",
        "",
        "A failed fetch is stored as a row with `run_status='error'`, so it is "
        "counted rather than absent. This section being empty is the claim that "
        "nothing failed; it being absent would prove nothing.",
        "",
        _table(by_name["07-failures"]["rows"]),
        "",
        "### Edits and removals",
        "",
        "A record that changed keeps its first sighting and counts a revision. "
        "A record the source stopped returning is stamped `removed_utc` and "
        "kept — removal is recorded, never deleted.",
        "",
        _table(by_name["06-edits-and-removals"]["rows"]),
        "",
        "### The twenty labeled cases",
        "",
        f"An archive that happens to contain no edits, removals or failed "
        f"fetches proves nothing about how they are handled. The gate does: "
        f"**{g['passed']}/{g['total']}** labeled cases pass across "
        f"{len(g['categories'])} categories "
        f"({', '.join(g['categories'])}) — the replay cases each against a "
        f"real table. Full results in `fixture-gate.json`; re-run with "
        f"`jester signals check`.",
        "",
        "---",
        "",
        "## 4. Check any of this yourself",
        "",
        "Every query in `queries/` is a plain `.sql` file with its result beside "
        "it as `.json`. Run one against the same server:",
        "",
        "```bash",
        f"curl --data-binary @queries/01-reconciliation.sql \\",
        f"  '{client.url}/?database={client.database}&user={client.user}&password=…'",
        "```",
        "",
        "| file | answers |",
        "|---|---|",
    ]
    for q in queries:
        state = "" if q["ok"] else " **(FAILED: " + q.get("error", "")[:80] + ")**"
        lines.append(f"| `queries/{q['name']}.sql` | {q['title']}{state} |")

    lines += [
        "",
        "| also in this folder | what it is |",
        "|---|---|",
        f"| `{csvm['csv']}` | the same rows as CSV ({csvm['rows_in_csv']}), "
        "for anyone without database access |",
        "| `field-map.md` | what every column means |",
        "| `schema.clickhouse.sql` | the exact DDL this table was created from |",
        "| `manifest.json` | the CSV/table reconciliation as data |",
        "| `fixture-gate.json` | every labeled case and what the classifier said |",
        "| `summary.json` | everything on this page as data |",
        "",
        "---",
        "",
        "## 5. What this pack does not claim",
        "",
        "* **`company` and `buyer_intent` are always `unknown`.** A post is a "
        "problem someone described. It is not evidence of a budget, a company, "
        "or an intent to buy, and there is no code path that infers one.",
        "* **No identities are resolved.** Handles are stored as published. "
        "Nothing is enriched, matched against a contact database, or messaged.",
        "* **Fixture rows are not results.** Where `mode = 'fixture'` the record "
        "was written by hand to exercise the pipeline. It has never been near "
        "Reddit.",
        "",
    ]
    return "\n".join(lines) + "\n"
