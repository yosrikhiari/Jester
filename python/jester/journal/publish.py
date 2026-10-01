"""Publishing the canonical article: a static page on your own site.

The original lives on your site (`/journal/<slug>/`); every other platform
gets a copy that links back. Exporting writes:

    journal/<slug>/index.html   the article, plus an Evidence section: the
                                plan, every run with its machine and code,
                                every source with the date its copy was
                                saved, every observation, and how AI was used
    journal/<slug>/figures/     the figures Jester drew from runs
    journal/index.html          every published article, newest first
    journal/feed.xml            an Atom feed of the same
    journal/journal.css         the page style (the site's own fonts)

It never touches your working copy of the site. The export goes into a git
worktree of the site repository (`<journal root>/_site/<slug>`), on a branch
`journal/<slug>` cut from the site's remote default branch, and is committed
there. Pushing it and opening the pull request is a separate, explicit step
(`--push`); merging that pull request is the act of publishing.

Refused unless the article is `ready` (or already published, for a fix), the
latest version passed every check, and you approved publishing exactly that
version.
"""

from __future__ import annotations

import html
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set

from jester import journal as J
from jester.journal import render as R
from jester.journal import snapshots as S
from jester.journal import text as T

DEFAULT_BASE_URL = "https://yosrikhiari.vercel.app"
SITE_DIR = "_site"
PUBLISHED_STATES = ("published", "shared", "measured")


def canonical_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/journal/{slug}/"


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                         encoding="utf-8", check=False)
    if out.returncode != 0:
        raise J.JournalError(f"git {' '.join(args)}: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


# ---- preconditions ----------------------------------------------------------------

def ready_to_export(db: sqlite3.Connection, root: Path, j: sqlite3.Row) -> List[str]:
    """Why this article cannot be exported now. Empty = it can."""
    from jester.journal import files as F

    out = []
    if j["state"] not in ("ready",) + PUBLISHED_STATES:
        out.append(f"it is {j['state']}; it must be ready (reviewed and approved) first")
    rev = J.latest_revision(db, j["id"])
    if rev is None:
        return out + ["nothing saved yet"]
    path = Path(root) / j["path"]
    if not path.exists():
        out.append(f"the article file is missing: {path}")
    elif F.content_sha256(path) != rev["content_sha256"]:
        out.append("the file has unsaved changes")
    checks = J.latest_checks(db, rev["id"])
    failing = [k for k in J.REQUIRED_CHECKS if not checks.get(k)]
    if failing:
        out.append(f"version {rev['n']} has not passed: {', '.join(failing)} (journal check)")
    approved = {a["revision_id"] for a in J.human_approvals(db, j["id"], "publish")}
    if rev["id"] not in approved:
        out.append(f"you have not approved publishing version {rev['n']} (approve publish)")
    if not j["disclosure"].strip():
        out.append("say how AI was used in it (set --disclosure)")
    return out


# ---- the evidence section ----------------------------------------------------------

def _referenced(md: str) -> Dict[str, Set[str]]:
    runs, sources, obs = set(), set(), set()
    for b in T.prose_blocks(md):
        for ln in T.links(b.text):
            if ln.target.startswith("run:"):
                runs.add(ln.target[4:].partition("#")[0])
            elif ln.target.startswith("obs:"):
                obs.add(ln.target[4:])
            elif ln.external and not ln.image:
                sources.add(S.strip_fragment(ln.target))
        for t in (b.backing or "").split(","):
            t = t.strip()
            if t.startswith("run:"):
                runs.add(t[4:].partition("#")[0])
    return {"runs": runs, "sources": sources, "obs": obs}


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _run_html(db, run_id: str, journal_id: int) -> str:
    row = db.execute(
        "SELECT r.*, e.id AS exp_id FROM experiment_run r JOIN experiment e ON e.id = r.experiment_id "
        "WHERE r.id = ? AND e.journal_id = ?", (int(run_id), journal_id)).fetchone() if run_id.isdigit() else None
    if row is None:
        return ""
    env = json.loads(row["env"] or "{}")
    code = env.get("code") or {}
    facts = [("status", row["status"]), ("source", row["source"]), ("command", row["command"]),
             ("started", row["started_at"]), ("finished", row["finished_at"]),
             ("duration", f"{row['duration_s']} s" if row["duration_s"] is not None else ""),
             ("os", env.get("os")), ("python", env.get("python")),
             ("gpu", "; ".join(env.get("gpu") or [])), ("seed", env.get("seed")),
             ("code", f"{code.get('commit')}{' (uncommitted changes)' if code.get('dirty') else ''}"
              if code.get("commit") else ""),
             ("ledger", f"{env.get('ledger')} run {env.get('ledger_run')}" if env.get("ledger") else "")]
    items = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in facts if v)
    note = '<p class="warn">This run happened before the plan was written, so it cannot confirm the plan; ' \
           'it is shown as data.</p>' if env.get("before_plan") else ""
    return (f'<section class="run" id="evidence-run-{row["id"]}"><h3>Run {row["id"]} '
            f'<small>experiment {row["exp_id"]}</small></h3>{note}<dl>{items}</dl></section>')


