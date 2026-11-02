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
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jester.console import server as srv

#: R4-03: writes always need a token, so every fixture that POSTs configures
#: one. Tests that assert on the refusal live in test_console_auth.py.
TOKEN = "server-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
def console(tmp_path, offline_config, monkeypatch):
    """The actual server, on an ephemeral port, torn down afterwards."""
    import argparse

    monkeypatch.setenv(srv.TOKEN_ENV, TOKEN)
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


def _get(base, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(base + path, method=method)
    if body is not None:
        req.data = body
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
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


# ---- S-10: headers, read auth, cross-origin POST ----------------------------

def test_every_response_carries_the_security_headers(console):
    for path in ("/", "/api/config", "/no-such-route"):
        _status, headers, _body = _get(console, path)
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


@pytest.fixture()
def read_auth_console(tmp_path, offline_config, monkeypatch):
    """A token-protected console that also guards reads."""
    import argparse

    monkeypatch.setenv(srv.TOKEN_ENV, "s3cret")
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0, read_auth=True)
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


def test_read_auth_refuses_a_get_without_the_token(read_auth_console):
    for path in ("/api/config", "/metrics"):
        status, headers, _body = _get(read_auth_console, path)
        assert status == 401, path
        assert headers["X-Content-Type-Options"] == "nosniff"


def test_read_auth_accepts_the_token_and_still_serves_the_page(read_auth_console):
    status, _headers, body = _get(
        read_auth_console, "/api/config",
        headers={"Authorization": "Bearer s3cret"})
    assert status == 200
    assert json.loads(body)["ok"] is not False

    status, _headers, _body = _get(read_auth_console, "/")
    assert status == 200, "the page must load so it can ask for a token"


