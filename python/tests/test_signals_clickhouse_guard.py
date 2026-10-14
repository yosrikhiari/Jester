"""F-07: the ClickHouse default-password guard.

The dev password `jester` is committed in docker-compose.yml. Talking to a
loopback container with it is the documented local setup; sending it to any
other host is a credential leak. The client refuses the second case loudly.
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


def test_default_password_allowed_on_loopback():
    client = ch.Client(url="http://127.0.0.1:8123", password="jester",
                       opener=_opener)
    assert client.uses_default_password() is True
    assert client.execute("SELECT 1") == ""  # no refusal


def test_default_password_refused_off_loopback():
    client = ch.Client(url="http://clickhouse.internal:8123", password="jester",
                       opener=_opener)
    with pytest.raises(ch.ClickHouseError, match="default password"):
        client.execute("SELECT 1")


def test_custom_password_allowed_anywhere():
    client = ch.Client(url="http://clickhouse.internal:8123",
                       password="a-real-secret", opener=_opener)
    assert client.uses_default_password() is False
    assert client.execute("SELECT 1") == ""


def test_env_default_is_the_documented_dev_password(monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_PASSWORD", raising=False)
    assert ch.Client(url="http://127.0.0.1:8123",
                     opener=_opener).uses_default_password() is True
