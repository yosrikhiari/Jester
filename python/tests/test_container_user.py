"""The console container does not run as root, and does not schedule (V3-25).

`docker/Dockerfile.python` installed `cron` and had no `USER`, so the console
— an HTTP server reachable from the port mapping — ran as root, and the
container was a second scheduler beside the host's. These are static checks on
the text and on the compose file, because the properties that matter (who the
process is, what it can write, whether a scheduler lives in it) are all
decided in the image.

The live proof — `docker build` and `id` inside the container — is run
separately and recorded in the task report; a static check here means a pull
request that reintroduces `USER root` fails without needing a daemon.

Two properties are behavioural rather than textual: the entrypoint's startup
sequence (the schema bootstrap and the console exec) runs the real script
against a stub `python` on PATH, and the compose healthcheck runs its own
`python -c` code against a local stub server. Grepping either string would
pass while the executed process did the wrong thing.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "docker" / "Dockerfile.python"
ENTRYPOINT = REPO / "docker" / "entrypoint.console.sh"
COMPOSE = REPO / "docker-compose.yml"

# The entrypoint is a POSIX sh script, so exercising it needs a real sh. The
# Linux CI job has one; a Windows dev box has one when pytest is launched from
# Git Bash. Skip rather than guess when neither is true.
SH = shutil.which("sh")


def _text(path):
    return path.read_text(encoding="utf-8")


def _user_lines(text):
    out = []
    for number, line in enumerate(text.splitlines(), start=1):
        match = re.match(r"\s*USER\s+(\S+)", line)
        if match:
            out.append((number, match.group(1)))
    return out


def test_the_final_user_is_not_root():
    users = _user_lines(_text(DOCKERFILE))
    assert users, "the image pins no USER: it runs as whoever docker ran as"
    line, who = users[-1]
    assert who not in ("root", "0", "0:0", "0:root", "root:root"), who
    # A numeric uid, so a volume written by one build stays writable by the
    # next; "jester" would be resolved per build.
    assert re.match(r"^\d+(:\d+)?$", who), f"USER {who!r} is not a fixed uid"
    assert int(who.split(":")[0]) != 0


def test_no_user_root_appears_after_the_final_user():
    users = _user_lines(_text(DOCKERFILE))
    assert users[-1][1] != "root"
    # Exactly one USER: a later `USER root` would silently undo the drop.
    assert len(users) == 1, f"USER lines: {users}"


def test_cron_is_no_longer_installed():
    """Cron was the only apt package in this image. With the host scheduler
    back in charge there is no reason for a package manager step at all, so
    every executable line is checked — comments may still explain why."""
    stripped = re.sub(r"(?m)^\s*#.*$", "", _text(DOCKERFILE))
    assert "cron" not in stripped, (
        "the runtime image still mentions cron outside its comments")
    package_lines = [ln for ln in stripped.splitlines()
                     if re.search(r"\b(apt-get|apt|apk|dnf|yum)\b", ln)]
    assert not package_lines, package_lines


def test_the_entrypoint_starts_no_scheduler():
    live = [line for line in _text(ENTRYPOINT).splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    joined = "\n".join(live)
    assert "cron" not in joined, joined
    assert "exec python -m jester.console" in joined


STUB_HEADER = "#!/bin/sh"

# The documented schema-bootstrap call (go/cmd/contractcheck documents the
# same one). The test does not pin the exact string, only that the entrypoint
# reaches `open_db` and does not shell out to `jester migrate`.
BOOTSTRAP_IMPORT = "from jester.store import open_db"
BOOTSTRAP_PATH_ARG = "sys.argv[1]"


def _run_entrypoint(tmp_path, *args, token=None, fail_bootstrap=False,
                    jester_db=None):
    """Run the real entrypoint with a stub `python` first on PATH.

    The stub records each argv it receives and exits 0 (or 1 for the schema
    bootstrap call), so the assertions are about what the script actually
    executes, not about the text it contains. No Docker daemon is involved.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    lines = [STUB_HEADER]
    lines.append('for arg in "$@"; do printf \'%s\\n\' "$arg" >> "$JESTER_STUB_LOG"; done')
    lines.append('printf \'%s\\n\' "====" >> "$JESTER_STUB_LOG"')
    if fail_bootstrap:
        lines.append('case "$*" in')
        lines.append('    *open_db*) exit 1 ;;')
        lines.append("esac")
    stub = bindir / "python"
    stub.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    stub.chmod(0o755)

    env = os.environ.copy()
    env.pop("JESTER_CONSOLE_TOKEN", None)
    env.pop("JESTER_DB", None)
    if token is not None:
        env["JESTER_CONSOLE_TOKEN"] = token
    if jester_db is not None:
        env["JESTER_DB"] = jester_db
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    # MSYS sh (Git Bash) opens this itself; forward slashes survive both.
    env["JESTER_STUB_LOG"] = str(log).replace("\\", "/")
    proc = subprocess.run([SH, str(ENTRYPOINT), *args], env=env,
                          capture_output=True, text=True, timeout=60)
    return proc, _calls(log)


