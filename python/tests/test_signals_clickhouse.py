"""the SQLite -> ClickHouse loader and the saved SQL.

The requirement: a queryable archive and a CSV, a repeat import that
creates no duplicates, and saved SQL that reconciles the two. Most of it is
checked here
against a fake HTTP transport, because the shape of what gets sent is what can
silently be wrong: a date the server reads as null, a `None` posted into a
`UInt8`, a count that quietly omits `FINAL`. The last test talks to a real
server when one is running, and skips when it is not — a suite that cannot run
without Docker is a suite people stop running.
"""
import json
import sqlite3
import urllib.error

import pytest

from jester import signals as sig
from jester.signals import clickhouse as ch


# ---- a fake transport ------------------------------------------------------

class FakeResponse:
    def __init__(self, body=""):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeServer:
    """Records every request and answers from a table of canned replies.

    Replies are matched against the *decoded* SQL, in insertion order, so a
    test reads as the SQL it stands in for rather than as percent-encoded URL
    fragments — and so a query that is reworded stops matching loudly instead
    of silently falling through to an empty answer.
    """

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    @staticmethod
    def _sql(url):
        import urllib.parse
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        return qs.get("query", [""])[0]

    def __call__(self, req, timeout=None):
        url = req.full_url
        sql = self._sql(url)
        self.calls.append({"url": url, "sql": sql,
                           "body": (req.data or b"").decode("utf-8")})
        for needle, reply in self.replies.items():
            if needle in sql:
                return FakeResponse(reply)
        return FakeResponse("")

    def queries(self):
        return [c["sql"] for c in self.calls]


@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


# ---- row conversion --------------------------------------------------------

def test_dates_are_written_in_the_format_the_server_parses():
    """SQLite holds ISO-8601 with an offset. ClickHouse's default
    `date_time_input_format` does not parse that, so relying on the server's
    setting would null the column on someone else's instance."""
    assert ch._clickhouse_time("2026-09-15T09:12:00+00:00") == "2026-09-15 09:12:00.000"
    assert ch._clickhouse_time("2026-09-15T11:12:00+02:00") == "2026-09-15 09:12:00.000"
    assert ch._clickhouse_time("2026-09-15T09:12:00Z") == "2026-09-15 09:12:00.000"


def test_an_absent_date_becomes_null_not_epoch_zero():
    """A fetch that failed has no post date. Writing 1970 there would make a
    failure row look like a real post from the dawn of time."""
    assert ch._clickhouse_time("") is None
    assert ch._clickhouse_time(None) is None


def test_a_date_that_is_not_a_date_is_passed_through_to_be_rejected():
    """Rather than invented. A loader that quietly substitutes a timestamp
    turns a data bug into a plausible-looking row."""
    assert ch._clickhouse_time("not a date") == "not a date"


def test_every_column_is_typed_the_way_its_column_is_declared(db):
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    row = db.execute(f"SELECT * FROM {sig.TABLE} LIMIT 1").fetchone()
    out = ch.to_clickhouse_row(row)
    assert set(out) == set(sig.FIELD_NAMES)
    assert isinstance(out["relevant"], int)
    assert isinstance(out["revisions"], int)
    assert isinstance(out["match_confidence"], float)
    assert out["first_seen_utc"] and " " in out["first_seen_utc"]
    # Never a bare None on a non-nullable column: the server would reject the
    # whole batch, and one unloadable row must not lose the other 1999.
    for name, _s, chtype, _d in sig.FIELDS:
        if not chtype.startswith("Nullable("):
            assert out[name] is not None, name


def test_a_row_that_predates_a_column_still_loads(db):
    """`CREATE TABLE IF NOT EXISTS` never widens a table, so old databases have
    NULLs where newer ones have values. Dropping those rows would make the two
    stores disagree, which is exactly what the reconciliation forbids."""
    row = {n: None for n in sig.FIELD_NAMES}
    row["record_id"] = "reddit:t3_old"
    out = ch.to_clickhouse_row(row)
    assert out["first_seen_utc"] == "1970-01-01 00:00:00.000"
    assert out["removed_utc"] is None
    assert out["relevant"] == 0


