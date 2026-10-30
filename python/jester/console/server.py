"""§37.29 Operator console HTTP server.

Serves the static operator UI at / and a JSON API under /api/*.
Run: python -m jester.console --db data/jester.db [--config config] [--port 8619]
"""
import argparse
import hmac
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote

from jester.console.api import ConsoleAPI
from jester.env import load_env_once
from jester.ops import SCORE_STALL_ATTEMPTS

STATIC = Path(__file__).resolve().parent / "static"

# Anything under static/ is fair game, but only these extensions — the console
# binds to localhost, and a narrow allow-list keeps it that way if it ever
# does not.
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    # Self-hosted UI font (static/fonts/). The console runs on machines with no
    # internet, so the typeface ships with it rather than loading from a CDN.
    ".woff2": "font/woff2",
}


#: What a thread id may contain. See the /api/post/ route for why this is
#: a whitelist rather than an escape: the id reaches a response body, so
#: the safe move is to refuse anything unexpected, not to sanitise it.
_THREAD_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,120}")


#: Environment variable carrying the operator token. When set (non-empty),
#: every mutating POST under /api/* must present it as
#: `Authorization: Bearer <token>`. GETs stay open so a dashboard can poll
#: without holding a secret; writes are what need the guard. Compared with
#: hmac.compare_digest so the check itself does not leak the token bytewise.
TOKEN_ENV = "JESTER_CONSOLE_TOKEN"


def _console_token() -> str:
    """The required bearer token, or "" when auth is disabled (local use)."""
    return os.environ.get(TOKEN_ENV, "")


def _is_authorized(handler: BaseHTTPRequestHandler) -> bool:
    """True when no token is configured, or the request presents it."""
    want = _console_token()
    if not want:
        return True
    got = (handler.headers.get("Authorization") or "").strip()
    scheme, _, credential = got.partition(" ")
    if scheme.lower() != "bearer" or not credential:
        return False
    return hmac.compare_digest(credential.strip(), want)


def _api(args):
    return ConsoleAPI(db_path=args.db, config_dir=args.config,
                      signals_db=getattr(args, "signals_db", None),
                      read_only=bool(getattr(args, "read_only", False)))


# ---- metrics ---------------------------------------------------------------
#
# Counters live in the process, not in SQLite: /metrics must answer while the
# archive is locked by the pipeline, which is exactly when the numbers matter.
# A restart starts them at zero, like every process-level Prometheus exporter.

_METRICS_LOCK = threading.Lock()
_REQUESTS = {}   # (route, status) -> count
_DURATIONS = {}  # route -> (seconds_total, count)

#: Prefixes whose tail is an identifier. Labelling by the raw path would make
#: one series per thread id -- unbounded cardinality for no information.
_DYNAMIC_ROUTES = ("/api/post/", "/api/job/", "/api/cluster/", "/api/idea/",
                   "/api/queue/batch/")


def _metric_route(path: str) -> str:
    route = path.partition("?")[0] or "/"
    for prefix in _DYNAMIC_ROUTES:
        if route.startswith(prefix):
            route = prefix + ":id"
            break
    else:
        if not route.startswith("/api/") and route != "/metrics":
            route = "/static"
    # Label values are quoted: a path is request-controlled text.
    return route.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "").replace("\r", "")


def _observe_request(path: str, status: int, seconds: float) -> None:
    route = _metric_route(path)
    with _METRICS_LOCK:
        key = (route, int(status or 0))
        _REQUESTS[key] = _REQUESTS.get(key, 0) + 1
        total, count = _DURATIONS.get(route, (0.0, 0))
        _DURATIONS[route] = (total + max(0.0, seconds), count + 1)