def _calls(log):
    if not log.exists():
        return []
    groups = log.read_text(encoding="utf-8").split("====\n")
    return [group.splitlines() for group in groups if group.strip()]


def _is_schema_bootstrap(argv, db):
    """`python -c "<open_db import>/open_db(...)" <db>` for exactly `db`."""
    if len(argv) != 3 or argv[0] != "-c" or argv[2] != db:
        return False
    return BOOTSTRAP_IMPORT in argv[1] and BOOTSTRAP_PATH_ARG in argv[1]


def _assert_bootstraps_then_consoles(calls, db, console_argv):
    assert len(calls) == 2, calls
    bootstrap, console = calls
    assert _is_schema_bootstrap(bootstrap, db), bootstrap
    assert console == console_argv, console
    # The order is the point: no console before the schema exists.
    assert calls.index(bootstrap) < calls.index(console)
    # `jester migrate` refuses on a stale `running` row and would exit 1 on a
    # perfectly good archive; the bootstrap must not reintroduce it.
    assert not any("jester.cli" in " ".join(call) or "migrate" in call
                   for call in calls), calls


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
@pytest.mark.parametrize("db_args", [
    ("--db", "/app/data/other.db"),
    ("--db=/app/data/other.db",),
])
def test_the_entrypoint_bootstraps_the_schema_before_a_read_only_console(
        tmp_path, db_args):
    """The container listens on 0.0.0.0, which _bind_refusal refuses without
    a token. Without one the entrypoint bootstraps the console's own archive
    once -- a read-only ConsoleAPI never creates the schema, so a fresh volume
    would answer 503 forever -- and then starts with --read-only. Both --db
    forms name the archive."""
    proc, calls = _run_entrypoint(
        tmp_path, "--host", "0.0.0.0", "--port", "8619", *db_args)
    assert proc.returncode == 0, proc.stderr
    _assert_bootstraps_then_consoles(
        calls, "/app/data/other.db",
        ["-m", "jester.console", "--host", "0.0.0.0", "--port", "8619",
         *db_args, "--read-only"])


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
def test_the_entrypoint_bootstraps_the_image_default_archive(tmp_path):
    proc, calls = _run_entrypoint(tmp_path)
    assert proc.returncode == 0, proc.stderr
    _assert_bootstraps_then_consoles(
        calls, "/app/data/jester-docker.db",
        ["-m", "jester.console", "--read-only"])


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
def test_the_entrypoint_prefers_jester_db_over_the_image_default(tmp_path):
    """`JESTER_DB` is the archive every other command reads, so the console
    must not bootstrap a second file beside it when the flag is absent."""
    proc, calls = _run_entrypoint(tmp_path, jester_db="/app/data/from-env.db")
    assert proc.returncode == 0, proc.stderr
    _assert_bootstraps_then_consoles(
        calls, "/app/data/from-env.db",
        ["-m", "jester.console", "--read-only"])


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
@pytest.mark.parametrize("db_args", [
    ("--db=",),
    ("--db", ""),
    ("--db",),
])
def test_an_empty_db_flag_falls_back_to_jester_db(tmp_path, db_args):
    """An empty or valueless --db is not a path: neither `open_db("")` nor a
    silent jump to the image default while JESTER_DB points elsewhere."""
    proc, calls = _run_entrypoint(
        tmp_path, *db_args, jester_db="/app/data/from-env.db")
    assert proc.returncode == 0, proc.stderr
    _assert_bootstraps_then_consoles(
        calls, "/app/data/from-env.db",
        ["-m", "jester.console", *db_args, "--read-only"])


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
@pytest.mark.parametrize("db_args", [("--db=",), ("--db",)])
def test_an_empty_db_flag_without_jester_db_uses_the_image_default(
        tmp_path, db_args):
    proc, calls = _run_entrypoint(tmp_path, *db_args)
    assert proc.returncode == 0, proc.stderr
    _assert_bootstraps_then_consoles(
        calls, "/app/data/jester-docker.db",
        ["-m", "jester.console", *db_args, "--read-only"])


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
def test_the_entrypoint_leaves_bootstrapping_to_the_operator_when_a_token_is_set(
        tmp_path):
    """A token means writes are possible, so the console's own schema path
    (and /api/migrate) is live; the entrypoint must not bootstrap on its own
    and must not add --read-only."""
    proc, calls = _run_entrypoint(
        tmp_path, "--host", "0.0.0.0", "--port", "8619", token="s3cret")
    assert proc.returncode == 0, proc.stderr
    assert calls == [
        ["-m", "jester.console", "--host", "0.0.0.0", "--port", "8619"],
    ]


@pytest.mark.skipif(SH is None, reason="needs a POSIX sh to run the entrypoint")
def test_a_failed_bootstrap_stops_the_entrypoint(tmp_path):
    """A real failure stays loud: no console is started and the reason names
    the archive."""
    proc, calls = _run_entrypoint(tmp_path, fail_bootstrap=True)
    assert proc.returncode != 0
    assert "schema bootstrap of /app/data/jester-docker.db failed" in proc.stderr
    assert len(calls) == 1, calls
    assert _is_schema_bootstrap(calls[0], "/app/data/jester-docker.db"), calls


