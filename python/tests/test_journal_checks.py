"""The proof checks (J1): reading the Markdown, saving copies of sources,
the four checks, and the fixture gates G2-G4.

Offline throughout: the web is a FakeWeb that answers only what a test lists.
"""
import io
import json
import urllib.error

import pytest

from jester import cli
from jester import journal as J
from jester.journal import checks as C
from jester.journal import fixtures as F
from jester.journal import snapshots as S
from jester.journal import states as js
from jester.journal import text as T

URL = "https://blog.example.org/post"


@pytest.fixture
def db(tmp_path):
    conn = J.open_journal(str(tmp_path / "j.db"))
    yield conn
    conn.close()


def _page(text, status=200, ctype="text/html; charset=utf-8", **kw):
    return {"status": status, "content_type": ctype, "body": f"<html><body><p>{text}</p></body></html>", **kw}


# ---- text ---------------------------------------------------------------------

def test_blocks_kinds_lines_and_backing():
    md = ("# Title\n\nA paragraph\nthat wraps.\n\n- item one\n  continued\n- item two\n\n"
          "> quoted\n\n<!-- backed-by: run:3 -->\n| a | b |\n|---|---|\n\n```\ncode 42\n```\n"
          "<!--\nmulti-line\n-->\nAfter.")
    kinds = [(b.kind, b.line) for b in T.blocks(md)]
    assert kinds == [("heading", 1), ("paragraph", 3), ("list", 6), ("quote", 10),
                     ("comment", 12), ("table", 13), ("code", 16), ("comment", 19), ("paragraph", 22)]
    table = [b for b in T.blocks(md) if b.kind == "table"][0]
    assert table.backing == "run:3"
    assert [b for b in T.blocks(md) if b.kind == "paragraph"][-1].backing == ""


def test_a_byte_order_mark_does_not_hide_the_title():
    md = "﻿# What PEP 20 says\n\nBody."
    assert T.title_of(md) == "What PEP 20 says"
    assert C.check_lint(md).ok
    assert C.check_numbers(None, None, 0, md).ok


def test_links_mask_and_offsets():
    text = "See [the post](https://x.org/a?b=1) and `code 9` <!-- 7 --> ![img](i.png)"
    lks = T.links(text)
    assert [(ln.text, ln.target, ln.image, ln.external) for ln in lks] == [
        ("the post", "https://x.org/a?b=1", False, True), ("img", "i.png", True, False)]
    masked = T.mask(text)
    assert len(masked) == len(text)
    assert "x.org" not in masked and "9" not in masked and "7" not in masked
    assert "the post" in masked


def test_normalize_title_and_quote_body():
    assert T.normalize("It’s  “FAST”—*really*…") == "it's \"fast\"-really..."
    assert T.title_of("intro\n\n# The Title\n\n## Sub") == "The Title"
    assert T.title_of("no heading") == ""
    block = T.blocks("> line one\n> line two\n> — [Author](https://a.org)")[0]
    assert T.quote_body(block) == "line one line two"
    assert T.quote_body(T.blocks("> — just a credit")[0]) is None
    assert T.paragraph_hash("A  b") == T.paragraph_hash("a b")


# ---- snapshots ----------------------------------------------------------------

def test_take_fetches_html_and_stores_text(db, tmp_path):
    web = F.FakeWeb({URL: _page("Hello &amp; welcome", final_url=URL + "/moved")})
    row = S.take(db, tmp_path, URL + "#section", opener=web)
    assert (row["url"], row["status"], row["final_url"], row["method"]) == (URL, 200, URL + "/moved", "fetch")
    assert S.read_text(tmp_path, row) == "Hello & welcome"
    assert S.latest(db, URL)["id"] == row["id"] and S.is_ok(row)
    assert S.describe(row) == "ok"


def test_take_records_dead_unreachable_and_unreadable(db, tmp_path):
    web = F.FakeWeb({URL: {"status": 404, "reason": "Not Found"},
                     URL + ".pdf": {"status": 200, "content_type": "application/pdf", "body": "%PDF"}})
    dead = S.take(db, tmp_path, URL, opener=web)
    assert dead["status"] == 404 and "dead (HTTP 404)" in S.describe(dead)
    gone = S.take(db, tmp_path, "https://nowhere.example/x", opener=web)
    assert gone["status"] == 0 and "could not reach it" in S.describe(gone)
    pdf = S.take(db, tmp_path, URL + ".pdf", opener=web)
    assert S.is_ok(pdf) and S.read_text(tmp_path, pdf) is None
    assert "no readable text" in S.describe(pdf)
    assert "no saved copy yet" in S.describe(None)