def _metrics_text(api) -> str:
    """Prometheus text for the console: process counters plus archive gauges.

    A stale archive still answers /metrics. DB gauges are omitted there --
    emitting zero would say "an empty archive" when the truth is "unreadable
    schema" -- and `jester_schema_ok 0` is the series that says so.
    """
    problem = api.schema_problem
    with _METRICS_LOCK:
        requests = sorted(_REQUESTS.items())
        durations = sorted(_DURATIONS.items())
    lines = [
        "# HELP jester_console_requests_total HTTP requests handled, by route and status.",
        "# TYPE jester_console_requests_total counter",
    ]
    for (route, status), count in requests:
        lines.append(f'jester_console_requests_total{{route="{route}",status="{status}"}} {count}')
    lines += [
        "# HELP jester_console_request_duration_seconds Wall time per request.",
        "# TYPE jester_console_request_duration_seconds summary",
    ]
    for route, (total, count) in durations:
        lines.append(f'jester_console_request_duration_seconds_sum{{route="{route}"}} {total:.6f}')
        lines.append(f'jester_console_request_duration_seconds_count{{route="{route}"}} {count}')
    lines += [
        "# HELP jester_schema_ok 1 when the archive has the schema this build reads.",
        "# TYPE jester_schema_ok gauge",
        f"jester_schema_ok {0 if problem else 1}",
    ]
    if problem is None:
        counts = api.overview()["counts"]
        gauges = (
            ("jester_nuggets_total", "nuggets"),
            ("jester_ideas_total", "ideas"),
            ("jester_pending_batches", "pending_batches"),
            ("jester_pending_listings", "pending_listings"),
            ("jester_failed_batches", "failed_batches"),
            ("jester_reembed_backlog", "reembed_backlog"),
            ("jester_unprocessed_nuggets", "unprocessed"),
            ("jester_runs_active", "runs_active"),
        )
        for name, key in gauges:
            value = counts.get(key)
            if value is not None:
                lines.append(f"{name} {value}")
        try:
            backlog = api.db.execute(
                "SELECT COUNT(*) FROM ideas WHERE needs_score = 1").fetchone()[0]
            lines.append(f"jester_rescore_backlog {backlog}")
            stalled = api.db.execute(
                "SELECT COUNT(*) FROM ideas WHERE needs_score = 1 "
                "AND rescore_attempts >= ?", (SCORE_STALL_ATTEMPTS,)
            ).fetchone()[0]
            lines.append(f"jester_rescore_stalled {stalled}")
        except Exception:  # noqa: BLE001 - a count must never take /metrics down
            pass
    return "\n".join(lines) + "\n"


def _static_path(url_path: str):
    """Resolve a URL path inside static/, or None if it escapes or is missing."""
    rel = url_path.lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    candidate = (STATIC / rel).resolve()
    try:
        candidate.relative_to(STATIC.resolve())
    except ValueError:
        return None  # path traversal attempt
    if not candidate.is_file() or candidate.suffix.lower() not in _STATIC_TYPES:
        return None
    return candidate