def test_bytecode_is_disabled_because_the_bind_mount_is_read_only_to_this_user():
    assert "PYTHONDONTWRITEBYTECODE=1" in _text(DOCKERFILE)


def test_the_data_volume_is_prepared_for_that_user_before_the_drop():
    """Docker copies a mount point's ownership into a fresh named volume, so
    the directory has to exist and be owned by the container's user at build
    time — a `USER` with no such directory leaves /app/data root-owned and the
    console unable to open its archive."""
    text = _text(DOCKERFILE)
    made = text.find("mkdir -p /app/data")
    assert made != -1, "nothing creates /app/data"
    assert "chown" in text[made:made + 400], text[made:made + 400]
    assert made < text.find("USER 10001"), "the drop comes before the chown"


def test_the_compose_file_parses():
    config = yaml.safe_load(_text(COMPOSE))
    assert isinstance(config, dict) and config.get("services")
    console = config["services"]["jester-console"]
    # The archive stays in its own volume, never the host's ./data.
    assert "jester_docker_data:/app/data" in console["volumes"]
    assert "jester_docker_data" in config["volumes"]
    # The healthcheck has to keep working as a non-root user: it is a plain
    # HTTP GET that needs nothing but the socket. /metrics also answers a
    # read-only console whose fresh volume has no schema yet.
    assert "/metrics" in " ".join(console["healthcheck"]["test"])


def test_the_healthcheck_needs_no_privilege():
    config = yaml.safe_load(_text(COMPOSE))
    console = config["services"]["jester-console"]
    assert console["healthcheck"]["test"][0] == "CMD"
    joined = " ".join(console["healthcheck"]["test"])
    assert "urllib.request" in joined
    assert "8619" in joined


HEALTHCHECK_HOST = "http://localhost:8619"


def _metrics_stub(status=200):
    """A local stand-in for the console's /metrics, recording each request."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def _healthcheck():
    config = yaml.safe_load(_text(COMPOSE))
    return config["services"]["jester-console"]["healthcheck"]


def _healthcheck_code():
    test = _healthcheck()["test"]
    assert test[0] == "CMD", test
    assert test[1] in ("python", "python3"), test
    assert test[2] == "-c", test
    return test[3]


def _run_healthcheck(base_url, token=None):
    """Execute the compose healthcheck's own code against `base_url`.

    The container URL is rewritten to the stub's port; nothing else changes,
    so this runs the probe the image will run, including its env handling.
    """
    code = _healthcheck_code()
    assert HEALTHCHECK_HOST in code, code
    code = code.replace(HEALTHCHECK_HOST, base_url)
    env = {k: v for k, v in os.environ.items() if k != "JESTER_CONSOLE_TOKEN"}
    if token is not None:
        env["JESTER_CONSOLE_TOKEN"] = token
    return subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True, timeout=60)


def _probe_with_stub(token=None, status=200):
    server, seen = _metrics_stub(status=status)
    url = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        proc = _run_healthcheck(url, token=token)
    finally:
        server.shutdown()
        server.server_close()
    return proc, seen


def test_the_healthcheck_answers_a_live_console():
    """The probe is executed, not grepped: a 200 on /metrics exits 0."""
    proc, seen = _probe_with_stub()
    assert proc.returncode == 0, proc.stderr
    assert seen == [("/metrics", None)]


def test_the_healthcheck_sends_the_token_when_one_is_set():
    """With --read-auth every GET needs the token, so the probe would report
    an unhealthy console forever without the header. The header is built from
    the environment only when it is non-empty, so the no-token stack still
    probes as before."""
    proc, seen = _probe_with_stub(token="s3cret")
    assert proc.returncode == 0, proc.stderr
    assert seen == [("/metrics", "Bearer s3cret")]

    proc, seen = _probe_with_stub(token="")
    assert proc.returncode == 0, proc.stderr
    assert seen == [("/metrics", None)]


def test_the_healthcheck_fails_when_the_console_is_unhealthy():
    proc, seen = _probe_with_stub(status=503)
    assert proc.returncode != 0
    assert seen and seen[0][0] == "/metrics"


def test_the_healthcheck_timeout_fits_inside_the_docker_timeout():
    """A probe timeout longer than the check's own timeout can never report a
    timeout: Docker kills it first, at the boundary, with no diagnosis."""
    check = _healthcheck()
    probe = re.search(r"timeout=(\d+)", _healthcheck_code())
    assert probe, _healthcheck_code()
    docker = re.match(r"(\d+)s$", str(check["timeout"]))
    assert docker, check["timeout"]
    assert 0 < int(probe.group(1)) < int(docker.group(1))


def test_compose_still_overrides_through_the_image_user():
    """`user:` in compose would be a second place to change the uid. The image
    owns it; this test fails if someone adds the override without thinking."""
    config = yaml.safe_load(_text(COMPOSE))
    assert "user" not in config["services"]["jester-console"]