def test_manual_copy_wins_and_is_marked(db, tmp_path):
    src = tmp_path / "extracted.txt"
    src.write_text("Text pulled from the PDF.", encoding="utf-8")
    row = S.take(db, tmp_path, URL, from_file=src)
    assert row["method"] == "manual" and S.read_text(tmp_path, row) == "Text pulled from the PDF."


def test_readable_text_types_and_charsets():
    assert S.readable_text(b"a,b", "text/csv") == "a,b"
    assert S.readable_text("caf\xe9".encode("latin-1"), "text/plain; charset=latin-1") == "caf\xe9"
    assert S.readable_text(b"x", "text/plain; charset=nonsense") == "x"
    assert S.readable_text(b"\x89PNG", "image/png") is None
    assert S.html_to_text("<style>p{}</style><script>x()</script><p>One</p><p>Two</p>") == "One\n\nTwo"


def test_fetch_never_raises(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("timed out")
    assert S.fetch(URL, opener=boom)["status"] == 0

    class Resp(io.BytesIO):
        headers = None

        def getcode(self):
            return 200
    got = S.fetch(URL, opener=lambda req, timeout=None: Resp(b"raw"))
    assert (got["status"], got["final_url"], got["content_type"], got["body"]) == (200, URL, "", b"raw")


def test_read_text_of_a_deleted_file(db, tmp_path):
    row = S.take(db, tmp_path, URL, opener=F.FakeWeb({URL: _page("x")}))
    (tmp_path / row["text_path"]).unlink()
    assert S.read_text(tmp_path, row) is None


# ---- the checks -----------------------------------------------------------------

def test_lint_title_rules():
    assert C.lint_title("") == ["the article has no # title"]
    assert C.lint_title("We measured four dedup methods on 113k insights") == []
    problems = C.lint_title("YOU WON'T BELIEVE THIS RESULT!")
    assert any("capitals" in p for p in problems) and any("exclamation" in p for p in problems)
    assert any("clickbait" in p for p in problems)
    assert any("clickbait" in p for p in C.lint_title("7 ways to speed up SQLite"))
    assert any("characters" in p for p in C.lint_title("a" * 101))


def test_lint_counts_structure_signals():
    md = ("# Fine title\n\nHave you ever wondered? This **is** a **lot** of **bold** text — really "
          "— truly — honestly — wow! Amazing! \U0001F680")
    score, findings, stats = C.lint_prose(md)
    messages = " | ".join(f["message"] for f in findings)
    for want in ("opener", "em dashes", "bold phrases", "exclamation marks", "emoji"):
        assert want in messages
    assert score >= C.FAIL_AT and stats["em_dashes"] == 4
    outcome = C.check_lint(md)
    assert not outcome.ok


def test_lint_warnings_below_the_threshold_do_not_fail():
    outcome = C.check_lint("# A plain title\n\nAdditionally, the cache halved the misses.")
    assert outcome.ok and outcome.findings


def test_origin_check(db):
    jid = J.create_journal(db, "Origin")["id"]
    setup = "The test ran on one laptop with the default settings."
    voiced = "We think this result should hold in general."
    observed = "Labelling took [4 hours](obs:log)."
    for para in (setup, voiced, observed):
        J.register_generated(db, jid, para, role="data-writer")
    md = f"# T\n\n{setup}\n\n{voiced}\n\n{observed}\n\nI wrote this one myself and I stand by it."
    out = C.check_origin(db, jid, md)
    messages = " | ".join(f["message"] for f in out.findings)
    assert not out.ok
    assert "first person" in messages and "gives an opinion" in messages and "observation" in messages
    assert out.stats == {"paragraphs": 4, "generated": 3}
    assert C.check_origin(db, jid, f"# T\n\n{setup}\n\nMine.").ok


def test_an_edited_generated_paragraph_becomes_yours(db):
    jid = J.create_journal(db, "Edited")["id"]
    J.register_generated(db, jid, "We recommend the rules.", role="data-writer")
    assert not C.check_origin(db, jid, "We recommend the rules.").ok
    assert C.check_origin(db, jid, "We recommend the rules, for now.").ok


def _saved_journal(db, tmp_path, body):
    from jester.journal import files as jfiles

    j = J.create_journal(db, "Checked", kind="note", question="q", path="checked/index.md")
    path = tmp_path / "checked" / "index.md"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    J.record_revision(db, j["id"], "sha", jfiles.content_sha256(path), j["path"])
    return j, path


def test_check_saved_stores_a_verdict_per_check(db, tmp_path):
    S.take(db, tmp_path, URL, opener=F.FakeWeb({URL: _page("readers trust numbers they can reproduce")}))
    body = f'# A careful title\n\nThe [post]({URL}) says "readers trust numbers they can reproduce".\n'
    j, _ = _saved_journal(db, tmp_path, body)
    rev, outcomes = C.check_saved(db, tmp_path, j["slug"])
    assert [o.kind for o in outcomes] == list(J.REQUIRED_CHECKS)
    assert all(o.ok for o in outcomes)
    assert J.latest_checks(db, rev["id"]) == {k: True for k in J.REQUIRED_CHECKS}
    stored = db.execute("SELECT detail FROM check_result WHERE kind='citations'").fetchone()
    assert json.loads(stored["detail"])["stats"]["quotes_found"] == 1


def test_check_results_open_the_review_gate(db, tmp_path):
    j, _ = _saved_journal(db, tmp_path, "# A careful title\n\nNothing to prove here.\n")
    J.approve(db, j["id"], "choose")
    for s in ("proposed", "chosen", "researching", "drafting"):
        js.move(db, j["slug"], s)
    assert "no citations check" in " ".join(js.blockers(db, J.get(db, j["slug"]), "in_review"))
    C.check_saved(db, tmp_path, j["slug"])
    assert js.move(db, j["slug"], "in_review")["state"] == "in_review"


def test_check_saved_refuses_unsaved_or_missing(db, tmp_path):
    j, path = _saved_journal(db, tmp_path, "# T\n\nText.\n")
    path.write_text("# T\n\nText, edited.\n", encoding="utf-8")
    with pytest.raises(J.JournalError, match="unsaved changes"):
        C.check_saved(db, tmp_path, j["slug"])
    path.unlink()
    with pytest.raises(J.JournalError, match="nothing saved"):
        C.check_saved(db, tmp_path, j["slug"])


def test_numbers_in_run_results_can_be_nested_lists(db, tmp_path):
    jid = J.create_journal(db, "Nested")["id"]
    run = J.add_run(db, J.add_experiment(db, jid), result={"folds": [{"f1": 0.8}, {"f1": 0.75}]})
    ok = C.check_numbers(db, tmp_path, jid, f"Fold two scored [0.75](run:{run}#folds.1.f1).")
    bad = C.check_numbers(db, tmp_path, jid, f"Fold three scored [0.7](run:{run}#folds.2.f1).")
    assert ok.ok and not bad.ok
    assert not C.check_numbers(db, tmp_path, jid, "It scored [0.7](run:abc).").ok


# ---- gates --------------------------------------------------------------------

def test_g2_citations_gate():
    gate = F.check_citations_gate()
    assert gate["pass"], [c for c in gate["cases"] if not c["pass"]]
    assert len(gate["cases"]) >= 30


def test_g3_numbers_gate():
    gate = F.check_numbers_gate()
    assert gate["pass"], [c for c in gate["cases"] if not c["pass"]]
    assert gate["planted"] >= F.MIN_PLANTED


def test_g4_lint_gate():
    gate = F.check_lint_gate()
    assert gate["pass"], (gate["ai_flagged"], gate["human_flagged"])
    assert (gate["ai"], gate["human"]) == (15, 15)


def test_lint_holdout_does_not_regress():
    """Measured once on paragraphs the rules never saw: 10/15 and 0/15. This
    is a floor so a rule change cannot quietly make it worse."""
    held = F.check_lint_gate(name="lint_holdout.json")
    assert held["ai_flagged"] >= 10 and held["human_flagged"] <= 1


def test_fixture_files_must_exist_and_be_lists(tmp_path):
    with pytest.raises(F.FixtureError, match="missing fixture"):
        F.check_lint_gate(tmp_path)
    (tmp_path / "lint.json").write_text("{}", encoding="utf-8")
    with pytest.raises(F.FixtureError, match="non-empty list"):
        F.check_lint_gate(tmp_path)


def test_a_wrong_reason_is_a_miss(tmp_path):
    case = {"id": "x", "want": "fail", "reason": "something else entirely",
            "article": 'Everyone says "this quote has no source at all".', "web": {}}
    (tmp_path / "citations.json").write_text(json.dumps([case]), encoding="utf-8")
    gate = F.check_citations_gate(tmp_path)
    assert not gate["pass"] and gate["cases"][0]["got"] == "fail"


# ---- CLI ----------------------------------------------------------------------

@pytest.fixture
def run(tmp_path, capsys):
    db, root = str(tmp_path / "journal.db"), str(tmp_path / "articles")

    def _run(*argv, ok=True):
        args = ["journal", argv[0], *argv[1:], "--db", db, "--root", root]
        if ok:
            cli.main(args)
        else:
            with pytest.raises(SystemExit) as exc:
                cli.main(args)
            assert exc.value.code == 1
        return capsys.readouterr().out

    _run.db, _run.root = db, root
    return _run


def test_cli_fixtures(run):
    out = run("fixtures")
    assert "PASS  G2 citations: 31/31 cases" in out
    assert "PASS  G3 numbers" in out and "12 planted" in out
    assert "PASS  G4 lint" in out and "held out" in out


def test_cli_snapshot_and_check(run, tmp_path, monkeypatch):
    run("new", "Checked note", "--kind", "note", "--slug", "cn")
    path = tmp_path / "articles" / "cn" / "index.md"
    path.write_text(f'# Checked note\n\nThe [post]({URL}) says "readers trust numbers they can reproduce".\n'
                    f"It took 3 days.\n", encoding="utf-8")
    run("save", "cn")
    out = run("check", "cn", ok=False)
    assert "FAIL  citations" in out and "no saved copy yet" in out
    assert "FAIL  numbers" in out and "3 has no proof" in out

    web = F.FakeWeb({URL: _page("Readers trust numbers they can reproduce.")})
    monkeypatch.setattr(S.urllib.request, "urlopen", web)
    out = run("snapshot", "cn")
    assert f"ok    {URL} (fetch, 200)" in out
    assert "kept" in run("snapshot", "cn")
    out = run("check", "cn", ok=False)
    assert "ok    citations" in out and "FAIL  numbers" in out

    path.write_text(path.read_text(encoding="utf-8").replace("3 days", "[3 days](obs:diary)"),
                    encoding="utf-8")
    assert "unsaved changes" in run("check", "cn", ok=False)
    run("save", "cn")
    out = run("check", "cn")
    assert "ok    numbers" in out and "observation 1" in out


def test_cli_snapshot_options(run, tmp_path, monkeypatch):
    run("new", "Opts", "--kind", "note")
    assert "no links to save" in run("snapshot", "opts")
    assert "--from-file needs exactly one --url" in run("snapshot", "opts", "--from-file", "x.txt", ok=False)
    src = tmp_path / "copy.txt"
    src.write_text("manual words", encoding="utf-8")
    out = run("snapshot", "opts", "--url", URL, "--from-file", str(src))
    assert f"ok    {URL} (manual, 200)" in out
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr(S.urllib.request, "urlopen", F.FakeWeb({}))
    path = tmp_path / "articles" / "opts" / "index.md"
    path.write_text("# Opts\n\n[a](https://a.example/x) and [b](https://b.example/y)\n", encoding="utf-8")
    out = run("snapshot", "opts")
    assert "FAIL  https://a.example/x" in out and "could not reach it" in out
    assert sleeps == [1.0]
    out = run("snapshot", "opts", "--url", URL, "--refresh")
    assert "FAIL" in out
    path.unlink()
    assert "article file is missing" in run("snapshot", "opts", ok=False)


def test_cli_snapshot_reports_a_page_without_text(run, monkeypatch):
    run("new", "Pdf", "--kind", "note")
    monkeypatch.setattr(S.urllib.request, "urlopen", F.FakeWeb(
        {URL: {"status": 200, "content_type": "application/pdf", "body": "%PDF"}}))
    out = run("snapshot", "pdf", "--url", URL)
    assert "saved" in out and "no readable text" in out


def test_cli_fixtures_reports_a_broken_set(run, monkeypatch, tmp_path):
    monkeypatch.setattr(F, "CASES_DIR", tmp_path / "nowhere")
    assert "missing fixture file" in run("fixtures", ok=False)


def test_cli_fixtures_exit_one_when_a_gate_fails(run, monkeypatch):
    real = F.check_all

    def broken(directory=None):
        gates = real(directory)
        gates[0]["pass"] = False
        gates[0]["cases"][0]["pass"] = False
        return gates
    monkeypatch.setattr(F, "check_all", broken)
    out = run("fixtures", ok=False)
    assert "FAIL  G2 citations" in out and "miss quote-found" in out
