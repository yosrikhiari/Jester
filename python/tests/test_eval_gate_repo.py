"""The eval gate runs in CI against the repo's own pairs (eval-gate wiring).

Why this file exists: `cmd_eval` shipped with two gaps that tests could not
see. First, it read `eval/known_good.md` without `encoding="utf-8"`, so the
default codec on Windows (1252) or POSIX (fontspec) was load-bearing for a
file that is ascii today but need not stay so; the ci.yml header documents
this exact bug class (a requirements.txt byte pip could not decode). Second,
CI never ran the gate at all — the scoring formula it calibrates had no clean
clone exercising it. These tests pin:

- the repo's committed pairs pass the repo's committed thresholds (this is
  what the CI step runs; here it runs in-process with the same paths);
- a known-good line whose subscores fail the gate exits 1 with a per-case
  FAIL count above zero (the mutation case: the gate must reject, not shrug);
- the reader accepts non-ascii titles, which the utf-8 read guarantees.

`offline_config` is deliberately NOT used: `cmd_eval` is pure file math
(`compute_overall` against `thresholds.good_idea_min`, no model call), so the
repo `config/` dir is pointed at directly — that mirrors the CI invocation
(`python -m jester.cli eval`, which defaults `--config` to the repo config).
"""
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from jester.cli_review import cmd_eval

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO_ROOT / "config"
REPO_GOOD = REPO_ROOT / "eval" / "known_good.md"
REPO_BAD = REPO_ROOT / "eval" / "known_bad.md"


def _eval_ns(good, bad):
    """Args namespace for cmd_eval; bad=None means 'skip that file'."""
    return Namespace(
        config=str(REPO_CONFIG),
        good=str(good) if good is not None else None,
        bad=str(bad) if bad is not None else None,
    )


def test_repo_eval_files_pass_the_repo_gate(capsys):
    """The committed pairs satisfy the committed threshold in the committed
    config. This is the CI step's assertion, in-process where pytest can see
    the exit path — CI runs the same thing through `python -m jester.cli eval`
    and relies on a nonzero exit code for failure."""
    cmd_eval(_eval_ns(REPO_GOOD, REPO_BAD))  # no SystemExit = success
    out = capsys.readouterr().out
    assert "eval OK" in out
    assert "eval FAILED" not in out


def test_known_good_line_that_fails_the_gate_exits_1(tmp_path, capsys):
    """Mutation case: a line claimed known-good whose subscores land below
    the gate. The gate must count the mismatch, print FAIL rows, and exit 1 —
    a gate that shrugs (exits 0, prints nothing) would let the formula drift
    silently. Uses tmp files; the repo's real pairs are untouched."""
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    # Competition 9 drags the weighted overall under every plausible gate —
    # the same shape as the shipped known_bad rows, only in the wrong file.
    good.write_text(
        "Niche meme generator | 3 | 4 | 7\n"
        "Yet another todo app | 2 | 3 | 8\n",
        encoding="utf-8",
    )
    bad.write_text(
        "# empty is fine — the failure we want is in the good file\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        cmd_eval(_eval_ns(good, bad))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "eval FAILED" in out
    # 2 mismatched rows, each named — the operator needs the count and the
    # offending titles, not just a bare failure.
    assert out.count("FAIL [") == 2


def test_eval_reader_accepts_non_ascii_titles(tmp_path, capsys):
    """The utf-8 read's regression: an eval pair whose titles carry non-ascii
    bytes. On the pre-fix reader this crashed with UnicodeDecodeError on hosts
    whose default codec is 1252 (Windows), or read mojibake. The same line is
    registered in BOTH files: it passes the gate in the good file and fails it
    in the bad file, so the FAIL printer must echo the title intact — proof
    that the line was decoded (a mid-byte crash or silent replacement would
    break either the count or the name echoed)."""
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    for f in (good, bad):
        f.write_text("Gestionnaire de sauvegardes — auto | 9 | 8 | 3\n",
                     encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval(_eval_ns(good, bad))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    # The good row passed but the run failed overall, so the OK line never
    # prints; the failure line names both counts and echoes the bad row's
    # title intact.
    assert "1/2 mismatched" in out
    assert "Gestionnaire de sauvegardes — auto" in out
    assert "\ufffd" not in out  # no replacement chars 
