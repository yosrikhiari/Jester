"""Close the Quality Gate: behavioral tests for new-code lines.

Each section names the Sonar gap it covers. Every test below asserts an
observable behavior (rows archived, output printed, status returned) — not
the coverage itself. If a test cannot state what bug it catches, it does not
belong here.
"""
import json
from argparse import Namespace
from pathlib import Path

import pytest

from jester.cli_review import (_parse_eval_line, cmd_eval, cmd_list, cmd_mark,
                               cmd_runs, cmd_show)
from jester.models import Idea, Nugget
from jester.store import (enqueue_batch, get_idea, insert_idea, insert_nugget,
                          open_db, start_run)

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def _ns(**kw):
    return Namespace(**kw)


# ---- cli_review: eval ------------------------------------------------------

EVAL_GOOD = "Auto-backup before updates | 9 | 8 | 3\n"
EVAL_BAD = "Niche meme generator | 3 | 4 | 7\n"


@pytest.mark.parametrize("line,expected", [
    ("# a comment", None),
    ("", None),
    ("title only, no scores", None),
    ("t | not-a-number | 8", None),
    ("T | 9 | 8 | 3", ("T", 9.0, 8.0, 3.0)),
    ("T | 9 | 8", ("T", 9.0, 8.0, None)),
    ("T | 9 | 8 | -", ("T", 9.0, 8.0, None)),
    ("T | 9 | 8 | unchecked", ("T", 9.0, 8.0, None)),
])
def test_parse_eval_line_rows(line, expected):
    assert _parse_eval_line(line) == expected


def test_cmd_eval_passes_on_matching_sets(tmp_path, offline_config, capsys):
    good = tmp_path / "good.md"
    bad = tmp_path / "bad.md"
    good.write_text(EVAL_GOOD, encoding="utf-8")
    bad.write_text(EVAL_BAD, encoding="utf-8")
    cmd_eval(_ns(config=str(offline_config), good=str(good), bad=str(bad)))
    assert "eval OK" in capsys.readouterr().out


