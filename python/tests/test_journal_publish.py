"""Publishing (J3): the Markdown renderer, the site export into a branch of
the site's own repository, platform versions, and posting.

G5 lives here: nothing leaves the machine without a stored human approval of
the exact file. The network is faked throughout; the site is a real git
repository with a real (bare) remote, so the worktree/branch/commit path is
the one that runs for real.
"""
import io
import json
import subprocess
import urllib.error

import pytest

from jester import cli
from jester import journal as J
from jester.journal import channels as CH
from jester.journal import files as F
from jester.journal import publish as P
from jester.journal import render as R
from jester.journal import states as js

BASE = "https://example.dev"
SRC = "https://blog.example.org/post"
ARTICLE = f"""# Rules beat the model on short posts

The rules kept [11](run:1#buyer) buyers out of [722](run:1#collected) posts. I counted
[40 minutes](obs:labelling) of labelling, and the [survey]({SRC}) agrees.

<!-- backed-by: run:1 -->
| run | buyer |
|---|---|
| a | 11 |

![Buyers per pass](figures/buyers.svg)

## What failed

- nothing *yet*
- the `--limit` flag
"""


def _git(path, *args):
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout


def _site(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    site = tmp_path / "site"
    subprocess.run(["git", "clone", "-q", str(origin), str(site)], check=True, capture_output=True)
    (site / "index.html").write_text("<h1>home</h1>\n", encoding="utf-8")
    _git(site, "add", ".")
    _git(site, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "home")
    _git(site, "push", "-q", "origin", "HEAD:master")
    _git(site, "remote", "set-head", "origin", "master")
    return site


@pytest.fixture
def setup(tmp_path):
    from jester.journal import lab as L

    root = tmp_path / "articles"
    db = J.open_journal(str(tmp_path / "j.db"))
    j = J.create_journal(db, "Rules beat the model", kind="article", slug="rules", question="q",
                         path="rules/index.md")
    path = root / "rules" / "index.md"
    path.parent.mkdir(parents=True)
    path.write_text(ARTICLE, encoding="utf-8")
    e = L.plan(db, j["id"], hypothesis="rules win", metric="buyers", threshold="over 10",
               baseline="model", n_planned=1, threats=["one day only"])
    run = J.add_run(db, e, raw_path="rules/runs/1", result={"buyer": 11, "collected": 722})
    db.execute("UPDATE experiment_run SET env = ? WHERE id = ?",
               (json.dumps({"os": "Windows-11", "gpu": ["RTX 4060"], "code": {"commit": "abc123", "dirty": False},
                            "before_plan": True}), run))
    L.plan(db, j["id"], experiment_id=e, threshold="over 9")
    J.answer_checklist(db, e, {"tuned": "both on defaults"})
    J.register_generated(db, j["id"], "| run | buyer |\n|---|---|\n| a | 11 |", role="table")
    db.commit()
    from jester.journal import snapshots as S
    from jester.journal.fixtures import FakeWeb
    S.take(db, root, SRC, opener=FakeWeb({SRC: {"status": 200, "content_type": "text/html",
                                                "body": "<p>agrees</p>"}}))
    from jester.journal import lab
    rows = db.execute("SELECT id FROM experiment_run").fetchall()
    made = lab.figure(db, root, "rules", [r["id"] for r in rows][:1], "buyer", "buyers", caption="Buyers")
    assert made["path"].exists()
    sha = F.commit(F.ensure_repo(root), path, "rules: v1")
    rev = J.record_revision(db, j["id"], sha, F.content_sha256(path), j["path"])
    for kind in J.REQUIRED_CHECKS:
        J.record_check(db, rev["id"], kind, True)
    J.approve(db, j["id"], "review", revision_id=rev["id"])
    J.approve(db, j["id"], "publish", revision_id=rev["id"])
    J.update_brief(db, "rules", disclosure="Written by hand; a local model proofread it.")
    db.execute("UPDATE journal SET state='ready' WHERE id=?", (j["id"],))
    db.commit()
    yield {"db": db, "root": root, "jid": j["id"], "rev": rev, "tmp": tmp_path}
    db.close()


# ---- render -------------------------------------------------------------------

def test_render_body():
    body = R.body_html(ARTICLE + "\n> Trust numbers you can reproduce.\n> — [A writer](https://w.org)\n"
                       "\n```python\nprint('<b>')\n```\n\n1. one\n   wrapped\n2. two\n\n<script>alert(1)</script>\n")
    assert "<h1" not in body and '<h2 id="what-failed">What failed</h2>' in body
    assert '<a href="#evidence-run-1" class="proof">11</a>' in body
    assert '<a href="#evidence-obs-labelling" class="proof">40 minutes</a>' in body
    assert f'<a href="{SRC}" rel="noopener">survey</a>' in body
    assert '<img src="figures/buyers.svg" alt="Buyers per pass" loading="lazy">' in body
    assert "<th>buyer</th>" in body and "<td>11</td>" in body and "backed-by" not in body
    assert "<em>yet</em>" in body and "<code>--limit</code>" in body
    assert "<footer>— <a href=\"https://w.org\" rel=\"noopener\">A writer</a></footer>" in body
    assert '<code class="language-python">print(&#x27;&lt;b&gt;&#x27;)</code>' in body
    assert "<ol><li>one wrapped</li><li>two</li></ol>" in body
    assert "<script>" not in body and "&lt;script&gt;" in body


def test_render_refuses_unsafe_links_and_keeps_bold():
    out = R.inline("[x](javascript:alert(1)) **bold** __also__ _em_")
    assert 'href="#"' in out and "<strong>bold</strong>" in out and "<strong>also</strong>" in out
    assert "<em>em</em>" in out
    assert R.body_html("# Only title") == ""
    assert R.body_html("| a | b |\n| c | d |").count("<td>") == 4


def test_first_paragraph_and_anchor():
    assert R.first_paragraph(ARTICLE).startswith("The rules kept 11 buyers out of 722 posts.")
    assert R.first_paragraph("# T\n\n## H") == ""
    assert R.anchor("!!!") == "section" and R.evidence_target("https://x") is None


# ---- export -------------------------------------------------------------------

def test_export_refuses_until_ready(setup):
    db, root = setup["db"], setup["root"]
    db.execute("UPDATE journal SET state='drafting', disclosure='' WHERE id=?", (setup["jid"],))
    db.commit()
    (root / "rules" / "index.md").write_text(ARTICLE + "\nmore\n", encoding="utf-8")
    problems = " | ".join(P.ready_to_export(db, root, J.get(db, "rules")))
    for want in ("it is drafting", "unsaved changes", "how AI was used"):
        assert want in problems
    with pytest.raises(J.JournalError, match="cannot export yet"):
        P.export(db, root, "rules", setup["tmp"], base_url=BASE)


def test_export_needs_checks_and_a_publish_approval(setup):
    db, root = setup["db"], setup["root"]
    J.record_check(db, setup["rev"]["id"], "lint", False)
    db.execute("DELETE FROM approval WHERE what='publish'")
    db.commit()
    problems = " | ".join(P.ready_to_export(db, root, J.get(db, "rules")))
    assert "has not passed: lint" in problems and "approved publishing" in problems
    (root / "rules" / "index.md").unlink()
    assert "file is missing" in " ".join(P.ready_to_export(db, root, J.get(db, "rules")))
    other = J.create_journal(db, "Empty")
    assert "nothing saved" in " ".join(P.ready_to_export(db, root, other))


def test_export_writes_the_site_branch_and_leaves_the_checkout_alone(setup):
    db, root, tmp = setup["db"], setup["root"], setup["tmp"]
    site = _site(tmp)
    made = P.export(db, root, "rules", site, base_url=BASE, at="2026-10-02T09:00:00+00:00")
    assert made["url"] == f"{BASE}/journal/rules/" and made["changed"] and made["figures"] == ["figures/buyers.svg"]
    assert J.get(db, "rules")["canonical_url"] == made["url"]
    out = made["site"] / "journal"
    page = (out / "rules" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://example.dev/journal/rules/">' in page
    assert "<h1>Rules beat the model on short posts</h1>" in page and "2026-10-02" in page
    assert 'id="evidence-run-1"' in page and "abc123" in page and "RTX 4060" in page
    assert "happened before the plan was written" in page
    assert "over 10 → over 9" in page and "both on defaults" in page and "one day only" in page
    assert f'<a href="{SRC}" rel="noopener">{SRC}</a>' in page and 'id="evidence-obs-labelling"' in page
    assert "a local model proofread it" in page
    assert (out / "rules" / "figures" / "buyers.svg").exists()
    assert "Rules beat the model" in (out / "index.html").read_text(encoding="utf-8")
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert "<id>https://example.dev/journal/rules/</id>" in feed
    assert (out / "journal.css").exists()
    assert _git(made["site"], "rev-parse", "--abbrev-ref", "HEAD").strip() == "journal/rules"
    assert "Journal: Rules beat the model (version 1)" in _git(made["site"], "log", "-1", "--format=%s")
    assert _git(site, "status", "--porcelain").strip() == ""
    assert _git(site, "rev-parse", "--abbrev-ref", "HEAD").strip() == "master"
    again = P.export(db, root, "rules", site, base_url=BASE, at="2026-10-02T09:00:00+00:00")
    assert not again["changed"] and again["commit"] == made["commit"]


def test_export_reuses_an_existing_branch(setup):
    db, root, tmp = setup["db"], setup["root"], setup["tmp"]
    site = _site(tmp)
    made = P.export(db, root, "rules", site, base_url=BASE)
    _git(site, "worktree", "remove", "--force", str(made["site"]))
    again = P.export(db, root, "rules", site, base_url=BASE)
    assert again["branch"] == "journal/rules" and again["site"].exists()


def test_default_branch_without_origin_head(setup):
    site = _site(setup["tmp"])
    _git(site, "remote", "set-head", "origin", "--delete")
    assert P._remote_default(site) == "origin/master"
    bare = setup["tmp"] / "bare"
    bare.mkdir()
    _git(bare, "init", "-q")
    with pytest.raises(J.JournalError, match="cannot tell the site's default branch"):
        P._remote_default(bare)


def test_export_needs_a_git_site(setup):
    plain = setup["tmp"] / "plain"
    plain.mkdir()
    with pytest.raises(J.JournalError, match="not a git checkout"):
        P.export(setup["db"], setup["root"], "rules", plain, base_url=BASE)


def test_push_opens_a_pull_request(setup):
    db, root, tmp = setup["db"], setup["root"], setup["tmp"]
    made = P.export(db, root, "rules", _site(tmp), base_url=BASE)
    calls = []

    def gh(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="https://github.com/me/site/pull/7\n", stderr="")
    assert P.push(made["site"], "Rules", runner=gh) == "https://github.com/me/site/pull/7"
    assert calls[0][:3] == ["gh", "pr", "create"]
    assert "journal/rules" in _git(tmp / "origin.git", "branch", "--list")

    def broken(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no auth")
    with pytest.raises(J.JournalError, match="opening the pull request failed: no auth"):
        P.push(made["site"], "Rules", runner=broken)
    assert P.push(made["site"], "Rules", runner=lambda a, **k: subprocess.CompletedProcess(a, 0, "", "")) == ""


def test_git_errors_surface(tmp_path):
    with pytest.raises(J.JournalError, match="git status"):
        P._git(tmp_path / "missing", "status")


# ---- versions -------------------------------------------------------------------

def _published(setup):
    db = setup["db"]
    J.update_brief(db, "rules", canonical_url=f"{BASE}/journal/rules/")
    db.execute("UPDATE journal SET state='published' WHERE id=?", (setup["jid"],))
    db.commit()


def test_versions_need_a_published_original(setup):
    with pytest.raises(J.JournalError, match="publish the original"):
        CH.make_version(setup["db"], setup["root"], "rules", "bluesky")
    with pytest.raises(J.JournalError, match="channel must be"):
        CH.channel("myspace")


def test_every_channel_builds_a_version_that_passes(setup):
    _published(setup)
    for name in CH.CHANNELS:
        made = CH.make_version(setup["db"], setup["root"], "rules", name)
        text = made["path"].read_text(encoding="utf-8")
        assert made["problems"] == [], (name, made["problems"])
        assert f"{BASE}/journal/rules/" in text
    devto = (setup["root"] / "rules" / "channels" / "devto.md").read_text(encoding="utf-8")
    assert "[11](https://example.dev/journal/rules/#evidence-run-1)" in devto
    assert "![Buyers per pass](https://example.dev/journal/rules/figures/buyers.svg)" in devto
    assert "backed-by" not in devto and not devto.startswith("# ") and "Originally published at" in devto
    sky = (setup["root"] / "rules" / "channels" / "bluesky.md").read_text(encoding="utf-8")
    assert CH.graphemes(sky) <= 300


def test_check_version_rules(setup):
    md, url = ARTICLE, f"{BASE}/journal/rules/"
    assert "is not in the original" in " ".join(CH.check_version(md, f"Rules won 99% of runs. {url}", "bluesky", url))
    assert "link to the original is missing" in " ".join(CH.check_version(md, "Rules won.", "mastodon", url))
    assert "allows 300" in " ".join(CH.check_version(md, "x" * 301 + url, "bluesky", url))
    slop = f"Additionally, it is crucial to delve into this pivotal tapestry. {url}"
    assert "reads as generated" in " ".join(CH.check_version(md, slop, "linkedin", url))


def test_fit_and_graphemes():
    assert CH._fit("One. Two. Three.", 9) == "One. Two."
    assert CH._fit("Averyveryverylongword and more", 10).endswith("…")
    assert CH.graphemes("é‍") == 1


# ---- approving and posting ----------------------------------------------------------

class Web:
    """Answers each URL with a canned JSON body and records every request."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        for prefix, answer in self.answers.items():
            if req.full_url.startswith(prefix):
                if isinstance(answer, int):
                    raise urllib.error.HTTPError(req.full_url, answer, "nope", {}, io.BytesIO(b'{"error":"bad"}'))
                return io.BytesIO(json.dumps(answer).encode())
        raise urllib.error.URLError("unreachable")


ENV = {"DEVTO_API_KEY": "k", "BLUESKY_HANDLE": "me.bsky.social", "BLUESKY_APP_PASSWORD": "p",
       "MASTODON_INSTANCE": "https://mastodon.social", "MASTODON_TOKEN": "t"}


def test_g5_nothing_is_posted_without_an_approval(setup):
    _published(setup)
    web = Web({"https://": {"id": 1}})
    for name, ch in CH.CHANNELS.items():
        CH.make_version(setup["db"], setup["root"], "rules", name)
        with pytest.raises(J.JournalError):
            CH.post(setup["db"], setup["root"], "rules", name, opener=web, env=ENV)
    assert web.requests == []


def test_an_edited_version_needs_approving_again(setup):
    _published(setup)
    made = CH.make_version(setup["db"], setup["root"], "rules", "mastodon")
    CH.approve_version(setup["db"], setup["root"], "rules", "mastodon")
    made["path"].write_text(made["path"].read_text(encoding="utf-8") + " edited", encoding="utf-8")
    web = Web({"https://": {"id": 1}})
    with pytest.raises(J.JournalError, match="changed after you approved it"):
        CH.post(setup["db"], setup["root"], "rules", "mastodon", opener=web, env=ENV)
    assert web.requests == []


def test_an_approval_by_anyone_else_does_not_count(setup):
    _published(setup)
    db = setup["db"]
    CH.make_version(db, setup["root"], "rules", "mastodon")
    CH.approve_version(db, setup["root"], "rules", "mastodon")
    db.execute("UPDATE approval SET actor='scheduler' WHERE what='post'")
    db.commit()
    with pytest.raises(J.JournalError, match="no approval from you"):
        CH.post(db, setup["root"], "rules", "mastodon", opener=Web({}), env=ENV)


def test_approve_refuses_failing_or_missing_versions(setup):
    _published(setup)
    with pytest.raises(J.JournalError, match="no linkedin version yet"):
        CH.approve_version(setup["db"], setup["root"], "rules", "linkedin")
    made = CH.make_version(setup["db"], setup["root"], "rules", "linkedin")
    made["path"].write_text("Rules won 99% of the time.", encoding="utf-8")
    with pytest.raises(J.JournalError, match="fails its check"):
        CH.approve_version(setup["db"], setup["root"], "rules", "linkedin")


def test_post_devto_as_a_draft_then_record_it(setup):
    _published(setup)
    db, root = setup["db"], setup["root"]
    CH.make_version(db, root, "rules", "devto")
    with pytest.raises(J.JournalError, match="send the devto version first"):
        CH.record_posted(db, "rules", "devto", "https://dev.to/me/rules")
    CH.approve_version(db, root, "rules", "devto")
    with pytest.raises(J.JournalError, match="already approved|is already"):
        db.execute("UPDATE publication SET status='sent' WHERE channel='devto'")
        CH.approve_version(db, root, "rules", "devto")
    db.execute("UPDATE publication SET status='approved' WHERE channel='devto'")
    db.commit()
    web = Web({"https://dev.to/api/articles": {"id": 42, "url": "https://dev.to/me/rules-temp"}})
    pub = CH.post(db, root, "rules", "devto", opener=web, env=ENV)
    sent = json.loads(web.requests[0].data)
    assert sent["article"]["published"] is False and sent["article"]["canonical_url"] == f"{BASE}/journal/rules/"
    assert web.requests[0].get_header("Api-key") == "k"
    assert (pub["status"], pub["external_id"], pub["posted_at"]) == ("sent", "42", "")
    done = CH.record_posted(db, "rules", "devto", "https://dev.to/me/rules")
    assert done["status"] == "posted" and done["url"] == "https://dev.to/me/rules"


def test_post_bluesky_with_a_link_facet(setup):
    _published(setup)
    db, root = setup["db"], setup["root"]
    CH.make_version(db, root, "rules", "bluesky")
    CH.approve_version(db, root, "rules", "bluesky")
    web = Web({"https://bsky.social/xrpc/com.atproto.server.createSession": {"did": "did:plc:x", "accessJwt": "jwt"},
               "https://bsky.social/xrpc/com.atproto.repo.createRecord":
                   {"uri": "at://did:plc:x/app.bsky.feed.post/3kabc"}})
    pub = CH.post(db, root, "rules", "bluesky", opener=web, env=ENV, now="2026-10-02T09:00:00.000Z")
    record = json.loads(web.requests[1].data)["record"]
    link = f"{BASE}/journal/rules/"
    start = record["facets"][0]["index"]["byteStart"]
    assert record["text"].encode("utf-8")[start:start + len(link)].decode() == link
    assert web.requests[1].get_header("Authorization") == "Bearer jwt"
    assert pub["status"] == "posted" and pub["url"] == "https://bsky.app/profile/me.bsky.social/post/3kabc"


def test_post_mastodon_and_share_gate(setup):
    _published(setup)
    db, root = setup["db"], setup["root"]
    CH.make_version(db, root, "rules", "mastodon")
    CH.approve_version(db, root, "rules", "mastodon")
    web = Web({"https://mastodon.social/api/v1/statuses": {"id": "9", "url": "https://mastodon.social/@me/9"}})
    pub = CH.post(db, root, "rules", "mastodon", opener=web, env=ENV)
    assert web.requests[0].get_header("Idempotency-key").startswith("jester-journal-")
    assert pub["status"] == "posted"
    assert js.move(db, "rules", "shared")["state"] == "shared"


def test_posting_errors(setup):
    _published(setup)
    db, root = setup["db"], setup["root"]
    for name in ("mastodon", "bluesky"):
        CH.make_version(db, root, "rules", name)
        CH.approve_version(db, root, "rules", name)
    with pytest.raises(J.JournalError, match="add MASTODON_INSTANCE, MASTODON_TOKEN to .env"):
        CH.post(db, root, "rules", "mastodon", opener=Web({}), env={})
    with pytest.raises(J.JournalError, match="HTTP 401"):
        CH.post(db, root, "rules", "mastodon", opener=Web({"https://": 401}), env=ENV)
    with pytest.raises(J.JournalError, match="could not reach"):
        CH.post(db, root, "rules", "bluesky", opener=Web({}), env=ENV)
    with pytest.raises(J.JournalError, match="does not post to hn"):
        CH.post(db, root, "rules", "hn", opener=Web({}), env=ENV)


def test_record_a_manual_post(setup):
    _published(setup)
    db = setup["db"]
    with pytest.raises(J.JournalError, match="https link"):
        CH.record_posted(db, "rules", "hn", "news.ycombinator.com/item?id=1")
    first = CH.record_posted(db, "rules", "hn", "https://news.ycombinator.com/item?id=1")
    second = CH.record_posted(db, "rules", "hn", "https://news.ycombinator.com/item?id=2")
    assert first["id"] != second["id"] and second["status"] == "posted"
    assert len([a for a in J.human_approvals(db, setup["jid"], "post")]) == 2
    assert js.move(db, "rules", "shared")["state"] == "shared"


# ---- CLI ----------------------------------------------------------------------

def test_cli_export_variant_approve_post_posted(setup, capsys, monkeypatch):
    db, root, tmp = setup["db"], setup["root"], setup["tmp"]
    site = _site(tmp)
    base = ["--db", str(tmp / "j.db"), "--root", str(root)]
    cli.main(["journal", "export", *base, "rules", "--site", str(site), "--base-url", BASE])
    out = capsys.readouterr().out
    assert f"exported rules -> {BASE}/journal/rules/" in out and "with --push" in out
    monkeypatch.setattr(P, "push", lambda site, title: "https://github.com/me/site/pull/1")
    cli.main(["journal", "export", *base, "rules", "--site", str(site), "--base-url", BASE, "--push"])
    assert "pull request: https://github.com/me/site/pull/1" in capsys.readouterr().out
    cli.main(["journal", "state", *base, "rules", "published"])
    capsys.readouterr()
    cli.main(["journal", "variant", *base, "rules", "mastodon"])
    assert "approve rules post --channel mastodon" in capsys.readouterr().out
    cli.main(["journal", "approve", *base, "rules", "post", "--channel", "mastodon"])
    assert "editing it means approving again" in capsys.readouterr().out
    monkeypatch.setattr(CH.urllib.request, "urlopen",
                        Web({"https://mastodon.social": {"id": "9", "url": "https://mastodon.social/@me/9"}}))
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    cli.main(["journal", "post", *base, "rules", "mastodon"])
    assert "posted to mastodon: https://mastodon.social/@me/9" in capsys.readouterr().out
    cli.main(["journal", "variant", *base, "rules", "devto"])
    cli.main(["journal", "approve", *base, "rules", "post", "--channel", "devto"])
    monkeypatch.setattr(CH.urllib.request, "urlopen",
                        Web({"https://dev.to": {"id": 5, "url": "https://dev.to/me/draft"}}))
    capsys.readouterr()
    cli.main(["journal", "post", *base, "rules", "devto"])
    assert "unpublished draft: https://dev.to/me/draft" in capsys.readouterr().out
    cli.main(["journal", "posted", *base, "rules", "hn", "--url", "https://news.ycombinator.com/item?id=3"])
    assert "recorded the hn post" in capsys.readouterr().out
    cli.main(["journal", "show", *base, "rules"])
    shown = capsys.readouterr().out
    assert "mastodon: posted https://mastodon.social/@me/9" in shown and "devto: sent" in shown


def test_cli_variant_reports_problems(setup, capsys):
    _published(setup)
    base = ["--db", str(setup["tmp"] / "j.db"), "--root", str(setup["root"])]
    J.update_brief(setup["db"], "rules", disclosure="Written by hand. Additionally, it is crucial to delve "
                                                    "into this pivotal tapestry of 99 things.")
    cli.main(["journal", "variant", *base, "rules", "devto"])
    out = capsys.readouterr().out
    assert "cannot be approved yet" in out and "99 is not in the original" in out
