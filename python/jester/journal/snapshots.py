"""Saved copies of cited pages.

A citation is only as good as the page behind it, and pages move, die and
change. So citing a URL means fetching it once and keeping what it said:
the HTTP status, where it ended up after redirects, and its text. The
citation check then runs against the saved copy -- offline, the same answer
every time -- and a page that later disappears still has its words on file.

Research tools invent URLs at a measured rate (a 2026 study found 3-13% of
deep-research links hallucinated and 5-18% not resolving), so a URL nobody
has fetched is unverified, whoever wrote it. That is why this module is the
only place in the journal that touches the network, and only when asked
(`jester journal snapshot`).

Pages that are not HTML or text (a PDF, an image) get their status saved but
no text: a quote from one cannot be checked automatically. `--from-file`
stores text you extracted yourself, marked `manual`, so the record says a
person vouched for that copy.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

from jester import journal as J

USER_AGENT = "jester-journal/1.0 (saving a copy of a page cited in an article)"
MAX_BYTES = 5_000_000
SNAPSHOT_DIR = "_snapshots"
_TEXT_TYPES = ("text/plain", "text/markdown", "application/json", "text/csv")


def strip_fragment(url: str) -> str:
    return url.split("#", 1)[0]


class _TextOfHtml(HTMLParser):
    """The words a reader sees: no scripts, styles or markup."""

    _SKIP = {"script", "style", "noscript", "svg", "template", "head"}
    _BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "blockquote", "pre", "table", "ul", "ol", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextOfHtml()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _decode(body: bytes, content_type: str) -> str:
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    try:
        return body.decode(m.group(1) if m else "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def readable_text(body: bytes, content_type: str) -> Optional[str]:
    """Text a quote can be matched against, or None if the type has none."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in ("text/html", "application/xhtml+xml"):
        return html_to_text(_decode(body, content_type))
    if ctype in _TEXT_TYPES:
        return _decode(body, content_type)
    return None


def fetch(url: str, opener: Optional[Callable] = None, timeout: float = 20.0) -> dict:
    """One polite GET. Never raises for a network problem: a dead link is a
    result to record, not an error to crash on."""
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5"})
    try:
        with opener(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode() or 200
            return {"status": status, "final_url": resp.geturl() if hasattr(resp, "geturl") else url,
                    "content_type": resp.headers.get("Content-Type", "") if resp.headers else "",
                    "body": resp.read(MAX_BYTES), "error": ""}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "final_url": url, "content_type": "", "body": b"",
                "error": f"HTTP {exc.code} {exc.reason}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return {"status": 0, "final_url": url, "content_type": "", "body": b"",
                "error": f"could not reach it: {reason}"}


def _store_text(root: Path, url: str, text: str, at: str) -> tuple:
    folder = Path(root) / SNAPSHOT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    stamp = re.sub(r"[^0-9]", "", at)[:14]
    path = folder / f"{stem}-{stamp}.txt"
    path.write_text(text, encoding="utf-8")
    return (path.relative_to(root).as_posix(), hashlib.sha256(text.encode("utf-8")).hexdigest())


def take(db: sqlite3.Connection, root: Path, url: str, opener: Optional[Callable] = None,
         from_file: Optional[Path] = None, at: Optional[str] = None) -> sqlite3.Row:
    """Fetch (or read from a file) one URL and record the snapshot."""
    url = strip_fragment(url)
    at = at or J.now_utc()
    if from_file is not None:
        text = Path(from_file).read_text(encoding="utf-8", errors="replace")
        got = {"status": 200, "final_url": url, "content_type": "text/plain", "error": ""}
        method = "manual"
    else:
        got = fetch(url, opener=opener)
        text = readable_text(got["body"], got["content_type"]) if 200 <= got["status"] < 300 else None
        method = "fetch"
    text_path, text_sha = _store_text(root, url, text, at) if text is not None else ("", "")
    cur = db.execute(
        "INSERT INTO snapshot (url, final_url, status, content_type, method, error, text_path, "
        "text_sha256, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (url, got["final_url"], got["status"], got["content_type"], method, got["error"],
         text_path, text_sha, at))
    db.commit()
    return db.execute("SELECT * FROM snapshot WHERE id = ?", (cur.lastrowid,)).fetchone()


def latest(db: sqlite3.Connection, url: str) -> Optional[sqlite3.Row]:
    return db.execute("SELECT * FROM snapshot WHERE url = ? ORDER BY id DESC LIMIT 1",
                      (strip_fragment(url),)).fetchone()


def is_ok(row: Optional[sqlite3.Row]) -> bool:
    return row is not None and 200 <= row["status"] < 300


def read_text(root: Path, row: Optional[sqlite3.Row]) -> Optional[str]:
    if row is None or not row["text_path"]:
        return None
    path = Path(root) / row["text_path"]
    return path.read_text(encoding="utf-8") if path.exists() else None


def describe(row: Optional[sqlite3.Row]) -> str:
    """Why a snapshot cannot back anything, in words."""
    if row is None:
        return "no saved copy yet (jester journal snapshot)"
    if row["status"] == 0:
        return row["error"] or "could not reach it"
    if not 200 <= row["status"] < 300:
        return f"the link is dead (HTTP {row['status']})"
    if not row["text_path"]:
        return (f"the saved copy has no readable text ({row['content_type'] or 'unknown type'}); "
                "add one with --from-file")
    return "ok"
