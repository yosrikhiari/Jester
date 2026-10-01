"""Experiments: write the question down first, run it for real, keep the data.

The order is the point:

1. `plan` records the hypothesis, the metric, the threshold that will count
   as "yes", the baseline and the number of runs -- before any result
   exists. The first run locks it. Editing it afterwards is allowed, but
   every change is appended to `changes_after_lock` and the article prints
   them, so a goalpost that moved is visible.
2. `run_command` executes the experiment. Output goes to a folder of its
   own (`<slug>/runs/<id>/`), with the command, exit code, timing and a
   fingerprint of the machine and the code: OS, Python, GPU and driver, the
   code's commit and whether it had uncommitted changes. A run from
   uncommitted code cannot be reproduced, so the drafting gate does not
   count it.
3. `attach_ledger` turns a run Jester already recorded (a signals collection,
   a pipeline pass) into an experiment run, with the ledger row saved as
   its raw data. Jester's own nightly work is evidence too.
4. `table` and `figure` are made FROM runs. The number check verifies every
   value in a generated table against the runs it names, and a figure is
   registered with the hash of the file as made, so editing the SVG by hand
   fails the check.

The command contract is small: it runs with `JESTER_RUN_DIR` (where to write)
and `JESTER_SEED` in its environment, and may write `result.json` -- a JSON
object -- into that folder. That object is the run's result; numbers in the
article point into it (`run:12#accuracy`).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

from jester import journal as J

PLAN_FIELDS = ("hypothesis", "metric", "threshold", "baseline", "n_planned", "threats")
OK_LEDGER_STATUSES = frozenset({"ok", "done", "completed", "complete", "success", "finished"})
#: The two ledgers Jester keeps, and which table and key each lives under.
LEDGERS = {
    "signals": ("signal_run", "run_id", "data/signals-live.db"),
    "pipeline": ("runs", "run_id", "data/jester.db"),
}


# ---- the plan -----------------------------------------------------------------

def plan(db: sqlite3.Connection, journal_id: int, experiment_id: Optional[int] = None,
         at: Optional[str] = None, **fields) -> int:
    """Create an experiment plan, or edit one. Returns the experiment id."""
    unknown = set(fields) - set(PLAN_FIELDS)
    if unknown:
        raise J.JournalError(f"not a plan field: {', '.join(sorted(unknown))}")
    changes = {k: v for k, v in fields.items() if v is not None}
    if "threats" in changes:
        changes["threats"] = json.dumps(list(changes["threats"]))
    if "n_planned" in changes:
        changes["n_planned"] = int(changes["n_planned"])
        if changes["n_planned"] < 0:
            raise J.JournalError("the number of runs cannot be negative")
    for k in ("hypothesis", "metric", "threshold", "baseline"):
        if k in changes:
            changes[k] = str(changes[k]).strip()
    at = at or J.now_utc()
    if experiment_id is None:
        exp = J.add_experiment(db, journal_id, at=at)
    else:
        exp = experiment_id
    row = experiment(db, journal_id, exp)
    if row["locked_at"] and experiment_id is not None:
        log = json.loads(row["changes_after_lock"] or "[]")
        for k, new in changes.items():
            if str(row[k]) != str(new):
                log.append({"field": k, "was": row[k], "now": new, "at": at})
        changes["changes_after_lock"] = json.dumps(log)
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        db.execute(f"UPDATE experiment SET {sets} WHERE id = ?", (*changes.values(), exp))
        db.commit()
    return exp


def experiment(db: sqlite3.Connection, journal_id: int, experiment_id: Optional[int] = None) -> sqlite3.Row:
    """One experiment of a journal: the one asked for, or the newest."""
    if experiment_id is None:
        row = db.execute("SELECT * FROM experiment WHERE journal_id = ? ORDER BY id DESC LIMIT 1",
                         (journal_id,)).fetchone()
        if row is None:
            raise J.JournalError("this journal has no experiment yet (journal plan)")
        return row
    row = db.execute("SELECT * FROM experiment WHERE id = ? AND journal_id = ?",
                     (experiment_id, journal_id)).fetchone()
    if row is None:
        raise J.JournalError(f"experiment {experiment_id} is not part of this journal")
    return row


# ---- fingerprint ----------------------------------------------------------------

def _quiet(argv: Sequence[str], cwd: Optional[str] = None, timeout: float = 10.0) -> Optional[str]:
    try:
        out = subprocess.run(list(argv), cwd=cwd, capture_output=True, text=True,
                             encoding="utf-8", timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def code_state(code_dir: Optional[Path]) -> Dict:
    if code_dir is None:
        return {}
    code_dir = Path(code_dir)
    commit = _quiet(["git", "-C", str(code_dir), "rev-parse", "HEAD"])
    if commit is None:
        return {"path": str(code_dir), "commit": None, "dirty": None, "note": "not a git repository"}
    status = _quiet(["git", "-C", str(code_dir), "status", "--porcelain"]) or ""
    return {"path": str(code_dir), "commit": commit, "dirty": bool(status.strip())}


def fingerprint(code_dir: Optional[Path] = None, seed: Optional[str] = None,
                gpu_query: Callable[[], Optional[str]] = None) -> Dict:
    """What a reader needs to know to reproduce a run on their machine."""
    gpu_query = gpu_query or (lambda: _quiet(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]))
    gpu = gpu_query()
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "gpu": [g.strip() for g in gpu.splitlines()] if gpu else [],
        "code": code_state(code_dir),
        "seed": seed,
    }


# ---- running ------------------------------------------------------------------

def _start_run(db, exp_id: int, source: str, command: str, env: Dict, at: str) -> int:
    cur = db.execute(
        "INSERT INTO experiment_run (experiment_id, status, started_at, source, command, env) "
        "VALUES (?, 'running', ?, ?, ?, ?)", (exp_id, at, source, command, json.dumps(env)))
    # The lock is when the plan stopped being free to change: now, not when
    # an attached ledger run happened (that can be days before the plan).
    db.execute("UPDATE experiment SET locked_at = ? WHERE id = ? AND locked_at = ''",
               (J.now_utc(), exp_id))
    db.commit()
    return cur.lastrowid


def _finish_run(db, run_id: int, status: str, raw_path: str, result: Dict,
                exit_code: Optional[int], duration: Optional[float], at: str) -> sqlite3.Row:
    db.execute("UPDATE experiment_run SET status = ?, finished_at = ?, raw_path = ?, result = ?, "
               "exit_code = ?, duration_s = ? WHERE id = ?",
               (status, at, raw_path, json.dumps(result), exit_code, duration, run_id))
    db.commit()
    return db.execute("SELECT * FROM experiment_run WHERE id = ?", (run_id,)).fetchone()


def run_dir(root: Path, slug: str, run_id: int) -> Path:
    return Path(root) / slug / "runs" / str(run_id)


def run_command(db: sqlite3.Connection, root: Path, slug: str, argv: Sequence[str],
                experiment_id: Optional[int] = None, code_dir: Optional[Path] = None,
                seed: Optional[str] = None, timeout: Optional[float] = None,
                runner: Callable = subprocess.run, gpu_query: Callable = None,
                clock: Callable[[], float] = time.monotonic) -> sqlite3.Row:
    """Execute one run of an experiment and record everything about it."""
    if not argv:
        raise J.JournalError("give the command to run after --")
    j = J.get(db, slug)
    exp = experiment(db, j["id"], experiment_id)
    env = fingerprint(code_dir, seed, gpu_query=gpu_query)
    rid = _start_run(db, exp["id"], "command", shlex.join(argv), env, J.now_utc())
    folder = run_dir(root, slug, rid)
    folder.mkdir(parents=True, exist_ok=True)
    child_env = {**os.environ, "JESTER_RUN_DIR": str(folder.resolve()), "JESTER_SEED": seed or ""}
    started = clock()
    try:
        done = runner(list(argv), cwd=str(code_dir) if code_dir else None, env=child_env,
                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                      timeout=timeout, check=False)
        stdout, stderr, code = done.stdout or "", done.stderr or "", done.returncode
        note = ""
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = exc.stdout or "", exc.stderr or "", None
        note = f"stopped after {timeout} s"
    except OSError as exc:
        stdout, stderr, code = "", str(exc), None
        note = f"could not start: {exc}"
    duration = round(clock() - started, 3)
    (folder / "stdout.txt").write_text(stdout if isinstance(stdout, str) else stdout.decode("utf-8", "replace"),
                                       encoding="utf-8")
    (folder / "stderr.txt").write_text(stderr if isinstance(stderr, str) else stderr.decode("utf-8", "replace"),
                                       encoding="utf-8")
    result, status = {}, "ok" if code == 0 else "failed"
    out_file = folder / "result.json"
    if out_file.exists():
        try:
            result = json.loads(out_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            result, status, note = {}, "failed", f"result.json is not valid JSON: {exc}"
        if not isinstance(result, dict):
            result, status, note = {}, "failed", "result.json must hold a JSON object"
    if note:
        result = {**result, "_note": note}
    rel = folder.relative_to(Path(root)).as_posix()
    return _finish_run(db, rid, status, rel, result, code, duration, J.now_utc())


def _decode_json_columns(row: Dict) -> Dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and v[:1] in "[{":
            try:
                out[k] = json.loads(v)
                continue
            except ValueError:
                pass
        out[k] = v
    return out


def attach_ledger(db: sqlite3.Connection, root: Path, slug: str, ledger: str, ledger_run: str,
                  ledger_db: Optional[str] = None, experiment_id: Optional[int] = None) -> sqlite3.Row:
    """Record a run Jester already made as a run of this experiment."""
    if ledger not in LEDGERS:
        raise J.JournalError(f"ledger must be one of {', '.join(LEDGERS)}")
    table, key, default_db = LEDGERS[ledger]
    path = ledger_db or default_db
    if not Path(path).exists():
        raise J.JournalError(f"no ledger database at {path}")
    src = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        row = src.execute(f"SELECT * FROM {table} WHERE {key} = ?", (ledger_run,)).fetchone()
    finally:
        src.close()
    if row is None:
        raise J.JournalError(f"no {ledger} run {ledger_run!r} in {path}")
    record = _decode_json_columns(dict(row))
    j = J.get(db, slug)
    exp = experiment(db, j["id"], experiment_id)
    started = str(record.get("started_utc") or record.get("started_at") or J.now_utc())
    # A run that happened before the plan was written cannot confirm that
    # plan -- the result was there to be seen. It is still data; it is
    # marked so the article says so.
    env = {"ledger": ledger, "ledger_db": str(Path(path)), "ledger_run": ledger_run,
           "before_plan": _earlier(started, exp["created_at"])}
    rid = _start_run(db, exp["id"], f"ledger:{ledger}", f"{ledger} run {ledger_run}", env, started)
    folder = run_dir(root, slug, rid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ledger.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    status = "ok" if str(record.get("status", "")).lower() in OK_LEDGER_STATUSES else \
        str(record.get("status") or "unknown")
    finished = record.get("finished_utc") or record.get("finished_at") or ""
    return _finish_run(db, rid, status, folder.relative_to(Path(root)).as_posix(), record,
                       None, None, finished or J.now_utc())


def _earlier(a: str, b: str) -> bool:
    """Is timestamp `a` before `b`? Ledgers write "2026-10-01 12:09:03"
    (no zone, UTC) or ISO with an offset; both are compared as UTC."""
    from datetime import datetime, timezone

    def parse(ts):
        try:
            d = datetime.fromisoformat(str(ts).replace(" ", "T"))
        except ValueError:
            return None
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    pa, pb = parse(a), parse(b)
    return bool(pa and pb and pa < pb)


def counts_for_gate(run: sqlite3.Row) -> bool:
    """Whether a run can stand behind an article: finished ok, raw data kept,
    and not made from uncommitted code."""
    if run["status"] != "ok" or not run["raw_path"]:
        return False
    code = json.loads(run["env"] or "{}").get("code") or {}
    return code.get("dirty") is not True


# ---- tables and figures ---------------------------------------------------------

def _dig(result: Dict, path: str):
    cur = result
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
            cur = cur[int(key)]
        else:
            return None
    return cur


def _runs_of(db, journal_id: int, run_ids: Iterable[int]) -> List[sqlite3.Row]:
    rows = []
    for rid in run_ids:
        row = db.execute(
            "SELECT r.* FROM experiment_run r JOIN experiment e ON e.id = r.experiment_id "
            "WHERE r.id = ? AND e.journal_id = ?", (rid, journal_id)).fetchone()
        if row is None:
            raise J.JournalError(f"run {rid} is not part of this journal")
        if row["status"] != "ok":
            raise J.JournalError(f"run {rid} did not finish ok ({row['status']})")
        rows.append(row)
    if not rows:
        raise J.JournalError("name at least one run")
    return rows


def fmt(value) -> str:
    """How a number is shown in a generated table: what is stored, at most
    four decimals. The number check accepts the shown form."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "" if value is None else str(value)
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def table(db: sqlite3.Connection, journal_id: int, run_ids: Sequence[int], keys: Sequence[str],
          label_key: str = "") -> str:
    """A Markdown table made from runs, with its proof comment above it.

    The table rows are registered as generated text, so the origin check
    knows a tool wrote them.
    """
    rows = _runs_of(db, journal_id, run_ids)
    if not keys:
        raise J.JournalError("name at least one key to tabulate")
    head = ["run"] + list(keys)
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        result = json.loads(r["result"] or "{}")
        label = str(_dig(result, label_key)) if label_key and _dig(result, label_key) is not None \
            else f"run {r['id']}"
        cells = [label] + [fmt(_dig(result, k)) for k in keys]
        lines.append("| " + " | ".join(cells) + " |")
    body = "\n".join(lines)
    J.register_generated(db, journal_id, body, role="table")
    proof = ",".join(f"run:{r['id']}" for r in rows)
    return f"<!-- backed-by: {proof} -->\n{body}"


