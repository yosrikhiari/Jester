"""The analytical archive: SQLite -> ClickHouse, and the saved SQL that
reconciles the two.

The requirement: a queryable archive and a CSV, a repeat import that creates
no duplicates, and saved SQL that reconciles the two. Three decisions follow
from that, and are worth stating once here rather than re-deriving:

* **Loading is idempotent by construction, not by discipline.** The table is a
  `ReplacingMergeTree` ordered on `record_id` alone, so a second import of the
  same rows collapses to the same rows. Collapsing happens at merge time, so
  every query that counts *must* say `FINAL` — a count without it is a count of
  parts, not of records, and it will drift upward after every load. That is the
  one way this design can lie, so the saved queries all carry `FINAL` and the
  reconciliation checks both.

* **The schema is not written here.** It is generated from `FIELDS` in
  `jester.signals`, the same map that generates the SQLite table and the CSV
  header, so the three cannot drift apart.

* **No SDK.** One `POST` of newline-delimited JSON to the HTTP interface, over
  stdlib `urllib`, the same stance `llm.py` takes. `clickhouse-connect` pulls a
  dependency tree to do what twenty lines do.

Times are normalised to ClickHouse's own `YYYY-MM-DD hh:mm:ss.mmm` on the way
out. SQLite stores ISO-8601 with an offset, which ClickHouse only parses when
the server happens to be set to `best_effort` — depending on a server setting
for correctness is how a load silently nulls a column.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from . import FIELDS, FIELD_NAMES, TABLE, clickhouse_ddl, counts

#: Rows per POST. Big enough that a full archive is a handful of requests,
#: small enough that one failure does not cost a long upload. ClickHouse's
#: default max body is 1 MiB *per line*, not per request, so the ceiling here
#: is memory, not the server.
BATCH = 2000

#: Which columns are dates, and which of those may be absent. A fetch that
#: failed has no post date; the row still has to be stored and counted, so the
#: column is Nullable and an empty string becomes `null`, never epoch zero.
_DATE_FIELDS = {n for n, _s, ch, _d in FIELDS if "DateTime" in ch}
_NULLABLE = {n for n, _s, ch, _d in FIELDS if ch.startswith("Nullable(")}
_INT_FIELDS = {n for n, _s, ch, _d in FIELDS if ch.startswith("UInt")}
_FLOAT_FIELDS = {n for n, _s, ch, _d in FIELDS if ch.startswith("Float")}

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


class ClickHouseError(RuntimeError):
    """The server refused, or is not there. Loud and with the server's own
    message attached: a loader that swallows an error is the "silent failures"
    a collector must never do."""


class Client:
    """A ClickHouse HTTP connection's worth of settings and two methods.

    Credentials come from the environment (`CLICKHOUSE_URL`, `_DB`, `_USER`,
    `_PASSWORD`), defaulting to the profile-gated container in
    `docker-compose.yml`. They are not written into the repo, and the loader
    never prints the password back — the evidence pack quotes the host and the
    database, which is what a reviewer needs to repeat the query.
    """

    def __init__(self, url: str | None = None, database: str | None = None,
                 user: str | None = None, password: str | None = None,
                 timeout: int = 60, opener=None):
        self.url = (url or os.getenv("CLICKHOUSE_URL") or "http://127.0.0.1:8123").rstrip("/")
        self.database = database or os.getenv("CLICKHOUSE_DB") or "jester"
        self.user = user or os.getenv("CLICKHOUSE_USER") or "jester"
        self.password = password if password is not None else (
            os.getenv("CLICKHOUSE_PASSWORD") or "jester")
        self.timeout = timeout
        self._opener = opener or urllib.request.urlopen

    # -- plumbing ----------------------------------------------------------
    def _endpoint(self, **params) -> str:
        # The database is always named on the request. The container creates
        # `jester` but leaves the user's default database as `default`, so a
        # bare query runs against the wrong (empty) schema and reports zero
        # rows — which looks exactly like a load that did nothing.
        q = {"user": self.user, "password": self.password, "database": self.database}
        q.update({k: v for k, v in params.items() if v is not None})
        return f"{self.url}/?{urllib.parse.urlencode(q)}"

    def execute(self, sql: str, body: str = "", **params) -> str:
        """POST one statement. `body` carries the rows for an INSERT; the
        statement itself always travels as the `query` parameter so the two
        cannot be confused in a log."""
        req = urllib.request.Request(
            self._endpoint(query=sql, **params),
            data=(body or "").encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise ClickHouseError(f"HTTP {exc.code}: {detail[:400]}") from exc
        except urllib.error.URLError as exc:
            raise ClickHouseError(
                f"cannot reach ClickHouse at {self.url}: {exc.reason}. "
                "Start it with: docker compose --profile signals up -d clickhouse"
            ) from exc

    def scalar(self, sql: str):
        return self.execute(sql).strip()

    def rows(self, sql: str) -> List[dict]:
        """A SELECT as a list of dicts, via JSONEachRow so the column names
        come back with the data and a query can be edited without breaking a
        positional index somewhere else."""
        text = self.execute(f"{sql.rstrip().rstrip(';')} FORMAT JSONEachRow")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    # -- schema ------------------------------------------------------------
    def ensure_database(self) -> None:
        # Runs against the server's default database: the target may not exist
        # yet, and naming it on a CREATE DATABASE request would fail first.
        req_url = f"{self.url}/?" + urllib.parse.urlencode(
            {"user": self.user, "password": self.password,
             "query": f"CREATE DATABASE IF NOT EXISTS {self.database}"})
        req = urllib.request.Request(req_url, data=b"", method="POST")
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise ClickHouseError(f"HTTP {exc.code}: {detail[:400]}") from exc
        except urllib.error.URLError as exc:
            raise ClickHouseError(
                f"cannot reach ClickHouse at {self.url}: {exc.reason}. "
                "Start it with: docker compose --profile signals up -d clickhouse"
            ) from exc

    def ensure_table(self) -> None:
        """Create the table from the generated DDL. `IF NOT EXISTS`, so this is
        safe on every run; the DDL comes from the field map, so the ClickHouse
        table cannot drift away from the SQLite one."""
        self.ensure_database()
        self.execute(clickhouse_ddl().rstrip().rstrip(";"))


# ---------------------------------------------------------------------------
# Row conversion
# ---------------------------------------------------------------------------

def _clickhouse_time(value) -> Optional[str]:
    """ISO-8601 (what SQLite holds) -> `YYYY-MM-DD hh:mm:ss.mmm` in UTC.

    Empty becomes `None`, which JSONEachRow reads as null. Anything that is
    not a date at all is returned untouched so the server rejects it loudly
    rather than this function quietly inventing a timestamp.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def to_clickhouse_row(row) -> dict:
    """One SQLite row as the JSON object ClickHouse will accept.

    Three coercions, each for a failure seen rather than imagined: dates get
    the server's own format; an empty date on a non-nullable column would be
    rejected, so `first_seen_utc`/`last_seen_utc` fall back to the record's own
    other stamps; and SQLite's untyped NULLs become the typed zero the column
    declares, because `relevant` is `UInt8` and `None` is not a number.
    """
    out: Dict[str, object] = {}
    for name in FIELD_NAMES:
        value = row[name] if isinstance(row, sqlite3.Row) else row.get(name)
        if name in _DATE_FIELDS:
            stamp = _clickhouse_time(value)
            if stamp is None and name not in _NULLABLE:
                # Non-nullable and empty: the row predates the column. Use the
                # epoch rather than dropping the record — a record that cannot
                # be loaded is a record that disappears between the two stores,
                # and the reconciliation exists to make that impossible.
                stamp = "1970-01-01 00:00:00.000"
            out[name] = stamp
        elif name in _INT_FIELDS:
            out[name] = int(value or 0)
        elif name in _FLOAT_FIELDS:
            out[name] = float(value or 0.0)
        else:
            out[name] = "" if value is None else str(value)
    return out


