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
"""
import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO / "docker" / "Dockerfile.python"
ENTRYPOINT = REPO / "docker" / "entrypoint.console.sh"
COMPOSE = REPO / "docker-compose.yml"


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


def test_the_entrypoint_goes_read_only_when_no_token_is_set():
    """The container listens on 0.0.0.0, which _bind_refusal refuses without
    a token. The entrypoint must turn the empty compose default into an
    explicit --read-only, or the shipped stack cannot start."""
    text = _text(ENTRYPOINT)
    assert "JESTER_CONSOLE_TOKEN" in text
    assert "--read-only" in text
    # The flag is only added on the empty branch; a configured token means
    # the operator asked for writes.
    assert "exec python -m jester.console \"$@\"" in text


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


def test_compose_still_overrides_through_the_image_user():
    """`user:` in compose would be a second place to change the uid. The image
    owns it; this test fails if someone adds the override without thinking."""
    config = yaml.safe_load(_text(COMPOSE))
    assert "user" not in config["services"]["jester-console"]