def _plan_html(db, journal_id: int) -> str:
    out = []
    for e in J.experiments(db, journal_id):
        facts = [("hypothesis", e["hypothesis"]), ("metric", e["metric"]),
                 ("counts as yes", e["threshold"]), ("baseline", e["baseline"]),
                 ("planned runs", e["n_planned"]), ("plan locked", e["locked_at"])]
        items = "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in facts if v not in ("", None))
        threats = json.loads(e["threats"] or "[]")
        if threats:
            items += "<dt>known threats</dt><dd>" + "; ".join(_esc(t) for t in threats) + "</dd>"
        changes = json.loads(e["changes_after_lock"] or "[]")
        moved = "".join(f"<li>{_esc(c['field'])}: {_esc(c['was'])} → {_esc(c['now'])} "
                        f"({_esc(c['at'])})</li>" for c in changes)
        moved_html = f"<p>Changed after the plan was locked:</p><ul>{moved}</ul>" if moved else \
            "<p>The plan was not changed after it was locked.</p>" if e["locked_at"] else ""
        answers = json.loads(e["checklist"] or "{}")
        checklist = "".join(f"<dt>{_esc(q)}</dt><dd>{_esc(answers.get(k, ''))}</dd>"
                            for k, q in J.CHECKLIST if str(answers.get(k, "")).strip())
        out.append(f'<section class="plan" id="evidence-experiment-{e["id"]}"><h3>Experiment {e["id"]}: '
                   f'the plan, written before the results</h3><dl>{items}</dl>{moved_html}'
                   + (f"<h4>Benchmark checklist</h4><dl>{checklist}</dl>" if checklist else "")
                   + "</section>")
    return "".join(out)


def evidence_html(db: sqlite3.Connection, root: Path, j: sqlite3.Row, md: str) -> str:
    refs = _referenced(md)
    parts = ['<section class="evidence" id="evidence"><h2>Evidence</h2>',
             "<p>Every number above links here. Each run lists the machine and code it ran on; "
             "each source lists when its copy was saved.</p>"]
    parts.append(_plan_html(db, j["id"]))
    parts += [_run_html(db, rid, j["id"]) for rid in sorted(refs["runs"], key=lambda r: (len(r), r))]
    if refs["sources"]:
        rows = []
        for url in sorted(refs["sources"]):
            snap = S.latest(db, url)
            when = snap["fetched_at"] if snap is not None else "not saved"
            how = " (copy supplied by the author)" if snap is not None and snap["method"] == "manual" else ""
            rows.append(f'<li><a href="{_esc(url)}" rel="noopener">{_esc(url)}</a> — copy saved {_esc(when)}{how}</li>')
        parts.append(f"<h3>Sources</h3><ul>{''.join(rows)}</ul>")
    if refs["obs"]:
        rows = "".join(f'<li id="evidence-obs-{R.anchor(o)}">{_esc(o)}: the author’s own observation</li>'
                       for o in sorted(refs["obs"]))
        parts.append(f"<h3>Observations</h3><ul>{rows}</ul>")
    parts.append(f'<h3>How this was written</h3><p>{_esc(j["disclosure"])}</p></section>')
    return "".join(parts)


# ---- pages ---------------------------------------------------------------------------

FONTS = ("https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;800"
         "&family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600&display=swap")

