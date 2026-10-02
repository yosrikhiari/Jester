"""Experiments (J2): plans that lock, runs that keep their data and their
machine, ledger runs attached as evidence, and tables/figures made from runs
that the number check can verify.
"""
import json
import sqlite3
import subprocess
import sys

import pytest

from jester import cli
from jester import journal as J
from jester.journal import checks as C
from jester.journal import lab as L
from jester.journal import states as js

WRITE_RESULT = ("import json, os; d = os.environ['JESTER_RUN_DIR']; "
                "json.dump({'accuracy': 0.912, 'n': 500, 'label': 'rules', 'seed': os.environ['JESTER_SEED']}, "
                "open(os.path.join(d, 'result.json'), 'w')); print('done')")


@pytest.fixture
def db(tmp_path):
    conn = J.open_journal(str(tmp_path / "j.db"))
    yield conn
    conn.close()


@pytest.fixture
def jid(db):
    return J.create_journal(db, "Lab", path="lab/index.md")["id"]


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout


def _repo(tmp_path, dirty=False):
    repo = tmp_path / "code"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "bench.py").write_text("print(1)\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
    if dirty:
        (repo / "bench.py").write_text("print(2)\n", encoding="utf-8")
    return repo


# ---- schema -------------------------------------------------------------------

def test_an_older_database_gets_the_new_columns(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE experiment (id INTEGER PRIMARY KEY, journal_id INTEGER, "
                      "hypothesis TEXT, metric TEXT, threshold TEXT, baseline TEXT, n_planned INTEGER, "
                      "checklist TEXT, created_at TEXT, locked_at TEXT);"
                      "CREATE TABLE experiment_run (id INTEGER PRIMARY KEY, experiment_id INTEGER, "
                      "status TEXT, started_at TEXT, finished_at TEXT, raw_path TEXT, result TEXT);")
    old.close()
    conn = J.open_journal(str(path))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(experiment_run)")}
    assert {"source", "command", "env", "exit_code", "duration_s"} <= cols
    assert J._catch_up_columns(conn) == []
    conn.close()


# ---- plan ---------------------------------------------------------------------

def test_plan_creates_then_edits_and_logs_changes_after_the_lock(db, jid):
    e = L.plan(db, jid, hypothesis=" rules lose ", metric="F1", threshold="+0.05", baseline="rules",
               n_planned=5, threats=["labels by one person"])
    row = L.experiment(db, jid)
    assert (row["hypothesis"], row["n_planned"], json.loads(row["threats"])) == \
        ("rules lose", 5, ["labels by one person"])
    L.plan(db, jid, experiment_id=e, threshold="+0.03")
    assert L.experiment(db, jid, e)["changes_after_lock"] == "[]"
    J.add_run(db, e)
    L.plan(db, jid, experiment_id=e, threshold="+0.01", metric="F1", at="2026-10-02T09:00:00+00:00")
    log = json.loads(L.experiment(db, jid, e)["changes_after_lock"])
    assert log == [{"field": "threshold", "was": "+0.03", "now": "+0.01", "at": "2026-10-02T09:00:00+00:00"}]


def test_plan_refuses_bad_fields(db, jid):
    with pytest.raises(J.JournalError, match="not a plan field"):
        L.plan(db, jid, vibe="good")
    with pytest.raises(J.JournalError, match="cannot be negative"):
        L.plan(db, jid, n_planned=-1)


def test_experiment_lookup_errors(db, jid):
    with pytest.raises(J.JournalError, match="no experiment yet"):
        L.experiment(db, jid)
    other = J.create_journal(db, "Other")["id"]
    e = L.plan(db, other)
    with pytest.raises(J.JournalError, match="not part of this journal"):
        L.experiment(db, jid, e)


# ---- fingerprint ----------------------------------------------------------------

def test_fingerprint_records_machine_gpu_code_and_seed(tmp_path):
    repo = _repo(tmp_path)
    env = L.fingerprint(repo, seed="7", gpu_query=lambda: "RTX 4060 Laptop GPU, 616.64, 8188 MiB")
    assert env["gpu"] == ["RTX 4060 Laptop GPU, 616.64, 8188 MiB"] and env["seed"] == "7"
    assert env["code"]["dirty"] is False and len(env["code"]["commit"]) == 40
    assert env["python"] == sys.version.split()[0]
    assert L.fingerprint(None, gpu_query=lambda: None)["gpu"] == []


def test_code_state_dirty_and_not_a_repo(tmp_path):
    assert L.code_state(_repo(tmp_path, dirty=True))["dirty"] is True
    plain = tmp_path / "plain"
    plain.mkdir()
    assert L.code_state(plain)["note"] == "not a git repository"
    assert L.code_state(None) == {}
    assert L._quiet(["definitely-not-a-command-xyz"]) is None


# ---- runs ---------------------------------------------------------------------

def test_run_command_keeps_output_result_env_and_locks(db, jid, tmp_path):
    e = L.plan(db, jid, n_planned=3)
    run = L.run_command(db, tmp_path, "lab", [sys.executable, "-c", WRITE_RESULT],
                        code_dir=_repo(tmp_path), seed="42", gpu_query=lambda: None)
    assert run["status"] == "ok" and run["exit_code"] == 0 and run["source"] == "command"
    result = json.loads(run["result"])
    assert result["accuracy"] == 0.912 and result["seed"] == "42"
    folder = tmp_path / run["raw_path"]
    assert run["raw_path"] == f"lab/runs/{run['id']}"
    assert (folder / "stdout.txt").read_text(encoding="utf-8").strip() == "done"
    assert json.loads(run["env"])["code"]["dirty"] is False
    assert L.experiment(db, jid, e)["locked_at"] >= L.experiment(db, jid, e)["created_at"]
    assert L.counts_for_gate(run)


def test_a_failing_command_and_bad_results(db, jid, tmp_path):
    L.plan(db, jid)
    failed = L.run_command(db, tmp_path, "lab", [sys.executable, "-c", "import sys; sys.exit(3)"],
                           gpu_query=lambda: None)
    assert (failed["status"], failed["exit_code"]) == ("failed", 3)
    bad = L.run_command(db, tmp_path, "lab", [sys.executable, "-c",
                        "import os; open(os.path.join(os.environ['JESTER_RUN_DIR'], 'result.json'), 'w')"
                        ".write('not json')"], gpu_query=lambda: None)
    assert bad["status"] == "failed" and "not valid JSON" in json.loads(bad["result"])["_note"]
    listy = L.run_command(db, tmp_path, "lab", [sys.executable, "-c",
                          "import os; open(os.path.join(os.environ['JESTER_RUN_DIR'], 'result.json'), 'w')"
                          ".write('[1, 2]')"], gpu_query=lambda: None)
    assert "JSON object" in json.loads(listy["result"])["_note"]
    with pytest.raises(J.JournalError, match="give the command"):
        L.run_command(db, tmp_path, "lab", [])


def test_timeouts_and_commands_that_cannot_start(db, jid, tmp_path):
    L.plan(db, jid)

    def slow(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 5, output=b"partial", stderr=None)
    run = L.run_command(db, tmp_path, "lab", ["x"], timeout=5, runner=slow, gpu_query=lambda: None)
    assert run["status"] == "failed" and "stopped after 5" in json.loads(run["result"])["_note"]
    assert (tmp_path / run["raw_path"] / "stdout.txt").read_text(encoding="utf-8") == "partial"
    run = L.run_command(db, tmp_path, "lab", ["definitely-not-a-command-xyz"], gpu_query=lambda: None)
    assert run["status"] == "failed" and "could not start" in json.loads(run["result"])["_note"]


def test_runs_from_uncommitted_code_do_not_open_drafting(db, tmp_path):
    slug = J.create_journal(db, "Dirty", question="q")["slug"]
    j = J.get(db, slug)
    db.execute("UPDATE journal SET state='experimenting' WHERE id=?", (j["id"],))
    e = L.plan(db, j["id"], n_planned=1)
    J.answer_checklist(db, e, {k: "yes" for k, _ in J.CHECKLIST})
    L.run_command(db, tmp_path, slug, [sys.executable, "-c", "print(1)"],
                  code_dir=_repo(tmp_path, dirty=True), gpu_query=lambda: None)
    assert "uncommitted code" in " ".join(js.blockers(db, J.get(db, slug), "drafting"))


# ---- ledger -------------------------------------------------------------------

def _ledgers(tmp_path):
    sig = tmp_path / "signals.db"
    c = sqlite3.connect(sig)
    c.execute("CREATE TABLE signal_run (run_id TEXT, status TEXT, started_utc TEXT, finished_utc TEXT, "
              "queries TEXT, collected INTEGER, buyer INTEGER)")
    c.execute("INSERT INTO signal_run VALUES ('hn-1', 'ok', '2026-10-01T08:30:48+00:00', "
              "'2026-10-01T08:30:49+00:00', '[\"r/forhire\"]', 722, 11)")
    c.execute("INSERT INTO signal_run VALUES ('hn-2', 'failed', '2026-10-01T09:00:00+00:00', '', '{bad', 0, 0)")
    c.commit()
    c.close()
    pipe = tmp_path / "jester.db"
    c = sqlite3.connect(pipe)
    c.execute("CREATE TABLE runs (run_id TEXT, status TEXT, started_at TEXT, finished_at TEXT, n_batches INTEGER)")
    c.execute("INSERT INTO runs VALUES ('treat-1', 'done', '2026-10-01 12:09:03', '2026-10-01 12:30:00', 58)")
    c.commit()
    c.close()
    return sig, pipe


def test_attach_a_ledger_run(db, jid, tmp_path):
    sig, pipe = _ledgers(tmp_path)
    L.plan(db, jid)
    run = L.attach_ledger(db, tmp_path, "lab", "signals", "hn-1", ledger_db=str(sig))
    result = json.loads(run["result"])
    assert run["status"] == "ok" and run["source"] == "ledger:signals"
    assert result["collected"] == 722 and result["queries"] == ["r/forhire"]
    assert run["started_at"] == "2026-10-01T08:30:48+00:00"
    saved = json.loads((tmp_path / run["raw_path"] / "ledger.json").read_text(encoding="utf-8"))
    assert saved["buyer"] == 11
    assert C.check_numbers(db, tmp_path, jid, f"It kept [11](run:{run['id']}#buyer) buyers.").ok
    failed = L.attach_ledger(db, tmp_path, "lab", "signals", "hn-2", ledger_db=str(sig))
    assert failed["status"] == "failed" and json.loads(failed["result"])["queries"] == "{bad"
    treat = L.attach_ledger(db, tmp_path, "lab", "pipeline", "treat-1", ledger_db=str(pipe))
    assert treat["status"] == "ok" and json.loads(treat["result"])["n_batches"] == 58


def test_a_ledger_run_older_than_the_plan_is_marked(db, jid, tmp_path):
    sig, pipe = _ledgers(tmp_path)
    e = L.plan(db, jid)
    run = L.attach_ledger(db, tmp_path, "lab", "signals", "hn-1", ledger_db=str(sig))
    assert json.loads(run["env"])["before_plan"] is True
    assert L.experiment(db, jid, e)["locked_at"] > run["started_at"]
    early = J.add_experiment(db, jid, at="2026-09-01T00:00:00+00:00")
    later = L.attach_ledger(db, tmp_path, "lab", "pipeline", "treat-1", ledger_db=str(pipe),
                            experiment_id=early)
    assert json.loads(later["env"])["before_plan"] is False
    assert L._earlier("not a time", "2026-01-01") is False


def test_attach_refuses_unknown_ledgers_dbs_and_runs(db, jid, tmp_path):
    sig, _ = _ledgers(tmp_path)
    L.plan(db, jid)
    with pytest.raises(J.JournalError, match="ledger must be"):
        L.attach_ledger(db, tmp_path, "lab", "vibes", "x")
    with pytest.raises(J.JournalError, match="no ledger database"):
        L.attach_ledger(db, tmp_path, "lab", "signals", "x", ledger_db=str(tmp_path / "none.db"))
    with pytest.raises(J.JournalError, match="no signals run 'nope'"):
        L.attach_ledger(db, tmp_path, "lab", "signals", "nope", ledger_db=str(sig))


# ---- tables -------------------------------------------------------------------

def _two_runs(db, jid):
    e = L.plan(db, jid)
    a = J.add_run(db, e, raw_path="r/1", result={"name": "rules", "f1": 0.8123, "n": 500})
    b = J.add_run(db, e, raw_path="r/2", result={"name": "llm", "f1": 0.77, "n": 500})
    return a, b


def test_table_from_runs_passes_the_number_check(db, jid, tmp_path):
    a, b = _two_runs(db, jid)
    snippet = L.table(db, jid, [a, b], ["f1", "n", "missing"], label_key="name")
    assert snippet.splitlines()[0] == f"<!-- backed-by: run:{a},run:{b} -->"
    assert "| rules | 0.8123 | 500 |  |" in snippet and "| llm | 0.77 | 500 |  |" in snippet
    md = f"# Results\n\n{snippet}\n"
    assert C.check_numbers(db, tmp_path, jid, md).ok
    assert C.check_origin(db, jid, md).stats["generated"] == 1
    edited = md.replace("0.77", "0.79")
    out = C.check_numbers(db, tmp_path, jid, edited)
    assert not out.ok and "0.79 is not a value recorded" in out.findings[0]["message"]


def test_table_refuses_bad_input(db, jid):
    a, _ = _two_runs(db, jid)
    e = L.experiment(db, jid)["id"]
    bad = J.add_run(db, e, status="failed")
    with pytest.raises(J.JournalError, match="did not finish ok"):
        L.table(db, jid, [bad], ["f1"])
    with pytest.raises(J.JournalError, match="not part of this journal"):
        L.table(db, jid, [999], ["f1"])
    with pytest.raises(J.JournalError, match="at least one key"):
        L.table(db, jid, [a], [])
    with pytest.raises(J.JournalError, match="at least one run"):
        L.table(db, jid, [], ["f1"])


def test_block_proof_from_a_failed_run(db, jid, tmp_path):
    e = L.plan(db, jid)
    bad = J.add_run(db, e, status="failed")
    out = C.check_numbers(db, tmp_path, jid, f"<!-- backed-by: run:{bad} -->\n| a |\n|---|\n| 0.5 |")
    assert not out.ok and "did not finish ok" in out.findings[0]["message"]


def test_fmt():
    assert (L.fmt(3), L.fmt(2.0), L.fmt(0.123456), L.fmt(None), L.fmt("x"), L.fmt(True)) == \
        ("3", "2", "0.1235", "", "x", "True")


# ---- figures ------------------------------------------------------------------

def test_figure_is_registered_and_checked(db, jid, tmp_path):
    a, b = _two_runs(db, jid)
    made = L.figure(db, tmp_path, "lab", [a, b], "f1", "f1 by method!", caption="F1 by method",
                    label_key="name", unit="")
    assert made["markdown"] == "![F1 by method](figures/f1-by-method.svg)"
    svg = made["path"].read_text(encoding="utf-8")
    assert "<svg" in svg and "rules" in svg and "0.8123" in svg
    assert len(L.artifacts(db, jid)) == 1
    md = f"# Results\n\n{made['markdown']}\n"
    out = C.check_numbers(db, tmp_path, jid, md)
    assert out.ok and out.stats["figures"] == 1
    made["path"].write_text(svg.replace("0.8123", "0.9"), encoding="utf-8")
    assert "edited after it was made" in C.check_numbers(db, tmp_path, jid, md).findings[0]["message"]
    made["path"].unlink()
    assert "is missing" in C.check_numbers(db, tmp_path, jid, md).findings[0]["message"]
    other = C.check_numbers(db, tmp_path, jid, "# R\n\n![x](figures/hand-drawn.svg)\n")
    assert "not made from runs" in other.findings[0]["message"]


def test_figure_refuses_bad_input(db, jid, tmp_path):
    a, _ = _two_runs(db, jid)
    with pytest.raises(J.JournalError, match="no number at 'name'"):
        L.figure(db, tmp_path, "lab", [a], "name", "x")
    with pytest.raises(J.JournalError, match="give the figure a name"):
        L.figure(db, tmp_path, "lab", [a], "f1", "!!!")


def test_figure_of_zero_values_still_draws(db, jid, tmp_path):
    e = L.plan(db, jid)
    r = J.add_run(db, e, raw_path="r", result={"v": 0})
    assert "<rect" in L.figure(db, tmp_path, "lab", [r], "v", "zero")["path"].read_text(encoding="utf-8")


# ---- CLI ----------------------------------------------------------------------

@pytest.fixture
def cli_run(tmp_path, capsys):
    db, root = str(tmp_path / "journal.db"), str(tmp_path / "articles")

    def _run(action, *argv, ok=True):
        args = ["journal", action, "--db", db, "--root", root, *argv]
        if ok:
            cli.main(args)
        else:
            with pytest.raises(SystemExit) as exc:
                cli.main(args)
            assert exc.value.code == 1
        return capsys.readouterr().out

    _run.db = db
    return _run


def test_cli_plan_run_table_figure_show(cli_run, tmp_path):
    cli_run("new", "Bench")
    assert "no experiment yet" in cli_run("run", "bench", "--", sys.executable, "-c", "print(1)", ok=False)
    assert "experiment 1 planned for bench" in cli_run(
        "plan", "bench", "--hypothesis", "rules lose", "--metric", "F1", "--threshold", "+0.05",
        "--baseline", "rules", "--runs", "2", "--threat", "one labeller")
    out = cli_run("run", "bench", "--seed", "1", "--code", str(_repo(tmp_path)), "--",
                  sys.executable, "-c", WRITE_RESULT)
    assert "run 1: ok" in out and "raw data: bench/runs/1" in out and "result keys: accuracy" in out
    assert "code " in out
    out = cli_run("plan", "bench", "--threshold", "+0.10")
    assert "plan updated" in out and "1 change(s) after the lock" in out
    assert "experiment 2 planned" in cli_run("plan", "bench", "--new", "--metric", "latency")
    out = cli_run("table", "bench", "--runs", "1", "--keys", "accuracy,n", "--label", "label")
    assert "<!-- backed-by: run:1 -->" in out and "| rules | 0.912 | 500 |" in out
    out = cli_run("figure", "bench", "--runs", "1", "--key", "accuracy", "--name", "acc",
                  "--caption", "Accuracy")
    assert "![Accuracy](figures/acc.svg)" in out
    out = cli_run("show", "bench")
    assert "changed after the lock: threshold '+0.05' -> '+0.10'" in out
    assert "run 1: ok (command)" in out and "hypothesis: rules lose" in out
    assert "--runs takes run ids" in cli_run("table", "bench", "--runs", "a,b", "--keys", "x", ok=False)


def test_cli_run_failure_and_dirty_code(cli_run, tmp_path):
    cli_run("new", "Fails")
    cli_run("plan", "fails", "--runs", "1")
    out = cli_run("run", "fails", "--code", str(_repo(tmp_path, dirty=True)), "--",
                  sys.executable, "-c", "import sys; sys.exit(2)", ok=False)
    assert "run 1: failed" in out and "UNCOMMITTED CHANGES" in out
    assert "UNCOMMITTED CODE" in cli_run("show", "fails")


def test_cli_run_of_data_recorded_before_the_plan(cli_run):
    cli_run("new", "Recount")
    cli_run("plan", "recount", "--runs", "1")
    cli_run("run", "recount", "--data-before-plan", "--", sys.executable, "-c", "print(1)")
    assert "ran before the plan was written" in cli_run("show", "recount")


def test_cli_run_note_is_printed(cli_run):
    cli_run("new", "Noted")
    cli_run("plan", "noted")
    out = cli_run("run", "noted", "--", sys.executable, "-c",
                  "import os; open(os.path.join(os.environ['JESTER_RUN_DIR'], 'result.json'), 'w').write('x')",
                  ok=False)
    assert "note: result.json is not valid JSON" in out


def test_cli_attach_and_checklist(cli_run, tmp_path):
    sig, _ = _ledgers(tmp_path)
    cli_run("new", "Ledger")
    cli_run("plan", "ledger")
    out = cli_run("attach", "ledger", "--ledger", "signals", "--run-id", "hn-1", "--ledger-db", str(sig))
    assert "run 1: ok" in out and "result keys: run_id, status" in out
    out = cli_run("checklist", "ledger")
    assert "--  tuned" in out and "0/10 answered" in out
    out = cli_run("checklist", "ledger", "--set", "tuned=both on defaults", "--set", "errors = none")
    assert "ok  tuned" in out and "both on defaults" in out and "2/10 answered" in out
    assert "takes key=answer" in cli_run("checklist", "ledger", "--set", "tuned", ok=False)
    out = cli_run("show", "ledger")
    assert "checklist: 2/10 answered" in out and "ran before the plan was written" in out
