"""F-07: the ClickHouse unconfigured-password guard.

Talking to a loopback container with the deployment default is the
documented local setup; sending an UNCONFIGURED credential to any other host
is a leak. The client refuses the second case loudly. The default value
itself lives in docker-compose.yml — this module only tracks whether the
operator configured one.
"""
import pytest

from jester.signals import clickhouse as ch


class _DummyResponse:
    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(req, timeout=None):
    return _DummyResponse()


def test_configured_password_allowed_on_loopback():
    client = ch.Client(url="http://127.0.0.1:8123", password="s3cret",
                       opener=_opener)
    assert client.password_configured is True
    assert client.execute("SELECT 1") == ""  # no refusal


def test_unconfigured_password_refused_off_loopback(monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    client = ch.Client(url="http://clickhouse.internal:8123",
                       opener=_opener)
    assert client.password_configured is False
    with pytest.raises(ch.ClickHouseError, match="no password configured"):
        client.execute("SELECT 1")


def test_explicit_choice_is_honored_anywhere():
    """An operator who deliberately sets a password — even a weak one — made
    a choice; the guard catches forgotten configuration, not weak choices."""
    client = ch.Client(url="http://clickhouse.internal:8123",
                       password="a-real-secret", opener=_opener)
    assert client.password_configured is True
    assert client.execute("SELECT 1") == ""


def test_env_password_counts_as_configured(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_PASSWORD", "from-env")
    client = ch.Client(url="http://clickhouse.internal:8123",
                       opener=_opener)
    assert client.password_configured is True
    assert client.password == "from-env"
    assert client.execute("SELECT 1") == ""
