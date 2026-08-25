"""Nightly wrapper tests (§37.7): R55 exit contract pieces + notify hook."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jester.store import enqueue_batch, mark_batch_failed, open_db, start_run, finish_run, update_run_summary


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


# --- Task 1: render_tokens ---------------------------------------------------

def test_render_tokens_substitutes_context():
    from jester.notify import render_tokens

    out = render_tokens(
        ["/bin/notify", "--code", "{exit_code}", "--run", "{run_id}", "--why", "{reason}"],
        exit_code=3,
        run_id="r9",
        reason="FAILED_BATCHES",
    )
    assert out == ["/bin/notify", "--code", "3", "--run", "r9", "--why", "FAILED_BATCHES"]


def test_render_tokens_strict_on_unknown_key():
    from jester.notify import render_tokens

    with pytest.raises(KeyError):
        render_tokens(["{nope}"], exit_code=1)


# --- Task 2: dispatch_notify --------------------------------------------------

def test_dispatch_notify_runs_and_returns_stdout():
    from jester.notify import dispatch_notify

    ok, out = dispatch_notify(
        [sys.executable, "-c", "print('code={exit_code}')"],
        exit_code=7,
    )
    assert ok is True
    assert out.strip() == "code=7"


def test_dispatch_notify_swallows_missing_executable():
    from jester.notify import dispatch_notify

    ok, out = dispatch_notify(["definitely-not-a-real-binary-xyz"], exit_code=1)
    assert ok is False
    assert out == ""


# --- Task 3: cmd_notify -------------------------------------------------------

_HELPER = (
    "import sys\n"
    "Path = __import__('pathlib').Path\n"
    f"Path(sys.argv[2]).write_text(f'{{sys.argv[1]}}')\n"
)


def test_cmd_notify_runs_hook_and_always_exits_zero(tmp_path):
    from jester.cli import cmd_notify

    helper = tmp_path / "hook.py"
    helper.write_text(_HELPER)
    outfile = tmp_path / "out.txt"

    cmd_notify(_ns(exit_code=1, db=None, run_id=None,
                   cmd=[sys.executable, str(helper), "{exit_code}|{reason}", str(outfile)]))

    assert outfile.read_text() == "1|hard_error"  # no --db -> default reason


def test_cmd_notify_reason_from_failed_batches_run(tmp_path):
    from jester.cli import cmd_notify

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    rid = start_run(db, "nightly-x")
    bid = enqueue_batch(db, "reddit", "src", "tid", [{"body": "x", "fingerprint": "z"}])
    mark_batch_failed(db, bid)
    finish_run(db, "nightly-x", status="completed", floor_flags=["FAILED_BATCHES"])

    helper = tmp_path / "hook.py"
    helper.write_text(_HELPER)
    outfile = tmp_path / "out.txt"

    cmd_notify(_ns(exit_code=1, db=str(db_path), run_id="nightly-x",
                   cmd=[sys.executable, str(helper), "{exit_code}|{reason}", str(outfile)]))

    assert outfile.read_text() == "1|FAILED_BATCHES"


def test_cmd_notify_reason_hard_error_when_no_failure_flags(tmp_path):
    from jester.cli import cmd_notify

    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    start_run(db, "nightly-y")
    finish_run(db, "nightly-y", status="aborted", floor_flags=[])

    helper = tmp_path / "hook.py"
    helper.write_text(_HELPER)
    outfile = tmp_path / "out.txt"

    cmd_notify(_ns(exit_code=2, db=str(db_path), run_id="nightly-y",
                   cmd=[sys.executable, str(helper), "{exit_code}|{reason}", str(outfile)]))

    assert outfile.read_text() == "2|hard_error"


# --- Task 4: scripts/nightly.sh ----------------------------------------------

def _working_bash():
    bash = shutil.which("bash")
    if not bash:
        return None
    try:
        subprocess.run([bash, "-c", "true"], timeout=5, check=True)
        return bash
    except Exception:
        return None


def test_nightly_script_syntax_valid():
    bash = _working_bash()
    if not bash:
        pytest.skip("no working bash on this machine (WSL stub)")
    script = Path(__file__).resolve().parents[2] / "scripts" / "nightly.sh"
    proc = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
