"""End-to-end journeys over the F-01..F-06 changes, through real processes.

What this file proves that the unit suites cannot: the Go worker binary, the
`jester` CLI, and the console server agree with each other across process
boundaries — same SQLite file, same env-var contracts, same HTTP surface.

Rules of this file (e2e-testing-patterns + repo conftest lessons):

* Everything runs in tmp dirs with an offline config copy (all providers
  `fake`). No live services, no repo `data/`, never the author's machine
  state. The repo `.env` points `JESTER_QDRANT_URL` at a dead localhost
  server, so the child env sets it to "" explicitly — `load_env_once`
  fills only MISSING keys, and an empty string selects per-DB local
  vector mode instead of the unreachable server.
* Subprocesses get a scrubbed env dict (never inherited ambient secrets or
  local Qdrant/Ollama pointers).
* HTTP assertions are on status codes and payloads, never retried; only
  TCP setup is retried while a fresh server thread starts.
* Each journey asserts ONE cross-boundary property (test-guard Rule 4):
  unit suites already cover each side in isolation.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GO_DIR = REPO_ROOT / "go"
REPO_CONFIG = REPO_ROOT / "config"

_OFFLINE_PROVIDERS = {
    "llm_provider": "fake",
    "embedding_provider": "fake",
    "extractor_provider": "fake",
    "synthesizer_provider": "fake",
    "critic_provider": "fake",
    "competitor_search_provider": "none",
}

go_binary = shutil.which("go")


def _offline_config(dst: Path) -> Path:
    """Copy of the repo config with every provider forced offline."""
    shutil.copytree(REPO_CONFIG, dst)
    path = dst / "thresholds.yaml"
    lines, seen, out = path.read_text(encoding="utf-8").splitlines(), set(), []
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in _OFFLINE_PROVIDERS:
            out.append(f"{key}: {_OFFLINE_PROVIDERS[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in _OFFLINE_PROVIDERS.items():
        if key not in seen:
            out.append(f"{key}: {value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dst


def _child_env(**overrides):
    """Scrubbed child env: offline, local vectors, no ambient credentials."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(REPO_ROOT / "python"),
        "JESTER_MOCK": "1",
        "JESTER_QDRANT_URL": "",
        # Empty, not absent: load_env treats presence as a deliberate choice,
        # so an empty value stops a developer's .env token from turning auth
        # on under these tests. Auth itself is covered in test_console_auth.
        "JESTER_CONSOLE_TOKEN": "",
        "NO_COLOR": "1",
    }
    env.update({k: v for k, v in overrides.items() if v is not None})
    for k, v in list(overrides.items()):
        if v is None and k in env:
            del env[k]
    return env


def _run_cli(*argv, env_extra=None, cwd=None, timeout=300):
    env = _child_env(**(env_extra or {}))
    proc = subprocess.run(
        [sys.executable, "-m", "jester.cli", *argv],
        capture_output=True, text=True, timeout=timeout,
        env=env, cwd=str(cwd or REPO_ROOT),
    )
    return proc


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(base, path="/api/doctor", timeout=30):
    """Wait for TCP setup only; the first HTTP ANSWER ends the wait, even a
    4xx — asserting on it is the test's job, not the helper's."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + path, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except OSError as exc:
            last = exc
            time.sleep(0.3)
    raise last or TimeoutError(f"{base}{path} never answered")


@pytest.fixture(scope="module")
def worker_bin(tmp_path_factory):
    """The Go worker, built once per session. `go run` per test would pay a
    full compile every invocation."""
    if not go_binary:
        pytest.skip("go toolchain not on PATH")
    out = tmp_path_factory.mktemp("e2e") / (
        "worker.exe" if sys.platform == "win32" else "worker")
    proc = subprocess.run(
        ["go", "build", "-o", str(out), "./cmd/worker"],
        capture_output=True, text=True, timeout=600, cwd=str(GO_DIR),
    )
    assert proc.returncode == 0, f"go build failed:\n{proc.stderr[-2000:]}"
    return str(out)


@pytest.fixture()
def e2e_home(tmp_path):
    """One isolated world per journey: db + offline config + exports dir."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"dir": home, "db": str(home / "j.db"),
            "config": str(_offline_config(home / "config"))}


