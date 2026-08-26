"""Curated source list: parse, validate, and read/write ``sources.yaml``.

Both the operator console (§37.29) and the Go ingestion worker read this file,
so every entry carries the two facts the worker needs and the old two-field
shape could not express:

``kind``    how to reach it — a subreddit is a listing to walk, a thread is a
            page to read, a channel is a listing of videos, a video is a page.
``enabled`` park a source without losing the entry.

Legacy entries (``platform``/``name``/``url`` only) still load: the kind is
inferred from the URL and ``enabled`` defaults to true.
"""
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Optional

import yaml


class SourceError(ValueError):
    """A source string that cannot be turned into something fetchable."""


# Every kind the config file understands, per platform.
PLATFORM_KINDS = {
    "reddit": ("subreddit", "thread"),
    "hackernews": ("feed", "story"),
    "discourse": ("forum", "topic"),
    "youtube": ("channel", "video"),
    "tiktok": ("profile", "video", "hashtag"),
    # 170+ sites of "this does not work and here is exactly how", behind a
    # documented keyless API. `site` walks recently-active questions;
    # `question` pins one.
    "stackexchange": ("site", "question"),
    # An issue tracker is a database of things that are broken, with
    # reproduction steps. `repo` walks the busiest issues; `issue` pins one.
    "github": ("repo", "issue"),
}

# Kinds the Go ingestion worker can actually fetch today (go/cmd/worker).
# Anything outside this set is still stored and shown — labelled honestly as
# not-yet-ingested rather than silently skipped (R29 spirit).
SUPPORTED_KINDS = frozenset({
    ("reddit", "subreddit"),
    ("reddit", "thread"),
    ("hackernews", "feed"),
    ("hackernews", "story"),
    ("discourse", "forum"),
    ("discourse", "topic"),
    ("youtube", "channel"),
    ("youtube", "video"),
    ("stackexchange", "site"),
    ("stackexchange", "question"),
    ("github", "repo"),
    ("github", "issue"),
})

PLATFORMS = tuple(PLATFORM_KINDS)


@dataclass
class Source:
    """One curated channel to ingest."""

    name: str
    platform: str
    url: str
    kind: str = ""
    enabled: bool = True
    notes: str = ""

    @property
    def supported(self) -> bool:
        return (self.platform, self.kind) in SUPPORTED_KINDS

    def to_dict(self) -> dict:
        d = asdict(self)
        d["supported"] = self.supported
        return d


# ---- parsing ---------------------------------------------------------------

_RE_REDDIT_THREAD = re.compile(r"reddit\.com/r/([A-Za-z0-9_]{2,21})/comments/([A-Za-z0-9]+)", re.I)
_RE_REDDIT_SUB = re.compile(r"reddit\.com/r/([A-Za-z0-9_]{2,21})", re.I)
_RE_SUB_SHORT = re.compile(r"^/?r/([A-Za-z0-9_]{2,21})/?$", re.I)

_RE_YT_VIDEO = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.I,
)
_RE_YT_CHANNEL = re.compile(
    r"youtube\.com/(?:(@[A-Za-z0-9_.\-]{3,30})|channel/([A-Za-z0-9_-]{6,})"
    r"|c/([A-Za-z0-9_.\-]+)|user/([A-Za-z0-9_.\-]+))",
    re.I,
)

_RE_TT_VIDEO = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})/video/(\d{6,})", re.I)
_RE_TT_PROFILE = re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{2,30})", re.I)
_RE_TT_TAG = re.compile(r"tiktok\.com/tag/([A-Za-z0-9_]{2,60})", re.I)

_RE_HANDLE = re.compile(r"^/?@([A-Za-z0-9_.\-]{2,30})$")
_RE_HASHTAG = re.compile(r"^#([A-Za-z0-9_]{2,60})$")
_RE_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG_RE.sub("-", str(text).strip().lower()).strip("-") or "source"


def _detect_platform(raw: str) -> str:
    low = raw.lower()
    if "reddit.com" in low or _RE_SUB_SHORT.match(raw):
        return "reddit"
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "tiktok.com" in low:
        return "tiktok"
    if "news.ycombinator.com" in low:
        return "hackernews"
    if "github.com" in low:
        return "github"
    if (
        "stackexchange.com" in low
        or "stackoverflow.com" in low
        or "serverfault.com" in low
        or "superuser.com" in low
        or "askubuntu.com" in low
    ):
        return "stackexchange"
    # Discourse is NOT auto-detected from a bare host: it runs on arbitrary
    # domains, so guessing would claim every unknown URL. Pick it explicitly.
    raise SourceError(
        "cannot tell which platform %r belongs to — pick one explicitly (%s) "
        "or paste the full URL" % (raw, ", ".join(PLATFORMS))
    )


