"""Where the console looks for its services.

The infra panel probed hardcoded 127.0.0.1 for cloakserve and Qdrant. On the
host that is right. Inside a container it is that container's own loopback, so
both read as down while both were up and answering on the compose network —
cloakserve at cloakbrowser:9222, Qdrant at qdrant:6333.

The console then told the operator to run
`docker compose --profile live up -d cloakbrowser` against a container that
had been up for forty hours, and switched off the live-ingest button, which
gates on `can_ingest_live = bool(go_bin) and cloak_up`.

Four lines above the bug, the same file already says a port check that does
not match the configuration is a lie — that comment was written after Qdrant
showed green for a day while every write went to a local file. The vector
state was fixed then; the port checks beside it were left.

So these tests pin the rule rather than the addresses: the panel probes what
the app is configured to use.
"""
import os

import pytest

from jester.console.api import ConsoleAPI, _host_port
from jester.fetchers.cloak import load_scraper_config


# ---- splitting a service URL ----------------------------------------------

@pytest.mark.parametrize("url, default_port, expected", [
    ("http://cloakbrowser:9222", 9222, ("cloakbrowser", 9222)),
    ("http://qdrant:6333", 6333, ("qdrant", 6333)),
    ("http://127.0.0.1:9222", 9222, ("127.0.0.1", 9222)),
    # No scheme. urlsplit reads "qdrant:6333" as scheme "qdrant" and reports no
    # host, which would quietly probe localhost instead of the configured
    # server — the exact failure this whole change is about.
    ("qdrant:6333", 6333, ("qdrant", 6333)),
    # No port: the service's default.
    ("http://qdrant", 6333, ("qdrant", 6333)),
    # Nothing configured is the single-machine developer, and localhost is the
    # right answer for them.
    ("", 6333, ("127.0.0.1", 6333)),
    ("   ", 9222, ("127.0.0.1", 9222)),
    (None, 9222, ("127.0.0.1", 9222)),
])
def test_a_service_url_splits_into_something_probeable(url, default_port, expected):
    assert _host_port(url, default_port) == expected


# ---- the cloakserve address ------------------------------------------------

def test_the_file_is_used_when_nothing_overrides_it(monkeypatch):
    monkeypatch.delenv("JESTER_CDP_URL", raising=False)
    assert load_scraper_config().cdp_url == "http://127.0.0.1:9222"


def test_the_environment_wins_over_the_file(monkeypatch):
    """The file is bind-mounted into the container, so it is the same for the
    host worker and the containerised one. The address has to come from the
    thing that differs."""
    monkeypatch.setenv("JESTER_CDP_URL", "http://cloakbrowser:9222")
    assert load_scraper_config().cdp_url == "http://cloakbrowser:9222"


def test_a_blank_override_is_not_an_address(monkeypatch):
    """An empty variable is compose leaving it unset, not someone asking for
    the empty string."""
    monkeypatch.setenv("JESTER_CDP_URL", "   ")
    assert load_scraper_config().cdp_url == "http://127.0.0.1:9222"


# ---- the panel probes what the app uses ------------------------------------

@pytest.fixture()
def api(tmp_path, offline_config):
    return ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(offline_config))


def test_the_panel_asks_the_fetcher_where_cloakserve_is(api, monkeypatch):
    """Not a second copy of the resolution logic. If the panel re-derived the
    address it could drift from the code it claims to describe."""
    monkeypatch.setenv("JESTER_CDP_URL", "http://cloakbrowser:9222")
    assert api._cdp_url() == "http://cloakbrowser:9222"


def test_a_broken_scraper_config_is_not_reported_as_an_outage(tmp_path, monkeypatch):
    """A missing or unreadable config file means we do not know where
    cloakserve is. Falling back to the default is right; raising out of a
    health probe and blanking the whole panel is not."""
    monkeypatch.delenv("JESTER_CDP_URL", raising=False)
    broken = tmp_path / "cfg"
    broken.mkdir()
    (broken / "scraper.yaml").write_text("{{ not yaml", encoding="utf-8")
    api = ConsoleAPI(db_path=str(tmp_path / "j.db"), config_dir=str(broken))
    assert api._cdp_url() == "http://127.0.0.1:9222"


def test_the_probe_targets_the_configured_hosts_not_localhost(api, monkeypatch):
    """The regression, stated as the rule: whatever the app is configured to
    talk to is what gets probed."""
    monkeypatch.setenv("JESTER_CDP_URL", "http://cloakbrowser:9222")
    monkeypatch.setenv("JESTER_QDRANT_URL", "http://qdrant:6333")

    attempted = []

    def fake_connection(address, timeout=None):
        attempted.append(address)
        raise OSError("nothing is listening in a test")

    monkeypatch.setattr("jester.console.api.socket.create_connection", fake_connection)
    monkeypatch.setattr(ConsoleAPI, "ollama_models", lambda self: None)
    api.infra(fresh=True)

    assert ("cloakbrowser", 9222) in attempted, \
        "cloakserve was probed somewhere other than where it is configured"
    assert ("qdrant", 6333) in attempted, \
        "Qdrant was probed somewhere other than where it is configured"
    assert not any(host == "127.0.0.1" for host, _ in attempted), \
        "a hardcoded localhost probe is the bug this guards"


def test_ollama_is_asked_where_its_own_client_would_ask(api, monkeypatch):
    """ollama.Client() reads OLLAMA_HOST. Probing a different address is how a
    panel ends up confidently describing a service nobody talks to."""
    monkeypatch.setenv("OLLAMA_HOST", "http://ollama:11434")
    asked = []

    def fake_urlopen(url, timeout=None):
        asked.append(url if isinstance(url, str) else url.full_url)
        raise OSError("down")

    monkeypatch.setattr("jester.console.api.urllib.request.urlopen", fake_urlopen)
    assert api.ollama_models() is None, "unreachable must be None, never an empty list"
    assert asked == ["http://ollama:11434/api/tags"]


def test_a_bare_ollama_host_still_becomes_a_url(api, monkeypatch):
    """OLLAMA_HOST is commonly written without a scheme."""
    monkeypatch.setenv("OLLAMA_HOST", "ollama:11434")
    asked = []
    monkeypatch.setattr("jester.console.api.urllib.request.urlopen",
                        lambda url, timeout=None: asked.append(url) or (_ for _ in ()).throw(OSError()))
    api.ollama_models()
    assert asked == ["http://ollama:11434/api/tags"]