def _seed(worker_bin, home, run_id="e2e-seed", max_comments=40):
    env = _child_env()
    proc = subprocess.run(
        [worker_bin, "-config", str(REPO_CONFIG), "-db", home["db"],
         "-run", run_id, "-max-comments", str(max_comments)],
        capture_output=True, text=True, timeout=300, env=env,
        cwd=str(GO_DIR),
    )
    assert proc.returncode == 0, f"worker mock seed failed:\n{proc.stderr[-2000:]}"
    return proc


def _nugget_rows(db_path):
    import sqlite3
    db = sqlite3.connect(db_path)
    try:
        return db.execute(
            "SELECT unique_key, extracted_insight, category, extractor_model "
            "FROM nuggets ORDER BY unique_key").fetchall()
    finally:
        db.close()


# ---- J1: Go enqueue -> treat -> list -> console overview --------------------

def test_pipeline_seed_treat_list_console_agree(worker_bin, e2e_home):
    """The cross-process contract holds end to end: rows the Go binary
    enqueued are extracted, archived, listed, and served over HTTP."""
    _seed(worker_bin, e2e_home)
    treat = _run_cli("treat", "--db", e2e_home["db"], "--config",
                     e2e_home["config"], "--run", "e2e-j1",
                     "--max-comments", "40")
    assert treat.returncode == 0, f"treat failed:\n{treat.stderr[-2000:]}"
    rows = _nugget_rows(e2e_home["db"])
    assert rows, "treat archived no nuggets from the seeded batch"

    listed = _run_cli("list", "--db", e2e_home["db"])
    assert listed.returncode == 0, f"list failed:\n{listed.stderr[-2000:]}"
    assert rows[0][0] in listed.stdout, "listed output misses an archived nugget"

    port = _free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "jester.console", "--db", e2e_home["db"],
         "--config", e2e_home["config"], "--host", "127.0.0.1",
         "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_child_env(), cwd=str(REPO_ROOT),
    )
    try:
        status, _body = _wait_http(f"http://127.0.0.1:{port}")
        assert status == 200, f"console never came up: {_body[:500]!r}"
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/overview",
                timeout=10) as resp:
            overview = json.loads(resp.read())
        assert overview["ok"] is True
        total = overview["counts"]["nuggets"]
        assert total >= len(rows), (
            f"console serves {total} nuggets but the archive holds {len(rows)}")
    finally:
        server.terminate()
        server.wait(timeout=15)


# ---- J2: batch-size parity at journey level (F-05 promotion gate) ------------

def test_batched_run_archives_the_same_nuggets(worker_bin, tmp_path):
    """treat with extractor_batch_size=4 must archive exactly the rows the
    legacy one-call-per-comment path archives: same keys, same insights,
    same categories, same model stamps. Fewer requests, identical archive."""
    homes = []
    for name in ("legacy", "batched"):
        home = {"dir": tmp_path / name, "db": str(tmp_path / name / "j.db"),
                "config": str(_offline_config(tmp_path / name / "config"))}
        home["dir"].mkdir(parents=True, exist_ok=True)
        homes.append(home)
        _seed(worker_bin, home, run_id=f"e2e-j2-{name}")
    legacy, batched = homes

    cfg_path = Path(batched["config"]) / "thresholds.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    assert "extractor_batch_size" not in text
    cfg_path.write_text(text + "\nextractor_batch_size: 4\n", encoding="utf-8")

    for home, run in ((legacy, "e2e-j2a"), (batched, "e2e-j2b")):
        proc = _run_cli("treat", "--db", home["db"], "--config",
                        home["config"], "--run", run, "--max-comments", "40")
        assert proc.returncode == 0, f"treat {run} failed:\n{proc.stderr[-2000:]}"
    assert _nugget_rows(legacy["db"]) == _nugget_rows(batched["db"]), (
        "batched extraction changed the archive — do not promote the knob")
    assert _nugget_rows(legacy["db"]), "seed produced no nuggets to compare"


# ---- J3: dedup-sweep CLI on archive-derived pairs (F-06 wiring) --------------

