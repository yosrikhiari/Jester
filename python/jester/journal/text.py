"""Reading an article's Markdown the way the proof checks need it.

Not a Markdown renderer. The checks only need to know, for each piece of
text, whether it is prose a reader will take as a claim (paragraphs, lists,
quotes, tables) or something that is not (code, headings, comments), plus
where the links are, so a number inside `[91%](run:12)` counts as backed and
a number inside a URL is not counted at all.

Every finding carries a line number, so a block remembers the line it
started on and offsets inside it map back to lines.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

PROSE_KINDS = frozenset({"paragraph", "list", "quote", "table"})

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_QUOTE = re.compile(r"^\s{0,3}>")
_TABLE = re.compile(r"^\s*\|")
_BACKED_BY = re.compile(r"<!--\s*backed-by:\s*(\S+)\s*-->")
_INLINE_CODE = re.compile(r"`[^`\n]*`")
_INLINE_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: [text](target) and ![alt](target). Nested brackets in link text are rare in
#: an article and not worth a real parser; titles after the target are allowed.
_LINK = re.compile(r"(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


@dataclass
class Block:
    kind: str
    line: int          # 1-based line of the block's first line
    text: str          # the block's lines joined with "\n"
    backing: str = ""  # target from a `<!-- backed-by: ... -->` just above it

    def line_at(self, offset: int) -> int:
        return self.line + self.text.count("\n", 0, offset)


@dataclass
class Link:
    start: int         # offset of "[" (or "!") in the block text
    end: int           # offset just past ")"
    text_start: int
    text_end: int
    text: str
    target: str
    image: bool

    @property
    def external(self) -> bool:
        return self.target.startswith(("http://", "https://"))


def _kind(line: str) -> str:
    if _HEADING.match(line):
        return "heading"
    if _QUOTE.match(line):
        return "quote"
    if _TABLE.match(line):
        return "table"
    if _LIST.match(line):
        return "list"
    return "paragraph"


def blocks(md: str) -> List[Block]:
    """Split an article into blocks, each tagged with its kind and first line."""
    out: List[Block] = []
    # A byte-order mark (PowerShell's utf8, old Notepad) would glue itself to
    # the first line and hide the title: found on the first live run.
    lines = md.lstrip("﻿").splitlines()
    i, backing = 0, ""
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        start = i
        if _FENCE.match(line):
            fence = _FENCE.match(line).group(1)
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                i += 1
            i += 1
            out.append(Block("code", start + 1, "\n".join(lines[start:i])))
            continue
        if line.lstrip().startswith("<!--"):
            while i < len(lines) and "-->" not in lines[i]:
                i += 1
            i += 1
            text = "\n".join(lines[start:i])
            out.append(Block("comment", start + 1, text))
            m = _BACKED_BY.search(text)
            backing = m.group(1) if m else ""
            continue
        kind = _kind(line)
        i += 1
        if kind != "heading":
            # A block runs until a blank line or a line that starts a
            # different kind of block. A list keeps its wrapped lines.
            while i < len(lines) and lines[i].strip():
                nxt = lines[i]
                if _FENCE.match(nxt) or _HEADING.match(nxt) or nxt.lstrip().startswith("<!--"):
                    break
                nk = _kind(nxt)
                if nk != kind and not (kind == "list" and nk == "paragraph"):
                    break
                i += 1
        out.append(Block(kind, start + 1, "\n".join(lines[start:i]), backing))
        backing = ""
    return out


def links(text: str) -> List[Link]:
    return [Link(m.start(), m.end(), m.start(2), m.end(2), m.group(2), m.group(3), bool(m.group(1)))
            for m in _LINK.finditer(text)]


def mask(text: str) -> str:
    """Blank out what is not prose -- inline code, inline comments, link
    targets -- keeping every offset where it was, so positions found in the
    masked text are positions in the original."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))

    text = _INLINE_CODE.sub(blank, text)
    text = _INLINE_COMMENT.sub(blank, text)
    out = list(text)
    for ln in links(text):
        for k in range(ln.text_end, ln.end):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def prose_blocks(md: str) -> List[Block]:
    return [b for b in blocks(md) if b.kind in PROSE_KINDS]


def title_of(md: str) -> str:
    for b in blocks(md):
        if b.kind == "heading" and re.match(r"^\s{0,3}#\s", b.text):
            return re.sub(r"^\s{0,3}#\s+", "", b.text).strip()
    return ""


_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", "−": "-", " ": " "})


def normalize(text: str) -> str:
    """The form two texts are compared in: same quotes, dashes, spacing, case.

    Emphasis markers are dropped too, because a writer bolding a word inside
    a quote has not changed what the source said.
    """
    text = unicodedata.normalize("NFKC", text).translate(_SMART)
    text = text.replace("…", "...")
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def paragraph_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def words(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text)


def quote_body(block: Block) -> Optional[str]:
    """The quoted words of a `>` block, without its attribution line."""
    kept = []
    for raw in block.text.splitlines():
        line = re.sub(r"^\s{0,3}>\s?", "", raw)
        if re.match(r"^\s*(—|--|-)\s", line):
            continue
        kept.append(line)
    body = " ".join(kept).strip()
    return body or None
