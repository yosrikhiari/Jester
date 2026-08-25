"""§37.29 Operator console HTTP server.

Serves the static operator UI at / and a JSON API under /api/*.
Run: python -m jester.console --db data/jester.db [--config config] [--port 8619]
"""
import argparse
import json
import mimetypes
import os
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from jester.console.api import ConsoleAPI

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
}


def _api(args):
    return ConsoleAPI(db_path=args.db, config_dir=args.config)


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

    def _route_get(self):
        path, _, query = self.path.partition("?")
        if path in ("/", "/index.html"):
            if not self._static("index.html"):
                self._json({"ok": False, "error": "console UI missing from static/"}, 500)
            return
        if not path.startswith("/api/"):
            if not self._static(path):
                self._json({"ok": False, "error": "not found"}, 404)
            return

        api = _api(self.args)
        routes = {
            "/api/overview": api.overview,
            "/api/runs": api.runs,
            "/api/ideas": api.ideas,
            "/api/nuggets": api.nuggets,
            "/api/doctor": api.doctor,
            "/api/eval": api.eval_gate,
            "/api/config": api.config,
            "/api/infra": api.infra,
            "/api/sources": api.sources,
        }
        if path in routes:
            self._json(routes[path]())
            return
        if path == "/api/models":
            profile = parse_qs(query).get("profile", [None])[0]
            self._json(api.models(profile))
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
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json({"ok": False, "error": f"bad JSON body: {exc}"}, 400)
            return

        api = _api(self.args)
        simple = {
            "/api/run": lambda: api.run_pipeline(
                live_models=bool(body.get("live_models")),
                run_id=body.get("run_id") or "console-run",
            ),
            "/api/ingest": lambda: api.ingest(
                live=bool(body.get("live")),
                run_id=body.get("run_id") or "console-ingest",
            ),
            "/api/export": lambda: api.export(body.get("out_dir") or None),
            "/api/smoke": api.smoke,
            "/api/requeue": api.requeue,
            "/api/reembed": api.reembed,
            "/api/migrate": api.migrate,
            "/api/retention": api.retention,
            "/api/seed-mock": api.seed_mock,
            "/api/sources/add": lambda: api.add_source(
                url=body.get("url", ""),
                platform=body.get("platform", "auto"),
                name=body.get("name") or None,
                enabled=body.get("enabled", True) is not False,
                notes=body.get("notes", ""),
            ),
            "/api/sources/update": lambda: api.update_source(
                str(body.get("name", "")),
                **{k: body[k] for k in ("url", "platform", "enabled", "notes", "new_name")
                   if k in body},
            ),
            "/api/sources/delete": lambda: api.delete_source(str(body.get("name", ""))),
            "/api/thresholds": lambda: api.update_thresholds(body.get("patch") or {}),
            "/api/models": lambda: api.update_models(body.get("patch") or {}, body.get("profile")),
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

    def log_message(self, *a):  # quiet
        pass


def serve(args):
    Handler.args = args
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"jester console: http://{args.host}:{args.port}/  (db={args.db} config={args.config})")
    server.serve_forever()


def main(argv=None):
    p = argparse.ArgumentParser(prog="jester-console")
    p.add_argument("--db", default=os.environ.get("JESTER_DB", "data/jester.db"))
    p.add_argument("--config", default=str(Path(__file__).resolve().parents[3] / "config"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8619)
    args = p.parse_args(argv)
    serve(args)


if __name__ == "__main__":
    main()
