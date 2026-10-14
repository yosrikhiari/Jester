"""Problem-signal + jobs-scraper console surface (F-04 split).

Pure move out of `jester/console/api.py` as `ApiSignalsMixin`, which
`ConsoleAPI` inherits. Routes, payloads, and the `QUEUE_WHERE` view are
unchanged; `CLI` moved with its only users and is re-exported by `api.py`.
"""
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from jester import schedule as _schedule

#: How a command is shown to a person: the CLI name, then its arguments.
CLI = "jester "


class ApiSignalsMixin:
    """Collector archive views and the hiring-scraper controls."""

    # So static analysis (and readers) see the attributes the mixin uses;
    # real values come from ConsoleAPI.__init__.
    db = None
    db_path = None
    config_dir = None
    repo_root = None
    read_only = False
    _signals_db_path = None

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

    #: The work queue: buyers nobody has marked, from the sources outreach may
    #: use. Reddit rooms are left out -- their records are set apart until
    #: there is an agreement with Reddit (docs/scope-and-access.md, section 6).
    QUEUE_WHERE = ("audience = 'buyer' AND COALESCE(outcome, '') = '' "
                   "AND community NOT LIKE 'r/%'")

    def signals(self, *, audience="", community="", kind="", q="", mode="",
                outcome="", queue="", offset=0, limit=50):
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
                       "kind": kind, "mode": mode, "outcome": outcome}

            def cond(col, val):
                # "untouched" is the work queue: no outcome recorded yet. It
                # is the empty value, which a plain `= ?` filter cannot name.
                if col == "outcome" and val == "untouched":
                    return "COALESCE(outcome, '') = ''", []
                return f"{col} = ?", [val]

            # The queue is a fixed view, not a facet: it narrows everything,
            # facets included, the same way.
            fixed = [self.QUEUE_WHERE] if queue else []
            where, args = list(fixed), []
            for col, val in filters.items():
                if val:
                    c, a = cond(col, val)
                    where.append(c)
                    args += a
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
                    # The one column a person writes, and the only evidence in
                    # the archive that is not the rules agreeing with
                    # themselves. A console that cannot show it can only
                    # report how confident the classifier is about itself.
                    "outcome, outcome_at, outcome_note, "
                    "run_status, error, created_utc, first_seen_utc, last_seen_utc, "
                    "edited_utc, removed_utc, revisions, source_url")
            rows = [dict(r) for r in db.execute(
                f"SELECT {cols} FROM {TABLE} {clause} "
                "ORDER BY match_confidence DESC, first_seen_utc DESC "
                "LIMIT ? OFFSET ?", (*args, limit, offset)).fetchall()]

            facets = {}
            for col in ("audience", "community", "kind", "mode", "outcome"):
                # Each facet is counted with its OWN filter dropped, so the
                # options a reader can switch to still show a number.
                sub, subargs = list(fixed), []
                for c, v in filters.items():
                    if v and c != col:
                        cc, ca = cond(c, v)
                        sub.append(cc)
                        subargs += ca
                if q:
                    sub.append("(title LIKE ? OR excerpt LIKE ? OR match_reason LIKE ?)")
                    subargs += [f"%{q}%"] * 3
                sub_clause = ("WHERE " + " AND ".join(sub)) if sub else ""
                # An empty outcome is a value worth clicking ("untouched");
                # an empty anything-else is not.
                expr = "COALESCE(NULLIF(outcome, ''), 'untouched')" if col == "outcome" else col
                facets[col] = [
                    {"value": r[0], "n": r[1]} for r in db.execute(
                        f"SELECT {expr} AS v, COUNT(*) FROM {TABLE} {sub_clause} "
                        "GROUP BY v ORDER BY COUNT(*) DESC", subargs).fetchall()
                    if r[0]]

            from jester.signals import counts as signal_counts
            return {"ok": True, "exists": True, "path": self._signals_path,
                    "total": total, "rows": rows, "facets": facets,
                    "counts": signal_counts(db), "fields": FIELD_NAMES,
                    "offset": offset, "limit": limit}
        finally:
            db.close()

    def signal_outcome(self, record_id, outcome, note=""):
        """Record what came of one lead, from the console.

        The same `set_outcome` the CLI calls, so the rules about outcomes (one
        record at a time, a fixed list, re-recording overwrites) live in one
        place. Returns the row as it now reads so the page can redraw it
        without reloading the list, plus the new worked/picked totals.
        """
        from jester.signals import OutcomeError, outcomes, set_outcome
        if self.read_only:
            return {"ok": False, "error": "this console is read-only"}
        if not Path(self._signals_path).exists():
            return {"ok": False, "error": f"no signal archive at {self._signals_path}"}
        db = self._signals_db()
        try:
            try:
                res = set_outcome(db, str(record_id or ""), str(outcome or ""),
                                  note=str(note or "").strip())
            except OutcomeError as exc:
                return {"ok": False, "error": str(exc)}
            row = db.execute(
                "SELECT record_id, outcome, outcome_at, outcome_note FROM problem_signal "
                "WHERE record_id = ?", (res["record_id"],)).fetchone()
            return {"ok": True, "was": res["was"], "row": dict(row),
                    "totals": outcomes(db, mode="live")}
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

    # ── the jobs scraper ──────────────────────────────────────────────────
    #
    # "Who is hiring?" threads on Hacker News, scored by the hiring rules. It
    # is the `signals-hiring` schedulable command, so the button, the schedule
    # and the CLI all run the same argv (schedule.COMMAND_SPEC) -- a console
    # button that ran a slightly different command than the 09:30 task would
    # make the two impossible to compare in the run ledger.

    JOBS_SCRAPER_COMMAND = "signals-hiring"
    #: How often it may repeat, besides daily-at-a-time. The adverts land in one
    #: thread a month; anything tighter than six hours re-reads the same page
    #: and dedupes it away, at the cost of someone else's API.
    JOBS_SCRAPER_EVERY = (360, 720)

    def _jobs_scraper_task(self) -> str:
        return _schedule.TASK_FOR_COMMAND[self.JOBS_SCRAPER_COMMAND]

    def jobs_scraper(self):
        """Is the scraper scheduled, is it running, and what did it last say."""
        from jester.jobs import recent_jobs

        st = _schedule.status(self._jobs_scraper_task())
        # The console's own run, if one is in flight: a reload mid-run must
        # pick the progress back up rather than offer the button again.
        active = next((j for j in recent_jobs(self.db, limit=20)
                       if j["kind"] == self.JOBS_SCRAPER_COMMAND and j["status"] == "running"),
                      None)
        log = _schedule.log_path(self.JOBS_SCRAPER_COMMAND)
        try:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        except OSError:
            tail = []
        return {
            "ok": True,
            "task": self._jobs_scraper_task(),
            "windows": _schedule.is_windows(),
            "installed": st.get("installed", False),
            "enabled": st.get("installed", False) and st.get("enabled", True),
            "running": st.get("running", False),
            "cadence": st.get("cadence", ""),
            "interval_minutes": st.get("interval_minutes"),
            "next_run": st.get("next_run", ""),
            "last_run": st.get("last_run", ""),
            "last_result": st.get("last_result", ""),
            "job": active,
            "every_presets": list(self.JOBS_SCRAPER_EVERY),
            "command": CLI + " ".join(_schedule.command_argv(self.JOBS_SCRAPER_COMMAND)),
            "log_tail": tail,
            # Does the registered task still run what the button runs? The
            # button builds its command fresh; the task runs a file written
            # at install time, and on 2026-09-28 the two had drifted apart.
            "launcher": self._launcher_state(st),
        }

    def _launcher_state(self, st):
        drift = _schedule.launcher_drift(self.JOBS_SCRAPER_COMMAND)
        if not st.get("installed") or not drift:
            return {"stale": False}
        return {"stale": drift["stale"], "runs": CLI + drift["argv"],
                "should": CLI + drift["want"]}

    def schedule_refresh(self):
        """Rewrite every stale launcher from the current code, keeping its
        settings. The registrations are untouched."""
        if self.read_only:
            return {"ok": False, "error": "this console is read-only"}
        done = _schedule.refresh_launchers()
        return {"ok": True, "refreshed": done,
                "detail": (f"{len(done)} launcher(s) rewritten" if done
                           else "every launcher already runs what this code would write")}

    def jobs_scraper_run(self):
        """Run the scraper now, in the background, exactly as the task would."""
        from jester.jobs import start_job

        if _schedule.status(self._jobs_scraper_task()).get("running"):
            # Two collectors writing one SQLite file at once is the shape that
            # corrupted the main archive in August.
            return {"ok": False, "error": "the scheduled run is in progress",
                    "detail": "wait for it to finish; its rows land in the same ledger"}

        # --archive last, so it overrides the spec's repo-relative default: the
        # Reddit rooms are scored from the archive THIS console is reading,
        # which is not data/jester.db in the Docker stack.
        argv = [sys.executable, "-u", "-m", "jester.cli",
                *_schedule.command_argv(self.JOBS_SCRAPER_COMMAND), "--db", self._signals_path,
                "--archive", self.db_path]
        env = {**os.environ,
               "PYTHONPATH": os.pathsep.join(filter(None, [
                   str(self.repo_root / "python"), os.environ.get("PYTHONPATH")])),
               "JESTER_ORIGIN": "manual"}
        log = _schedule.log_path(self.JOBS_SCRAPER_COMMAND)
        root = self.repo_root

        def append_log(text):
            # Same file the scheduled task writes, so one log tells the whole
            # story. Best effort: a scheduled run holding it open must not
            # fail a run that otherwise worked.
            try:
                # data/ is gitignored, so a fresh clone has no folder for it.
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as fh:
                    fh.write(text)
            except OSError:
                pass

        def work(progress):
            progress("reading Hacker News “Who is hiring?” threads")
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            append_log(f"[{stamp}] jester {self.JOBS_SCRAPER_COMMAND} starting (console)\n")
            # Relative paths in the argv (config/hiring_rules.yaml) are
            # repo-relative, as they are for the launcher, which cds there.
            p = subprocess.run(argv, cwd=root, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=1800)
            out = (p.stdout or "") + (p.stderr or "")
            append_log(out + ("" if out.endswith("\n") else "\n")
                       + f"[{datetime.now():%Y-%m-%d %H:%M:%S}] jester "
                         f"{self.JOBS_SCRAPER_COMMAND} exited {p.returncode} (console)\n")
            lines = [ln for ln in out.splitlines() if ln.strip()]
            if p.returncode != 0:
                raise RuntimeError(lines[-1] if lines else f"exited {p.returncode}")
            progress(lines[1] if len(lines) > 1 else "finished")
            # Two runs when the fallback fires, six lines or so each.
            return {"exit": p.returncode, "output": lines[-16:]}

        return start_job(self.db_path, self.JOBS_SCRAPER_COMMAND, work)

    def jobs_scraper_schedule(self, enabled=True, every=None, at=None):
        """Turn the scraper's schedule on (at a cadence) or off.

        Off disables the task rather than deleting it, so the cadence someone
        chose survives a pause -- the way the tasks have been paused before.
        """
        task = self._jobs_scraper_task()
        if not _schedule.is_windows():
            return {"ok": False, "error": "scheduling from the console needs "
                    "Windows Task Scheduler on this host",
                    "detail": "use `jester schedule install --command signals-hiring`"}
        # The console posts JSON false for "off"; anything else means on.
        if enabled is False:
            if not _schedule.status(task).get("installed"):
                return {"ok": True, "detail": "not scheduled", "scraper": self.jobs_scraper()}
            return self._scraper_result(_schedule.set_enabled(task, False))

        kw, error = self._scraper_cadence(every, at)
        if error:
            return {"ok": False, "error": error}
        # Re-registering rewrites the launcher and recreates the task enabled,
        # which is also what turns a paused task back on.
        return self._scraper_result(_schedule.install(
            db=self._signals_path, config=self.config_dir,
            task_name=task, command=self.JOBS_SCRAPER_COMMAND, **kw))

    def _scraper_result(self, res: dict) -> dict:
        return {"ok": res["ok"], "error": None if res["ok"] else res["detail"],
                "detail": res["detail"], "scraper": self.jobs_scraper()}

    def _scraper_cadence(self, every, at):
        """`every` (minutes, one of the presets) or `at` (HH:MM) as install()
        keyword arguments, or the reason they are refused."""
        if every not in (None, ""):
            try:
                minutes = int(every)
            except (TypeError, ValueError):
                return None, f"interval {every!r} is not a whole number"
            if minutes not in self.JOBS_SCRAPER_EVERY:
                return None, "interval must be one of " + ", ".join(
                    f"{m // 60}h" for m in self.JOBS_SCRAPER_EVERY)
            return {"every": minutes}, None
        when = str(at or "09:30").strip()
        m = re.match(r"^(\d{1,2}):(\d{2})$", when)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None, f"time {when!r} is not HH:MM"
        return {"at": f"{int(m.group(1)):02d}:{m.group(2)}"}, None