@pytest.fixture()
def read_only_fresh_console(tmp_path, offline_config):
    """The default container posture: --read-only against a fresh volume, so
    no schema exists and /api/* would answer 503."""
    import argparse

    args = argparse.Namespace(db=str(tmp_path / "fresh.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0, read_only=True)
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


def test_the_healthcheck_endpoint_answers_a_read_only_fresh_console(
        read_only_fresh_console):
    """compose probes /metrics, which answers before the schema preflight;
    /api/doctor would 503 on an archive that a read-only console never
    creates."""
    status, _headers, body = _get(read_only_fresh_console, "/metrics")
    assert status == 200
    assert b"jester_schema_ok 0" in body


def test_a_cross_origin_post_is_refused(console):
    status, _headers, body = _get(
        console, "/api/migrate", method="POST", body=b"{}",
        headers={"Origin": "http://evil.test", **AUTH})
    assert status == 403
    assert "cross-origin" in json.loads(body)["error"]


def test_a_refused_post_with_a_body_still_delivers_its_403(console):
    """The refusal is decided before the body is parsed. If the handler then
    closes the connection with unread bytes in the socket, Windows resets it
    and the client loses the 403 that was sent — measured at roughly one in
    thirty with a two-byte body, and routinely with a larger one.

    The wait before reading is the point: it lets the close land first, so the
    reset wins the race deterministically instead of depending on timing."""
    body = b" " * (768 * 1024)
    host, port = console.removeprefix("http://").rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(
            b"POST /api/migrate HTTP/1.0\r\n"
            b"Host: " + host.encode() + b"\r\n"
            b"Origin: http://evil.test\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
            + body)
        time.sleep(0.1)
        raw = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    assert raw.startswith(b"HTTP/1.0 403"), raw[:80]
    assert b"cross-origin" in raw


def test_a_same_origin_post_is_not_refused_for_its_origin(console):
    status, _headers, _body = _get(
        console, "/api/migrate", method="POST", body=b"{}",
        headers={"Origin": console, **AUTH})
    assert status == 200


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


# ---- R4-03: /api/config reports the Qdrant URL by presence, not by value ----

def test_config_never_serialises_the_qdrant_url(console, monkeypatch):
    """A Qdrant URL carries credentials (http://user:pass@host:6333), and the
    Config page only needs to know whether it is set."""
    monkeypatch.setenv("JESTER_QDRANT_URL", "http://user:pass@host:6333")
    status, _headers, body = _get(console, "/api/config")
    assert status == 200
    text = body.decode("utf-8")
    assert "user:pass" not in text
    assert "host:6333" not in text
    env = json.loads(text)["env"]
    assert env["JESTER_QDRANT_URL_set"] is True
    assert "JESTER_QDRANT_URL" not in env


def test_infra_never_serialises_the_qdrant_url(console, monkeypatch):
    """/api/infra is an open GET too: it reports presence, not the URL.

    The fake client raises with the URL in the message, so this also proves
    an error string cannot smuggle the credential out through `detail`."""
    url = "http://user:pass@host:6333"
    monkeypatch.setenv("JESTER_QDRANT_URL", url)

    import qdrant_client

    class Unreachable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError(f"cannot reach {url}")

    monkeypatch.setattr(qdrant_client, "QdrantClient", Unreachable)
    status, _headers, body = _get(console, "/api/infra")
    assert status == 200
    text = body.decode("utf-8")
    assert "user:pass" not in text
    assert "host:6333" not in text
    vectors = json.loads(text)["vectors"]
    assert vectors["url_set"] is True
    assert "url" not in vectors


def test_head_returns_the_headers_without_the_body(console):
    status, headers, body = _get(console, "/", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0, "the length must still describe the page"
    assert body == b"", "a HEAD response carries no body"


def test_a_malformed_post_body_is_reported_as_such(console):
    status, _, body = _get(console, "/api/run", method="POST", body=b"{not json",
                           headers=AUTH)
    assert status == 400
    assert "bad JSON body" in json.loads(body)["error"]


def test_a_post_body_that_is_not_an_object_is_refused(console):
    """`_read_json` promises a dict. A bare list would otherwise reach the
    route and fail later with an AttributeError about `.get`."""
    status, _, body = _get(console, "/api/run", method="POST", body=b"[1, 2, 3]",
                           headers=AUTH)
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


# ---- V3-12: bounded request, Host allow-list --------------------------------

def test_an_oversized_body_is_refused_with_413_before_parsing(console):
    body = b"x" * (srv.MAX_BODY_BYTES + 1)
    status, _headers, payload = _get(console, "/api/run", method="POST", body=body,
                                     headers=AUTH)
    assert status == 413
    assert "too large" in json.loads(payload)["error"].lower()


def test_a_foreign_host_header_is_refused_with_403(console):
    status, _headers, body = _get(
        console, "/api/config", headers={"Host": "evil.example"})
    assert status == 403
    assert "host" in json.loads(body)["error"].lower()


def test_localhost_is_still_allowed_for_the_docker_healthcheck(console):
    status, _headers, body = _get(console, "/api/doctor", headers={"Host": "localhost"})
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_a_jobs_poll_writes_nothing_to_the_archive(console, tmp_path, offline_config,
                                                  monkeypatch):
    """Fix round 1, Important 2: /api/jobs is polled every few seconds, and it
    used to call reap_stale_jobs — which ran ensure_schema (DDL) and committed
    an UPDATE on every tick. The route the task exists to protect still wrote.
    A GET must not CREATE, INSERT, UPDATE or DELETE anything."""
    from jester.console.api import ConsoleAPI
    from jester.store import open_db

    db_path = tmp_path / "poll.db"
    open_db(str(db_path)).close()  # base schema; `jobs` has never existed
    statements = []

    def tracked_api(args):
        # Built in the request thread, as `_api` is in production: a sqlite
        # connection may only be used in the thread that opened it.
        api = ConsoleAPI(db_path=str(db_path), config_dir=str(offline_config))
        api.db.set_trace_callback(statements.append)
        return api

    monkeypatch.setattr(srv, "_api", tracked_api)

    status, _headers, body = _get(console, "/api/jobs")

    assert status == 200
    assert json.loads(body)["jobs"] == []
    writes = [s for s in statements
              if s.strip().upper().startswith(("CREATE", "INSERT", "UPDATE", "DELETE"))]
    assert not writes, writes
    db = open_db(str(db_path), migrate=False)
    try:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone() is None, "the poll created the jobs table"
    finally:
        db.close()


def test_an_idle_client_is_dropped_before_it_sends_a_request_line(console, monkeypatch):
    """Fix round 1, Important 3: the socket timeout used to live in `_guard`,
    which BaseHTTPRequestHandler runs only after the request line and headers
    are read. A client that connected and sent nothing held one
    ThreadingHTTPServer thread until the process ended. The timeout has to be
    applied at accept, not at dispatch."""
    monkeypatch.setattr(srv, "REQUEST_TIMEOUT", 0.2)
    host, port = console.removeprefix("http://").rsplit(":", 1)
    started = time.time()
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.settimeout(5)
        assert sock.recv(1024) == b"", "the idle connection was never closed"
    assert time.time() - started < 4


def test_an_oversized_body_also_says_the_connection_will_close(console):
    """Fix round 1, minor: the 413 path set close_connection but never said so
    on the wire, so an HTTP/1.1 client could still believe it was reusable."""
    body = b"x" * (srv.MAX_BODY_BYTES + 1)
    status, headers, _payload = _get(console, "/api/run", method="POST", body=body,
                                     headers=AUTH)
    assert status == 413
    assert (headers.get("Connection") or "").lower() == "close", dict(headers)


def test_a_chunked_post_is_refused_with_411(console):
    """Fix round 1, minor: a Transfer-Encoding body has no Content-Length, and
    the old code returned {} without reading it — the POST ran with defaults
    and the unread chunk stream poisoned the next keep-alive request."""
    host, port = console.removeprefix("http://").rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(
            b"POST /api/migrate HTTP/1.1\r\n"
            b"Host: " + host.encode() + b"\r\n"
            b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Content-Type: application/json\r\n\r\n"
            b"2\r\n{}\r\n0\r\n\r\n")
        sock.settimeout(5)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    assert b" 411 " in raw.split(b"\r\n", 1)[0], raw[:160]
    assert b"connection: close" in raw.lower(), raw[:400]


def test_a_slow_body_hits_the_request_timeout(console, monkeypatch):
    monkeypatch.setattr(srv, "REQUEST_TIMEOUT", 0.2)
    host, port = console.removeprefix("http://").rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=10) as sock:
        sock.sendall(
            b"POST /api/migrate HTTP/1.1\r\n"
            b"Host: " + host.encode() + b"\r\n"
            b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 100\r\n\r\n"
            b"{"
        )
        sock.settimeout(5)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    assert raw.startswith(b"HTTP/1."), raw[:80]
    assert b" 408 " in raw.split(b"\r\n", 1)[0], raw[:120]


# ---- wiring ----------------------------------------------------------------

class _StubServer:
    """Accept the bind arguments and return, so serve() can be inspected."""

    def __init__(self, address, handler):
        self.address = address

    def serve_forever(self):
        pass


def _serve_and_capture(monkeypatch, capsys, allowed_hosts):
    import argparse

    monkeypatch.setenv(srv.ALLOWED_HOSTS_ENV, allowed_hosts)
    monkeypatch.setattr(srv, "ThreadingHTTPServer", _StubServer)
    srv.serve(argparse.Namespace(db="j.db", config="cfg",
                                 host="127.0.0.1", port=8619))
    out, err = capsys.readouterr()
    return out + err


def test_an_allowed_hosts_list_without_loopback_warns_about_the_healthcheck(
        monkeypatch, capsys):
    """Fix round 1, Important 4: JESTER_ALLOWED_HOSTS replaces the defaults. An
    operator who adds only a LAN name silently turns the Docker healthcheck
    (http://localhost:8619/metrics) into a 403, so the container goes
    unhealthy with nothing in the logs saying why."""
    text = _serve_and_capture(monkeypatch, capsys, "jester.lan,192.168.1.9")
    assert "WARNING" in text
    assert "healthcheck" in text.lower()
    assert srv.ALLOWED_HOSTS_ENV in text


def test_an_allowed_hosts_list_with_localhost_does_not_warn(monkeypatch, capsys):
    text = _serve_and_capture(monkeypatch, capsys, "jester.lan,localhost")
    assert "WARNING" not in text
    assert "healthcheck" not in text.lower()


def test_serve_binds_what_it_was_told_and_says_where(capsys, tmp_path, monkeypatch):
    """serve() is three lines and one of them is the only thing that tells an
    operator which address to open. A non-loopback bind needs a token now
    (R4-03), so the test configures one."""
    import argparse

    monkeypatch.setenv(srv.TOKEN_ENV, TOKEN)
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


# ---- R4-03: a non-loopback bind needs a token -------------------------------

def _args(host, **kw):
    import argparse

    return argparse.Namespace(db="j.db", config="cfg", host=host, port=8619, **kw)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_binds_need_no_token(monkeypatch, host):
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)
    assert srv._bind_refusal(_args(host)) is None


def test_a_network_bind_with_no_token_is_refused(monkeypatch):
    """Binding every interface used to only print a warning, so the console
    became an unauthenticated control plane for anyone on the LAN."""
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)
    reason = srv._bind_refusal(_args("0.0.0.0"))
    assert reason and srv.TOKEN_ENV in reason and "--read-only" in reason
    assert "network-reachable" in reason
    assert "writes are enabled" not in reason