CSS = """:root{--bg:#000;--cream:#fafaf8;--body:rgba(255,255,255,.72);--dim:rgba(255,255,255,.45);
--line:rgba(255,255,255,.1);--accent:#4ade80;--serif:'Playfair Display',Georgia,serif;
--mono:'DM Mono','SF Mono',Consolas,monospace;--sans:'DM Sans',system-ui,sans-serif}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--body);font:17px/1.7 var(--sans);padding:0 18px}
main,header.top,footer.site{max-width:720px;margin:0 auto}
header.top{padding:28px 0 0;font:13px var(--mono)}header.top a{color:var(--dim);text-decoration:none}
header.top a:hover{color:var(--cream)}
h1,h2,h3,h4{color:var(--cream);font-family:var(--serif);line-height:1.2;text-wrap:balance}
h1{font-size:clamp(32px,6vw,46px);margin:28px 0 8px}h2{font-size:28px;margin-top:48px}
h3{font-size:20px;margin-top:32px}h3 small{font:13px var(--mono);color:var(--dim)}
.meta{font:13px var(--mono);color:var(--dim);margin-bottom:32px}
a{color:var(--cream);text-underline-offset:3px}a.proof{color:var(--accent)}
code{font:.9em var(--mono);color:var(--cream)}pre{overflow-x:auto;padding:14px;border:1px solid var(--line)}
blockquote{margin:24px 0;padding-left:18px;border-left:2px solid var(--accent)}
blockquote footer{font:13px var(--mono);color:var(--dim)}
img{max-width:100%;height:auto;background:#fff;border-radius:4px}
.table{overflow-x:auto}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:8px 10px;text-align:left}th{color:var(--cream)}
.evidence{margin-top:64px;padding-top:8px;border-top:1px solid var(--line);font-size:15px}
dl{display:grid;grid-template-columns:160px 1fr;gap:4px 16px}dt{color:var(--dim);font:13px var(--mono)}
dd{margin:0;min-width:0;overflow-wrap:anywhere}.warn{color:#fbbf24}
ul.list{list-style:none;padding:0}ul.list li{border-bottom:1px solid var(--line);padding:16px 0}
ul.list a{font:600 20px var(--serif);text-decoration:none}ul.list p{margin:4px 0 0}
footer.site{margin:64px auto 40px;font:13px var(--mono);color:var(--dim)}
@media (max-width:600px){dl{grid-template-columns:1fr}}
"""