# ---- the load ---------------------------------------------------------------

def test_the_load_posts_json_each_row_and_reports_what_it_sent(db):
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer()
    res = ch.load(db, ch.Client(opener=server), optimize=False)

    assert res["rows_sent"] == 5 and res["requests"] == 1
    inserts = [c for c in server.calls if "JSONEachRow" in c["url"]]
    assert len(inserts) == 1
    lines = inserts[0]["body"].splitlines()
    assert len(lines) == 5
    parsed = [json.loads(line) for line in lines]
    assert all(set(p) == set(sig.FIELD_NAMES) for p in parsed)


def test_the_load_batches_rather_than_posting_one_giant_body(db):
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer()
    res = ch.load(db, ch.Client(opener=server), batch=2, optimize=False)
    assert res["rows_sent"] == 5 and res["requests"] == 3


def test_the_table_is_created_from_the_generated_ddl(db):
    server = FakeServer()
    ch.load(db, ch.Client(opener=server), optimize=False)
    assert any("ReplacingMergeTree" in q for q in server.queries())


def test_the_database_is_named_on_every_data_request(db):
    """The container creates `jester` but leaves the user's default database
    as `default`. A request that does not name it runs against an empty schema
    and reports zero rows, which looks identical to a load that did nothing."""
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer()
    ch.load(db, ch.Client(database="jester", opener=server), optimize=False)
    data_calls = [c for c in server.calls if "JSONEachRow" in c["url"]]
    assert data_calls and all("database=jester" in c["url"] for c in data_calls)


def test_a_server_that_is_not_there_says_how_to_start_it(db):
    def dead(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(ch.ClickHouseError, match="docker compose --profile signals"):
        ch.load(db, ch.Client(opener=dead))


def test_a_rejected_batch_carries_the_servers_own_message(db):
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            __import__("io").BytesIO(b"Code: 62. DB::Exception: Syntax error"))

    with pytest.raises(ch.ClickHouseError, match="DB::Exception"):
        ch.load(db, ch.Client(opener=refuse))


# ---- reconciliation --------------------------------------------------------

def test_reconcile_counts_with_final_and_separates_duplicates_from_unmerged(db):
    """Three numbers that can disagree in two different ways. Un-merged parts
    are normal; `rows_final` exceeding the distinct ids is the broken dedup the
    milestone is actually about."""
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer({
        # Insertion order is the matching order: the narrower patterns first,
        # or the bare count would answer for all four.
        "uniqExact": "5\n",
        "GROUP BY mode": '{"mode":"fixture","n":"5","relevant":"3","buyer":"2",'
                         '"practitioner":"1","removed":"0","errors":"1"}\n',
        "count() FROM problem_signal FINAL": "5\n",
        "count() FROM problem_signal": "9\n",
    })
    rec = ch.reconcile(db, ch.Client(opener=server))
    assert rec["sqlite_rows"] == 5
    assert rec["clickhouse_rows_final"] == 5
    assert rec["duplicates"] == 0
    assert rec["unmerged_parts"] == 4
    assert rec["reconciles"] is True


def test_a_broken_dedup_key_fails_the_reconciliation(db):
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer({
        "uniqExact": "5\n",
        "GROUP BY mode": "",
        "count() FROM problem_signal": "7\n",
    })
    rec = ch.reconcile(db, ch.Client(opener=server))
    assert rec["duplicates"] == 2
    assert rec["reconciles"] is False


# ---- the saved SQL ---------------------------------------------------------

def test_every_saved_query_is_readable_on_its_own():
    """"Own saved SQL" means someone can open a file and run it. A query with
    no header comment is a query nobody else can use."""
    qs = ch.saved_queries()
    assert len(qs) >= 5
    for q in qs:
        assert q["title"], f"{q['name']}: no header comment saying what it answers"
        assert q["sql"].strip().endswith(";"), f"{q['name']}: not a runnable statement"


