"""R4-03: /api/export must not write outside the exports root.

`out_dir` used to travel straight from the request body to `export_csv`, so
`../..` wrote anywhere the console process could. The root is
`JESTER_EXPORTS_DIR` when set, else `default_export_dir(db_path)`; both the
root and the request are resolved before comparing, which also catches a
symlink pointing out of the tree.
"""
import argparse
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from jester.console import server as srv
from jester.export import ExportPathError, exports_root, resolve_export_dir

TOKEN = "export-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# ---- the containment helper -------------------------------------------------

def test_the_root_is_the_env_override_when_set(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    assert exports_root(str(tmp_path / "j.db")) == root.resolve()


def test_the_root_defaults_to_beside_the_database(tmp_path, monkeypatch):
    monkeypatch.delenv("JESTER_EXPORTS_DIR", raising=False)
    db = tmp_path / "sub" / "j.db"
    assert exports_root(str(db)) == (tmp_path / "sub" / "exports").resolve()


def test_no_out_dir_means_the_root(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    assert resolve_export_dir(None, str(tmp_path / "j.db")) == root.resolve()


def test_a_subdirectory_of_the_root_is_allowed(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    got = resolve_export_dir(str(root / "nightly"), str(tmp_path / "j.db"))
    assert got == (root / "nightly").resolve()


def test_a_relative_out_dir_resolves_against_the_exports_root(tmp_path, monkeypatch):
    """`nightly` means `<root>/nightly`, not `<cwd>/nightly`: the console's cwd
    is the server's, and an export request must not depend on where it ran."""
    root = tmp_path / "exports"
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    monkeypatch.chdir(tmp_path.parent)
    assert resolve_export_dir("nightly", str(tmp_path / "j.db")) == \
        (root / "nightly").resolve()


def test_a_relative_parent_escape_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(tmp_path / "exports"))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ExportPathError):
        resolve_export_dir(os.path.join("..", ".."), str(tmp_path / "j.db"))


def test_an_absolute_path_outside_the_root_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(tmp_path / "exports"))
    with pytest.raises(ExportPathError):
        resolve_export_dir(str(tmp_path / "elsewhere"), str(tmp_path / "j.db"))


def test_a_symlink_pointing_outside_the_root_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "exports"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"symlinks unavailable here: {exc}")
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    with pytest.raises(ExportPathError):
        resolve_export_dir(str(link), str(tmp_path / "j.db"))


def test_the_error_is_a_value_error():
    """So a caller that only knows the request was wrong still catches it."""
    assert issubclass(ExportPathError, ValueError)


def test_a_non_string_out_dir_is_refused(tmp_path, monkeypatch):
    """A JSON list or object is a bad request, not a server fault."""
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(tmp_path / "exports"))
    for bad in (["nightly"], {"dir": "nightly"}, 7):
        with pytest.raises(ExportPathError):
            resolve_export_dir(bad, str(tmp_path / "j.db"))


# ---- over HTTP --------------------------------------------------------------

@pytest.fixture()
def console(tmp_path, offline_config, monkeypatch):
    root = tmp_path / "exports"
    monkeypatch.setenv(srv.TOKEN_ENV, TOKEN)
    monkeypatch.setenv("JESTER_EXPORTS_DIR", str(root))
    args = argparse.Namespace(db=str(tmp_path / "j.db"), config=str(offline_config),
                              signals_db=str(tmp_path / "s.db"),
                              host="127.0.0.1", port=0)
    previous = srv.Handler.args
    srv.Handler.args = args
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        srv.Handler.args = previous


def _export(base, out_dir):
    payload = {} if out_dir is None else {"out_dir": str(out_dir)}
    req = urllib.request.Request(base + "/api/export",
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in AUTH.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_a_parent_escape_over_http_is_400(console):
    base, root = console
    status, payload = _export(base, os.path.join("..", ".."))
    assert status == 400, payload
    assert payload["ok"] is False
    assert str(root.resolve()) in payload["error"]


def test_an_absolute_path_outside_the_root_is_400(console, tmp_path):
    base, _root = console
    status, payload = _export(base, tmp_path / "somewhere-else")
    assert status == 400, payload
    assert payload["ok"] is False


def test_a_non_string_out_dir_is_400(console):
    """Before the server branch validated the type, a list reached Path() and
    raised TypeError, which the route reported as a 500."""
    base, _root = console
    for bad in (["nightly"], {"dir": "nightly"}):
        req = urllib.request.Request(
            base + "/api/export",
            data=json.dumps({"out_dir": bad}).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        for key, value in AUTH.items():
            req.add_header(key, value)
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=20)
        assert exc.value.code == 400, bad
        assert json.loads(exc.value.read())["ok"] is False


def test_an_out_dir_inside_the_root_is_200_and_files_land_there(console):
    base, root = console
    target = root / "nightly"
    status, payload = _export(base, target)
    assert status == 200, payload
    assert payload["ok"] is True
    assert Path(payload["out_dir"]) == target.resolve()
    assert (target / "manifest.json").exists()


def test_the_default_target_still_works(console):
    base, root = console
    status, payload = _export(base, None)
    assert status == 200, payload
    assert payload["ok"] is True
    assert Path(payload["out_dir"]) == root.resolve()
    assert (root / "manifest.json").exists()