#: The only two values `mode` can hold. The SQLite side parameterises it; the
#: ClickHouse side cannot (the HTTP interface takes one statement, not a
#: statement and a bind list), so the value is checked against this instead of
#: being interpolated on trust.
MODES = ("live", "fixture")


def _checked_mode(mode: str) -> str:
    if mode and mode not in MODES:
        raise ClickHouseError(f"mode {mode!r} is not one of {', '.join(MODES)}")
    return mode


def _batches(rows: Iterable, size: int):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ---------------------------------------------------------------------------
# Load and reconcile
# ---------------------------------------------------------------------------

def load(db: sqlite3.Connection, client: Client | None = None, *, mode: str = "",
         batch: int = BATCH, optimize: bool = True) -> dict:
    """Copy the SQLite table into ClickHouse and report what landed.

    `optimize` runs `OPTIMIZE TABLE ... FINAL` afterwards, which forces the
    replacing merge instead of waiting for the background one. It is not
    required for correctness — every saved query says `FINAL` — but it makes
    the state on disk match what the queries report, so a reviewer poking at
    the table by hand sees the same numbers as the evidence pack.
    """
    client = client or Client()
    mode = _checked_mode(mode)
    client.ensure_table()

    where, args = ("WHERE mode = ?", (mode,)) if mode else ("", ())
    cur = db.execute(
        f"SELECT {','.join(FIELD_NAMES)} FROM {TABLE} {where} "
        "ORDER BY first_seen_utc, record_id", args)

    sent = requests = 0
    for chunk in _batches(cur, batch):
        body = "\n".join(json.dumps(to_clickhouse_row(r), ensure_ascii=False) for r in chunk)
        client.execute(f"INSERT INTO {TABLE} FORMAT JSONEachRow", body)
        sent += len(chunk)
        requests += 1

    if optimize and sent:
        client.execute(f"OPTIMIZE TABLE {TABLE} FINAL")

    return {"rows_sent": sent, "requests": requests, "mode_filter": mode or "all",
            "database": client.database, "url": client.url}


