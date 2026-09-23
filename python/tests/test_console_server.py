"""The console's HTTP server — the layer nothing was testing.

`server.py` was at 0% coverage: 195 lines, and the console is the only surface
the operator has. The API underneath it is well covered, because the existing
tests call `ConsoleAPI` directly and never go through HTTP. Everything between
a socket and that object — routing, static file serving, the path-traversal
guard, the error contract — had never been executed by a test.

Two of those are not "lines to cover", they are promises the code makes in its
own comments and that nobody checked:

* `_static_path` refuses to serve anything outside static/. It is the only
  thing standing between a URL and the filesystem.
* `_guard` promises that a crashing route answers with JSON rather than
  dropping the socket, because "a dead fetch says nothing" to the operator.

These tests drive a real ThreadingHTTPServer over a real socket, because a
test that calls the handler's methods directly would not have caught either.
"""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jester.console import server as srv


# ---- the path-traversal guard ---------------------------------------------

def test_a_normal_asset_resolves():
    assert srv._static_path("/app.js") is not None
    assert srv._static_path("/app.js").name == "app.js"


def test_the_root_becomes_index_html():
    assert srv._static_path("/").name == "index.html"
    assert srv._static_path("").name == "index.html"


def test_climbing_out_of_static_is_refused():
    """The guard's entire job. Each of these resolves outside static/, and
    every one of them must come back None rather than a file."""
    for attempt in ("/../server.py",
                    "/../../jester/cli.py",
                    "/../../../../../../etc/passwd",
                    "/subdir/../../server.py"):
        assert srv._static_path(attempt) is None, f"{attempt} escaped static/"


def test_a_file_type_we_do_not_serve_is_refused():
    """Resolving inside static/ is not sufficient. The suffix allow-list is
    what stops the console handing out whatever happens to be in there."""
    assert srv._static_path("/../console/server.py") is None


def test_a_missing_file_is_not_a_crash():
    assert srv._static_path("/no-such-asset.js") is None


# ---- a real server on a real socket ----------------------------------------

@pytest.fixture()
def console(tmp_path, offline_config):
    """The actual server, on an ephemeral port, torn down afterwards."""
    import argparse

    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0)
    previous = srv.Handler.args
    srv.Handler.args = args
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        srv.Handler.args = previous


def _get(base, path, method="GET", body=None):
    req = urllib.request.Request(base + path, method=method)
    if body is not None:
        req.data = body
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_the_console_page_is_served(console):
    status, headers, body = _get(console, "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"<title>" in body.lower() or b"<!doctype" in body.lower()


def test_an_asset_is_served_with_its_own_type(console):
    status, headers, body = _get(console, "/app.js")
    assert status == 200
    assert "javascript" in headers["Content-Type"]
    assert body


def test_a_missing_page_is_json_not_an_html_error(console):
    """The client parses every response as JSON. An HTML 404 from the base
    handler would surface as a parse error and say nothing useful."""
    status, _, body = _get(console, "/does-not-exist.js")
    assert status == 404
    assert json.loads(body) == {"ok": False, "error": "not found"}


def test_an_api_route_answers_and_refuses_to_be_cached(console):
    """The console reads live pipeline state; a cached /api/* is a stale
    console, which is worse than an extra request."""
    status, headers, body = _get(console, "/api/config")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body)["ok"] is True


def test_an_unknown_api_route_is_a_json_404(console):
    status, _, body = _get(console, "/api/not-a-route")
    assert status == 404
    assert json.loads(body)["ok"] is False


def test_head_returns_the_headers_without_the_body(console):
    status, headers, body = _get(console, "/", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0, "the length must still describe the page"
    assert body == b"", "a HEAD response carries no body"


def test_a_malformed_post_body_is_reported_as_such(console):
    status, _, body = _get(console, "/api/run", method="POST", body=b"{not json")
    assert status == 400
    assert "bad JSON body" in json.loads(body)["error"]


def test_a_post_body_that_is_not_an_object_is_refused(console):
    """`_read_json` promises a dict. A bare list would otherwise reach the
    route and fail later with an AttributeError about `.get`."""
    status, _, body = _get(console, "/api/run", method="POST", body=b"[1, 2, 3]")
    assert status == 400
    assert "bad JSON body" in json.loads(body)["error"]


def test_a_crashing_route_answers_with_json_instead_of_dropping_the_socket(
        console, monkeypatch):
    """The promise in `_guard`'s docstring. Without it the browser sees a
    failed fetch with no status and no body, and the operator sees a console
    that did nothing for no stated reason."""
    from jester.console import api as api_mod

    def explode(self, *a, **kw):
        raise RuntimeError("the archive fell over")

    monkeypatch.setattr(api_mod.ConsoleAPI, "overview", explode, raising=True)
    status, _, body = _get(console, "/api/overview")
    assert status == 500
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "RuntimeError" in payload["error"] and "fell over" in payload["error"]


def test_a_traversal_attempt_over_real_http_gets_nothing(console):
    """Belt and braces: urllib normalises some of these before they leave, so
    this asserts the outcome rather than the mechanism — no 200, ever."""
    for attempt in ("/../server.py", "/static/../../cli.py", "/..%2f..%2fserver.py"):
        status, _, _ = _get(console, attempt)
        assert status != 200, f"{attempt} was served"


# ---- wiring ----------------------------------------------------------------

def test_serve_binds_what_it_was_told_and_says_where(capsys, tmp_path, monkeypatch):
    """serve() is three lines and one of them is the only thing that tells an
    operator which address to open."""
    import argparse

    bound = {}

    class _FakeServer:
        def __init__(self, address, handler):
            bound["address"] = address
            bound["handler"] = handler

        def serve_forever(self):
            bound["served"] = True

    monkeypatch.setattr(srv, "ThreadingHTTPServer", _FakeServer)
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config="cfg",
                              host="0.0.0.0", port=9999)
    srv.serve(args)

    assert bound["address"] == ("0.0.0.0", 9999)
    assert bound["handler"] is srv.Handler
    assert bound["served"] is True
    assert srv.Handler.args is args, "the handler reads its config off the class"
    assert "9999" in capsys.readouterr().out


def test_main_defaults_and_flags_reach_serve(monkeypatch):
    """--signals-db exists because the only way to point at another archive
    used to be an environment variable that never appeared in --help."""
    seen = {}
    monkeypatch.setattr(srv, "serve", lambda args: seen.update(vars(args)))
    monkeypatch.setattr(srv, "load_env_once", lambda: None)

    srv.main([])
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8619
    assert seen["signals_db"] is None

    seen.clear()
    srv.main(["--port", "1234", "--host", "0.0.0.0", "--signals-db", "/tmp/other.db"])
    assert seen["port"] == 1234 and seen["host"] == "0.0.0.0"
    assert seen["signals_db"] == "/tmp/other.db"