def _parse_reddit(raw: str):
    m = _RE_REDDIT_THREAD.search(raw)
    if m:
        sub, tid = m.group(1), m.group(2)
        return "thread", "https://www.reddit.com/r/%s/comments/%s/" % (sub, tid), "%s-%s" % (sub.lower(), tid)
    m = _RE_REDDIT_SUB.search(raw) or _RE_SUB_SHORT.match(raw)
    if m:
        sub = m.group(1)
        return "subreddit", "https://www.reddit.com/r/%s/" % sub, sub.lower()
    if re.fullmatch(r"[A-Za-z0-9_]{2,21}", raw):  # bare subreddit name
        return "subreddit", "https://www.reddit.com/r/%s/" % raw, raw.lower()
    raise SourceError(
        "%r is not a subreddit or thread — try 'r/selfhosted' or a "
        "https://www.reddit.com/r/.../comments/... link" % raw
    )


def _channel_path(match) -> str:
    handle, channel_id, c_name, user = match.groups()
    if handle:
        return handle
    if channel_id:
        return "channel/%s" % channel_id
    if c_name:
        return "c/%s" % c_name
    return "user/%s" % user


def _parse_youtube(raw: str):
    m = _RE_YT_VIDEO.search(raw)
    if m:
        vid = m.group(1)
        return "video", "https://www.youtube.com/watch?v=%s" % vid, vid.lower()
    m = _RE_YT_CHANNEL.search(raw)
    if m:
        path = _channel_path(m)
        return "channel", "https://www.youtube.com/%s" % path, slugify(path.split("/")[-1])
    m = _RE_HANDLE.match(raw)
    if m:
        return "channel", "https://www.youtube.com/@%s" % m.group(1), slugify(m.group(1))
    if _RE_YT_ID.match(raw):
        return "video", "https://www.youtube.com/watch?v=%s" % raw, raw.lower()
    raise SourceError(
        "%r is not a YouTube channel or video — try '@fosdem', a /watch?v=... "
        "link, or a youtu.be/... link" % raw
    )


def _parse_tiktok(raw: str):
    m = _RE_TT_VIDEO.search(raw)
    if m:
        user, vid = m.group(1), m.group(2)
        return "video", "https://www.tiktok.com/@%s/video/%s" % (user, vid), "%s-%s" % (slugify(user), vid)
    m = _RE_TT_TAG.search(raw)
    if m:
        return "hashtag", "https://www.tiktok.com/tag/%s" % m.group(1), slugify(m.group(1))
    m = _RE_TT_PROFILE.search(raw)
    if m:
        return "profile", "https://www.tiktok.com/@%s" % m.group(1), slugify(m.group(1))
    m = _RE_HANDLE.match(raw)
    if m:
        return "profile", "https://www.tiktok.com/@%s" % m.group(1), slugify(m.group(1))
    m = _RE_HASHTAG.match(raw)
    if m:
        return "hashtag", "https://www.tiktok.com/tag/%s" % m.group(1), slugify(m.group(1))
    raise SourceError(
        "%r is not a TikTok profile, video or hashtag — try '@indiehackers', "
        "'#buildinpublic', or a /@user/video/... link" % raw
    )


_RE_HN_ITEM = re.compile(r"news\.ycombinator\.com/item\?id=(\d+)", re.I)
_RE_HN_ID = re.compile(r"^(?:hn:)?(\d{5,})$", re.I)
# Feeds worth curating. "ask" is the default: a question about a problem is a
# better pain signal than commentary on a link.
_HN_FEEDS = {
    "ask": ("https://news.ycombinator.com/ask", "hn-ask"),
    "ask_hn": ("https://news.ycombinator.com/ask", "hn-ask"),
    "show": ("https://news.ycombinator.com/show", "hn-show"),
    "show_hn": ("https://news.ycombinator.com/show", "hn-show"),
    "front": ("https://news.ycombinator.com/news", "hn-front"),
    "news": ("https://news.ycombinator.com/news", "hn-front"),
    "hn": ("https://news.ycombinator.com/ask", "hn-ask"),
}

_RE_DISCOURSE_TOPIC = re.compile(
    r"^(https?://[^/]+)/t/(?:([A-Za-z0-9\-_%]+)/)?(\d+)", re.I
)
_RE_HOST = re.compile(r"^(?:https?://)?([A-Za-z0-9.\-]+\.[A-Za-z]{2,})(?:[/:].*)?$")