def test_every_counting_query_says_final():
    """Without FINAL a count counts parts, not records, and drifts upward
    after every import — the one way a ReplacingMergeTree archive can lie."""
    for q in ch.saved_queries():
        if "count(" not in q["sql"] and "count()" not in q["sql"]:
            continue
        if q["name"].startswith("01-"):
            continue  # the reconciliation deliberately counts both ways
        assert "FINAL" in q["sql"], f"{q['name']}: counts without FINAL"


def test_a_failing_query_is_recorded_not_raised():
    """One broken file must not hide the answers the other six give."""
    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad", {}, __import__("io").BytesIO(b"nope"))

    res = ch.run_saved_queries(ch.Client(opener=refuse))
    assert len(res) >= 5
    assert all(not r["ok"] and r["error"] for r in res)


# ---- against a real server, when one is running ----------------------------

def _server_is_up(client):
    try:
        return "1" in client.scalar("SELECT 1")
    except ch.ClickHouseError:
        return False


@pytest.mark.parametrize("_", [None])
def test_a_repeat_import_creates_no_duplicates(tmp_path, _):
    """The no-duplicates check itself, end to end, when ClickHouse is running.

    Uses its own database so it cannot disturb the real archive, and drops it
    afterwards. Skipped rather than failed when no server is up: the rest of
    this file already covers what is sent.
    """
    client = ch.Client(database="jester_test_m2")
    if not _server_is_up(ch.Client(database="default")):
        pytest.skip("no ClickHouse on CLICKHOUSE_URL; "
                    "docker compose --profile signals up -d clickhouse")
    db = sig.open_signals(str(tmp_path / "s.db"))
    try:
        # Through the server's default database: a request that names a
        # database the server does not have is refused before the statement is
        # read, which would defeat the point of DROP ... IF EXISTS.
        client.execute("DROP DATABASE IF EXISTS jester_test_m2", database="default")
        sig.upsert(db, sig.synthetic_signals(run_id="m2-test"))
        for _round in range(3):
            ch.load(db, client)
        rec = ch.reconcile(db, client)
        assert rec["sqlite_rows"] == 5
        assert rec["clickhouse_rows_final"] == 5, "a repeat import duplicated rows"
        assert rec["duplicates"] == 0
        assert rec["reconciles"] is True
        assert ch.run_saved_queries(client) and all(
            q["ok"] for q in ch.run_saved_queries(client))
    finally:
        db.close()
        try:
            client.execute("DROP DATABASE IF EXISTS jester_test_m2", database="default")
        except ch.ClickHouseError:
            pass


# ---- the mode filter -------------------------------------------------------

def test_an_unknown_mode_is_refused_rather_than_interpolated(db):
    """SQLite parameterises `mode`; ClickHouse's HTTP interface takes one
    statement and no bind list, so the value goes into the SQL text. It is
    checked against the two values the column can hold instead of trusted."""
    with pytest.raises(ch.ClickHouseError, match="not one of live, fixture"):
        ch.load(db, ch.Client(opener=FakeServer()), mode="x' OR 1=1--")
    with pytest.raises(ch.ClickHouseError, match="not one of live, fixture"):
        ch.reconcile(db, ch.Client(opener=FakeServer()), mode="anything")


def test_a_filter_that_matches_nothing_sends_nothing(db):
    """There are no live rows until Reddit access exists. Loading with that
    filter must be a no-op, not an empty INSERT the server has to reject."""
    sig.upsert(db, sig.synthetic_signals(run_id="t"))
    server = FakeServer()
    res = ch.load(db, ch.Client(opener=server), mode="live", optimize=False)
    assert res["rows_sent"] == 0 and res["requests"] == 0
    assert not [c for c in server.calls if "JSONEachRow" in c["url"]]