def _page(title: str, description: str, canonical: str, body: str, kind: str = "article") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<meta name="description" content="{_esc(description)}">
<link rel="canonical" href="{_esc(canonical)}">
<meta property="og:type" content="{kind}">
<meta property="og:title" content="{_esc(title)}">
<meta property="og:description" content="{_esc(description)}">
<meta property="og:url" content="{_esc(canonical)}">
<link rel="alternate" type="application/atom+xml" title="Journal" href="/journal/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="/journal/journal.css">
</head>
<body>
<header class="top"><a href="/">Yosri Khiari</a> / <a href="/journal/">Journal</a></header>
<main>
{body}
</main>
<footer class="site">Written by Yosri Khiari. Every number links to its evidence.</footer>
</body>
</html>
"""


def article_page(db: sqlite3.Connection, root: Path, j: sqlite3.Row, md: str, canonical: str,
                 published_on: str) -> str:
    title = T.title_of(md) or j["title"]
    description = R.first_paragraph(md)[:200]
    body = (f"<article><h1>{_esc(title)}</h1><p class=\"meta\">{_esc(published_on)} · {_esc(j['kind'])}</p>"
            f"{R.body_html(md)}{evidence_html(db, root, j, md)}</article>")
    return _page(title, description, canonical, body)


def _listed(db: sqlite3.Connection, also: Optional[int]) -> List[sqlite3.Row]:
    rows = db.execute(
        f"SELECT * FROM journal WHERE state IN ({','.join('?' * len(PUBLISHED_STATES))}) OR id = ? "
        "ORDER BY COALESCE(NULLIF(published_at, ''), updated_at) DESC",
        (*PUBLISHED_STATES, also or -1)).fetchall()
    return rows


def _summary(root: Path, j: sqlite3.Row) -> str:
    path = Path(root) / j["path"]
    return R.first_paragraph(path.read_text(encoding="utf-8"))[:220] if j["path"] and path.exists() else ""


def index_page(db, root: Path, base_url: str, also: Optional[int] = None) -> str:
    items = []
    for j in _listed(db, also):
        date = (j["published_at"] or j["updated_at"])[:10]
        items.append(f'<li><a href="/journal/{_esc(j["slug"])}/">{_esc(j["title"])}</a>'
                     f'<p class="meta">{_esc(date)} · {_esc(j["kind"])}</p><p>{_esc(_summary(root, j))}</p></li>')
    body = ("<h1>Journal</h1><p>Experiments, measurements and what they taught me. Every number links "
            "to the run or source behind it.</p>" f"<ul class=\"list\">{''.join(items)}</ul>")
    return _page("Journal — Yosri Khiari", "Engineering experiments with the evidence attached.",
                 f"{base_url.rstrip('/')}/journal/", body, kind="website")


def feed_xml(db, root: Path, base_url: str, also: Optional[int] = None) -> str:
    base = base_url.rstrip("/")
    entries = []
    # Dates come from the articles, never from the clock: exporting the same
    # articles twice must write the same bytes, or every export is a commit.
    stamps = []
    for j in _listed(db, also):
        url = canonical_url(base, j["slug"])
        updated = j["published_at"] or j["updated_at"]
        stamps.append(updated)
        entries.append(f"<entry><title>{_esc(j['title'])}</title><link href=\"{url}\"/><id>{url}</id>"
                       f"<updated>{_esc(updated)}</updated><summary>{_esc(_summary(root, j))}</summary></entry>")
    newest = max(stamps) if stamps else "1970-01-01T00:00:00+00:00"
    return ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f"<title>Yosri Khiari — Journal</title><link href=\"{base}/journal/\"/>"
            f"<link rel=\"self\" href=\"{base}/journal/feed.xml\"/><id>{base}/journal/</id>"
            f"<updated>{newest}</updated><author><name>Yosri Khiari</name></author>{''.join(entries)}</feed>\n")


# ---- the site worktree ---------------------------------------------------------------

def _remote_default(site_repo: Path) -> str:
    """origin/HEAD if the clone knows it; a clone made without it (or with
    `git init` + `remote add`) falls back to origin/main, then origin/master."""
    try:
        return _git(site_repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    except J.JournalError:
        pass
    for name in ("origin/main", "origin/master"):
        try:
            _git(site_repo, "rev-parse", "--verify", "-q", name)
            return name
        except J.JournalError:
            continue
    raise J.JournalError("cannot tell the site's default branch (no origin/HEAD, main or master)")


def site_worktree(site_repo: Path, root: Path, slug: str) -> Path:
    """A worktree of the site on branch journal/<slug>, cut from the remote default."""
    site_repo = Path(site_repo)
    if not (site_repo / ".git").exists():
        raise J.JournalError(f"{site_repo} is not a git checkout of the site")
    where = Path(root) / SITE_DIR / slug
    branch = f"journal/{slug}"
    if where.exists():
        return where
    _git(site_repo, "fetch", "-q", "origin")
    head = _remote_default(site_repo)
    where.parent.mkdir(parents=True, exist_ok=True)
    have = _git(site_repo, "branch", "--list", branch)
    if have:
        _git(site_repo, "worktree", "add", str(where), branch)
    else:
        _git(site_repo, "worktree", "add", "-b", branch, str(where), head)
    return where


def export(db: sqlite3.Connection, root: Path, slug: str, site_repo: Path,
           base_url: str = DEFAULT_BASE_URL, at: Optional[str] = None) -> Dict:
    """Write the article into a site worktree and commit it there."""
    from jester.journal import files as F

    j = J.get(db, slug)
    problems = ready_to_export(db, root, j)
    if problems:
        raise J.JournalError("cannot export yet: " + "; ".join(problems))
    url = canonical_url(base_url, slug)
    if j["canonical_url"] != url:
        J.update_brief(db, slug, canonical_url=url)
        j = J.get(db, slug)
    md = (Path(root) / j["path"]).read_text(encoding="utf-8")
    published_on = (j["published_at"] or at or J.now_utc())[:10]
    site = site_worktree(site_repo, root, slug)
    out_dir = site / "journal" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_bytes(article_page(db, root, j, md, url, published_on).encode("utf-8"))
    article_dir = Path(root) / Path(j["path"]).parent
    copied = []
    for b in T.prose_blocks(md):
        for ln in T.links(b.text):
            if ln.image and ln.target.startswith("figures/"):
                src = article_dir / ln.target
                dst = out_dir / ln.target
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                copied.append(ln.target)
    journal_dir = site / "journal"
    (journal_dir / "journal.css").write_bytes(CSS.encode("utf-8"))
    (journal_dir / "index.html").write_bytes(index_page(db, root, base_url, also=j["id"]).encode("utf-8"))
    (journal_dir / "feed.xml").write_bytes(feed_xml(db, root, base_url, also=j["id"]).encode("utf-8"))
    rev = J.latest_revision(db, j["id"])
    _git(site, "add", "--", "journal")
    status = _git(site, "status", "--porcelain", "--", "journal")
    if status:
        _git(site, *F._identity_args(site), "commit", "-q", "-m",
             f"Journal: {j['title']} (version {rev['n']})")
    return {"site": site, "branch": f"journal/{slug}", "url": url, "figures": copied,
            "commit": _git(site, "rev-parse", "HEAD"), "changed": bool(status)}


def push(site: Path, title: str, runner=subprocess.run) -> str:
    """Push the export branch and open a pull request on the site. Returns its URL."""
    branch = _git(site, "rev-parse", "--abbrev-ref", "HEAD")
    _git(site, "push", "-q", "-u", "origin", branch)
    out = runner(["gh", "pr", "create", "--fill", "--title", f"Journal: {title}"], cwd=str(site),
                 capture_output=True, text=True, encoding="utf-8", check=False)
    if out.returncode != 0:
        raise J.JournalError(f"pushed {branch}, but opening the pull request failed: "
                             f"{(out.stderr or out.stdout).strip()}")
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
