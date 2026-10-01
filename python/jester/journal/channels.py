"""Platform versions of a published article, and posting them.

The original lives on your site. Each platform gets a version that links
back to it, written to `<slug>/channels/<channel>.md` so you can read and
edit it before anything happens:

    channel    what Jester makes                       who posts it
    devto      the full article, proof links pointing  Jester, as an unpublished
               at the original's evidence section      DRAFT; you publish it there
    bluesky    title + opening + link, <= 300 chars    Jester, after your approval
    mastodon   title + opening + link, <= 500 chars    Jester, after your approval
    linkedin   a native post: opening + link last      you (no API for articles)
    hn         the original title and the link         you, once, never asking for votes
    reddit     title + body draft                      you, after reading the sub's rules
    medium     the link for Medium's import tool       you (API closed to new users)

A version may rephrase; it may not add a claim. The check re-runs on every
version: every number in it must appear in the original, it must fit the
platform, and it must pass the lint. Approving a version records the
fingerprint of the file you read; editing it afterwards blocks posting until
you approve again. Nothing is posted without that approval, and HN, Reddit,
LinkedIn and Medium are never posted by Jester at all.

Versions are built from templates, not by a model. Rephrasing with a model is
a later option; a template cannot invent a claim.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from jester import journal as J
from jester.journal import checks as C
from jester.journal import render as R
from jester.journal import text as T
from jester.journal.publish import PUBLISHED_STATES


@dataclass(frozen=True)
class Channel:
    name: str
    mode: str            # "draft" (API, unpublished), "post" (API), "manual"
    limit: Optional[int]
    unit: str = "chars"  # or "graphemes"


CHANNELS: Dict[str, Channel] = {c.name: c for c in (
    Channel("devto", "draft", None),
    Channel("bluesky", "post", 300, "graphemes"),
    Channel("mastodon", "post", 500),
    Channel("linkedin", "manual", 3000),
    Channel("hn", "manual", 80),
    Channel("reddit", "manual", 40000),
    Channel("medium", "manual", None),
)}

MANUAL_HELP = {
    "linkedin": "paste it as a post on LinkedIn (articles have no API)",
    "hn": "submit it at news.ycombinator.com/submit with the original title; once; never ask for votes",
    "reddit": "post it yourself after reading that subreddit's self-promotion rules",
    "medium": "use Medium's 'Import a story' with the original's URL (it sets the canonical link)",
}


def channel(name: str) -> Channel:
    if name not in CHANNELS:
        raise J.JournalError(f"channel must be one of {', '.join(CHANNELS)}")
    return CHANNELS[name]


def graphemes(text: str) -> int:
    """Close enough to what Bluesky counts: characters, without combining
    marks, joiners and variation selectors."""
    return sum(1 for ch in text if not unicodedata.combining(ch) and ch not in "‍︎️")


def length(text: str, ch: Channel) -> int:
    return graphemes(text) if ch.unit == "graphemes" else len(text)


# ---- building versions -------------------------------------------------------------

def _absolute(md: str, canonical: str) -> str:
    """Proof links and figures, pointed at the original."""
    def link(m):
        bang, label, target = m.group(1), m.group(2), m.group(3)
        ev = R.evidence_target(target)
        if ev:
            return f"[{label}]({canonical}{ev})"
        if bang and not target.startswith(("http://", "https://")):
            return f"![{label}]({canonical}{target})"
        return m.group(0)
    md = re.sub(r"<!--.*?-->\n?", "", md, flags=re.S)
    return re.sub(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?\s*\)", link, md)


def _fit(text: str, budget: int) -> str:
    """The longest run of whole sentences that fits, else whole words + an ellipsis."""
    if len(text) <= budget:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + (1 if out else 0) > budget:
            break
        out = f"{out} {s}".strip()
    if out:
        return out
    cut = text[: max(budget - 1, 0)].rsplit(" ", 1)[0]
    return cut + "…"


def build(j: sqlite3.Row, md: str, name: str) -> str:
    ch = channel(name)
    canonical = j["canonical_url"]
    title = T.title_of(md) or j["title"]
    opening = R.first_paragraph(md)
    if name == "devto":
        body = _absolute(md, canonical)
        body = re.sub(r"^\s{0,3}#\s+.*\n+", "", body, count=1)  # Dev.to shows the title itself
        return (f"{body.rstrip()}\n\n---\n\n*Originally published at [{canonical}]({canonical}), "
                f"where every number links to the run or source behind it.* {j['disclosure']}\n")
    if name in ("bluesky", "mastodon"):
        budget = ch.limit - len(title) - len(canonical) - 4
        return f"{title}\n\n{_fit(opening, budget)}\n\n{canonical}\n"
    if name == "linkedin":
        return f"{title}\n\n{_fit(opening, ch.limit - len(title) - len(canonical) - 40)}\n\n" \
               f"The full write-up, with the data: {canonical}\n"
    if name == "hn":
        return f"{title}\n{canonical}\n"
    if name == "reddit":
        return f"{title}\n\n{opening}\n\nWrite-up with the data and code: {canonical}\n"
    return f"{canonical}\n"  # medium: the import tool needs only the URL


def _numbers(text: str) -> List[str]:
    masked = T.mask(text)
    return [re.sub(r"\s+", "", tok) for _s, _e, tok in C._figures(masked)]


def check_version(md: str, text: str, name: str, canonical: str) -> List[str]:
    """Why this version may not be approved. Empty = it may."""
    ch = channel(name)
    problems = []
    if ch.limit is not None and length(text, ch) > ch.limit:
        problems.append(f"{length(text, ch)} {ch.unit}; {name} allows {ch.limit}")
    original = set(_numbers(md))
    for tok in _numbers(text.replace(canonical, " ")):
        if tok not in original:
            problems.append(f"{tok} is not in the original; a version may not add a claim")
    if name in ("bluesky", "mastodon", "linkedin", "hn", "reddit", "medium") and canonical not in text:
        problems.append("the link to the original is missing")
    score, _findings, _ = C.lint_prose(text)
    if score >= C.FAIL_AT:
        problems.append(f"it reads as generated (lint score {score})")
    return problems


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def latest_publication(db: sqlite3.Connection, journal_id: int, name: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM publication WHERE journal_id = ? AND channel = ? ORDER BY id DESC LIMIT 1",
                      (journal_id, name)).fetchone()


def _require_published(j: sqlite3.Row) -> None:
    if j["state"] not in PUBLISHED_STATES or not j["canonical_url"]:
        raise J.JournalError("publish the original on your site first; every version links to it")


def make_version(db: sqlite3.Connection, root: Path, slug: str, name: str) -> Dict:
    """Write the version file and record it as a draft publication."""
    ch = channel(name)
    j = J.get(db, slug)
    _require_published(j)
    md = (Path(root) / j["path"]).read_text(encoding="utf-8")
    text = build(j, md, ch.name)
    rel = f"{Path(j['path']).parent.as_posix()}/channels/{ch.name}.md"
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    rev = J.latest_revision(db, j["id"])
    pub = J.add_publication(db, j["id"], rev["id"], ch.name, status="draft")
    db.execute("UPDATE publication SET body_path = ? WHERE id = ?", (rel, pub))
    db.commit()
    return {"path": path, "publication": pub, "problems": check_version(md, text, ch.name, j["canonical_url"])}


def approve_version(db: sqlite3.Connection, root: Path, slug: str, name: str, note: str = "") -> sqlite3.Row:
    ch = channel(name)
    j = J.get(db, slug)
    pub = latest_publication(db, j["id"], ch.name)
    if pub is None or not pub["body_path"]:
        raise J.JournalError(f"no {name} version yet (journal variant {slug} {name})")
    if pub["status"] not in ("draft", "approved"):
        raise J.JournalError(f"the {name} version is already {pub['status']}")
    path = Path(root) / pub["body_path"]
    md = (Path(root) / j["path"]).read_text(encoding="utf-8")
    problems = check_version(md, path.read_text(encoding="utf-8"), ch.name, j["canonical_url"])
    if problems:
        raise J.JournalError(f"the {name} version fails its check: " + "; ".join(problems))
    db.execute("UPDATE publication SET status = 'approved', approved_sha256 = ? WHERE id = ?",
               (_sha(path), pub["id"]))
    db.commit()
    J.approve(db, j["id"], "post", actor=J.HUMAN, note=note, publication_id=pub["id"])
    return latest_publication(db, j["id"], ch.name)


# ---- posting -------------------------------------------------------------------------

def _request(method: str, url: str, payload: Optional[Dict], headers: Dict[str, str],
             opener: Callable) -> Dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          "User-Agent": "jester-journal/1.0", **headers})
    try:
        with opener(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace") if hasattr(exc, "read") else ""
        raise J.JournalError(f"{url} answered HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise J.JournalError(f"could not reach {url}: {getattr(exc, 'reason', exc)}") from exc
    return json.loads(body or b"{}")


def _need(env: Mapping[str, str], *names: str) -> List[str]:
    missing = [n for n in names if not env.get(n)]
    if missing:
        raise J.JournalError(f"add {', '.join(missing)} to .env first")
    return [env[n] for n in names]


def _post_devto(j, md_title: str, text: str, env, opener) -> Dict:
    (key,) = _need(env, "DEVTO_API_KEY")
    got = _request("POST", "https://dev.to/api/articles",
                   {"article": {"title": md_title, "body_markdown": text, "published": False,
                                "canonical_url": j["canonical_url"]}},
                   {"api-key": key}, opener)
    return {"status": "sent", "external_id": str(got.get("id", "")), "url": got.get("url", "")}


def _post_bluesky(text: str, env, opener, now: str) -> Dict:
    handle, password = _need(env, "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD")
    pds = env.get("BLUESKY_PDS") or "https://bsky.social"
    session = _request("POST", f"{pds}/xrpc/com.atproto.server.createSession",
                       {"identifier": handle, "password": password}, {}, opener)
    record = {"$type": "app.bsky.feed.post", "text": text.strip(), "createdAt": now}
    m = re.search(r"https?://\S+", record["text"])
    if m:
        start = len(record["text"][:m.start()].encode("utf-8"))
        record["facets"] = [{"index": {"byteStart": start, "byteEnd": start + len(m.group(0).encode("utf-8"))},
                             "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group(0)}]}]
    got = _request("POST", f"{pds}/xrpc/com.atproto.repo.createRecord",
                   {"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
                   {"Authorization": f"Bearer {session['accessJwt']}"}, opener)
    rkey = str(got.get("uri", "")).rsplit("/", 1)[-1]
    return {"status": "posted", "external_id": got.get("uri", ""),
            "url": f"https://bsky.app/profile/{handle}/post/{rkey}"}


def _post_mastodon(text: str, env, opener, pub_id: int) -> Dict:
    instance, token = _need(env, "MASTODON_INSTANCE", "MASTODON_TOKEN")
    got = _request("POST", f"{instance.rstrip('/')}/api/v1/statuses",
                   {"status": text.strip(), "visibility": "public"},
                   {"Authorization": f"Bearer {token}", "Idempotency-Key": f"jester-journal-{pub_id}"}, opener)
    return {"status": "posted", "external_id": str(got.get("id", "")), "url": got.get("url", "")}


def post(db: sqlite3.Connection, root: Path, slug: str, name: str, opener: Optional[Callable] = None,
         env: Optional[Mapping[str, str]] = None, now: Optional[str] = None) -> sqlite3.Row:
    """Send an approved version. Refuses anything you have not approved as-is."""
    ch = channel(name)
    j = J.get(db, slug)
    _require_published(j)
    if ch.mode == "manual":
        raise J.JournalError(f"Jester does not post to {name}: {MANUAL_HELP[name]}; then record it with "
                             f"journal posted {slug} {name} --url <link>")
    pub = latest_publication(db, j["id"], ch.name)
    if pub is None or pub["status"] != "approved":
        raise J.JournalError(f"the {name} version is not approved (journal approve {slug} post --channel {name})")
    if pub["id"] not in {a["publication_id"] for a in J.human_approvals(db, j["id"], "post")}:
        raise J.JournalError(f"the {name} version has no approval from you")
    path = Path(root) / pub["body_path"]
    if not path.exists() or _sha(path) != pub["approved_sha256"]:
        raise J.JournalError(f"the {name} version changed after you approved it; approve it again")
    text = path.read_text(encoding="utf-8")
    opener = opener or urllib.request.urlopen
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    md = (Path(root) / j["path"]).read_text(encoding="utf-8")
    if name == "devto":
        got = _post_devto(j, T.title_of(md) or j["title"], text, env, opener)
    elif name == "bluesky":
        got = _post_bluesky(text, env, opener, now)
    else:
        got = _post_mastodon(text, env, opener, pub["id"])
    posted_at = J.now_utc() if got["status"] == "posted" else ""
    db.execute("UPDATE publication SET status = ?, url = ?, external_id = ?, posted_at = ? WHERE id = ?",
               (got["status"], got["url"], got["external_id"], posted_at, pub["id"]))
    db.commit()
    return latest_publication(db, j["id"], ch.name)


def record_posted(db: sqlite3.Connection, slug: str, name: str, url: str,
                  at: Optional[str] = None) -> sqlite3.Row:
    """You posted it yourself (or published the Dev.to draft): record where.

    For a channel Jester never posts to, recording it IS your decision, so it
    is stored as your approval too.
    """
    ch = channel(name)
    j = J.get(db, slug)
    _require_published(j)
    if not url.startswith(("http://", "https://")):
        raise J.JournalError("give the post's https link")
    at = at or J.now_utc()
    pub = latest_publication(db, j["id"], ch.name)
    if ch.mode != "manual" and (pub is None or pub["status"] not in ("sent", "posted")):
        raise J.JournalError(f"send the {name} version first (journal post {slug} {name})")
    if pub is None or pub["status"] == "posted" and ch.mode == "manual":
        rev = J.latest_revision(db, j["id"])
        pid = J.add_publication(db, j["id"], rev["id"], ch.name, status="draft")
    else:
        pid = pub["id"]
    db.execute("UPDATE publication SET status = 'posted', url = ?, posted_at = ? WHERE id = ?", (url, at, pid))
    db.commit()
    if ch.mode == "manual":
        J.approve(db, j["id"], "post", actor=J.HUMAN, note=f"posted by hand: {url}", publication_id=pid)
    return db.execute("SELECT * FROM publication WHERE id = ?", (pid,)).fetchone()
