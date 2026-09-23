"""§37.29 Operator console API: every jester feature surfaced as JSON.

Pure methods (no HTTP) so tests drive the console without sockets.
Actions wrap CLI behaviour and translate SystemExit into {ok, code} payloads.
"""
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from jester.cli import (
    _vector_for,
    cmd_mark,
    cmd_migrate,
    cmd_resynth,
    cmd_retention,
    cmd_run,
    evaluate_doctor,
    evaluate_smoke,
)
from jester.llm import GROQ_CHAT_MODELS, GroqClient, provider_for
from jester.config import (
    EMBEDDING_PROVIDERS,
    LLM_PROVIDERS,
    ROLE_PROVIDER_KEYS,
)
from jester.config import (
    MODEL_ROLES,
    ConfigError,
    Thresholds,
    load_config,
    save_models,
    save_thresholds,
)
from jester.export import default_export_dir, export_csv
from jester.worker import go_binary, ingest as worker_ingest
from jester import schedule as _schedule
from jester.fetchers.cloak import load_scraper_config
from jester.llm import PROMPT_VERSIONS, RUBRIC_VERSION
from jester.quota import day_key
from jester.sources import (
    PLATFORM_KINDS,
    SUPPORTED_KINDS,
    SourceError,
    duplicate_names,
    gap_reason,
    find as find_source,
    load_sources,
    parse_source,
    save_sources,
    slugify,
    unique_name,
)
from jester.store import (
    _NUGGET_DETAIL_COLUMNS,
    cluster_members,
    count_nuggets,
    delete_cluster_idea,
    get_cluster,
    insert_cluster_idea,
    list_cluster_ideas,
    list_clusters,
    promote_cluster_idea,
    get_idea,
    run_activity,
    get_runs,
    list_ideas,
    list_nuggets,
    mark_reembedded,
    open_db,
    pending_reembeds,
    requeue_failed,
)

_LIVE_CONFIG_DIR = "config-live"
_PLATFORMS = tuple(PLATFORM_KINDS)


#: Knobs whose value is a closed vocabulary rather than a number. The console
#: draws these as a <select>, so a new provider shows up in the UI the moment
#: it is valid in code rather than the next time someone edits the JavaScript.
_CHOICES = {
    "llm_provider": list(LLM_PROVIDERS),
    "embedding_provider": list(EMBEDDING_PROVIDERS),
    "competitor_search_provider": ["none", "fake", "duckduckgo"],
}