def test_dedup_sweep_cli_reports_best_f1(worker_bin, e2e_home):
    """Pairs built from the real archive calibrate through the real command:
    identical texts must split perfectly from unrelated ones."""
    _seed(worker_bin, e2e_home)
    proc = _run_cli("treat", "--db", e2e_home["db"], "--config",
                    e2e_home["config"], "--run", "e2e-j3",
                    "--max-comments", "40")
    assert proc.returncode == 0
    rows = _nugget_rows(e2e_home["db"])
    assert len(rows) >= 2, "need two nuggets to build pairs from"
    ( _k0, insight0, _c0, _m0), (_k1, insight1, _c1, _m1) = rows[0], rows[1]
    pairs = [
        {"a": insight0, "b": insight0, "same": True},
        {"a": insight0, "b": "quarterly tax filing deadlines for contractors",
         "same": False},
        {"a": insight1, "b": insight1, "same": True},
        {"a": insight1, "b": "best espresso grind size for pour over",
         "same": False},
    ]
    pairs_path = e2e_home["dir"] / "pairs.jsonl"
    pairs_path.write_text("\n".join(json.dumps(p) for p in pairs),
                          encoding="utf-8")
    sweep = _run_cli("dedup-sweep", "--pairs", str(pairs_path), "--config",
                     e2e_home["config"])
    assert sweep.returncode == 0, f"dedup-sweep failed:\n{sweep.stderr[-2000:]}"
    assert "best F1:" in sweep.stdout
    assert "pairs: 4" in sweep.stdout


# ---- J4: token-gated console boot (F-01 entry wiring) ------------------------

def test_guarded_console_boot_journey(e2e_home):
    """The main() entry honors the token env end to end: anonymous writes
    die at 401, the wrong token dies at 401, the right token's malformed
    body reaches the route (400), and --read-only answers 403."""
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    def _post(path, body=b"{}", token=None):
        req = urllib.request.Request(base + path, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    server = subprocess.Popen(
        [sys.executable, "-m", "jester.console", "--db", e2e_home["db"],
         "--config", e2e_home["config"], "--host", "127.0.0.1",
         "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_child_env(JESTER_CONSOLE_TOKEN="e2e-token"), cwd=str(REPO_ROOT),
    )
    try:
        _wait_http(base)
        assert _post("/api/run")[0] == 401
        assert _post("/api/run", token="wrong")[0] == 401
        status, payload = _post("/api/run", body=b"{oops",
                                token="e2e-token")
        assert status == 400 and "bad JSON body" in payload["error"]
    finally:
        server.terminate()
        server.wait(timeout=15)

    port2 = _free_port()
    base2 = f"http://127.0.0.1:{port2}"
    ro = subprocess.Popen(
        [sys.executable, "-m", "jester.console", "--db", e2e_home["db"],
         "--config", e2e_home["config"], "--host", "127.0.0.1",
         "--port", str(port2), "--read-only"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_child_env(JESTER_CONSOLE_TOKEN="e2e-token"), cwd=str(REPO_ROOT),
    )
    try:
        _wait_http(base2)

        def _post2(path, body=b"{}"):
            req = urllib.request.Request(base2 + path, data=body,
                                         method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer e2e-token")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())

        status, payload = _post2("/api/run")
        assert status == 403
        assert payload == {"ok": False, "error": "this console is read-only"}
    finally:
        ro.terminate()
        ro.wait(timeout=15)


# ---- J5: knob round-trip, console write -> pipeline read ---------------------

def test_threshold_knob_console_to_pipeline_round_trip(worker_bin, e2e_home):
    """The new knob travels the whole loop: console API writes it into the
    tmp thresholds.yaml, the file keeps validating, and a batched treat on
    it archives nuggets."""
    import yaml

    _seed(worker_bin, e2e_home)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "jester.console", "--db", e2e_home["db"],
         "--config", e2e_home["config"], "--host", "127.0.0.1",
         "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env=_child_env(), cwd=str(REPO_ROOT),
    )
    try:
        _wait_http(base)
        body = json.dumps({"patch": {"extractor_batch_size": 4}}).encode()
        req = urllib.request.Request(base + "/api/thresholds", data=body,
                                     method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read())
        assert payload["ok"] is True, payload
    finally:
        server.terminate()
        server.wait(timeout=15)

    raw = yaml.safe_load(
        (Path(e2e_home["config"]) / "thresholds.yaml").read_text(
            encoding="utf-8"))
    assert raw.get("extractor_batch_size") == 4
    proc = _run_cli("treat", "--db", e2e_home["db"], "--config",
                    e2e_home["config"], "--run", "e2e-j5",
                    "--max-comments", "40")
    assert proc.returncode == 0, f"batched treat failed:\n{proc.stderr[-2000:]}"
    assert _nugget_rows(e2e_home["db"]), "batched treat archived nothing"