def test_cmd_eval_fails_loudly_on_mismatch(tmp_path, offline_config, capsys):
    good = tmp_path / "good.md"
    good.write_text("Actually terrible | 1 | 1 | 9\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval(_ns(config=str(offline_config), good=str(good), bad=None))
    assert exc.value.code == 1
    assert "eval FAILED" in capsys.readouterr().out


# ---- cli_review: mark / show / list / runs ----------------------------------

def _idea_db(tmp_path):
    db = open_db(str(tmp_path / "j.db"))
    db.execute("INSERT INTO nuggets(unique_key, category, raw_text, "
               "extracted_insight) VALUES('k-cited', 'pain_point', 'body', 'insight')")
    cited = insert_idea(db, Idea(title="cited idea", problem_statement="p",
                                 proposed_solution="s",
                                 supporting_nuggets=["k-cited"]))
    orphan = insert_idea(db, Idea(title="orphan idea", problem_statement="p",
                                  proposed_solution="s",
                                  supporting_nuggets=["k-missing"],
                                  needs_score=True))
    return db, cited, orphan


def test_cmd_mark_moves_status_and_names_missing(tmp_path, capsys):
    db, cited, _orphan = _idea_db(tmp_path)
    db.close()
    cmd_mark(_ns(db=str(tmp_path / "j.db"), id=cited, status="reviewed"))
    assert get_idea(open_db(str(tmp_path / "j.db")), cited)["status"] == "reviewed"
    with pytest.raises(SystemExit):
        cmd_mark(_ns(db=str(tmp_path / "j.db"), id=99999, status="reviewed"))
    assert "not found" in capsys.readouterr().out


def test_cmd_show_renders_evidence_and_missing_citations(tmp_path, capsys):
    db, cited, orphan = _idea_db(tmp_path)
    db.close()
    cmd_show(_ns(db=str(tmp_path / "j.db"), id=cited))
    out = capsys.readouterr().out
    assert "cited idea" in out and "k-cited" in out
    cmd_show(_ns(db=str(tmp_path / "j.db"), id=orphan))
    out = capsys.readouterr().out
    assert "MISSING citation: k-missing" in out
    assert "needs_score" in out
    with pytest.raises(SystemExit):
        cmd_show(_ns(db=str(tmp_path / "j.db"), id=99999))


def test_cmd_list_and_runs_report_totals(tmp_path, capsys):
    db, _cited, _orphan = _idea_db(tmp_path)
    insert_nugget(db, Nugget(unique_key="k-loose", platform="hn",
                             category="question", extracted_insight="q?",
                             raw_text="why?"))
    start_run(db, "review-run")
    db.close()
    cmd_list(_ns(db=str(tmp_path / "j.db")))
    out = capsys.readouterr().out
    assert "total: 2 nuggets" in out and "total: 2 idea(s)" in out
    cmd_runs(_ns(db=str(tmp_path / "j.db")))
    assert "review-run" in capsys.readouterr().out


# ---- cli.py: batched cmd_run path --------------------------------------------

def _queue(db_path):
    db = open_db(str(db_path))
    enqueue_batch(db, "reddit", "https://reddit.test/t/0", "t0", [
        {"body": "The nightly backup fails silently after upgrades", "fingerprint": "g1"},
        {"body": "Restore drills show empty backup files marked green", "fingerprint": "g2"},
    ])
    db.close()


def _set_knob(config_dir, key, value):
    path = Path(config_dir) / "thresholds.yaml"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
             if ln.split(":", 1)[0].strip() != key]
    lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cmd_treat_batch_path_archives_like_legacy(tmp_path, offline_config,
                                                   monkeypatch):
    """The grouping loop, chunk call, and shared triage in cmd_run: same
    archive as the one-call-per-comment path on identical input."""
    from jester.cli import cmd_treat

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    legacy_db = tmp_path / "legacy.db"
    batched_db = tmp_path / "batched.db"
    _queue(legacy_db)
    _queue(batched_db)
    cmd_treat(Namespace(config=str(offline_config), db=str(legacy_db),
                        run="gap-legacy", force=False))
    _set_knob(offline_config, "extractor_batch_size", 10)
    cmd_treat(Namespace(config=str(offline_config), db=str(batched_db),
                        run="gap-batched", force=False))

    def rows(p):
        db = open_db(str(p))
        try:
            return db.execute(
                "SELECT unique_key, extracted_insight, category, extractor_model "
                "FROM nuggets ORDER BY unique_key").fetchall()
        finally:
            db.close()

    assert rows(legacy_db) == rows(batched_db)
    assert rows(legacy_db), "seed produced nothing to compare"


def test_cmd_treat_batch_path_stops_on_quota(tmp_path, offline_config,
                                             monkeypatch):
    """Quota death mid-run stops the batched loop the same way: the first
    chunk archives, later groups stay queued instead of spending refused
    requests."""
    from jester.cli import cmd_treat
    from jester.llm import FakeLLM

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    _set_knob(offline_config, "extractor_batch_size", 2)
    db_path = tmp_path / "j.db"
    db = open_db(str(db_path))
    enqueue_batch(db, "reddit", "https://reddit.test/t/0", "t0", [
        {"body": "The nightly backup fails silently after every upgrade",
         "fingerprint": "q1"},
        {"body": "Restore drills show empty backup files marked green",
         "fingerprint": "q2"},
        {"body": "Nobody notices failed backups until a restore is needed",
         "fingerprint": "q3"},
    ])
    db.close()

    class QuotaDiesMidRun(FakeLLM):
        def extract_batch(self, bodies):
            out = super().extract_batch(bodies)
            self.rate_limited = 1  # the allowance died serving this chunk
            return out

    stub = QuotaDiesMidRun()
    monkeypatch.setattr("jester.cli.select_extractor_llm",
                        lambda thresholds: stub)
    cmd_treat(Namespace(config=str(offline_config), db=str(db_path),
                        run="gap-quota", force=False))
    db2 = open_db(str(db_path))
    kept = db2.execute("SELECT COUNT(*) FROM nuggets").fetchone()[0]
    left = db2.execute(
        "SELECT COUNT(*) FROM ingest_batch WHERE status='pending'").fetchone()[0]
    assert kept == 2 and left == 1


