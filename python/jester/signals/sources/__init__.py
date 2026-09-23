"""Where problem signals come from, and on what authority.

One rule shapes this package: **a source is only usable if its own operator
says so in writing.** Bypassing limits or collecting outside approved
access are the two ways this goes wrong, so a source here declares three
things before it declares a single method:

* `access` — the basis on which we are allowed to call it, in one sentence a
  reviewer can check.
* `terms_url` — where that sentence comes from.
* `rate` — the pace we hold ourselves to, which is a self-imposed floor, not
  the ceiling the service permits.

Reddit is deliberately **not** here. The free Data API is non-commercial, and
this work is commercial, so the Reddit path needs approved commercial access
that the scope owner decides. A path that cannot legally run must not exist
as code that can be called by accident.

Hacker News is here because it is the replacement the plan already named, and
its search API is public, keyless and documented. Building it now means the
switch is a five-minute confirmation rather than a week of drift — and
everything downstream (field map, filters, fixtures, ClickHouse, digest) is
source-agnostic, so only this layer ever changes.
"""
from __future__ import annotations

from typing import Dict, Iterator, List, Protocol

from .. import Signal


class SourceError(RuntimeError):
    """The source could not be read. Carries the query that failed so the run
    can store it as a counted error record rather than dropping it — "silent
    failure must never be silent."""

    def __init__(self, message: str, *, query: str = "", status: str = ""):
        super().__init__(message)
        self.query = query
        self.status = status


class Source(Protocol):
    """What a collector has to provide. Deliberately small: one search, and
    the paperwork that says we may run it."""

    name: str
    platform: str
    access: str
    terms_url: str

    def search(self, query: str, *, since_utc: str = "", limit: int = 100) -> Iterator[Signal]:
        ...


_REGISTRY: Dict[str, type] = {}


def register(cls):
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str, **kwargs):
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise SourceError(f"no source named {name!r}; available: {known}")
    return _REGISTRY[name](**kwargs)


def available() -> List[dict]:
    """Every source and its paperwork — what `jester signals sources` prints,
    and what the scope document quotes."""
    return [{"name": c.name, "platform": c.platform, "access": c.access,
             "terms_url": c.terms_url, "rate": getattr(c, "rate", "")}
            for c in sorted(_REGISTRY.values(), key=lambda c: c.name)]


from . import hackernews  # noqa: E402,F401  (registers itself on import)
from . import hn_hiring   # noqa: E402,F401