def reconcile(db: sqlite3.Connection, client: Client | None = None, *, mode: str = "") -> dict:
    """Does ClickHouse hold exactly what SQLite holds?

    Four numbers, because three of them can agree while the archive is still
    wrong. `rows_final` is what a query sees; `rows_raw` is what is physically
    stored before the replacing merge. When they differ the table is merely
    un-merged, which is normal — but if `rows_final` exceeds `unique_ids` the
    dedup key is broken, and that is the failure this check exists for.
    """
    client = client or Client()
    mode = _checked_mode(mode)
    where, args = ("WHERE mode = ?", (mode,)) if mode else ("", ())
    sqlite_rows = db.execute(f"SELECT COUNT(*) FROM {TABLE} {where}", args).fetchone()[0]

    cond = f" WHERE mode = '{mode}'" if mode else ""
    rows_final = int(client.scalar(f"SELECT count() FROM {TABLE} FINAL{cond}") or 0)
    rows_raw = int(client.scalar(f"SELECT count() FROM {TABLE}{cond}") or 0)
    unique_ids = int(client.scalar(f"SELECT uniqExact(record_id) FROM {TABLE}{cond}") or 0)

    by_mode = {}
    for r in client.rows(f"SELECT mode, count() AS n, sum(relevant) AS relevant, "
                         f"countIf(audience = 'buyer') AS buyer, "
                         f"countIf(audience = 'practitioner') AS practitioner, "
                         f"countIf(removed_utc IS NOT NULL) AS removed, "
                         f"countIf(run_status != 'ok') AS errors "
                         f"FROM {TABLE} FINAL{cond} GROUP BY mode ORDER BY mode"):
        by_mode[r["mode"]] = {k: int(v) for k, v in r.items() if k != "mode"}

    return {
        "sqlite_rows": sqlite_rows,
        "clickhouse_rows_final": rows_final,
        "clickhouse_rows_raw": rows_raw,
        "clickhouse_unique_ids": unique_ids,
        "reconciles": sqlite_rows == rows_final == unique_ids,
        "duplicates": max(0, rows_final - unique_ids),
        "unmerged_parts": max(0, rows_raw - rows_final),
        "clickhouse_by_mode": by_mode,
        "sqlite_by_mode": counts(db),
        "mode_filter": mode or "all",
    }


# ---------------------------------------------------------------------------
# Saved queries
# ---------------------------------------------------------------------------

def saved_queries(directory: Path | str | None = None) -> List[dict]:
    """The `.sql` files, in filename order, each with the comment header that
    says what it answers.

    They are files rather than string literals because the point of "own saved
    SQL" is that someone else can open one, read it, and run it against the
    same server without this codebase.
    """
    d = Path(directory or QUERIES_DIR)
    if not d.is_dir():
        raise ClickHouseError(f"no saved queries at {d}")
    out = []
    for path in sorted(d.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        title = ""
        for line in text.splitlines():
            if line.startswith("--"):
                title = line.lstrip("- ").strip()
                break
        out.append({"name": path.stem, "title": title, "sql": text, "path": path})
    if not out:
        raise ClickHouseError(f"no .sql files in {d}")
    return out


def run_saved_queries(client: Client | None = None,
                      directory: Path | str | None = None) -> List[dict]:
    """Run each saved query and keep its rows. A query that fails is recorded
    as a failure and the rest still run: one broken file should not hide the
    answers the others give."""
    client = client or Client()
    results = []
    for q in saved_queries(directory):
        entry = {"name": q["name"], "title": q["title"], "sql": q["sql"].strip()}
        try:
            entry["rows"] = client.rows(q["sql"])
            entry["ok"] = True
        except ClickHouseError as exc:
            entry["rows"], entry["ok"], entry["error"] = [], False, str(exc)
        results.append(entry)
    return results