def test_cmd_treat_batch_mismatch_falls_back_per_comment(
        tmp_path, offline_config, monkeypatch):
    """A short batch reply inside a real treat degrades to one call per
    comment rather than misattributing drafts — archive stays correct."""
    from jester.cli import cmd_treat

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    _set_knob(offline_config, "extractor_batch_size", 10)
    db_path = tmp_path / "j.db"
    _queue(db_path)
    # The agents level never returns short (it falls back internally), so
    # the CLI-level guard is driven directly: empty reply, per-item recovery.
    monkeypatch.setattr("jester.cli.extract_batch", lambda *a, **k: [])
    cmd_treat(Namespace(config=str(offline_config), db=str(db_path),
                        run="gap-short", force=False))
    db2 = open_db(str(db_path))
    keys = [r[0] for r in db2.execute(
        "SELECT unique_key FROM nuggets ORDER BY unique_key").fetchall()]
    assert keys == ["reddit:t0:g1", "reddit:t0:g2"]


# ---- cli.py: dedup-sweep command + reembed chunk paths -------------------------

def test_cmd_dedup_sweep_reports_best_f1(tmp_path, offline_config, capsys):
    from jester.cli import cmd_dedup_sweep

    pairs = tmp_path / "pairs.jsonl"
    pairs.write_text("\n".join([
        json.dumps({"a": "deploy takes all weekend",
                    "b": "deploy takes all weekend", "same": True}),
        json.dumps({"a": "deploy takes all weekend",
                    "b": "quarterly tax deadlines", "same": False}),
    ]), encoding="utf-8")
    cmd_dedup_sweep(_ns(pairs=str(pairs), config=str(offline_config),
                        min_pairs=20))
    out = capsys.readouterr().out
    assert "best F1:" in out and "pairs: 2" in out


def test_cmd_reembed_chunk_path_clears_via_batch(tmp_path, offline_config,
                                                 monkeypatch):
    """The upsert_many chunk loop: one batch call drains the backlog."""
    from jester.cli import cmd_reembed
    from jester.embed import FakeEmbedding
    from jester.vector import VectorStore

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    db = open_db(str(tmp_path / "j.db"))
    for k in ("b1", "b2", "b3"):
        insert_nugget(db, Nugget(unique_key=k, platform="hn",
                                 category="pain_point", extracted_insight="x",
                                 raw_text="body", needs_reembed=True))
    db.close()
    calls = []
    store = VectorStore.in_memory(FakeEmbedding())
    orig = store.upsert_many

    def counting(items):
        calls.append(list(items))
        return orig(items)

    monkeypatch.setattr(store, "upsert_many", counting)
    monkeypatch.setattr("jester.cli._vector_for", lambda cfg, p: store)
    cmd_reembed(_ns(db=str(tmp_path / "j.db"), config=str(offline_config),
                    pending=True))
    assert len(calls) == 1 and len(calls[0]) == 3
    db2 = open_db(str(tmp_path / "j.db"))
    still = db2.execute(
        "SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1").fetchone()[0]
    assert still == 0