def test_a_network_bind_with_a_token_is_allowed(monkeypatch):
    monkeypatch.setenv(srv.TOKEN_ENV, "s3cret")
    assert srv._bind_refusal(_args("0.0.0.0")) is None


def test_a_network_bind_read_only_is_allowed(monkeypatch):
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)
    assert srv._bind_refusal(_args("0.0.0.0", read_only=True)) is None


def test_serve_exits_nonzero_instead_of_binding_a_network_address(
        monkeypatch, capsys):
    """The refusal must happen before the socket exists: a warning followed by
    a live listener is the defect."""
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)

    def must_not_bind(address, handler):
        raise AssertionError(f"serve bound {address} despite the refusal")

    monkeypatch.setattr(srv, "ThreadingHTTPServer", must_not_bind)
    with pytest.raises(SystemExit) as exc:
        srv.serve(_args("0.0.0.0"))
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert srv.TOKEN_ENV in err and "--read-only" in err


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


def test_every_request_leaves_one_stderr_line(console, capsys):
    """F-11: the access log. Docker captures stderr, so a silent console is
    an undebuggable one; one line per request names method, path, status."""
    _get(console, "/api/config")
    err = capsys.readouterr().err
    assert "GET" in err and "/api/config" in err and "200" in err and "ms" in err


