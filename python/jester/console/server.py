"""§37.29 Operator console HTTP server.

Serves the static operator UI at / and a JSON API under /api/*.
Run: python -m jester.console --db data/jester.db [--config config] [--port 8619]
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jester.console.api import ConsoleAPI

STATIC = Path(__file__).resolve().parent / "static"


def _api(args):
    return ConsoleAPI(db_path=args.db, config_dir=args.config)


class Handler(BaseHTTPRequestHandler):
    args: argparse.Namespace = None  # injected by serve()

    def _json(self, payload, code=200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - http.server API
        path = self.path.split("?")[0]
        api = _api(self.args)
        if path in ("/", "/index.html"):
            body = (STATIC / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        routes = {
            "/api/overview": api.overview,
            "/api/runs": api.runs,
            "/api/ideas": api.ideas,
            "/api/nuggets": api.nuggets,
            "/api/doctor": api.doctor,
            "/api/eval": api.eval_gate,
            "/api/config": api.config,
            "/api/infra": api.infra,
        }
        if path in routes:
            self._json(routes[path]())
            return
        if path.startswith("/api/idea/"):
            self._json(api.idea(int(path.rsplit("/", 1)[1])))
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.read_body(length) or "{}")
        api = _api(self.args)
        route = {
            "/api/run": lambda: api.run_pipeline(
                live_models=bool(body.get("live_models")),
                live_fetch_env=bool(body.get("live_fetch_env")),
                run_id=body.get("run_id") or "console-run",
            ),
            "/api/smoke": api.smoke,
            "/api/requeue": api.requeue,
            "/api/reembed": api.reembed,
            "/api/migrate": api.migrate,
            "/api/retention": api.retention,
            "/api/seed-mock": api.seed_mock,
        }.get(self.path)
        if route:
            self._json(route())
            return
        if self.path == "/api/mark":
            self._json(api.mark_idea(int(body["id"]), str(body["status"])))
            return
        if self.path == "/api/resynth":
            self._json(api.resynth(int(body["id"])))
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def read_body(self, length):
        return self.rfile.read(length).decode("utf-8") if length else None

    def log_message(self, *a):  # quiet
        pass


def serve(args):
    Handler.args = args
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"jester console: http://{args.host}:{args.port}/  (db={args.db})")
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