def test_cmd_reembed_chunk_failure_falls_back_per_row(tmp_path, offline_config,
                                                      monkeypatch, capsys):
    """A failed chunk must not fail the backlog: per-row retry reports each
    row's own error and exits 1 with flags kept."""
    from jester.cli import cmd_reembed

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    db = open_db(str(tmp_path / "j.db"))
    insert_nugget(db, Nugget(unique_key="f1", platform="hn",
                             category="pain_point", extracted_insight="x",
                             raw_text="body", needs_reembed=True))
    db.close()

    class ChunkBoom:
        def upsert_many(self, items):
            raise RuntimeError("batch endpoint down")

        def upsert(self, *a, **k):
            raise RuntimeError("single endpoint down too")

    monkeypatch.setattr("jester.cli._vector_for", lambda cfg, p: ChunkBoom())
    with pytest.raises(SystemExit) as exc:
        cmd_reembed(_ns(db=str(tmp_path / "j.db"), config=str(offline_config),
                        pending=True))
    assert exc.value.code == 1
    assert "reembed FAIL f1" in capsys.readouterr().out


# ---- console api.reembed -------------------------------------------------------

def test_api_reembed_batch_and_fallback(tmp_path, offline_config, monkeypatch):
    """Same two paths through the console action: batch drain, then the
    per-row fallback when the batch call fails."""
    from jester.console.api import ConsoleAPI
    from jester.embed import FakeEmbedding
    from jester.vector import VectorStore

    monkeypatch.delenv("JESTER_QDRANT_URL", raising=False)
    main = str(tmp_path / "j.db")
    db = open_db(main)
    for k in ("c1", "c2"):
        insert_nugget(db, Nugget(unique_key=k, platform="hn",
                                 category="pain_point", extracted_insight="x",
                                 raw_text="body", needs_reembed=True))
    db.close()
    monkeypatch.setattr("jester.console.api._vector_for",
                        lambda cfg, p: VectorStore.in_memory(FakeEmbedding()))
    res = ConsoleAPI(main, str(offline_config)).reembed()
    assert res == {"ok": True, "fixed": 2, "failed": 0, "last_error": None}

    db = open_db(main)
    insert_nugget(db, Nugget(unique_key="c3", platform="hn",
                             category="pain_point", extracted_insight="x",
                             raw_text="body", needs_reembed=True))
    db.close()

    class ChunkBoom:
        def upsert_many(self, items):
            raise RuntimeError("batch down")

        def upsert(self, *a, **k):
            raise RuntimeError("single down")

    monkeypatch.setattr("jester.console.api._vector_for",
                        lambda cfg, p: ChunkBoom())
    res = ConsoleAPI(main, str(offline_config)).reembed()
    assert res["ok"] is False and res["fixed"] == 0 and res["failed"] == 1
    assert "single down" in res["last_error"]


# ---- api_signals: missing archive + text search ---------------------------------

def _signals_db_with_rows(tmp_path):
    from jester import signals as sig

    path = str(tmp_path / "signals-live.db")
    db = sig.open_signals(path)
    for sid, text in (("1", "Acme 1 seeks contract Python help"),
                      ("2", "Acme 2 needs weekend deploy relief")):
        sig.upsert(db, [sig.Signal(
            source_id=sid, platform="hackernews",
            source_url=f"https://news.ycombinator.com/item?id={sid}",
            community="hn/hiring", kind="post", text=text, mode="live",
            audience="buyer", match_confidence=0.9)])
    db.close()
    return path


def _console(tmp_path, signals_path):
    from jester.console.api import ConsoleAPI

    main = str(tmp_path / "j.db")
    open_db(main).close()
    return ConsoleAPI(main, str(REPO_CONFIG), signals_db=signals_path)


def test_signals_views_report_missing_archive(tmp_path):
    api = _console(tmp_path, str(tmp_path / "no-such-signals.db"))
    assert api.signals() == {"ok": True, "exists": False,
                             "path": str(tmp_path / "no-such-signals.db"),
                             "total": 0, "rows": [], "counts": {},
                             "facets": {}}
    assert api.signal_runs() == {"ok": True, "exists": False, "runs": [],
                                 "sources": []}