class ConsoleAPI:
    def __init__(self, db_path: str, config_dir: str | None = None,
                 signals_db: str | None = None):
        self.repo_root = Path(__file__).resolve().parents[3]
        self.config_dir = config_dir or str(self.repo_root / "config")
        self.db_path = db_path if db_path == ":memory:" else os.path.abspath(db_path)
        self.db = open_db(self.db_path)
        self._signals_db_path = signals_db

    # ---- helpers ----------------------------------------------------------

    def _cfg(self):
        return load_config(self.config_dir)

    @property
    def _sources_path(self) -> Path:
        return Path(self.config_dir) / "sources.yaml"

    @property
    def _thresholds_path(self) -> Path:
        return Path(self.config_dir) / "thresholds.yaml"

    def _mirror_paths(self, filename: str):
        """config-live differs from config only in model choice, so the shared
        facts (which sources to scrape, which thresholds to apply) are kept in
        step across both dirs.

        Only the repo's own config/ mirrors: a console pointed at some other
        directory (a test fixture, a second profile) writes exactly there.
        """
        primary = Path(self.config_dir) / filename
        paths = [primary]
        repo_config = self.repo_root / "config"
        if not _same_dir(Path(self.config_dir), repo_config):
            return paths
        live = self.repo_root / _LIVE_CONFIG_DIR / filename
        if live.exists() and not _same_dir(live, primary):
            paths.append(live)
        return paths

    def _counts(self):
        q = lambda sql: self.db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "nuggets": q("SELECT COUNT(*) FROM nuggets"),
            "ideas": q("SELECT COUNT(*) FROM ideas"),
            "pending_batches": q("SELECT COUNT(*) FROM ingest_batch WHERE status='pending'"),
            "failed_batches": q("SELECT COUNT(*) FROM ingest_batch WHERE status='failed'"),
            "reembed_backlog": q("SELECT COUNT(*) FROM nuggets WHERE needs_reembed=1"),
            "unprocessed": q("SELECT COUNT(*) FROM nuggets WHERE synthesized_at IS NULL"),
            "runs_active": q("SELECT COUNT(*) FROM runs WHERE status='running'"),
        }

    @staticmethod
    def _rowdict(row):
        return {k: row[k] for k in row.keys()} if row is not None else None

    def _quota_today(self):
        key = day_key(datetime.now(timezone.utc))
        per_platform = {}
        total = 0
        for platform in _PLATFORMS:
            row = self.db.execute(
                "SELECT used FROM quota WHERE platform=? AND day_key=?",
                (platform, key),
            ).fetchone()
            used = row[0] if row else 0
            per_platform[platform] = used
            total += used
        return {"day_key": key, "used_total": total, "used_by_platform": per_platform}

    def _action_guard(self, fn, *a, **kw):
        try:
            fn(*a, **kw)
            return {"ok": True}
        except SystemExit as e:
            return {"ok": False, "code": e.code}
        except Exception as exc:  # noqa: BLE001 - surfaced to the console UI
            return {"ok": False, "error": str(exc)}

    # ---- reads ------------------------------------------------------------

    def overview(self):
        runs = get_runs(self.db, limit=1)
        srcs = load_sources(self._sources_path)
        return {
            "ok": True,
            "db": self.db_path,
            "config_dir": self.config_dir,
            "counts": self._counts(),
            "last_run": self._rowdict(runs[0]) if runs else None,
            "quota": self._quota_today(),
            "prompt_versions": PROMPT_VERSIONS,
            "rubric_version": RUBRIC_VERSION,
            "sources_summary": {
                "total": len(srcs),
                "enabled": sum(1 for s in srcs if s.enabled),
                "unsupported": sum(1 for s in srcs if s.enabled and not s.supported),
            },
        }

    def trends(self, days=7):
        """Daily counts for the last `days` days: nuggets archived, ideas
        synthesized, batches queued, runs that ended badly. The Overview tiles
        used to show a total and nothing else, so "39 474 nuggets" could not
        say it grew by ten thousand today or by nothing in a week."""
        try:
            days = max(2, min(30, int(days)))
        except (TypeError, ValueError):
            days = 7
        from datetime import timedelta
        today = datetime.now(timezone.utc).date()
        keys = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        since = keys[0]

        def daily(sql):
            out = {k: 0 for k in keys}
            try:
                for day, n in self.db.execute(sql, (since,)).fetchall():
                    if day in out:
                        out[day] = n
            except Exception:  # noqa: BLE001 - table absent on a fresh db
                pass
            return [out[k] for k in keys]

        return {
            "ok": True,
            "days": keys,
            "nuggets": daily("SELECT substr(created_at,1,10) d, COUNT(*) FROM nuggets "
                             "WHERE substr(created_at,1,10) >= ? GROUP BY d"),
            "ideas": daily("SELECT substr(created_at,1,10) d, COUNT(*) FROM ideas "
                           "WHERE substr(created_at,1,10) >= ? GROUP BY d"),
            "queued": daily("SELECT substr(created_at,1,10) d, COUNT(*) FROM ingest_batch "
                            "WHERE substr(created_at,1,10) >= ? GROUP BY d"),
            "bad_runs": daily("SELECT substr(started_at,1,10) d, COUNT(*) FROM runs "
                              "WHERE substr(started_at,1,10) >= ? "
                              "AND (status IN ('failed','aborted') OR error IS NOT NULL) GROUP BY d"),
        }

    def running(self):
        """The runs in flight right now, for the strip every page shows.
        Cheap on purpose: a few columns, no JSON unpacking except the last
        phase checkpoint."""
        try:
            rows = self.db.execute(
                "SELECT run_id, started_at, origin, phase_checkpoints, n_comments, n_nuggets_kept "
                "FROM runs WHERE status='running' ORDER BY id DESC LIMIT 3"
            ).fetchall()
        except Exception:  # noqa: BLE001
            rows = []
        out = []
        for r in rows:
            try:
                phases = json.loads(r[3] or "[]")
            except (TypeError, ValueError):
                phases = []
            out.append({
                "run_id": r[0], "started_at": r[1], "origin": r[2] or "manual",
                "phase": (phases[-1].get("phase") if phases else None) or "fetch",
                "n_comments": r[4], "n_nuggets_kept": r[5],
            })
        return {"ok": True, "running": out}

    def runs(self, limit=25):
        out = []
        for r in get_runs(self.db, limit=limit):
            d = self._rowdict(r)
            d["floor_flags_list"] = json.loads(d.pop("floor_flags") or "[]")
            d["prefilter_funnel_obj"] = json.loads(d.pop("prefilter_funnel") or "{}")
            d["top_near_misses_list"] = json.loads(d.pop("top_near_misses") or "[]")
            d["platform_counts_obj"] = json.loads(d.pop("platform_counts") or "{}")
            d["models"] = json.loads(d.pop("models_used") or "null")
            out.append(d)
        return {"ok": True, "runs": out}

    # ---- the collector problem signals -------------------------------------------
    #
    # A separate database on purpose. The nugget pipeline and the problem-signal
    # collector answer to different people and different rules — one is Jester's
    # own archive, the other is a task deliverable whose every row has to say
    # whether it is live or fixture. Mixing them into one file would make "how
    # many live records do we have" a question about a join.

    @property
    def _signals_path(self) -> str:
        return (self._signals_db_path
                or os.environ.get("JESTER_SIGNALS_DB")
                or str(Path(self.db_path).parent / "signals-live.db"))

    def _signals_db(self):
        from jester.signals import open_signals
        return open_signals(self._signals_path)

    def signals(self, *, audience="", community="", kind="", q="", mode="",
                offset=0, limit=50):
        """The archive, filtered, with the facet counts for what is left.

        Facets are counted UNDER the other filters, not over the whole table:
        a count that ignores the filters beside it tells you how many records
        exist, when the question on screen is how many you would get if you
        clicked.
        """
        from jester.signals import FIELD_NAMES, TABLE
        offset, limit = int(offset or 0), max(1, min(int(limit or 50), 200))
        if not Path(self._signals_path).exists():
            return {"ok": True, "exists": False, "path": self._signals_path,
                    "total": 0, "rows": [], "counts": {}, "facets": {}}

        db = self._signals_db()
        try:
            filters = {"audience": audience, "community": community,
                       "kind": kind, "mode": mode}
            where, args = [], []
            for col, val in filters.items():
                if val:
                    where.append(f"{col} = ?")
                    args.append(val)
            if q:
                where.append("(title LIKE ? OR excerpt LIKE ? OR match_reason LIKE ?)")
                args += [f"%{q}%"] * 3
            clause = ("WHERE " + " AND ".join(where)) if where else ""

            total = db.execute(
                f"SELECT COUNT(*) FROM {TABLE} {clause}", args).fetchone()[0]
            # company and buyer_intent are here for the hiring records, where
            # they are the two most useful columns on the page: the advert
            # names the company and quotes its own engagement line. On forum
            # records both stay "unknown" and the page renders nothing.
            cols = ("record_id, mode, community, kind, author, title, excerpt, "
                    "query, match_reason, match_confidence, audience, relevant, "
                    "company, buyer_intent, "
                    "run_status, error, created_utc, first_seen_utc, last_seen_utc, "
                    "edited_utc, removed_utc, revisions, source_url")
            rows = [dict(r) for r in db.execute(
                f"SELECT {cols} FROM {TABLE} {clause} "
                "ORDER BY match_confidence DESC, first_seen_utc DESC "
                "LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()]

            facets = {}
            for col in ("audience", "community", "kind", "mode"):
                # Each facet is counted with its OWN filter dropped, so the
                # options a reader can switch to still show a number.
                sub = [f"{c} = ?" for c, v in filters.items() if v and c != col]
                subargs = [v for c, v in filters.items() if v and c != col]
                if q:
                    sub.append("(title LIKE ? OR excerpt LIKE ? OR match_reason LIKE ?)")
                    subargs += [f"%{q}%"] * 3
                sub_clause = ("WHERE " + " AND ".join(sub)) if sub else ""
                facets[col] = [
                    {"value": r[0], "n": r[1]} for r in db.execute(
                        f"SELECT {col}, COUNT(*) FROM {TABLE} {sub_clause} "
                        f"GROUP BY {col} ORDER BY COUNT(*) DESC", subargs).fetchall()
                    if r[0]]

            from jester.signals import counts as signal_counts
            return {"ok": True, "exists": True, "path": self._signals_path,
                    "total": total, "rows": rows, "facets": facets,
                    "counts": signal_counts(db), "fields": FIELD_NAMES,
                    "offset": offset, "limit": limit}
        finally:
            db.close()

    def signal_runs(self, limit=25):
        """The run ledger. Whether the collector ran on schedule, and whether
        a failure was recovered, is read off this — so it is a first-class view
        rather than something to grep out of a log."""
        if not Path(self._signals_path).exists():
            return {"ok": True, "exists": False, "runs": [], "sources": []}
        from jester.signals.run import runs as signal_run_rows
        from jester.signals.sources import available
        db = self._signals_db()
        try:
            return {"ok": True, "exists": True,
                    "runs": signal_run_rows(db, limit=int(limit or 25)),
                    "sources": available()}
        finally:
            db.close()

    IDEA_SORTS = {"overall", "created_at", "demand_signal", "feasibility", "competition", "id", "title", "status"}

    def ideas(self, status="", q="", sort="overall", dir="desc", offset=0, limit=0):
        """Ideas, filtered and sorted server-side, one page at a time.

        1 321 rows each carrying a <select> froze the tab; the page now asks
        for 50 and says how many exist. `limit=0` keeps the old
        everything-at-once answer for callers that need it."""
        all_rows = [self._rowdict(r) for r in list_ideas(self.db)]
        counts = {}
        for r in all_rows:
            s = r.get("status") or "new"
            counts[s] = counts.get(s, 0) + 1
        rows = all_rows
        q = (q or "").strip().lower()
        if status and status != "all":
            rows = [r for r in rows if r.get("status") == status]
        if q:
            rows = [r for r in rows if q in f"{r.get('title') or ''} {r.get('problem_statement') or ''}".lower()]
        sort = sort if sort in self.IDEA_SORTS else "overall"
        rev = (dir or "desc") != "asc"

        def key(r):
            v = r.get(sort)
            if sort in ("title", "status", "created_at"):
                return (v is None, str(v or "").lower())
            try:
                return (v is None, float(v))
            except (TypeError, ValueError):
                return (True, 0.0)
        rows.sort(key=key, reverse=rev)
        total = len(rows)
        try:
            offset = max(0, int(offset or 0))
            limit = max(0, int(limit or 0))
        except (TypeError, ValueError):
            offset, limit = 0, 0
        page = rows[offset:offset + limit] if limit else rows
        return {"ok": True, "ideas": page, "total": total, "offset": offset,
                "limit": limit, "sort": sort, "dir": "desc" if rev else "asc",
                "status_counts": counts}

    def idea(self, idea_id):
        row = get_idea(self.db, int(idea_id))
        if row is None:
            return {"ok": False, "error": f"idea #{idea_id} not found"}
        d = self._rowdict(row)
        keys = json.loads(d.get("supporting_nuggets") or "[]")
        cites = []
        for k in keys:
            n = self.db.execute(
                "SELECT unique_key, category, extracted_insight, source_url "
                "FROM nuggets WHERE unique_key=?",
                (k,),
            ).fetchone()
            cites.append({
                "key": k,
                "found": n is not None,
                "detail": dict(n) if n else None,
            })
        d["citations"] = cites
        return {"ok": True, "idea": d}

    #: Indexes the Nuggets facet rail needs. Without them every filter click
    #: rescans 80k rows for three GROUP BYs and four counts — measured at
    #: 630 ms of SQL per click before, 150 ms after. Created on demand so a
    #: fresh database gets them without a migration step someone has to run.
    NUGGET_INDEXES = (
        ("idx_nuggets_platform", "nuggets(platform)"),
        ("idx_nuggets_community", "nuggets(community)"),
        ("idx_nuggets_category", "nuggets(category)"),
        ("idx_nuggets_trivial", "nuggets(trivial)"),
        ("idx_nuggets_unsynth", "nuggets(synthesized_at)"),
    )
    _indexed: set = set()

    def _ensure_nugget_indexes(self):
        key = str(self.db_path)
        if key in self._indexed:
            return
        try:
            for name, target in self.NUGGET_INDEXES:
                self.db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
            self.db.commit()
        except Exception:  # noqa: BLE001 - a read page must not die on an index
            pass
        self._indexed.add(key)

    NUGGET_FLAGS = {
        "trivial": "trivial = 1",
        "reembed": "needs_reembed = 1",
        "unclustered": "synthesized_at IS NULL",
        "flagged": "(trivial = 1 OR needs_reembed = 1)",
    }

    def nuggets_page(self, offset=0, limit=100, platform="", community="", category="",
                     flag="", q=""):
        """One page of the archive plus the facet counts the rail needs.

        The old endpoint handed the console every row (39 745 at the time of
        writing) and the browser painted them all. This one filters in SQL,
        returns a page, and reports the true total and the per-facet counts so
        the rail can say "reddit 935" without loading reddit."""
        self._ensure_nugget_indexes()
        # Each filter is (column-or-None, clause, args). The column lets a
        # facet drop its own filter and keep the others, so a count is always
        # "what you would get if you clicked this".
        filters = []
        if platform:
            filters.append(("platform", "platform = ?", [platform]))
        if community:
            filters.append(("community", "COALESCE(community,'') = ?", [community]))
        if category:
            filters.append(("category", "COALESCE(category,'') = ?", [category]))
        if flag in self.NUGGET_FLAGS:
            filters.append((None, self.NUGGET_FLAGS[flag], []))
        q = (q or "").strip()
        if q:
            filters.append((None, "(extracted_insight LIKE ? OR raw_text LIKE ? OR unique_key LIKE ?)",
                            [f"%{q}%"] * 3))

        def build(skip=None):
            ws, args = [], []
            for col, clause, a in filters:
                if skip and col == skip:
                    continue
                ws.append(clause); args += a
            return ((" WHERE " + " AND ".join(ws)) if ws else ""), args

        sql_where, args = build()
        try:
            offset = max(0, int(offset or 0)); limit = max(1, min(500, int(limit or 100)))
        except (TypeError, ValueError):
            offset, limit = 0, 100
        self.db.row_factory = sqlite3.Row
        # Only what the list renders. The full row is 44 columns and 2 KB —
        # `raw_text` alone was two thirds of a 205 KB response for one page —
        # and the table shows nine of them. Same rule the queue already
        # follows: the list carries what you scan, the detail view fetches the
        # body when you open one.
        cols = ("unique_key, category, extracted_insight, platform, community, source_url, "
                "trivial, needs_reembed, synthesized_at")
        try:
            rows = self.db.execute(
                f"SELECT {cols} FROM nuggets{sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*args, limit, offset)).fetchall()
            total = self.db.execute(f"SELECT COUNT(*) FROM nuggets{sql_where}", args).fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

        def facet(col):
            w2, a2 = build(skip=col)
            try:
                return [{"value": r[0] or "", "n": r[1]} for r in self.db.execute(
                    f"SELECT COALESCE({col},''), COUNT(*) FROM nuggets{w2} GROUP BY 1 ORDER BY 2 DESC LIMIT 60",
                    a2).fetchall()]
            except Exception:  # noqa: BLE001
                return []

        flags = {}
        for name, cond in self.NUGGET_FLAGS.items():
            try:
                flags[name] = self.db.execute(
                    f"SELECT COUNT(*) FROM nuggets{sql_where}{' AND ' if sql_where else ' WHERE '}{cond}", args
                ).fetchone()[0]
            except Exception:  # noqa: BLE001
                flags[name] = 0
        return {
            "ok": True,
            "nuggets": [self._rowdict(r) for r in rows],
            "total": total,
            "archive_total": count_nuggets(self.db),
            "offset": offset, "limit": limit,
            "facets": {
                "platform": facet("platform"),
                "community": facet("community"),
                "category": facet("category"),
                "flags": flags,
            },
        }

    def nuggets(self, limit=None):
        """Every archived nugget, newest first.

        The default used to be 500, applied AFTER loading and dicting every
        row, and the route never passed anything else — so an archive of 1,761
        showed 500 and counted itself as 500. Nothing said it had been cut.

        `total` is always the real count, so a caller that does pass a limit
        still reports the archive honestly rather than describing its own
        page as the whole of it (R55: no silent caps).
        """
        total = count_nuggets(self.db)
        cap = None
        if limit not in (None, "", 0, "0"):
            try:
                cap = max(1, int(limit))
            except (TypeError, ValueError):
                cap = None
        rows = list_nuggets(self.db, limit=cap)
        out = [self._rowdict(r) for r in rows]
        return {
            "ok": True,
            "nuggets": out,
            "total": total,
            "truncated": len(out) < total,
        }

    # ---- queue (what has been scraped but not yet treated) ----------------

    #: Batches listed on the Queue page in one response.
    #:
    #: The bodies are NOT included — 646 batches hold tens of megabytes of
    #: comment JSON, and a page that has to parse all of it to show a count is
    #: a page nobody opens twice. The summary reads counts; opening one batch
    #: fetches that batch.
    QUEUE_PAGE_SIZE = 300

    QUEUE_KINDS = ("comment", "listing")

    @staticmethod
    def _kind_clause(kind):
        return (" AND kind = ?", (kind,)) if kind in ConsoleAPI.QUEUE_KINDS else ("", ())

    def _queue_rows(self, status, limit, kind=""):
        """Batch metadata plus a comment COUNT, without the comments.

        json_array_length does the counting inside SQLite. Selecting the blob
        and calling len() in Python would move every byte of the queue across
        for a number the database can produce on its own.
        """
        try:
            return self.db.execute(
                "SELECT id, run_id, platform, source, thread_id, status, "
                "       created_at, claimed_at, attempts, error, "
                "       CASE WHEN json_valid(comments) "
                "            THEN json_array_length(comments) ELSE NULL END AS n_comments "
                "FROM ingest_batch WHERE status = ?" + self._kind_clause(kind)[0] + " "
                "ORDER BY id LIMIT ?",
                (status, *self._kind_clause(kind)[1], int(limit)),
            ).fetchall()
        except Exception:  # noqa: BLE001 - table absent until the worker runs
            return []

    def _queue_totals(self, kind=""):
        """Per-status batch and comment counts for the whole queue (or one kind)."""
        out = {}
        clause, args = self._kind_clause(kind)
        try:
            rows = self.db.execute(
                "SELECT status, COUNT(*), "
                "       COALESCE(SUM(CASE WHEN json_valid(comments) "
                "                    THEN json_array_length(comments) ELSE 0 END), 0) "
                "FROM ingest_batch WHERE 1=1" + clause + " GROUP BY status", args
            ).fetchall()
        except Exception:  # noqa: BLE001
            return out
        for status, batches, comments in rows:
            out[status] = {"batches": batches, "comments": comments}
        return out

    def _queue_by_kind(self, status):
        """Comment batches and listing batches are two queues wearing one
        table: different treatment, different budget. The page shows them as
        two tabs, and the pending headline must not add them together."""
        out = {k: {"batches": 0, "items": 0} for k in self.QUEUE_KINDS}
        try:
            rows = self.db.execute(
                "SELECT kind, COUNT(*), "
                "  COALESCE(SUM(CASE WHEN kind='listing' AND json_valid(listings) THEN json_array_length(listings) "
                "                    WHEN json_valid(comments) THEN json_array_length(comments) ELSE 0 END), 0) "
                "FROM ingest_batch WHERE status = ? GROUP BY kind", (status,)
            ).fetchall()
        except Exception:  # noqa: BLE001
            return out
        for kind, batches, items in rows:
            out[kind or "comment"] = {"batches": batches, "items": items}
        return out

    def _queue_by_platform(self, status, kind=""):
        """Where the waiting work came from.

        This is the number that matters most on the page: a queue that is 93%
        one platform is not a backlog, it is a skew waiting to be baked into
        the archive, and it should be visible before it is treated rather than
        discovered afterwards.
        """
        try:
            rows = self.db.execute(
                "SELECT platform, COUNT(*), "
                "       COALESCE(SUM(CASE WHEN json_valid(comments) "
                "                    THEN json_array_length(comments) ELSE 0 END), 0) "
                "FROM ingest_batch WHERE status = ?" + self._kind_clause(kind)[0] + " GROUP BY platform "
                "ORDER BY 3 DESC",
                (status, *self._kind_clause(kind)[1]),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return []
        return [{"platform": r[0], "batches": r[1], "comments": r[2]} for r in rows]

    def _treatment_block(self):
        """When treatment can next run, straight from the recorded block.

        Without this the Queue page shows a number going nowhere and says
        nothing about why. "9,000 waiting" and "9,000 waiting, treatment
        resumes in 22 minutes" are different situations.
        """
        from jester import llm_quota

        # "groq" and not the CONFIGURED provider, because cmd_treat records the
        # block under that literal name whatever thresholds.yaml says — and it
        # is the only writer. Reading the configured one instead looked up
        # "fake" against a block filed under "groq" and reported a queue that
        # was free to drain while treatment was in fact shut until 12:08.
        # The two must agree; this is the side that has to follow.
        provider = llm_quota.TREATMENT_PROVIDER
        try:
            st = llm_quota.status(self.db, provider)
        except Exception:  # noqa: BLE001 - table absent before the first block
            return {"provider": provider, "blocked": False}
        waiting = int(st.get("seconds_remaining") or 0)
        return {
            "provider": provider,
            "blocked": bool(st.get("blocked")) and waiting > 0,
            "seconds_remaining": waiting,
            "human": llm_quota.human(waiting) if waiting > 0 else "",
            "blocked_until": st.get("blocked_until") or "",
            "reason": st.get("reason") or "",
        }

    def queue(self, status="pending", limit=None, kind=""):
        """Everything scraped and not yet treated.

        The gap this fills: ingestion and treatment run on separate clocks by
        design — scraping is cheap and bounded by politeness, treatment is
        bounded by a token quota that resets on someone else's schedule — so
        the queue between them is a real, long-lived thing. It was visible
        only as a single number on the Overview page.
        """
        cap = int(limit or self.QUEUE_PAGE_SIZE)
        kind = kind if kind in self.QUEUE_KINDS else ""
        rows = self._queue_rows(status, cap, kind)
        totals = self._queue_totals(kind)
        here = totals.get(status, {"batches": 0, "comments": 0})
        return {
            "ok": True,
            "status": status,
            "kind": kind,
            "totals": totals,
            "by_kind": self._queue_by_kind(status),
            "by_platform": self._queue_by_platform(status, kind),
            "treatment": self._treatment_block(),
            # R55: the page says what it is showing versus what exists, so a
            # capped list can never read as the whole queue.
            "shown": len(rows),
            "total_batches": here["batches"],
            "total_comments": here["comments"],
            "truncated": len(rows) < here["batches"],
            "batches": [
                {
                    "id": r[0], "run_id": r[1], "platform": r[2], "source": r[3],
                    "thread_id": r[4], "status": r[5], "created_at": r[6],
                    "claimed_at": r[7], "attempts": r[8], "error": r[9],
                    "n_comments": r[10],
                }
                for r in rows
            ],
        }

    def queue_batch(self, batch_id):
        """One batch's actual comments, plus the post they came from."""
        row = self.db.execute(
            "SELECT id, run_id, platform, source, thread_id, status, created_at, "
            "       attempts, error, comments, thread_meta "
            "FROM ingest_batch WHERE id = ?",
            (int(batch_id),),
        ).fetchone()
        if row is None:
            return {"ok": False, "error": f"batch #{batch_id} not found"}
        try:
            comments = json.loads(row[9] or "[]")
        except (TypeError, ValueError) as exc:
            # A batch whose JSON will not parse is why it is stuck. Saying so
            # beats an empty list that looks like an empty batch.
            return {"ok": False, "error": f"batch #{batch_id} holds unreadable JSON: {exc}"}
        try:
            post = json.loads(row[10]) if row[10] else None
        except (TypeError, ValueError):
            post = None
        return {
            "ok": True,
            "batch": {
                "id": row[0], "run_id": row[1], "platform": row[2], "source": row[3],
                "thread_id": row[4], "status": row[5], "created_at": row[6],
                "attempts": row[7], "error": row[8],
            },
            "post": post,
            "comments": comments,
        }

    # ---- clusters (semantic regrouping) ---------------------------------

    def clusters(self):
        """Every theme the last clustering pass produced.

        No default cap — see `nuggets` for what a silent one costs.
        """
        rows = [self._rowdict(r) for r in list_clusters(self.db)]
        for r in rows:
            for key in ("platforms", "nugget_keys"):
                try:
                    r[key] = json.loads(r.get(key) or "[]")
                except (TypeError, ValueError):
                    r[key] = []
        stale = any(
            (r.get("embedding_model") or "").startswith("fake") for r in rows
        )
        return {
            "ok": True,
            "clusters": rows,
            "total": len(rows),
            # A pass run against hash vectors is noise wearing labels. It
            # cannot happen through this API, but a database carried over from
            # an older build can hold one, and it must be visibly marked
            # rather than silently trusted.
            "fake_embeddings": stale,
            "nuggets_total": count_nuggets(self.db),
        }

    def cluster(self, cluster_id: int):
        """One theme, its members, and any draft ideas made from it."""
        row = get_cluster(self.db, cluster_id)
        if row is None:
            return {"ok": False, "error": f"no cluster {cluster_id}"}
        d = self._rowdict(row)
        for key in ("platforms", "nugget_keys"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except (TypeError, ValueError):
                d[key] = []
        d["members"] = [self._rowdict(m) for m in cluster_members(self.db, cluster_id)]
        d["ideas"] = [
            self._rowdict(i) for i in list_cluster_ideas(self.db, cluster_id)
        ]
        return {"ok": True, "cluster": d}

    def cluster_run_async(self, threshold=None, min_size=None, min_nuggets=None,
                          limit=None, run_id="console-cluster"):
        """Start a regroup in the background and return immediately.

        The synchronous version holds one HTTP request for the whole pass —
        577 seconds measured on 1,763 nuggets, most of it embedding. The
        browser abandons the request long before that, so the work completed
        and nobody ever saw the result.
        """
        from jester.jobs import start_job

        def work(progress):
            progress("embedding and clustering the archive")
            # A fresh API bound to the same database, built INSIDE the thread:
            # sqlite3 connections belong to the thread that opened them, and
            # `self.db` was opened on the request thread. Reusing it fails with
            # "SQLite objects created in a thread can only be used in that same
            # thread" the moment the job touches the archive.
            worker_api = ConsoleAPI(self.db_path, self.config_dir)
            out = worker_api.cluster_run(
                threshold=threshold, min_size=min_size,
                min_nuggets=min_nuggets, limit=limit, run_id=run_id,
            )
            if out.get("ok") is False:
                # Surface a refusal as a job failure rather than a "done" job
                # carrying an error nobody reads.
                raise RuntimeError(out.get("error") or "clustering failed")
            progress(
                f"{out.get('clusters', 0)} theme(s) from "
                f"{out.get('nuggets', 0)} nugget(s)"
            )
            return out

        return start_job(self.db_path, "cluster", work)

    def job(self, job_id: int):
        from jester.jobs import get_job

        got = get_job(self.db, int(job_id))
        if got is None:
            return {"ok": False, "error": f"no job {job_id}"}
        return {"ok": True, "job": got}

    def jobs(self, limit: int = 10):
        from jester.jobs import reap_stale_jobs, recent_jobs

        # A daemon thread does not survive a restart; a row still saying
        # "running" after one is a job nobody is working on.
        reap_stale_jobs(self.db)
        return {"ok": True, "jobs": recent_jobs(self.db, limit)}

    def cluster_run(self, threshold=None, min_size=None, min_nuggets=None,
                    limit=None, run_id="console-cluster"):
        """Regroup the archive. The console's copy of `jester cluster`.

        Calls the same `cluster_archive` the CLI does rather than
        reimplementing the pass — the `jester schedule` lesson: behaviour that
        exists in only one caller is behaviour nobody can check.
        """
        from jester import clustering
        from jester.clustering import FakeEmbeddingRefused, cluster_archive

        def _num(v, default, cast=float):
            if v in (None, "", "0", 0):
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        try:
            return cluster_archive(
                self.db,
                self._cfg().thresholds,
                run_id=run_id,
                threshold=_num(threshold, clustering.DEFAULT_THRESHOLD, float),
                min_size=_num(min_size, clustering.DEFAULT_MIN_SIZE, int),
                min_nuggets=_num(min_nuggets, clustering.DEFAULT_MIN_NUGGETS, int),
                limit=_num(limit, None, int),
            )
        except FakeEmbeddingRefused as exc:
            # A refusal, not a crash. The operator needs the knob, not a 500.
            return {
                "ok": False,
                "error": str(exc),
                "fix": "set embedding_provider: ollama in config/thresholds.yaml, "
                       "make sure Ollama is reachable, then run this again",
            }
        except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
            return {"ok": False, "error": f"clustering failed: {exc}"}

    def cluster_generate_idea(self, cluster_id: int):
        """Draft an idea FROM one theme.

        Deliberately writes to `cluster_ideas`, not `ideas`. Drafts are cheap
        and most get discarded; letting them into the archive directly would
        turn it into a scratchpad. Promotion is a separate, explicit act.
        """
        from jester.agents.critic import Critic
        from jester.agents.synthesizer import row_to_nugget
        from jester.llm import (
            model_name,
            select_critic_llm,
            select_synthesizer_llm,
        )
        from jester.models import Idea, IdeaScores

        row = get_cluster(self.db, cluster_id)
        if row is None:
            return {"ok": False, "error": f"no cluster {cluster_id}"}
        try:
            keys = json.loads(row["nugget_keys"] or "[]")
        except (TypeError, ValueError):
            keys = []
        if not keys:
            return {"ok": False, "error": "this cluster cites no nuggets"}

        self.db.row_factory = sqlite3.Row
        rows = self.db.execute(
            "SELECT * FROM nuggets WHERE unique_key IN (%s)"
            % ",".join("?" * len(keys)),
            keys,
        ).fetchall()
        if not rows:
            return {"ok": False, "error": "the cluster's nuggets are gone from the archive"}
        nuggets = [row_to_nugget(r) for r in rows]

        synth = select_synthesizer_llm(self._cfg().thresholds)
        draft = synth.synthesize(nuggets)
        idea = Idea(
            title=draft.title,
            problem_statement=draft.problem_statement,
            proposed_solution=draft.proposed_solution,
            supporting_nuggets=[n.unique_key for n in nuggets],
            synthesis_model=model_name(synth),
            scores=IdeaScores(),
        )
        # Critic.score persists ONLY when idea.id is set. A draft has none, so
        # this scores in memory and nothing reaches the ideas archive — which
        # is the whole point of the draft/promote split.
        critic = Critic(self.db, self._cfg().thresholds)
        idea = critic.score(idea, select_critic_llm(self._cfg().thresholds))

        draft_id = insert_cluster_idea(self.db, cluster_id, idea)
        return {
            "ok": True,
            "draft_id": draft_id,
            "idea": {
                "id": draft_id,
                "title": idea.title,
                "problem_statement": idea.problem_statement,
                "proposed_solution": idea.proposed_solution,
                "supporting_nuggets": idea.supporting_nuggets,
                "demand_signal": idea.scores.demand_signal,
                "feasibility": idea.scores.feasibility,
                "competition": idea.scores.competition,
                "overall": idea.scores.overall,
                "synthesis_model": idea.synthesis_model,
            },
            # Said out loud: a draft is not archived until somebody saves it.
            "detail": "draft only — not in the Ideas archive until you save it",
        }

    def cluster_save_idea(self, draft_id: int):
        """Promote one draft into the Ideas archive."""
        return promote_cluster_idea(self.db, int(draft_id))

    def cluster_discard_idea(self, draft_id: int):
        if delete_cluster_idea(self.db, int(draft_id)):
            return {"ok": True, "detail": f"draft {draft_id} discarded"}
        return {"ok": False, "error": f"no draft idea {draft_id}"}

    # ---- search ----------------------------------------------------------

    def search(self, q="", limit=30, platform="", category=""):
        """Semantic search over the archive.

        The gap this closes: every nugget has been embedded since the first
        run, `VectorStore.search_scored` has existed the whole time, and it was
        wired only into dedup. The archive was searchable and there was no way
        to search it.

        Falls back to a substring scan when no vector backend is reachable —
        a worse search is better than a dead box, and the response says which
        one answered so nobody mistakes one for the other.
        """
        q = (q or "").strip()
        if not q:
            return {"ok": False, "error": "type something to search for"}
        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 30

        rows, mode, detail = [], "text", ""
        cfg = self._cfg()
        fake = (getattr(cfg.thresholds, "embedding_provider", "") or "").lower() == "fake"

        if not fake:
            try:
                from jester.cli import _vector_for

                vector = _vector_for(cfg, self.db_path)
                # No threshold: the caller ranks. A score_threshold here would
                # silently return nothing for a query phrased differently from
                # the corpus, which reads as "no results" rather than "try
                # other words".
                hits = vector.search_scored(q, limit=limit * 3)
                keys = [k for _, k in hits if k]
                scores = {k: s for s, k in hits if k}
                if keys:
                    self.db.row_factory = sqlite3.Row
                    found = self.db.execute(
                        "SELECT * FROM nuggets WHERE unique_key IN (%s)"
                        % ",".join("?" * len(keys)),
                        keys,
                    ).fetchall()
                    by_key = {r["unique_key"]: r for r in found}
                    for k in keys:  # preserve rank order
                        if k in by_key:
                            d = self._rowdict(by_key[k])
                            d["score"] = round(float(scores.get(k, 0.0)), 4)
                            rows.append(d)
                    mode = "semantic"
                else:
                    # The vector store answered and had nothing for this
                    # database. Distinct from "unavailable" and from "fake
                    # embeddings", and the commonest cause is simply that
                    # nothing has been embedded into this collection yet.
                    detail = (
                        "no vectors indexed for this database yet — "
                        "run `jester reembed --all`; searched text instead"
                    )
            except Exception as exc:  # noqa: BLE001
                detail = f"vector search unavailable ({exc}); fell back to text"
        else:
            detail = (
                "embedding_provider is `fake`, so semantic search would rank by "
                "hash collision — using text search instead"
            )

        if mode == "text":
            self.db.row_factory = sqlite3.Row
            like = f"%{q}%"
            found = self.db.execute(
                "SELECT * FROM nuggets WHERE raw_text LIKE ? OR extracted_insight LIKE ? "
                "ORDER BY id DESC LIMIT ?",
                (like, like, limit * 3),
            ).fetchall()
            rows = [self._rowdict(r) for r in found]

        # Filters apply AFTER retrieval so the mode is what changed, not the
        # meaning of the query.
        if platform:
            rows = [r for r in rows if (r.get("platform") or "") == platform]
        if category:
            rows = [r for r in rows if (r.get("category") or "") == category]

        return {
            "ok": True,
            "mode": mode,
            "query": q,
            "results": rows[:limit],
            "returned": len(rows[:limit]),
            "detail": detail,
        }

    def doctor(self):
        findings = evaluate_doctor(self.db)
        return {"ok": len(findings) == 0, "findings": findings}

    def eval_gate(self):
        """Run the M2.2 calibration gate over known_good/bad sets."""
        from jester.cli import _parse_eval_line
        from jester.scoring import compute_overall

        cfg = self._cfg()
        gate = cfg.thresholds.good_idea_min
        good_path = self.repo_root / "eval" / "known_good.md"
        bad_path = self.repo_root / "eval" / "known_bad.md"
        checks = failures = 0
        details = []
        for path, expect_pass in ((good_path, True), (bad_path, False)):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                parsed = _parse_eval_line(line)
                if not parsed:
                    continue
                checks += 1
                title, demand, feasibility, competition = parsed
                overall = compute_overall(demand, feasibility, competition)
                ok = (overall >= gate) == expect_pass
                if not ok:
                    failures += 1
                details.append({"title": title, "overall": overall,
                                "expect_pass": expect_pass, "ok": ok})
        return {"ok": failures == 0, "checks": checks, "failures": failures,
                "gate": gate, "details": details}

    # ---- sources ----------------------------------------------------------

    def _source_state(self):
        """When the worker last walked each source, keyed by name.

        `source_state` is one of the tables Go OWNS (§25.3/D-17), so a database
        that has never seen the worker simply does not have it. Missing is a
        normal state here, not an error — it means "nothing fetched yet".
        """
        try:
            rows = self.db.execute(
                "SELECT name, last_fetched_at, last_queued, total_queued, "
                "measured_visits, visits FROM source_state"
            ).fetchall()
        except Exception:  # noqa: BLE001 - table absent, or predates the columns
            # The worker adds both columns on its next Open, but until then the
            # rest of the row is still true and dropping it would reset every
            # visit count on this page to "never visited". Retry without them:
            # zero measured visits is exactly right for a history that was
            # never measured, and the verdict below then withholds judgement
            # rather than guessing.
            try:
                rows = [(r[0], r[1], r[2], 0, 0, r[3]) for r in self.db.execute(
                    "SELECT name, last_fetched_at, last_queued, visits FROM source_state"
                ).fetchall()]
            except Exception:  # noqa: BLE001 - table absent until the worker runs
                return {}
        # Positional, not by name: row_factory is toggled between sqlite3.Row
        # and the default by other readers on this same connection, so a
        # name-keyed access here works or raises depending on call order.
        return {r[0]: {"last_fetched_at": r[1], "last_queued": r[2],
                       "total_queued": r[3], "measured_visits": r[4],
                       "visits": r[5]}
                for r in rows}

    #: Visits a source gets before a run of zeroes is treated as evidence
    #: rather than luck. Two is too few — a subreddit can genuinely have
    #: nothing new twice running, and Reddit's own 403s cost a visit without
    #: saying anything about the source.
    PARK_AFTER_BARREN_VISITS = 3

    @staticmethod
    def _source_health(state):
        """A verdict on one source's row of source_state.

        Returns (status, reason). None of them is a soft `dead`: two separate
        states mean "no evidence", and they are separate because they read
        differently to an operator. `unknown` is a source nobody has walked.
        `unmeasured` is one that WAS walked, before the worker recorded what
        those visits yielded — showing that as "not yet visited" next to a
        row reading "3 visits" is a straight contradiction on the page.
        """
        if not state:
            return "unknown", "never visited"
        if int(state.get("visits") or 0) == 0:
            return "unknown", "never visited"
        # Only visits whose yield was actually recorded can support a verdict.
        # A database that predates the yield counters has real visits behind it
        # and no measurement of them, and calling that barren would park a
        # source on the strength of a column default.
        measured = int(state.get("measured_visits") or 0)
        total = int(state.get("total_queued") or 0)
        if total > 0:
            return "producing", f"{total} comment(s) queued across {measured} measured visit(s)"
        if measured == 0:
            visits = int(state.get("visits") or 0)
            return "unmeasured", (
                f"{visits} visit(s), all before yields were recorded")
        if measured >= ConsoleAPI.PARK_AFTER_BARREN_VISITS:
            return "barren", f"{measured} measured visits, nothing ever queued"
        # Zero so far, but not yet enough measured visits to mean anything.
        return "quiet", f"{measured} measured visit(s), nothing queued yet"

    def sources(self):
        """The curated ingest list, plus the vocabulary the UI needs to render
        an add-form without hard-coding platform knowledge in JavaScript."""
        srcs = load_sources(self._sources_path)
        state = self._source_state()
        return {
            "ok": True,
            "path": str(self._sources_path),
            # The worker walks least-recently-fetched first, so "when was this
            # last visited" is now the thing that decides run order — it has to
            # be visible, or the rotation looks like randomness.
            "sources": [self._with_health(s, state.get(s.name)) for s in srcs],
            "platform_kinds": {k: list(v) for k, v in PLATFORM_KINDS.items()},
            "supported_kinds": [list(pair) for pair in sorted(SUPPORTED_KINDS)],
            # Why a platform the add-form offers still cannot be fetched. A gap
            # with no stated reason reads as an oversight; this one is a
            # decision, and the page should say which it is.
            "platform_gaps": {
                p: gap_reason(p) for p in PLATFORM_KINDS if gap_reason(p)
            },
            # source_state is keyed by name, so two sources sharing one share a
            # health row and a rotation slot — each hides the other's yield.
            # The console's own add/rename path calls unique_name(), so this
            # can only arrive from a hand-edited file; it is reported rather
            # than silently repaired, because which one to rename is a
            # judgement about the list, not about the collision.
            "duplicate_names": duplicate_names(srcs),
            "park_after_barren_visits": self.PARK_AFTER_BARREN_VISITS,
        }

    def _with_health(self, src, state):
        status, reason = self._source_health(state)
        return {**src.to_dict(), "state": state,
                "health": status, "health_reason": reason}

    def _write_sources(self, srcs):
        written = []
        for path in self._mirror_paths("sources.yaml"):
            save_sources(path, srcs)
            written.append(str(path))
        return written

    def add_source(self, url, platform="auto", name=None, enabled=True, notes=""):
        try:
            src = parse_source(url, platform, name=name, enabled=enabled, notes=notes)
        except SourceError as exc:
            return {"ok": False, "error": str(exc)}
        srcs = load_sources(self._sources_path)
        if any(s.url.rstrip("/").lower() == src.url.rstrip("/").lower() for s in srcs):
            return {"ok": False, "error": f"{src.url} is already in the list"}
        src.name = unique_name(srcs, src.name)
        srcs.append(src)
        written = self._write_sources(srcs)
        return {"ok": True, "source": src.to_dict(), "written_to": written}

    def update_source(self, name, **fields):
        srcs = load_sources(self._sources_path)
        try:
            src = find_source(srcs, name)
        except SourceError as exc:
            return {"ok": False, "error": str(exc)}
        if "url" in fields and fields["url"]:
            try:
                reparsed = parse_source(fields["url"], fields.get("platform") or "auto")
            except SourceError as exc:
                return {"ok": False, "error": str(exc)}
            src.url, src.platform, src.kind = reparsed.url, reparsed.platform, reparsed.kind
        if "enabled" in fields:
            src.enabled = bool(fields["enabled"])
        if "notes" in fields:
            src.notes = str(fields["notes"] or "").strip()
        if fields.get("new_name"):
            src.name = unique_name(srcs, slugify(fields["new_name"]), skip=name)
        written = self._write_sources(srcs)
        return {"ok": True, "source": src.to_dict(), "written_to": written}

    def delete_source(self, name):
        srcs = load_sources(self._sources_path)
        remaining = [s for s in srcs if s.name != name]
        if len(remaining) == len(srcs):
            return {"ok": False, "error": f"no source named {name!r}"}
        written = self._write_sources(remaining)
        return {"ok": True, "removed": name, "written_to": written}

    # ---- config -----------------------------------------------------------

    def config(self):
        t = self._cfg().thresholds
        scraper = load_scraper_config(Path(self.config_dir) / "scraper.yaml").__dict__
        return {
            "ok": True,
            "config_dir": self.config_dir,
            "thresholds": {
                "llm_provider": t.llm_provider,
                "embedding_provider": t.embedding_provider,
                "extractor_model": t.extractor_model,
                "archivist_model": t.archivist_model,
                "synthesizer_model": t.synthesizer_model,
                "critic_model": t.critic_model,
                "embedding_model": t.embedding_model,
                "dedup_threshold": t.dedup_threshold,
                "good_idea_min": t.good_idea_min,
                "request_delay_ms": t.request_delay_ms,
                "max_comments_per_thread": t.max_comments_per_thread,
                "max_threads_per_source": t.max_threads_per_source,
                "min_comments_per_thread": t.min_comments_per_thread,
                "min_upvotes": t.min_upvotes,
                "prefilter": {
                    "min_chars": t.prefilter_min_chars,
                    "max_chars": t.prefilter_max_chars,
                    "max_emoji": t.prefilter_max_emoji,
                    "max_mentions": t.prefilter_max_mentions,
                    "min_words": t.prefilter_min_words,
                },
                "competitor_ttl_days": t.competitor_ttl_days,
                "critic_web_rate_limit": t.critic_web_rate_limit,
                "critic_web_daily_budget": t.critic_web_daily_budget,
            },
            # `_EDITABLE` is the write allow-list; this is the render list,
            # and they are not the same set. The per-role providers stay
            # writable through /api/thresholds but are drawn by the Agent
            # models card, which offers the three valid values as a dropdown
            # beside the model each one governs. Rendering them here too would
            # be a second, typo-prone place to set one thing.
            "editable": {
                key: {"type": caster.__name__, "range": Thresholds._RANGES.get(key),
                      "value": getattr(t, key), "choices": _CHOICES.get(key)}
                for key, caster in Thresholds._EDITABLE.items()
                if key not in ROLE_PROVIDER_KEYS
            },
            "scraper": scraper,
            "sources": [s.to_dict() for s in load_sources(self._sources_path)],
            "env": {
                "JESTER_QDRANT_URL": os.environ.get("JESTER_QDRANT_URL", ""),
                "JESTER_ORIGIN": os.environ.get("JESTER_ORIGIN", "manual"),
                "JESTER_NOTIFY_CMD_set": bool(os.environ.get("JESTER_NOTIFY_CMD")),
            },
        }

    # ---- agent models -----------------------------------------------------

    def _profiles(self):
        """Config directories the console may edit. Model choice is what
        separates them, so each is edited on its own — never mirrored.

        The repo's config/config-live pair is offered only when the console is
        actually serving it; pointed anywhere else, the configured dir is the
        one and only profile.
        """
        active = Path(self.config_dir)
        candidates = [active]
        if _same_dir(active, self.repo_root / "config"):
            candidates.append(self.repo_root / _LIVE_CONFIG_DIR)
        out, seen = [], set()
        for path in candidates:
            if not (path / "thresholds.yaml").exists():
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        return out

    def _resolve_profile(self, profile):
        """Map a profile name from the UI onto a config dir, refusing anything
        that is not one of the known profiles."""
        if not profile:
            return Path(self.config_dir)
        for path in self._profiles():
            if profile in (str(path), path.name, str(path.resolve())):
                return path
        raise ConfigError(f"unknown config profile {profile!r}")

    def ollama_models(self):
        """Names Ollama currently has pulled, or an empty list when it is down.
        Never fabricated: an empty list means "could not ask", not "none"."""
        try:
            with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2) as r:
                payload = json.loads(r.read())
        except Exception:  # noqa: BLE001 - daemon down is a normal state here
            return None
        names = [m.get("name") for m in payload.get("models", []) if m.get("name")]
        return sorted(set(names))

    def models(self, profile=None):
        try:
            path = self._resolve_profile(profile)
        except ConfigError as exc:
            return {"ok": False, "error": str(exc)}
        t = load_config(str(path)).thresholds
        installed = self.ollama_models()
        # Which backends are in play once per-role overrides are resolved —
        # keying this off `llm_provider` alone missed the whole point of the
        # overrides, which exist so one role can use Groq while the rest do not.
        in_use = {provider_for(t, role) for role in ("extractor", "synthesizer", "critic")}
        groq_ready = None
        groq_choices = []
        if "groq" in in_use:
            # Ask Groq what this key can actually reach. An empty list means
            # "could not ask" (no key, no network), never "no models".
            groq_choices = GroqClient().list_models()
            groq_ready = bool(groq_choices)
            if not groq_choices:
                groq_choices = list(GROQ_CHAT_MODELS)
        current = {
            "extractor": t.extractor_model,
            "archivist": t.archivist_model,
            "synthesizer": t.synthesizer_model,
            "critic": t.critic_model,
            "embedding_model": t.embedding_model,
        }
        # "Missing" means "configured but not pulled", which is only a
        # question for an Ollama-served role. A Groq role has nothing to pull,
        # so flagging it would be a permanent false alarm.
        missing = set()
        if installed is not None:
            by_role = {
                "extractor": t.extractor_model,
                "synthesizer": t.synthesizer_model,
                "critic": t.critic_model,
            }
            for role, name in by_role.items():
                if provider_for(t, role) == "ollama" and name                         and not _model_installed(name, installed):
                    missing.add(name)
            # The archivist has no provider of its own; it follows llm_provider.
            if t.llm_provider == "ollama" and t.archivist_model                     and not _model_installed(t.archivist_model, installed):
                missing.add(t.archivist_model)
            # Embedding is Ollama's job regardless of the chat provider.
            if t.embedding_provider == "ollama" and t.embedding_model                     and not _model_installed(t.embedding_model, installed):
                missing.add(t.embedding_model)
        missing = sorted(missing)
        return {
            "ok": True,
            "profile": path.name,
            "profile_dir": str(path),
            "profiles": [
                {"name": p.name, "dir": str(p),
                 "llm_provider": load_config(str(p)).thresholds.llm_provider}
                for p in self._profiles()
            ],
            "roles": list(MODEL_ROLES) + ["embedding_model"],
            "models": current,
            "llm_provider": t.llm_provider,
            "embedding_provider": t.embedding_provider,
            # Which backend actually serves each chat role once its override is
            # resolved. The UI must not re-implement that precedence.
            "role_providers": {
                role: provider_for(t, role) for role in ("extractor", "synthesizer", "critic")
            },
            "ollama_up": installed is not None,
            "installed": installed or [],
            # One catalogue per backend rather than one merged "available
            # models" list: which of these applies is a per-role question, and
            # the role's provider is the only thing that answers it.
            "groq_choices": groq_choices,
            "ollama_choices": installed or [],
            "groq_ready": groq_ready,
            # Anything configured but not pulled would fail at run time — the
            # UI marks these rather than letting the run discover it. Ollama
            # reports `name:latest`, so a bare `name` is the same model. Only
            # Ollama-served roles can be checked this way; Groq has nothing to
            # pull, so an unreachable Groq is reported as `groq_ready: false`
            # instead of marking every model "missing".
            "missing": missing,
        }

    def update_models(self, patch, profile=None):
        try:
            path = self._resolve_profile(profile)
            applied = save_models(path / "thresholds.yaml", patch)
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "profile": path.name,
                "written_to": str(path / "thresholds.yaml")}

    def update_thresholds(self, patch, profile=None):
        """Write threshold keys back to thresholds.yaml.

        With no `profile` the shared facts mirror across config/ and
        config-live/, which is what the Config page's knobs want. With one,
        the write is scoped to that profile alone: a per-role provider is a
        profile's own choice, exactly like its model names, and mirroring it
        would erase the only difference between the two profiles.
        """
        try:
            if profile is not None:
                paths = [self._resolve_profile(profile) / "thresholds.yaml"]
            else:
                paths = self._mirror_paths("thresholds.yaml")
            written = []
            applied = {}
            for path in paths:
                applied = save_thresholds(path, patch)
                written.append(str(path))
        except (ConfigError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "applied": applied, "written_to": written,
                "profile": Path(paths[0]).parent.name}

    @staticmethod
    def _go_binary():
        """Resolve the Go toolchain. Kept as a method because tests and the
        infra probe both reach for it; the resolution itself lives in
        `jester.worker` alongside the code that runs the binary."""
        return go_binary()

    #: /api/infra probes Ollama, cloakserve, Qdrant and the Go toolchain on
    #: every call — about 3 s — and the console asked for it on every page
    #: open, which was most of the blank first paint. One answer is good for
    #: 30 s; a service that flips inside that window shows up on the next
    #: refresh, and the run buttons re-check before they act anyway.
    INFRA_TTL_S = 30
    _infra_cache: dict = {}

    def infra(self, fresh=False):
        key = str(self.db_path)
        hit = self._infra_cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < self.INFRA_TTL_S:
            return dict(hit[1], cached=True)
        out = self._infra_probe()
        self._infra_cache[key] = (time.monotonic(), out)
        return dict(out, cached=False)

    def _infra_probe(self):
        def tcp(host, port, timeout=1.5):
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        installed = self.ollama_models()
        ollama_up = installed is not None
        ollama_models = installed or []

        cloak_up = tcp("127.0.0.1", 9222)
        go_bin = self._go_binary()
        return {
            "ok": True,
            "ollama": {"up": ollama_up, "models": ollama_models},
            "qdrant_6333": tcp("127.0.0.1", 6333),
            # A port check says a container is listening, not that anything
            # talks to it. For an entire day this panel showed Qdrant green
            # while every write went to a 32-dimension local file, because
            # JESTER_QDRANT_URL was unset. Report what the app is CONFIGURED
            # to use, and how wide the vectors in it actually are.
            "vectors": self._vector_state(),
            "cloakserve_9222": cloak_up,
            "worker_go_available": bool(go_bin),
            "go_bin": go_bin or "",
            # Live ingestion needs both halves; the UI gates its button on this.
            "can_ingest_live": bool(go_bin) and cloak_up,
        }

    def _vector_state(self):
        """Where vectors actually go, and whether that store is usable.

        `mode` is the honest headline: "server" only when JESTER_QDRANT_URL is
        set AND answers; "local file" otherwise, which is a working but
        single-process store that no other component shares.
        """
        from jester.cli import collection_for

        url = os.environ.get("JESTER_QDRANT_URL") or ""
        # Namespaced per database: a shared server has no other notion of which
        # database a vector belongs to, and reporting a collection this console
        # does not actually write to would be the same class of lie the port
        # check was.
        collection = collection_for(self.db_path)
        state = {
            "mode": "server" if url else "local file",
            "url": url,
            "collection": collection,
            "points": None,
            "dim": None,
            "embedding_provider": "",
            "embedding_dim": None,
            "usable": False,
            "detail": "",
        }
        try:
            t = self._cfg().thresholds
            state["embedding_provider"] = getattr(t, "embedding_provider", "")
            from jester.embed import select_embedding

            state["embedding_dim"] = int(getattr(select_embedding(t), "dim", 0))
        except Exception as exc:  # noqa: BLE001
            state["detail"] = f"cannot read embedding config: {exc}"
            return state

        if not url:
            state["detail"] = (
                "JESTER_QDRANT_URL is unset, so vectors go to a per-database "
                "file that no other process can share"
            )
            return state
        try:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=url, timeout=5)
            if not client.collection_exists(collection):
                state["detail"] = (
                    f"server reachable; {collection} not created yet — "
                    "run a cycle or `jester reembed --all`"
                )
                state["usable"] = True
                return state
            info = client.get_collection(collection)
            state["points"] = int(info.points_count or 0)
            state["dim"] = int(info.config.params.vectors.size)
        except Exception as exc:  # noqa: BLE001
            state["detail"] = f"unreachable: {exc}"
            return state

        # The failure this exists to catch: a collection built for one vector
        # width while the configured embedder produces another. It reads as
        # healthy right up until the first upsert.
        if state["dim"] and state["embedding_dim"] and state["dim"] != state["embedding_dim"]:
            state["detail"] = (
                f"collection is {state['dim']}-dim but {state['embedding_provider']} "
                f"produces {state['embedding_dim']} — delete the store and re-embed"
            )
            return state
        state["usable"] = True
        if (state["embedding_provider"] or "").lower() == "fake":
            state["detail"] = (
                "embedding_provider is `fake`: these vectors are SHA-256 hashes "
                "and carry no meaning"
            )
        return state

    # ---- actions ----------------------------------------------------------

    # The goal types the console offers. `posts` and `comments` are ingestion
    # ceilings the Go worker enforces; `ideas` is a synthesis ceiling the
    # Python pipeline enforces. Anything else is refused rather than dropped —
    # a goal the operator set and the run ignored is the worst outcome.
    _GOAL_TYPES = ("posts", "comments", "ideas")

    def run_pipeline(self, live_models=False, run_id="console-run",
                     source_names=None, goal=None, ingest=None):
        """One operator-facing run: optionally ingest the selected sources,
        then run the Python agent pipeline over everything queued.

        The console's own copy used to do only the second half. That reads fine
        on day one and is inert forever after: `cmd_run` falls back to the
        FixtureFetcher when nothing is pending, the skip-list rejects every
        fixture comment the second time, and so each press completed in 0.2s
        having archived nothing. Pressing "run pipeline" has to be able to
        produce new work, which means it has to be able to fetch.

        `source_names` selects which curated sources to fetch (None/empty =>
        every enabled one). `goal` is `{"type": ..., "value": N}`.
        `ingest` forces the fetch half on or off; the default fetches whenever
        the operator named sources or the worker is reachable.
        """
        steps = []
        goal_type, goal_value = None, None
        if goal:
            goal_type = str(goal.get("type") or "").strip().lower()
            if goal_type in ("", "none"):
                goal_type = None
            elif goal_type not in self._GOAL_TYPES:
                return {"ok": False, "error": f"unknown goal type {goal_type!r}"}
            else:
                try:
                    goal_value = int(goal.get("value"))
                except (TypeError, ValueError):
                    return {"ok": False, "error": f"goal {goal_type!r} needs a whole number"}
                if goal_value < 1:
                    return {"ok": False, "error": f"goal {goal_type!r} must be at least 1"}

        names = [str(n).strip() for n in (source_names or []) if str(n).strip()]
        known = {s.name for s in load_sources(self._sources_path)}
        unknown = [n for n in names if n not in known]
        if unknown:
            return {"ok": False, "error": "no such source(s): " + ", ".join(sorted(unknown))}

        infra = self.infra()
        want_ingest = bool(names) if ingest is None else bool(ingest)
        if want_ingest and not infra.get("worker_go_available"):
            return {"ok": False,
                    "error": "the Go worker is not available, so the selected sources "
                             "cannot be fetched — install Go or set GO_BIN"}

        if want_ingest:
            step = self.ingest(
                live=bool(infra.get("can_ingest_live")),
                run_id=f"{run_id}-ingest",
                only=names,
                max_comments=goal_value if goal_type == "comments" else None,
                max_posts=goal_value if goal_type == "posts" else None,
            )
            steps.append({"step": "ingest", **step})
            if not step.get("ok"):
                # A failed fetch must not be followed by a pipeline pass that
                # reports "ok" over an empty queue.
                return {"ok": False, "steps": steps,
                        "error": "ingest failed: " + (step.get("error") or f"exit {step.get('code')}"),
                        "overview": self.overview()["counts"]}

        before = self._counts()
        args = type("Args", (), {})()
        args.db = self.db_path
        args.run = run_id
        args.max_ideas = goal_value if goal_type == "ideas" else None
        args.config = (
            str(self.repo_root / _LIVE_CONFIG_DIR)
            if live_models
            else self.config_dir
        )
        # Having just fetched, an empty queue is the answer, not a gap: seeding
        # the canned fixture here would report scraped counts for a run that
        # scraped nothing. A pipeline-only press keeps the demo fallback.
        args.seed_fixtures = not want_ingest
        old_origin = os.environ.get("JESTER_ORIGIN")
        os.environ["JESTER_ORIGIN"] = "manual"
        try:
            result = self._action_guard(cmd_run, args)
        finally:
            if old_origin is None:
                os.environ.pop("JESTER_ORIGIN", None)
            else:
                os.environ["JESTER_ORIGIN"] = old_origin

        after = self._counts()
        result["steps"] = steps
        result["overview"] = after
        result["archived"] = {
            "nuggets": after["nuggets"] - before["nuggets"],
            "ideas": after["ideas"] - before["ideas"],
        }
        # A run that archived nothing is the exact failure that made the
        # console feel broken, and it is NOT an error — say plainly what
        # happened so the next click is an informed one.
        if result.get("ok") and not any(result["archived"].values()):
            result["idle"] = True
            result["note"] = (
                "the run completed but archived nothing new — "
                + ("every fetched comment was already in the skip-list"
                   if want_ingest else
                   "nothing was queued to process; fetch some sources first")
            )
        return result

    def ingest(self, live=False, run_id="console-ingest", timeout=None,
               only=None, max_comments=None, max_posts=None):
        """Run the Go ingestion worker over the curated source list.

        The mechanics live in `jester.worker` so the scheduled job — which
        goes through the CLI, not the console — fetches exactly the same way.
        """
        return worker_ingest(
            self.config_dir, self.db_path, run_id,
            live=live, only=only, max_comments=max_comments,
            max_posts=max_posts, timeout=timeout, counts=self._counts,
        )

    # ---- schedule ---------------------------------------------------------

    #: Offered cadences. Free-form minutes are accepted too, but a picker with
    #: sane steps stops someone scheduling a live scrape every minute against a
    #: rate-limited API by mistyping one digit.
    SCHEDULE_PRESETS = (15, 30, 60, 120, 360, 720)

    def schedule(self):
        """What the host scheduler actually has registered — never a guess."""
        st = _schedule.status()
        # One log per scheduled command now — they used to share a file, and
        # cmd.exe grants no write sharing on a `>>` target, so a long run made
        # every other task's logging fail and report failure. Read whichever
        # exist, newest last, and keep the legacy single file so history
        # written before the split is not silently dropped from this view.
        candidates = [self.repo_root / "data" / "schedule.log"]
        candidates += sorted(self.repo_root.glob("data/schedule-*.log"))
        chunks = []
        for log in candidates:
            if not log.is_file():
                continue
            try:
                # The scheduled run's own account of itself. schtasks records
                # an exit code and nothing else, so without this the console
                # could say "installed" and nothing about whether it works.
                body = log.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                continue
            if body.strip():
                chunks.append(f"--- {log.name} ---\n{body}")
        tail = "\n".join(chunks)[-8000:]
        return {
            "ok": True,
            "installed": st.get("installed", False),
            "scheduler": st.get("scheduler", ""),
            "next_run": st.get("next_run", ""),
            "cadence": st.get("cadence", ""),
            "interval_minutes": st.get("interval_minutes"),
            # How many times it has actually fired, and what that should have
            # been. A schedule that is registered and silent looks identical to
            # a healthy one until you put these two numbers side by side.
            "activity": self._schedule_activity(st.get("interval_minutes")),
            "detail": st.get("detail", ""),
            "presets": list(self.SCHEDULE_PRESETS),
            # What the REGISTERED task will fetch, read back out of the launcher
            # the scheduler actually runs — not what someone last typed into
            # the form. The two diverge the moment a schedule is installed from
            # the CLI or edited by hand.
            "options": _schedule.installed_options() if st.get("installed") else {},
            "scope_limits": {k: list(v) for k, v in self.SCOPE_LIMITS.items()},
            "sources": [
                {"name": s.name, "platform": s.platform, "kind": s.kind,
                 "enabled": s.enabled, "supported": s.supported}
                for s in load_sources(self._sources_path)
            ],
            "windows": _schedule.is_windows(),
            "task_name": _schedule.TASK_NAME,
            "log_path": str(log),
            "log_tail": tail,
            # The command a schedule runs is the whole point of the fix that
            # created this page: `cycle` fetches AND processes.
            "command": "jester cycle (ingest + pipeline)",
        }

    #: Depth ceilings the schedule form offers, with the range each accepts.
    #: An unattended job wants these far more than a manual one does: nobody is
    #: watching it decide to walk sixteen sources at 3am.
    SCOPE_LIMITS = {
        "max_posts": (1, 500),
        "max_comments": (1, 100000),
        "max_ideas": (1, 1000),
    }

    def _scrape_scope(self, options):
        """Validate a scope dict from the console. Returns (scope, error)."""
        options = options or {}
        names = [str(n).strip() for n in (options.get("only") or []) if str(n).strip()]
        if names:
            known = {s.name for s in load_sources(self._sources_path)}
            unknown = [n for n in names if n not in known]
            if unknown:
                return None, "no such source(s): " + ", ".join(sorted(unknown))
        scope = {"only": names}
        for key, (lo, hi) in self.SCOPE_LIMITS.items():
            raw = options.get(key)
            if raw in (None, "", 0, "0"):
                continue  # absent means no ceiling, which is not the same as 0
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None, f"{key.replace('_', ' ')} must be a whole number"
            if not (lo <= value <= hi):
                return None, f"{key.replace('_', ' ')} must be between {lo} and {hi}"
            scope[key] = value
        return scope, None

    def _schedule_activity(self, interval_minutes):
        """How often the schedule actually ran, against how often it should have.

        The count alone cannot tell a healthy schedule from a broken one — this
        console spent months reporting "ok" for runs that archived nothing. The
        expected figure is what turns a number into a verdict.
        """
        act = run_activity(self.db, origin="scheduled")
        act["interval_minutes"] = interval_minutes

        # A window is only a fair yardstick for the time the schedule has
        # actually existed. Judging a job installed ten minutes ago against a
        # full 24h window reports it as having missed 47 of 48 ticks, which is
        # both false and the kind of red number people learn to ignore.
        first = self.db.execute(
            "SELECT MIN(started_at) FROM runs WHERE origin='scheduled'"
        ).fetchone()[0]
        act["first_run_at"] = first
        act["age_minutes"] = None
        if first:
            row = self.db.execute(
                "SELECT CAST((julianday('now') - julianday(?)) * 24 * 60 AS INTEGER)",
                (first,),
            ).fetchone()
            act["age_minutes"] = max(0, row[0] or 0)

        for hours, bucket in act["windows"].items():
            window_minutes = int(hours) * 60
            age = act["age_minutes"]
            if not interval_minutes or age is None:
                bucket["expected"] = None
                bucket["partial"] = False
                continue
            span = min(window_minutes, age)
            # +1 because the first run sits at the start of the span: a
            # 46-minute-old 30-minute schedule should have fired at t=0 and
            # t=30, which is two ticks, not one.
            bucket["expected"] = int(span // interval_minutes) + 1
            # True when the schedule is younger than the window, so the UI can
            # say "since the first run" rather than implying a full window.
            bucket["partial"] = age < window_minutes
        return act

    def schedule_install(self, every=None, at=None, options=None):
        """Register the recurring scrape.

        `every` is minutes, `at` is HH:MM, and `options` says WHAT to fetch and
        HOW DEEP — the half the scheduler had no way to express, so every
        unattended tick meant "every enabled source, no ceiling".
        """
        scope, err = self._scrape_scope(options)
        if err:
            return {"ok": False, "error": err}
        extra = _schedule.scrape_flags(scope)
        if every is not None:
            try:
                minutes = int(every)
            except (TypeError, ValueError):
                return {"ok": False, "error": f"interval {every!r} is not a whole number"}
            res = _schedule.install(db=self.db_path, config=self.config_dir,
                                    every=minutes, extra=extra)
        else:
            when = str(at or _schedule.DEFAULT_TIME).strip()
            if not re.match(r"^\d{1,2}:\d{2}$", when):
                return {"ok": False, "error": f"time {when!r} is not HH:MM"}
            res = _schedule.install(db=self.db_path, config=self.config_dir,
                                    at=when, extra=extra)
        return {"ok": res["ok"], "detail": res["detail"], "action": res["action"],
                "schedule": self.schedule()}

    def schedule_remove(self):
        res = _schedule.remove()
        return {"ok": res["ok"], "detail": res["detail"], "schedule": self.schedule()}

    def schedule_run_now(self):
        """Fire the registered task immediately — the only proof that the
        thing the host will run at 3am actually works."""
        res = _schedule.run_now()
        return {"ok": res["ok"], "detail": res["detail"]}

    def export(self, out_dir=None):
        """CSV dump of the archive, split by content type and origin."""
        try:
            target = out_dir or default_export_dir(self.db_path)
            manifest = export_csv(self.db, target)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "out_dir": str(target), **manifest}

    def smoke(self):
        findings = evaluate_smoke(open_db(self.db_path))
        return {"ok": not findings, "findings": findings}

    def mark_idea(self, idea_id, status):
        return self._action_guard(cmd_mark, _ns(db=self.db_path, id=int(idea_id), status=status))

    def requeue(self):
        n = requeue_failed(self.db)
        return {"ok": True, "requeued": n}

    def reembed(self):
        cfg = self._cfg()
        vector = _vector_for(cfg, self.db_path)
        fixed = failed = 0
        last_err = None
        for r in pending_reembeds(self.db):
            key = r["unique_key"]
            try:
                vector.upsert(key, r["raw_text"] or "", {})
                mark_reembedded(self.db, key, f"emb:{abs(hash(key)) % (2 ** 63)}")
                fixed += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                last_err = str(exc)
        return {"ok": failed == 0, "fixed": fixed, "failed": failed,
                "last_error": last_err}

    def resynth(self, idea_id):
        return self._action_guard(
            cmd_resynth, _ns(db=self.db_path, config=self.config_dir, id=int(idea_id))
        )

    def migrate(self):
        return self._action_guard(cmd_migrate, _ns(db=self.db_path))

    def retention(self):
        return self._action_guard(cmd_retention, _ns(db=self.db_path))

    def seed_mock(self):
        """Queue the deterministic fixture so a pipeline run has input."""
        from jester.fetchers import FixtureFetcher
        from jester.store import enqueue_new_only

        queued = 0
        for b in FixtureFetcher().fetch():
            queued += enqueue_new_only(
                self.db, b["platform"], b["source"], b["thread_id"], b["comments"],
                run_id="console-seed",
            )
        return {"ok": True, "queued_new_comments": queued}


def _model_installed(name: str, installed) -> bool:
    """`nomic-embed-text` and `nomic-embed-text:latest` are the same pull."""
    if not installed:
        return False
    wanted = name if ":" in name else f"{name}:latest"
    return any(have in (name, wanted) for have in installed)


def _same_dir(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:  # a path that cannot be resolved is not the repo's own
        return False


def _ns(**kw):
    class NS:
        pass

    ns = NS()
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns
