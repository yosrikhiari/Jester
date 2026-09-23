"""Recover the community a nugget was read from, where the row already says.

`community` — the subreddit, forum host, Stack Exchange site, Lemmy community
or YouTube channel behind a nugget — was added to the schema after the archive
already held thousands of rows. Those rows are not anonymous: most of them
carry a `source_url` that names the community directly, and the console's
grouped view was showing them as "source not recorded" purely because nobody
had read it back out.

Two rules govern everything here:

1. **Recovery, never invention.** Each rule below reproduces what the Go
   adapter for that platform writes today, so a recovered value is the same
   string a re-fetch would produce. Anything a rule cannot derive from the row
   itself stays empty — the point of the exercise is to stop the archive
   guessing at its own provenance, not to guess more confidently.

2. **The feed is not the site.** Hacker News publishes no sub-communities, so
   its adapter recorded the site name and, since 2026-08-27, the listing a
   story was found through (Ask HN / Show HN / front page). That listing is
   NOT in the row — Algolia's story node never carried it — so recovery can
   restore "Hacker News" and must not pretend to know which feed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

#: Anything that is not an absolute http(s) URL cannot name a community. The
#: archive holds a handful of rows whose source_url is the literal string
#: "mock" — fixture output from the earliest runs — and those are honestly
#: unidentifiable rather than a parsing failure.
#: Asked of the parser, not of the string's first few characters. The prefix
#: test this replaces parsed the URL on the very next line anyway, so it was
#: doing the work twice and trusting the cheaper half.
_WEB_SCHEMES = frozenset({"http", "https"})


def _is_web_url(url: str) -> bool:
    """True when `url` is an absolute http(s) URL with a host in it."""
    if not url:
        return False
    parts = urlsplit(url)
    return parts.scheme.lower() in _WEB_SCHEMES and bool(parts.hostname)


def _host(url: str) -> str:
    """Lowercased host with any leading www., or "" if url is not a URL."""
    if not _is_web_url(url):
        return ""
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _reddit(url: str) -> Optional[Tuple[str, str]]:
    """r/<name> from a thread URL, matching what dom.go reads off the page."""
    m = re.search(r"/r/([A-Za-z0-9_]+)", url or "")
    if not m:
        return None
    name = m.group(1)
    return f"r/{name}", f"https://www.reddit.com/r/{name}/"


def _discourse(url: str) -> Optional[Tuple[str, str]]:
    """The forum host, which is exactly what discourse.go stores."""
    host = _host(url)
    if not host:
        return None
    return host, f"https://{host}"


def _stackexchange(url: str) -> Optional[Tuple[str, str]]:
    """The site slug. stackexchange.go stores "unix", not the hostname, and
    serverfault/superuser/stackoverflow have no .stackexchange.com suffix."""
    host = _host(url)
    if not host:
        return None
    if host.endswith(".stackexchange.com"):
        return host[: -len(".stackexchange.com")], f"https://{host}"
    if host.endswith(".com"):
        return host[: -len(".com")], f"https://{host}"
    return None


def _hackernews(url: str) -> Optional[Tuple[str, str]]:
    """The site, and only the site.

    Which listing found the story — Ask HN, Show HN, the front page — is the
    thing worth knowing, and it is not in the row: it is known to the worker at
    fetch time and to nothing else. Recording a guess here would put three
    made-up feed names on 1,386 rows, so this restores the sibling rows' value
    and stops.
    """
    if _host(url) != "news.ycombinator.com":
        return None
    return "Hacker News", "https://news.ycombinator.com/"


def _lemmy(url: str) -> Optional[Tuple[str, str]]:
    """lemmy.go stores the bare community name (federation makes the local
    instance the wrong identity), so /c/<name> is the whole answer."""
    m = re.search(r"/c/([A-Za-z0-9_.-]+)", url or "")
    if not m:
        return None
    return m.group(1), url


def _github(url: str) -> Optional[Tuple[str, str]]:
    """owner/repo, which is what github.go stores."""
    if _host(url) != "github.com":
        return None
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if len(parts) < 2:
        return None
    repo = f"{parts[0]}/{parts[1]}"
    return repo, f"https://github.com/{repo}"


#: Per-platform recovery. A platform absent from this map is one whose
#: community cannot be read off a URL at all: YouTube stores the channel NAME,
#: and a watch URL carries only a video id.
RULES = {
    "reddit": _reddit,
    "discourse": _discourse,
    "stackexchange": _stackexchange,
    "hackernews": _hackernews,
    "lemmy": _lemmy,
    "github": _github,
}


@dataclass
class Report:
    """What one pass could and could not identify."""

    #: platform -> community -> row count, for rows this pass would fill in.
    identified: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: platform -> reason -> row count, for rows it deliberately left alone.
    unidentified: Dict[str, Dict[str, int]] = field(default_factory=dict)
    applied: bool = False

    @property
    def n_identified(self) -> int:
        return sum(sum(v.values()) for v in self.identified.values())

    @property
    def n_unidentified(self) -> int:
        return sum(sum(v.values()) for v in self.unidentified.values())

    def _bump(self, bucket, platform, key, n=1):
        bucket.setdefault(platform, {}).setdefault(key, 0)
        bucket[platform][key] += n

    def lines(self) -> List[str]:
        """Human-readable, and honest about the half it could not do."""
        out = []
        verb = "identified" if self.applied else "would identify"
        out.append(f"{verb} {self.n_identified:,} nugget(s):")
        for platform in sorted(self.identified):
            for community, n in sorted(
                self.identified[platform].items(), key=lambda kv: -kv[1]
            ):
                out.append(f"  {platform:<14} {community:<32} {n:>6,}")
        if self.n_unidentified:
            out.append(f"still unidentified: {self.n_unidentified:,} nugget(s):")
            for platform in sorted(self.unidentified):
                for reason, n in sorted(
                    self.unidentified[platform].items(), key=lambda kv: -kv[1]
                ):
                    out.append(f"  {platform:<14} {reason:<32} {n:>6,}")
        return out


def _rows_missing_community(db):
    return db.execute(
        "SELECT id, platform, source_url FROM nuggets "
        "WHERE community IS NULL OR TRIM(community) = ''"
    ).fetchall()


def identify(db, apply: bool = False) -> Report:
    """Fill in `community` wherever the row's own source_url names it.

    Dry by default. `apply=True` writes, and writes ONLY the rows a rule
    resolved — a row whose rule declined is left exactly as it was, so running
    this twice is safe and running it on an archive it cannot help is a no-op.
    """
    report = Report(applied=apply)
    updates = []
    for row_id, platform, source_url in _rows_missing_community(db):
        platform = (platform or "").strip().lower()
        rule = RULES.get(platform)
        if rule is None:
            report._bump(report.unidentified, platform or "unknown",
                         "no rule for this platform")
            continue
        if not _is_web_url(source_url or ""):
            # Nothing to parse, and a row that never had a real origin should
            # keep saying so. The distinction below matters to whoever reads
            # the report: "mock" is not a source that failed to parse, it is
            # the fixture runs' marker — that text was written for a test and
            # was never scraped from anywhere.
            reason = ("fixture row (source_url = 'mock') — never scraped"
                      if (source_url or "").strip().lower() == "mock"
                      else "source_url is not a URL")
            report._bump(report.unidentified, platform, reason)
            continue
        got = rule(source_url)
        if got is None:
            report._bump(report.unidentified, platform,
                         "URL does not name a community")
            continue
        community, community_url = got
        report._bump(report.identified, platform, community)
        updates.append((community, community_url, row_id))

    if apply and updates:
        # `community` is the only one of the pair the nuggets table stores;
        # community_url lives on the fetched post and never had a column here.
        db.executemany(
            "UPDATE nuggets SET community = ? WHERE id = ?",
            [(community, row_id) for community, _url, row_id in updates],
        )
        db.commit()
    return report