def _svg_bars(labels: List[str], values: List[float], title: str, unit: str = "") -> str:
    """A plain bar chart. Fixed colours, because an SVG shown through <img>
    cannot follow the page's theme; mid-grey text reads on light and dark."""
    width, bar_h, gap, left, top = 640, 28, 12, 150, 40
    height = top + len(values) * (bar_h + gap) + 20
    vmax = max(values) if values and max(values) > 0 else 1.0
    span = width - left - 90
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'font-family="system-ui, sans-serif" font-size="13">',
           f'<text x="{left}" y="22" font-weight="600" fill="#6b7280">{_esc(title)}</text>']
    for i, (label, v) in enumerate(zip(labels, values)):
        y = top + i * (bar_h + gap)
        w = max(1.0, span * (v / vmax)) if v > 0 else 1.0
        out.append(f'<text x="{left - 10}" y="{y + bar_h * 0.68:.1f}" text-anchor="end" fill="#6b7280">'
                   f'{_esc(label)}</text>')
        out.append(f'<rect x="{left}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" fill="#3b6fb6"/>')
        out.append(f'<text x="{left + w + 8:.1f}" y="{y + bar_h * 0.68:.1f}" fill="#6b7280">'
                   f'{_esc(fmt(v) + unit)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _esc(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def figure(db: sqlite3.Connection, root: Path, slug: str, run_ids: Sequence[int], key: str,
           name: str, caption: str = "", label_key: str = "", unit: str = "") -> Dict:
    """Draw one value across runs as an SVG bar chart and register it."""
    j = J.get(db, slug)
    rows = _runs_of(db, j["id"], run_ids)
    labels, values = [], []
    for r in rows:
        result = json.loads(r["result"] or "{}")
        v = _dig(result, key)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise J.JournalError(f"run {r['id']} has no number at {key!r}")
        lab = _dig(result, label_key) if label_key else None
        labels.append(str(lab) if lab is not None else f"run {r['id']}")
        values.append(float(v))
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name).strip("-")
    if not safe:
        raise J.JournalError("give the figure a name (letters, digits, - or _)")
    rel = f"{slug}/figures/{safe}.svg"
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    svg = _svg_bars(labels, values, caption or key, unit).encode("utf-8")
    # Bytes, not write_text: on Windows text mode turns \n into \r\n and the
    # hash of the file would never match the hash of what was made.
    path.write_bytes(svg)
    digest = hashlib.sha256(svg).hexdigest()
    db.execute("INSERT INTO artifact (journal_id, kind, path, sha256, runs, caption, created_at) "
               "VALUES (?, 'figure', ?, ?, ?, ?, ?)",
               (j["id"], rel, digest, json.dumps([r["id"] for r in rows]), caption, J.now_utc()))
    db.commit()
    return {"path": path, "markdown": f"![{caption or safe}](figures/{safe}.svg)"}


def artifacts(db: sqlite3.Connection, journal_id: int) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM artifact WHERE journal_id = ? ORDER BY id", (journal_id,)).fetchall()


def artifact_problem(db: sqlite3.Connection, root: Path, journal_id: int, article_dir: str,
                     target: str) -> Optional[str]:
    """None if a figure the article shows is one Jester made from runs, as made."""
    rel = (Path(article_dir) / target).as_posix()
    row = db.execute("SELECT * FROM artifact WHERE journal_id = ? AND path = ? ORDER BY id DESC LIMIT 1",
                     (journal_id, rel)).fetchone()
    if row is None:
        return f"{target} was not made from runs (journal figure)"
    path = Path(root) / rel
    if not path.exists():
        return f"{target} is missing"
    if hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
        return f"{target} was edited after it was made; draw it again from the runs"
    return None