def test_signals_text_search_and_untouched_queue(tmp_path):
    api = _console(tmp_path, _signals_db_with_rows(tmp_path))
    hit = api.signals(q="Acme")
    assert hit["ok"] is True and hit["total"] == 2
    assert set(hit["facets"]) == {"audience", "community", "kind", "mode",
                                  "outcome"}
    miss = api.signals(q="espresso-grind-size")
    assert miss["total"] == 0
    queue = api.signals(outcome="untouched")
    assert queue["total"] == 2
    runs = api.signal_runs()
    assert runs["exists"] is True and runs["runs"] == []


# ---- llm batch leftovers ---------------------------------------------------------

class _ReplyClient:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        return {"message": {"content": self._script[self.calls - 1]}}


def test_extract_batch_single_body_uses_single_path():
    from jester.llm import OllamaLLM

    llm = OllamaLLM(model="m", client=_ReplyClient(
        ['{"category": "question", "insight": "solo insight"}']))
    (draft,) = llm.extract_batch(["solo pain"])
    assert (draft.category, draft.extracted_insight, draft.model) == \
        ("question", "solo insight", "m")
    assert llm.calls == 1


def test_extract_batch_bad_item_falls_back_per_comment():
    from jester.llm import OllamaLLM

    llm = OllamaLLM(model="m", client=_ReplyClient([
        json.dumps({"items": [
            {"category": "pain_point", "insight": "fine"},
            {"category": "bogus", "insight": "   "}]}),
        '{"category": "pain_point", "insight": "first again"}',
        '{"category": "question", "insight": "second again"}',
    ]))
    drafts = llm.extract_batch(["first pain", "second pain"])
    assert [(d.category, d.extracted_insight) for d in drafts] == [
        ("pain_point", "first again"), ("question", "second again")]
    assert llm.calls == 3 and llm.fallbacks == 1


# ---- vector / sweep / clickhouse / server leftovers -------------------------------

def test_upsert_many_rejects_short_embed_reply():
    from jester.embed import FakeEmbedding
    from jester.vector import VectorStore

    class ShortEmbed(FakeEmbedding):
        def embed(self, texts):
            return super().embed(texts)[:1]

    store = VectorStore.in_memory(ShortEmbed())
    with pytest.raises(RuntimeError, match="1 vectors for 2 texts"):
        store.upsert_many([("a", "text a", {}), ("b", "text b", {})])


def test_load_pairs_skips_blank_lines(tmp_path):
    from jester.dedup_sweep import load_pairs

    path = tmp_path / "pairs.jsonl"
    path.write_text('\n{"a": "x", "b": "x", "same": true}\n\n', encoding="utf-8")
    pairs, skipped = load_pairs(str(path))
    assert pairs == [("x", "x", True)] and skipped == 0


def test_sweep_rejects_short_embed_reply():
    from jester.dedup_sweep import sweep
    from jester.embed import FakeEmbedding

    class ShortEmbed(FakeEmbedding):
        def embed(self, texts):
            return super().embed(texts)[:1]

    with pytest.raises(ValueError, match="1 vectors for 2 texts"):
        sweep([("a", "b", True)], ShortEmbed())


def test_malformed_clickhouse_url_is_not_loopback(monkeypatch):
    from jester.signals import clickhouse as ch

    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    assert ch._is_loopback_url("http://[::1") is False
    client = ch.Client(url="http://[::1", opener=lambda *a, **k: None)
    with pytest.raises(ch.ClickHouseError, match="no password configured"):
        client.execute("SELECT 1")


def test_access_log_never_breaks_serving(monkeypatch):
    """The F-11 guarantee, pinned directly: a logging failure is swallowed,
    never propagated into the request path."""
    from jester.console import server as srv

    handler = srv.Handler.__new__(srv.Handler)
    handler.command = "GET"
    handler.path = "/api/doctor"
    handler._t0 = 0.0

    class BrokenErr:
        def write(self, *a, **k):
            raise OSError("disk full")

    monkeypatch.setattr(srv.sys, "stderr", BrokenErr())
    handler.log_message('"%s" %s %s', "GET /api/doctor", "200", "12")