class Handler(BaseHTTPRequestHandler):
    args: argparse.Namespace = None  # injected by serve()
    server_version = "jester-console"

    def _send(self, body: bytes, content_type: str, code=200, extra=()):
        self._status = code
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in extra:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload, code=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        # The console reads live pipeline state; a cached /api/overview is a
        # stale console, which is worse than an extra request.
        self._send(body, "application/json", code, extra=[("Cache-Control", "no-store")])

    def _static(self, url_path: str) -> bool:
        path = _static_path(url_path)
        if path is None:
            return False
        ctype = _STATIC_TYPES.get(path.suffix.lower()) or (
            mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self._send(path.read_bytes(), ctype)
        return True

    # ---- routing ----------------------------------------------------------

    def do_GET(self):  # noqa: N802 - http.server API
        self._guard(self._route_get)

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        self._guard(self._route_post)

    def _guard(self, route):
        """A route crash must answer with JSON, not drop the socket: the console
        is the only surface the operator has, and a dead fetch says nothing."""
        self._t0 = time.time()
        try:
            route()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            try:
                self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)
            except Exception:  # noqa: BLE001
                self.close_connection = True
        finally:
            _observe_request(self.path, getattr(self, "_status", 0),
                             time.time() - self._t0)

    def _refuse_schema(self, problem):
        missing = ", ".join(problem["missing"][:6])
        extra = len(problem["missing"]) - 6
        if extra > 0:
            missing += f" (+{extra} more)"
        self._json({
            "ok": False,
            "error": (f"this archive is missing schema this build needs: {missing}; "
                      f"run: python -m jester.cli migrate --db {self.args.db}"),
            "schema": problem,
        }, 503)

    def _route_get(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            if not self._static("index.html"):
                self._json({"ok": False, "error": "console UI missing from static/"}, 500)
            return
        if path == "/metrics":
            # Deliberately ahead of the schema preflight: an operator whose
            # archive is stale needs the exporter most, and `jester_schema_ok`
            # is how the alert says why the other gauges are absent.
            api = _api(self.args)
            self._send(_metrics_text(api).encode("utf-8"),
                       "text/plain; version=0.0.4; charset=utf-8")
            return
        if not path.startswith("/api/"):
            if not self._static(path):
                self._json({"ok": False, "error": "not found"}, 404)
            return

        api = _api(self.args)
        problem = api.schema_problem
        if problem is not None:
            self._refuse_schema(problem)
            return
        routes = {
            "/api/overview": api.overview,
            "/api/runs": api.runs,
            "/api/doctor": api.doctor,
            "/api/eval": api.eval_gate,
            "/api/config": api.config,
            "/api/sources": api.sources,
            "/api/schedule": api.schedule,
            "/api/clusters": api.clusters,
            "/api/jobs": api.jobs,
            "/api/running": api.running,
            "/api/signals/scraper": api.jobs_scraper,
        }
        if path in routes:
            self._json(routes[path]())
            return
        if path == "/api/chat/sessions":
            self._json(api.chat_sessions())
            return
        if path == "/api/chat/session":
            # One transcript, by session id. Malformed ids answer 400 the
            # same way /api/post does: never echoed back.
            sid = parse_qs(query).get("id", [""])[0]
            res = api.chat_session(sid)
            self._json(res, 200 if res.get("ok") else 404)
            return
        if path == "/api/trends":
            self._json(api.trends(parse_qs(query).get("days", [7])[0]))
            return
        if path == "/api/infra":
            self._json(api.infra(fresh=parse_qs(query).get("fresh", ["0"])[0] == "1"))
            return
        if path == "/api/ideas":
            qs = parse_qs(query)
            g = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
            self._json(api.ideas(status=g("status"), q=g("q"), sort=g("sort", "overall"),
                                 dir=g("dir", "desc"), offset=g("offset", 0), limit=g("limit", 0)))
            return
        if path == "/api/signals":
            qs = parse_qs(query)
            g = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
            self._json(api.signals(audience=g("audience"), community=g("community"),
                                   kind=g("kind"), q=g("q"), mode=g("mode"),
                                   outcome=g("outcome"), queue=g("queue"),
                                   offset=g("offset", 0), limit=g("limit", 50)))
            return
        if path == "/api/signals/runs":
            self._json(api.signal_runs(limit=parse_qs(query).get("limit", [25])[0]))
            return
        if path == "/api/posts":
            qs = parse_qs(query)
            g = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
            self._json(api.posts_page(offset=g("offset", 0), limit=g("limit", 50),
                                      platform=g("platform"), community=g("community"),
                                      q=g("q")))
            return
        if path.startswith("/api/post/"):
            thread = unquote(path[len("/api/post/"):])
            # Validated at the boundary, and NEVER echoed back. Whatever
            # arrives here came from the URL, and the 404 used to quote it --
            # a taint flow straight from the request line into the response
            # body. Content-Type: application/json makes that hard to exploit
            # rather than impossible, and "hard to exploit" is not the bar.
            #
            # The pattern is measured, not guessed: all 4,000 distinct
            # thread_ids sampled from the live archive match it, and the
            # longest is 11 characters. A request outside it cannot be for a
            # post that exists, so it is refused without being repeated.
            if not _THREAD_ID_RE.fullmatch(thread):
                self._json({"ok": False, "error": "bad thread id"}, 400)
                return
            res = api.post(thread)
            self._json(res, 200 if res.get("ok") else 404)
            return
        if path == "/api/nuggets/page":
            qs = parse_qs(query)
            g = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
            self._json(api.nuggets_page(offset=g("offset", 0), limit=g("limit", 100),
                                        platform=g("platform"), community=g("community"),
                                        category=g("category"), flag=g("flag"), q=g("q")))
            return
        if path == "/api/nuggets":
            # Query-aware, unlike the plain table above, which calls its
            # handler with no arguments — so `?limit=` was accepted by the URL
            # and ignored by the code. Absent means every nugget.
            self._json(api.nuggets(parse_qs(query).get("limit", [None])[0]))
            return
        if path == "/api/queue":
            qs = parse_qs(query)
            self._json(api.queue(
                status=qs.get("status", ["pending"])[0],
                limit=qs.get("limit", [None])[0],
                kind=qs.get("kind", [""])[0],
            ))
            return
        if path.startswith("/api/queue/batch/"):
            tail = path.rsplit("/", 1)[1]
            if not tail.isdigit():
                self._json({"ok": False, "error": f"bad batch id {tail!r}"}, 400)
                return
            self._json(api.queue_batch(int(tail)))
            return
        if path == "/api/search":
            qs = parse_qs(query)
            self._json(api.search(
                q=qs.get("q", [""])[0],
                limit=qs.get("limit", [30])[0],
                platform=qs.get("platform", [""])[0],
                category=qs.get("category", [""])[0],
            ))
            return
        if path == "/api/models":
            profile = parse_qs(query).get("profile", [None])[0]
            self._json(api.models(profile))
            return
        if path.startswith("/api/job/"):
            tail = path.rsplit("/", 1)[1]
            if not tail.isdigit():
                self._json({"ok": False, "error": f"bad job id {tail!r}"}, 400)
                return
            self._json(api.job(int(tail)))
            return
        if path.startswith("/api/cluster/"):
            tail = path.rsplit("/", 1)[1]
            if not tail.isdigit():
                self._json({"ok": False, "error": f"bad cluster id {tail!r}"}, 400)
                return
            self._json(api.cluster(int(tail)))
            return
        if path.startswith("/api/idea/"):
            tail = path.rsplit("/", 1)[1]
            if not tail.isdigit():
                self._json({"ok": False, "error": f"bad idea id {tail!r}"}, 400)
                return
            self._json(api.idea(int(tail)))
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _route_post(self):
        path = self.path.split("?")[0]
        # Server-level guards run before any body is parsed: a rejected write
        # must not depend on the body being well-formed, and some ConsoleAPI
        # methods predate the --read-only flag, so the layer that owns the
        # socket enforces it for every POST rather than trusting each route.
        if bool(getattr(self.args, "read_only", False)):
            self._json({"ok": False, "error": "this console is read-only"}, 403)
            return
        if not _is_authorized(self):
            self._json({"ok": False, "error": "missing or invalid console token"}, 401)
            return

        api = _api(self.args)
        # `/api/migrate` is the recovery route: it must stay reachable from a
        # stale archive, and it is the only write that does not need the
        # schema it is about to install.
        if path != "/api/migrate":
            problem = api.schema_problem
            if problem is not None:
                self._refuse_schema(problem)
                return
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json({"ok": False, "error": f"bad JSON body: {exc}"}, 400)
            return

        simple = {
            "/api/run": lambda: api.run_pipeline(
                live_models=bool(body.get("live_models")),
                run_id=body.get("run_id") or "console-run",
                # The pipeline modal posts these. They were accepted and
                # dropped, so ticking a source or setting a goal changed
                # nothing about the run it started.
                source_names=body.get("source_names"),
                goal=body.get("goal"),
                ingest=body.get("ingest"),
            ),
            "/api/ingest": lambda: api.ingest(
                live=bool(body.get("live")),
                run_id=body.get("run_id") or "console-ingest",
                only=body.get("source_names"),
                max_comments=body.get("max_comments"),
                max_posts=body.get("max_posts"),
            ),
            "/api/export": lambda: api.export(body.get("out_dir") or None),
            # Regroup the archive by meaning. Same call the CLI makes.
            # Async by default: the synchronous pass holds one request for
            # ~577s on this archive and the browser gives up first.
            "/api/cluster/run": lambda: api.cluster_run_async(
                threshold=body.get("threshold"),
                min_size=body.get("min_size"),
                min_nuggets=body.get("min_nuggets"),
                limit=body.get("limit"),
                run_id=body.get("run_id") or "console-cluster",
            ),
            "/api/cluster/run-sync": lambda: api.cluster_run(
                threshold=body.get("threshold"),
                min_size=body.get("min_size"),
                min_nuggets=body.get("min_nuggets"),
                limit=body.get("limit"),
                run_id=body.get("run_id") or "console-cluster",
            ),
            # Draft an idea from one theme. Lands in cluster_ideas, NOT ideas.
            # The raw body value goes to the API: `or 0` here would turn a
            # request that named no theme into a request for the detached
            # group, and the operator would be told about a group they never
            # mentioned.
            "/api/cluster/idea": lambda: api.cluster_generate_idea(
                body.get("cluster_id")
            ),
            # The same draft as a background job, so the page can show its
            # stages instead of a dead button for half a minute.
            "/api/cluster/idea/start": lambda: api.cluster_generate_idea_async(
                body.get("cluster_id")),
            # The explicit act that puts a draft into the archive.
            "/api/cluster/idea/save": lambda: api.cluster_save_idea(
                int(body.get("draft_id") or 0)
            ),
            "/api/cluster/idea/discard": lambda: api.cluster_discard_idea(
                int(body.get("draft_id") or 0)
            ),
            "/api/smoke": api.smoke,
            "/api/requeue": api.requeue,
            "/api/reembed": api.reembed,
            "/api/migrate": api.migrate,
            "/api/retention": api.retention,
            "/api/seed-mock": api.seed_mock,
            "/api/schedule/install": lambda: api.schedule_install(
                every=body.get("every"), at=body.get("at"),
                # What to fetch and how deep. Accepted-and-dropped here would
                # be the pipeline-modal bug all over again.
                options=body.get("options")),
            "/api/schedule/remove": api.schedule_remove,
            "/api/schedule/run": api.schedule_run_now,
            "/api/schedule/refresh": api.schedule_refresh,
            # What came of a lead: picked, contacted, replied ... one record.
            "/api/signals/outcome": lambda: api.signal_outcome(
                body.get("record_id"), body.get("outcome"), body.get("note", "")),
            # The jobs scraper: run it now, or set/pause its own schedule.
            "/api/signals/scraper/run": api.jobs_scraper_run,
            "/api/signals/scraper/schedule": lambda: api.jobs_scraper_schedule(
                enabled=body.get("enabled", True),
                every=body.get("every"), at=body.get("at")),
            "/api/sources/add": lambda: api.add_source(
                url=body.get("url", ""),
                platform=body.get("platform", "auto"),
                name=body.get("name") or None,
                enabled=body.get("enabled", True),
                notes=body.get("notes", ""),
            ),
            "/api/sources/update": lambda: api.update_source(
                str(body.get("name", "")),
                **{k: body[k] for k in ("url", "platform", "enabled", "notes", "new_name")
                   if k in body},
            ),
            "/api/sources/delete": lambda: api.delete_source(str(body.get("name", ""))),
            "/api/thresholds": lambda: api.update_thresholds(
                body.get("patch") or {}, body.get("profile")),
            "/api/models": lambda: api.update_models(body.get("patch") or {}, body.get("profile")),
            "/api/models/roles": lambda: api.update_roles(
                body.get("patch") or {}, body.get("profile")),
            "/api/models/test": lambda: api.test_role(
                str(body.get("role", "")), body.get("profile")),
            # The chat: a turn runs as a background job (one at a time, like
            # everything else); apply/reject/revert are the HUMAN decisions
            # on what the model proposed. The gate (chat_enabled) lives in
            # chat_start; these three stay usable after the flag goes off.
            "/api/chat": lambda: api.chat_start(
                body.get("text", ""), body.get("session_id")),
            "/api/chat/apply": lambda: api.chat_apply(
                str(body.get("proposal_id", ""))),
            "/api/chat/reject": lambda: api.chat_reject(
                str(body.get("proposal_id", "")),
                reason=str(body.get("reason", ""))),
            "/api/chat/revert": lambda: api.chat_revert(
                str(body.get("edit_id", "")),
                reason=str(body.get("reason", ""))),
        }
        if path in simple:
            self._json(simple[path]())
            return
        if path == "/api/mark":
            self._json(api.mark_idea(int(body["id"]), str(body["status"])))
            return
        if path == "/api/resynth":
            self._json(api.resynth(int(body["id"])))
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed

    def log_message(self, *a):
        """One stderr line per request (F-11): method, path, status, size,
        wall time. Docker captures stderr, so this is the console's access
        log; stdout stays for the startup line and deliberate prints."""
        try:
            dt_ms = (time.time() - getattr(self, "_t0", time.time())) * 1000.0
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            sys.stderr.write(f"{stamp} console {self.command} {self.path} "
                             f"{a[0] % a[1:] if len(a) > 1 else (a[0] if a else '')} "
                             f"{dt_ms:.0f}ms\n")
        except Exception:  # noqa: BLE001 - logging must never break serving
            pass


def serve(args):
    Handler.args = args
    if (str(getattr(args, "host", "")) == "0.0.0.0"
            and not _console_token()
            and not bool(getattr(args, "read_only", False))):
        # Binding every interface with open writes is how a local-operator
        # console becomes somebody else's scheduler. This stays a warning
        # rather than a refusal so existing Docker setups keep booting; set
        # JESTER_CONSOLE_TOKEN (or --read-only for viewing) to silence it.
        print("jester console WARNING: listening on 0.0.0.0 with no "
              f"{TOKEN_ENV} set — any host that can reach this port can run "
              "pipeline mutations via POST /api/*.")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"jester console: http://{args.host}:{args.port}/  (db={args.db} config={args.config})")
    server.serve_forever()


def main(argv=None):
    load_env_once()  # GROQ_API_KEY et al. before the first request lands
    p = argparse.ArgumentParser(prog="jester-console")
    p.add_argument("--db", default=os.environ.get("JESTER_DB", "data/jester.db"))
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[3] / "config"))
    # The problem-signal archive is a separate database, and until now the
    # only way to point at a different one was an environment variable that
    # does not appear in --help. A flag is discoverable; the variable still
    # works as a fallback so nothing that set it breaks.
    p.add_argument("--signals-db", default=None,
                   help="problem-signal archive to browse "
                        "(default: signals-live.db beside --db)")
    # A second console pointed at somebody else's live archive. Without this
    # it tries to migrate the database on every request and dies with "attempt
    # to write a readonly database", and with write access it becomes another
    # process competing for a lock this archive already loses runs to.
    p.add_argument("--read-only", action="store_true",
                   help="open the archive for viewing: never migrate it, "
                        "never write to it")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8619)
    args = p.parse_args(argv)
    serve(args)


if __name__ == "__main__":
    main()