def _parse_hackernews(raw: str):
    m = _RE_HN_ITEM.search(raw)
    if m:
        sid = m.group(1)
        return "story", "https://news.ycombinator.com/item?id=%s" % sid, "hn-%s" % sid
    m = _RE_HN_ID.match(raw)
    if m:
        sid = m.group(1)
        return "story", "https://news.ycombinator.com/item?id=%s" % sid, "hn-%s" % sid
    key = raw.strip().lower().lstrip("/").replace("news.ycombinator.com/", "")
    key = key.split("?")[0].strip("/")
    if key in _HN_FEEDS:
        url, name = _HN_FEEDS[key]
        return "feed", url, name
    if "news.ycombinator.com" in raw.lower():
        return "feed", _HN_FEEDS["ask"][0], _HN_FEEDS["ask"][1]
    raise SourceError(
        "%r is not a Hacker News feed or item — try 'ask', 'show', or a "
        "https://news.ycombinator.com/item?id=... link" % raw
    )


def _parse_discourse(raw: str):
    """Discourse runs on arbitrary domains, so any host is a candidate forum.

    A /t/<slug>/<id> path is a single topic; anything else is the forum root.
    """
    m = _RE_DISCOURSE_TOPIC.match(raw.strip())
    if m:
        base, slug, tid = m.group(1), m.group(2) or "", m.group(3)
        url = "%s/t/%s/%s" % (base.rstrip("/"), slug, tid) if slug else \
              "%s/t/%s" % (base.rstrip("/"), tid)
        host = _RE_HOST.match(base).group(1)
        return "topic", url, "%s-%s" % (slugify(host.split(".")[0]), tid)
    m = _RE_HOST.match(raw.strip())
    if m:
        host = m.group(1)
        scheme = "https://"
        if raw.strip().lower().startswith("http://"):
            scheme = "http://"
        return "forum", scheme + host, slugify(host)
    raise SourceError(
        "%r is not a Discourse forum — paste the forum's root URL "
        "(e.g. community.home-assistant.io) or a /t/<slug>/<id> topic link" % raw
    )


_RE_SE_QUESTION = re.compile(
    r"^https?://([A-Za-z0-9.\-]+)/questions/(\d+)", re.I
)
#: Sites that live on their own domain rather than *.stackexchange.com.
_SE_OWN_DOMAIN = ("stackoverflow", "serverfault", "superuser", "askubuntu", "mathoverflow")


def _se_host(slug: str) -> str:
    return f"{slug}.com" if slug in _SE_OWN_DOMAIN else f"{slug}.stackexchange.com"


def _se_slug(host: str) -> str:
    host = host.lower().strip()
    if host.endswith(".stackexchange.com"):
        return host[: -len(".stackexchange.com")]
    return host.split(".")[0]


def _parse_stackexchange(raw: str):
    """A whole site to walk, or one question to pin."""
    m = _RE_SE_QUESTION.match(raw.strip())
    if m:
        slug = _se_slug(m.group(1))
        qid = m.group(2)
        return (
            "question",
            f"https://{_se_host(slug)}/questions/{qid}",
            f"{slugify(slug)}-{qid}",
        )
    text = raw.strip().lower()
    text = text.replace("https://", "").replace("http://", "").strip("/")
    host = text.split("/")[0]
    if not host:
        raise SourceError("a Stack Exchange source needs a site, e.g. 'serverfault'")
    slug = _se_slug(host) if "." in host else host
    if not re.fullmatch(r"[a-z0-9\-]{2,40}", slug):
        raise SourceError(
            "%r is not a Stack Exchange site — try 'serverfault', "
            "'unix.stackexchange.com', or a /questions/<id> link" % raw
        )
    return "site", f"https://{_se_host(slug)}", slugify(slug)


_RE_GH_ISSUE = re.compile(
    r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/issues/(\d+)", re.I
)
_RE_GH_REPO = re.compile(
    r"(?:github\.com/)?([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)/?$", re.I
)


def _parse_github(raw: str):
    """A repo to walk, or one issue to pin."""
    text = raw.strip().rstrip("/")
    m = _RE_GH_ISSUE.search(text)
    if m:
        owner, repo, num = m.group(1), m.group(2), m.group(3)
        return (
            "issue",
            f"https://github.com/{owner}/{repo}/issues/{num}",
            f"{slugify(owner)}-{slugify(repo)}-{num}",
        )
    stripped = text.replace("https://", "").replace("http://", "")
    stripped = stripped[len("github.com/"):] if stripped.lower().startswith("github.com/") else stripped
    m = _RE_GH_REPO.match(stripped)
    if m:
        owner, repo = m.group(1), m.group(2)
        return (
            "repo",
            f"https://github.com/{owner}/{repo}",
            f"{slugify(owner)}-{slugify(repo)}",
        )
    raise SourceError(
        "%r is not a GitHub repo or issue — try 'rclone/rclone' or a "
        "https://github.com/<owner>/<repo>/issues/<n> link" % raw
    )


