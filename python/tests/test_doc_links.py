"""Every relative path in a tracked Markdown file must resolve (V3-21).

`docs/STATUS.md` is the one index, and a plan is only useful while the pages it
points at exist. A moved file used to leave the link behind, and nothing in the
suite read prose, so the index could lie for weeks. This test reads every `.md`
file Git tracks, extracts the link targets that name a path, and opens the
file.

Deliberately narrow, so it stays a test rather than a Markdown parser:

- fenced code blocks and inline code spans are stripped first, because the
  journal's documentation examples are links whose targets are not paths
  (`[91.2%](run:12#accuracy)`, `[4 hours](obs:notes|timed-the-session)`);
- a target with a URI scheme is skipped (the docs cite `https://` pages and
  local `file:///D:/...` ones);
- a target that is empty or names only a fragment is skipped;
- `#anchor` and `?query` suffixes are removed before the path is resolved, so
  the file the anchor lives in is what gets checked. The `#anchor` part itself
  is **not** validated in this cycle: nothing here reads a heading or derives
  a slug. A link to `page.md#no-such-heading` passes as long as `page.md` is
  on disk.

Underscores are not emphasis here, so `snake_case.md` links are left alone; the
test only looks at the inside of `](...)` and an HTML `src=`/`href=`.

The paths are resolved with `pathlib` against the containing file, so the test
runs the same on Windows and Linux, and it touches the filesystem only.
"""
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]

_FENCED = re.compile(r"^(```|~~~).*?^\1", re.DOTALL | re.MULTILINE)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_MD_LINK = re.compile(
    r"\]\(\s*(?:<(?P<angled>[^>\n]*)>|(?P<plain>[^)\s]*))"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
_HTML_LINK = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'\n]+)["']""")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def tracked_markdown():
    """The `.md` files Git tracks, relative to the repository root."""
    listed = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, check=True,
        capture_output=True, text=True, encoding="utf-8").stdout
    return [line.strip() for line in listed.splitlines()
            if line.strip() and not line.startswith(".superpowers/")]


def link_targets(text):
    """Every raw link target in `text`, with code and schemes removed."""
    text = _FENCED.sub("", text)
    text = _INLINE_CODE.sub("", text)
    raw = [m.group("angled") if m.group("angled") is not None else m.group("plain")
           for m in _MD_LINK.finditer(text)]
    raw.extend(_HTML_LINK.findall(text))
    for target in raw:
        target = target.strip()
        if not target or target.startswith("#") or _SCHEME.match(target):
            continue
        if target.startswith("//"):
            continue
        path = target.split("#", 1)[0].split("?", 1)[0]
        if not path:
            continue
        yield target, unquote(path)


def broken_links():
    """(file, target, resolved path) for every target that is not on disk."""
    broken = []
    for name in tracked_markdown():
        page = ROOT / name
        text = page.read_text(encoding="utf-8")
        for target, path in link_targets(text):
            resolved = (page.parent / path).resolve()
            if not resolved.exists():
                broken.append((name, target, resolved))
    return broken


def test_every_tracked_markdown_file_is_read_at_least_once():
    """The guard on the guard: an empty file list would pass silently."""
    names = tracked_markdown()
    assert "README.md" in names
    assert "docs/STATUS.md" in names
    assert len(names) > 30


def test_the_extractor_ignores_code_and_accepts_relative_paths():
    """A parser this narrow needs its own cases, or it can pass by reading
    nothing. Each line below is a form the documentation actually uses."""
    text = (
        "A [page](../README.md) and an [image](docs/img/logo.png \"a title\").\n"
        "[angled](<docs/with space.md>) and <img src=\"docs/img/logo.png\">.\n"
        "[anchor](../README.md#operating-state) and [query](../README.md?x=1).\n"
        "[encoded](docs/a%20b.md).\n"
        "[run](run:12#accuracy) and [obs](obs:notes|timed-the-session).\n"
        "[web](https://example.com/x) and [file](file:///D:/tmp/x.md).\n"
        "[empty]() and [fragment](#operating-state).\n"
        "```\n[fenced](nowhere/at/all.md)\n```\n"
        "An inline `[code](nowhere.md)` span.\n")
    got = [path for _, path in link_targets(text)]
    assert got == ["../README.md", "docs/img/logo.png", "docs/with space.md",
                   "../README.md", "../README.md", "docs/a b.md",
                   "docs/img/logo.png"]


def test_no_relative_link_points_at_a_file_that_is_not_there():
    """The failing case names the page and the target, because a link is
    fixed by opening the page it is written on."""
    broken = broken_links()
    assert not broken, "\n".join(
        f"{name}: {target} -> {resolved}" for name, target, resolved in broken)


def test_the_resolution_reports_a_missing_file_by_name(tmp_path):
    """The same resolution the test above uses, on a page written here, so a
    green suite above cannot mean the check no longer checks."""
    page = tmp_path / "docs" / "example.md"
    page.parent.mkdir()
    page.write_text("[here](missing.md) [there](https://example.com/x.md)",
                    encoding="utf-8")
    broken = [(target, (page.parent / path).resolve())
              for target, path in link_targets(page.read_text(encoding="utf-8"))
              if not (page.parent / path).resolve().exists()]
    assert [target for target, _ in broken] == ["missing.md"]
    assert not broken[0][1].exists()
