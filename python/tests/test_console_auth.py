"""F-01/R4-03: console write-guard — token auth + read-only POST rejection.

Every mutating POST under /api/* must present JESTER_CONSOLE_TOKEN, and a
console with no token configured refuses every write (read-only until a
token exists). When --read-only is on, every POST is refused regardless.
GETs stay open so dashboards can poll without holding the secret.
"""
import argparse
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jester.console import server as srv


@pytest.fixture()
def guarded_console(tmp_path, offline_config, monkeypatch):
    """Real server with a token configured. Closes over monkeypatch so the
    token cannot leak into neighbouring tests via os.environ."""
    monkeypatch.setenv(srv.TOKEN_ENV, "test-token-123")
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0, read_only=False)
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


@pytest.fixture()
def open_console(tmp_path, offline_config, monkeypatch):
    """Real server with NO token configured: writes are refused, GETs open."""
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0, read_only=False)
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


@pytest.fixture()
def readonly_console(tmp_path, offline_config, monkeypatch):
    """Real server in --read-only mode with a token also set: read-only wins."""
    monkeypatch.setenv(srv.TOKEN_ENV, "test-token-123")
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
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


@pytest.fixture()
def readauth_no_token_console(tmp_path, offline_config, monkeypatch):
    """Read auth on, no token: nothing can satisfy it, so every read is 401."""
    monkeypatch.delenv(srv.TOKEN_ENV, raising=False)
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0, read_only=False,
                              read_auth=True)
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


def _post(base, path, body=b"{}", token=None):
    """POST with retries on connection setup only (Windows loopback can
    refuse a connection while a fresh server thread is still starting).
    HTTP responses — including 4xx — are never retried: those are the
    assertions, not the transport."""
    import time as _time
    req = urllib.request.Request(base + path, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("Authorization", f"Bearer {token}")
    last = None
    for _ in range(5):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())
        except OSError as exc:  # connection refused/reset mid-startup
            last = exc
            _time.sleep(0.2)
    raise last


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return resp.status, json.loads(resp.read())


def test_post_without_token_is_401(guarded_console):
    status, payload = _post(guarded_console, "/api/run")
    assert status == 401
    assert payload["ok"] is False
    assert "token" in payload["error"]


def test_post_with_wrong_token_is_401(guarded_console):
    status, payload = _post(guarded_console, "/api/run", token="not-the-token")
    assert status == 401
    assert payload["ok"] is False


def test_post_with_correct_token_reaches_the_route(guarded_console):
    # Malformed body proves auth passed: the 400 comes from _read_json,
    # which runs after the guard.
    status, payload = _post(guarded_console, "/api/run", body=b"{not json",
                            token="test-token-123")
    assert status == 400
    assert "bad JSON body" in payload["error"]


def test_read_only_rejects_posts_even_with_token(readonly_console):
    status, payload = _post(readonly_console, "/api/run", token="test-token-123")
    assert status == 403
    assert payload == {"ok": False, "error": "this console is read-only"}


def test_gets_stay_open_when_token_is_set(guarded_console):
    status, payload = _get(guarded_console, "/api/config")
    assert status == 200
    assert payload["ok"] is True


def test_no_token_configured_refuses_writes(open_console):
    """R4-03: a console with no token cannot authenticate a write, so it must
    not perform one. The default posture is read-only until a token exists,
    and the refusal has to name both ways out."""
    status, payload = _post(open_console, "/api/thresholds",
                            body=json.dumps({"patch": {}}).encode())
    assert status == 401
    assert payload["ok"] is False
    assert srv.TOKEN_ENV in payload["error"]
    assert "--read-only" in payload["error"]


def test_no_token_still_serves_reads(open_console):
    """GETs stay open (unchanged): a dashboard polls without holding a
    secret, and the write guard is the thing that closes the mutation path."""
    status, payload = _get(open_console, "/api/config")
    assert status == 200
    assert payload["ok"] is True


def test_read_auth_without_a_token_fails_closed(readauth_no_token_console):
    """`--read-auth` asks for protected reads. With no token configured no
    request can satisfy the check, so the archive is refused (401) rather
    than quietly served unauthenticated."""
    import urllib.error

    for path in ("/api/config", "/metrics"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(readauth_no_token_console, path)
        assert exc.value.code == 401, path
        assert json.loads(exc.value.read())["ok"] is False