_PARSERS = {
    "reddit": _parse_reddit,
    "hackernews": _parse_hackernews,
    "discourse": _parse_discourse,
    "youtube": _parse_youtube,
    "tiktok": _parse_tiktok,
    "stackexchange": _parse_stackexchange,
    "github": _parse_github,
}


def parse_source(
    raw: str,
    platform: str = "auto",
    *,
    name: Optional[str] = None,
    enabled: bool = True,
    notes: str = "",
) -> Source:
    """Turn a pasted URL/handle into a canonical :class:`Source`.

    ``platform="auto"`` infers from the URL. An explicit platform that
    disagrees with the URL is an error, not a silent re-label.
    """
    raw = (raw or "").strip()
    if not raw:
        raise SourceError("a source needs a URL or handle")
    platform = (platform or "auto").strip().lower()

    detected = None
    if "://" in raw or "." in raw.split("/")[0] or _RE_SUB_SHORT.match(raw):
        try:
            detected = _detect_platform(raw)
        except SourceError:
            detected = None

    if platform in ("", "auto"):
        platform = detected or _detect_platform(raw)
    if platform not in _PARSERS:
        raise SourceError("unknown platform %r; valid: %s" % (platform, ", ".join(PLATFORMS)))
    if detected and detected != platform:
        raise SourceError("that link is a %s link, but %s was selected" % (detected, platform))

    kind, url, derived = _PARSERS[platform](raw)
    return Source(
        name=slugify(name) if name else derived,
        platform=platform,
        url=url,
        kind=kind,
        enabled=bool(enabled),
        notes=str(notes or "").strip(),
    )


def infer_kind(platform: str, url: str) -> str:
    """Best-effort kind for a legacy entry that predates the ``kind`` field."""
    parser = _PARSERS.get((platform or "").lower())
    if not parser:
        return ""
    try:
        return parser(url or "")[0]
    except SourceError:
        return ""


# ---- collection operations -------------------------------------------------


def unique_name(sources: Iterable[Source], desired: str, *, skip: Optional[str] = None) -> str:
    """``desired``, or ``desired-2``, ``desired-3``… when already taken."""
    taken = {s.name for s in sources if s.name != skip}
    if desired not in taken:
        return desired
    for i in range(2, 1000):
        candidate = "%s-%d" % (desired, i)
        if candidate not in taken:
            return candidate
    raise SourceError("too many sources named %r" % desired)


def find(sources: Iterable[Source], name: str) -> Source:
    for s in sources:
        if s.name == name:
            return s
    raise SourceError("no source named %r" % name)


# ---- persistence -----------------------------------------------------------

def _header() -> str:
    """Generated, not hand-written: a literal kind list went stale the moment a
    platform was added, and the console rewrites this file on every edit."""
    kinds = " - ".join(
        "%s=%s" % (platform, "|".join(kinds)) for platform, kinds in PLATFORM_KINDS.items()
    )
    return (
        "# Curated source list. Edit here or in the operator console (Sources tab).\n"
        "# kind: %s\n"
        "# Set `enabled: false` to park a source without losing it; `notes` is\n"
        "# free text and survives console edits.\n" % kinds
    )


def load_sources(path) -> List[Source]:
    path = Path(path)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):
        data = data.get("sources") or []
    out = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        platform = str(raw.get("platform", "")).strip().lower()
        url = str(raw.get("url", "")).strip()
        kind = str(raw.get("kind", "") or "").strip().lower()
        if kind not in PLATFORM_KINDS.get(platform, ()):
            kind = infer_kind(platform, url)
        out.append(Source(
            name=str(raw.get("name", "")).strip(),
            platform=platform,
            url=url,
            kind=kind,
            enabled=raw.get("enabled", True) is not False,
            notes=str(raw.get("notes", "") or ""),
        ))
    return out


def save_sources(path, sources: Iterable[Source]) -> None:
    """Atomic write: a crashed console never leaves a half-written config."""
    path = Path(path)
    rows = []
    for s in sources:
        row = {
            "platform": s.platform,
            "name": s.name,
            "kind": s.kind,
            "url": s.url,
            "enabled": bool(s.enabled),
        }
        if s.notes:
            row["notes"] = s.notes
        rows.append(row)
    body = _header() + yaml.safe_dump(
        {"sources": rows}, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".sources-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
