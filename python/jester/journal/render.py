"""Markdown to HTML, for exactly the Markdown an article uses.

Jester keeps its dependencies few on purpose, and the journal's Markdown is a
known subset: headings, paragraphs, lists, quotes, tables, code, links,
images, emphasis. This renders that subset and nothing else -- no raw HTML
passes through (it is escaped), so an article cannot inject a script into
the site.

Proof links are rewritten on the way out. A reader has no use for
`run:12#accuracy`; they get a link to that run in the page's evidence
section instead. `obs:` links go to the observations list. Comments
(`<!-- backed-by: ... -->`) are dropped: the evidence section says the same
thing in words.
"""

from __future__ import annotations

import html
import re
from typing import Callable, List, Optional
from urllib.parse import urlsplit

from jester.journal import text as T

Rewrite = Callable[[str], str]


def anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def evidence_target(target: str) -> Optional[str]:
    """Where a proof link points on the page, or None if it is a normal link."""
    if target.startswith("run:"):
        run_id = target[len("run:"):].partition("#")[0]
        return f"#evidence-run-{run_id}"
    if target.startswith("obs:"):
        return f"#evidence-obs-{anchor(target[len('obs:'):])}"
    return None


def default_rewrite(target: str) -> str:
    return evidence_target(target) or target


_CODE = re.compile(r"`([^`\n]+)`")
_IMG = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?\s*\)")
_LNK = re.compile(r"\[([^\]\n]+)\]\(\s*<?([^)\s>]+)>?(?:\s+&quot;[^&]*&quot;)?\s*\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*|__([^_\n]+)__")
_EM_STAR = re.compile(r"(?<![\w*])\*([^*\s][^*\n]*)\*(?![\w*])")
_EM_UNDER = re.compile(r"(?<!\w)_([^_\s][^_\n]*)_(?!\w)")
_WEB_SCHEMES = frozenset({"http", "https"})
_LOCAL_PREFIXES = ("#", "/", "./", "../")
_RELATIVE_PATH = re.compile(r"[\w\-./]+")


def _is_web(url: str) -> bool:
    return urlsplit(url).scheme.lower() in _WEB_SCHEMES


def _safe(url: str) -> str:
    """Only web, anchor and relative URLs survive; `javascript:` does not."""
    if _is_web(url):
        return url
    if not urlsplit(url).scheme and (url.startswith(_LOCAL_PREFIXES) or _RELATIVE_PATH.fullmatch(url)):
        return url
    return "#"


def inline(text: str, rewrite: Rewrite = default_rewrite) -> str:
    codes: List[str] = []

    def keep_code(m):
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = _CODE.sub(keep_code, text)
    text = html.escape(text, quote=True)

    def img(m):
        alt, src = m.group(1), _safe(html.unescape(m.group(2)))
        return f'<img src="{html.escape(src)}" alt="{alt}" loading="lazy">'

    def lnk(m):
        label, target = m.group(1), html.unescape(m.group(2))
        href = _safe(rewrite(target))
        ext = ' rel="noopener"' if _is_web(href) else ""
        cls = ' class="proof"' if evidence_target(target) else ""
        return f'<a href="{html.escape(href)}"{cls}{ext}>{label}</a>'

    text = _IMG.sub(img, text)
    text = _LNK.sub(lnk, text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = _EM_STAR.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    text = _EM_UNDER.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _list(block: T.Block, rewrite: Rewrite) -> str:
    items: List[str] = []
    ordered = bool(re.match(r"^\s*\d+[.)]\s", block.text))
    for line in block.text.splitlines():
        stripped = line.strip()
        marker = re.match(r"(?:[-*+]|\d+[.)])\s", stripped)
        if marker:
            items.append(stripped[marker.end():].strip())
        elif items:
            items[-1] += " " + stripped
    tag = "ol" if ordered else "ul"
    body = "".join(f"<li>{inline(i, rewrite)}</li>" for i in items)
    return f"<{tag}>{body}</{tag}>"


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(block: T.Block, rewrite: Rewrite) -> str:
    lines = [ln for ln in block.text.splitlines() if ln.strip()]
    if len(lines) >= 2 and "-" in lines[1] and set(lines[1].strip()) <= set("|:- "):
        head, rows = _cells(lines[0]), lines[2:]
    else:
        head, rows = None, lines
    out = ['<div class="table"><table>']
    if head:
        out.append("<thead><tr>" + "".join(f"<th>{inline(c, rewrite)}</th>" for c in head) + "</tr></thead>")
    out.append("<tbody>")
    for r in rows:
        out.append("<tr>" + "".join(f"<td>{inline(c, rewrite)}</td>" for c in _cells(r)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _quote(block: T.Block, rewrite: Rewrite) -> str:
    body, credit = [], ""
    for raw in block.text.splitlines():
        line = re.sub(r"^\s{0,3}>\s?", "", raw)
        if re.match(r"^\s*(\u2014|--|-)\s", line):
            credit = re.sub(r"^\s*(\u2014|--|-)\s+", "", line)
        else:
            body.append(line)
    paras = [p for p in re.split(r"\n\s*\n", "\n".join(body)) if p.strip()]
    inner = "".join(f"<p>{inline(' '.join(p.split()), rewrite)}</p>" for p in paras)
    if credit:
        inner += f"<footer>\u2014 {inline(credit, rewrite)}</footer>"
    return f"<blockquote>{inner}</blockquote>"


def _code(block: T.Block) -> str:
    lines = block.text.splitlines()
    lang = lines[0].strip().lstrip("`~").strip()
    body = lines[1:-1] if len(lines) > 1 and lines[-1].strip().startswith(("```", "~~~")) else lines[1:]
    cls = f' class="language-{html.escape(lang)}"' if lang else ""
    return f"<pre><code{cls}>{html.escape(chr(10).join(body))}</code></pre>"


def body_html(md: str, rewrite: Rewrite = default_rewrite, drop_title: bool = True) -> str:
    """The article body. The `# title` is dropped by default: the page
    template prints it in the header."""
    out: List[str] = []
    title_seen = False
    for b in T.blocks(md):
        if b.kind == "heading":
            level = len(re.match(r"^\s{0,3}(#+)", b.text).group(1))
            words = re.sub(r"^\s{0,3}#+\s+", "", b.text).strip()
            if level == 1 and drop_title and not title_seen:
                title_seen = True
                continue
            out.append(f'<h{level} id="{anchor(words)}">{inline(words, rewrite)}</h{level}>')
        elif b.kind == "paragraph":
            out.append(f"<p>{inline(' '.join(b.text.split()), rewrite)}</p>")
        elif b.kind == "list":
            out.append(_list(b, rewrite))
        elif b.kind == "quote":
            out.append(_quote(b, rewrite))
        elif b.kind == "table":
            out.append(_table(b, rewrite))
        elif b.kind == "code":
            out.append(_code(b))
    return "\n".join(out)


def first_paragraph(md: str) -> str:
    """The first paragraph as plain text: the page description and the
    starting point of the short platform versions."""
    for b in T.blocks(md):
        if b.kind == "paragraph":
            plain = re.sub(r"<!--.*?-->", "", b.text, flags=re.S)
            plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", plain)
            plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
            plain = re.sub(r"[`*_]", "", plain)
            return " ".join(plain.split())
    return ""
