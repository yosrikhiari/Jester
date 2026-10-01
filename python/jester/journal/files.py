"""Article files: Markdown in a git repository of their own.

Each journal is `<root>/<slug>/index.md`. The root is its own git repository
-- `git init` on first use -- so every saved version is a commit and a diff
between two versions is `git diff`, not a feature to build.

Why its own repository and not Jester's: the default root is `data/journal`,
and `data/` is ignored by Jester's repository on purpose (it holds the live
archives). Drafts are private until you publish them, and a public repo is
the wrong place for a half-written opinion. Asking git for "the repository
this folder is in" would answer *Jester's*, and a commit there would either
fail on the ignore rule or, worse, succeed. So the root counts as initialised
only when it has its own `.git`.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import List

DEFAULT_ROOT = "data/journal"

#: Commit identity when git has none configured (a fresh CI runner). A real
#: machine's own `user.name`/`user.email` always wins.
_FALLBACK_IDENTITY = ("Jester journal", "journal@jester.localhost")


class GitError(Exception):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                             text=True, encoding="utf-8", check=False)
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    if out.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def ensure_repo(root: Path) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        _git(root, "init", "-q")
    return root


def _identity_args(root: Path) -> List[str]:
    try:
        name = _git(root, "config", "user.name")
        email = _git(root, "config", "user.email")
    except GitError:
        name = email = ""
    if name and email:
        return []
    return ["-c", f"user.name={_FALLBACK_IDENTITY[0]}", "-c", f"user.email={_FALLBACK_IDENTITY[1]}"]


def article_path(root: Path, slug: str) -> Path:
    return Path(root) / slug / "index.md"


def skeleton(title: str, kind: str) -> str:
    """The first version of a new article.

    No front matter: the database holds the metadata, and two copies of a
    title drift apart the first time one is edited. The site export (slice
    J3) writes front matter from the database when it publishes.
    """
    lines = [f"# {title}", ""]
    if kind in ("article", "benchmark"):
        lines += ["<!-- TL;DR: the headline number and what it means, in two sentences. -->", "",
                  "## Why I ran this", "", "## Setup", "", "## Results", "",
                  "## What failed or surprised me", "", "## Caveats", "", "## Reproduce it", ""]
    else:
        lines += ["<!-- What happened, what you learned, and the link that proves it. -->", ""]
    return "\n".join(lines)


def create_article(root: Path, slug: str, title: str, kind: str) -> Path:
    path = article_path(ensure_repo(root), slug)
    if path.exists():
        raise GitError(f"{path} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skeleton(title, kind), encoding="utf-8")
    return path


def content_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def commit(root: Path, path: Path, message: str) -> str:
    """Commit one article file; return the commit sha.

    If the file is identical to what git already has, there is nothing to
    commit and the current HEAD is the version -- the caller decides whether
    that counts as a new revision (it does not; see `cmd_journal save`).
    """
    root = ensure_repo(root)
    rel = Path(path).resolve().relative_to(root.resolve()).as_posix()
    _git(root, "add", "--", rel)
    if _git(root, "status", "--porcelain", "--", rel):
        _git(root, *_identity_args(root), "commit", "-q", "-m", message, "--", rel)
    return _git(root, "rev-parse", "HEAD")