def test_metrics_serves_prometheus_text_and_counts_requests(console):
    _get(console, "/api/config")
    status, headers, body = _get(console, "/metrics")
    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    text = body.decode("utf-8")
    assert "# TYPE jester_console_requests_total counter" in text
    assert 'jester_console_requests_total{route="/api/config",status="200"}' in text
    assert "# TYPE jester_console_request_duration_seconds" in text


def test_metrics_queue_depth_matches_the_overview(console):
    overview = json.loads(_get(console, "/api/overview")[2])["counts"]
    text = _get(console, "/metrics")[2].decode("utf-8")
    for metric, key in (
        ("jester_pending_batches", "pending_batches"),
        ("jester_runs_active", "runs_active"),
        ("jester_nuggets_total", "nuggets"),
        ("jester_ideas_total", "ideas"),
    ):
        assert f"{metric} {overview[key]}" in text


def test_metrics_reports_stalled_rescores_not_just_the_backlog(console, tmp_path):
    """`jester_rescore_backlog` says how many wait; it cannot say whether any
    of them has stopped moving. The stalled gauge is the one an alert watches."""
    from jester.ops import SCORE_STALL_ATTEMPTS
    from jester.store import open_db

    db = open_db(str(tmp_path / "j.db"))
    db.execute("INSERT INTO ideas(title, needs_score, rescore_attempts) "
               "VALUES ('stuck', 1, ?)", (SCORE_STALL_ATTEMPTS,))
    db.execute("INSERT INTO ideas(title, needs_score, rescore_attempts) "
               "VALUES ('moving', 1, 1)")
    db.commit()
    db.close()

    text = _get(console, "/metrics")[2].decode("utf-8")
    assert "jester_rescore_backlog 2" in text
    assert "jester_rescore_stalled 1" in text


def test_metrics_counts_quota_stopped_runs(console, tmp_path):
    """A truncated run is a terminal state; the exporter must be able to
    count it, or no alert can see a pipeline that keeps being cut off."""
    from jester.store import finish_run, open_db, start_run

    db = open_db(str(tmp_path / "j.db"))
    start_run(db, "q1")
    finish_run(db, "q1", status="quota_stopped")
    db.close()

    text = _get(console, "/metrics")[2].decode("utf-8")
    assert "jester_runs_quota_stopped_total 1" in text


@pytest.fixture()
def stale_console(tmp_path, offline_config, monkeypatch):
    """A real server pointed at an archive that predates the schema."""
    import argparse

    monkeypatch.setenv(srv.TOKEN_ENV, TOKEN)
    db = tmp_path / "stale.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, run_id TEXT);")
    conn.close()
    args = argparse.Namespace(db=str(db), config=str(offline_config),
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


def test_a_stale_archive_gets_one_actionable_503(stale_console):
    status, _, body = _get(stale_console, "/api/config")
    assert status == 503
    payload = json.loads(body)
    assert payload["ok"] is False
    assert "migrate" in payload["error"]
    assert "python -m jester.cli migrate --db" in payload["error"]
    assert payload["schema"]["missing"]


def test_a_stale_archive_still_serves_the_shell_and_metrics(stale_console):
    assert _get(stale_console, "/")[0] == 200
    status, _, body = _get(stale_console, "/metrics")
    assert status == 200
    assert b"jester_schema_ok 0" in body


def test_the_migrate_route_is_reachable_through_the_preflight(stale_console):
    status, _, body = _get(stale_console, "/api/migrate", method="POST", body=b"{}",
                           headers=AUTH)
    assert status == 200 and json.loads(body)["ok"] is True
    assert _get(stale_console, "/api/config")[0] == 200
