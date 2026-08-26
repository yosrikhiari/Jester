"""Split a nugget's text into embeddable chunks.

WHY CHUNK AT ALL when the median nugget is a few hundred characters: because
the long ones are the ones worth splitting. A 1,400-character forum post
(config/thresholds.yaml lets those through deliberately, since forum prose runs
longer than a Reddit comment) routinely carries two or three distinct
complaints. Embedded whole, it produces one averaged vector that sits between
all of them and is close to none — so it joins no theme, or worse, drags an
unrelated theme toward its midpoint.

Split, the same post can legitimately land in two themes, which is what
actually happened in the text.

Deliberately NOT a token-aware splitter. That would mean a tokenizer
dependency, and this repo keeps its runtime list to four packages on purpose
(see env.py on why python-dotenv was refused). Character budgets tuned against
nomic-embed-text's 8192-token window are nowhere near the boundary — the cap
here exists to separate ideas, not to fit the model.
"""

import re
from dataclasses import dataclass
from typing import List

#: Chunk budget in characters. Comfortably inside any embedding model's window;
#: chosen so a chunk holds ONE line of argument rather than a whole post.
MAX_CHARS = 700

#: Below this, a trailing fragment is folded back into the previous chunk
#: instead of standing alone. A 30-character tail ("Anyone else?") is not a
#: theme, but it will happily anchor a cluster of other 30-character tails.
MIN_CHARS = 120

#: Characters of the preceding chunk repeated at the start of the next, so a
#: point split across a boundary keeps its context on both sides.
OVERLAP = 120

#: Sentence-ish boundaries. Kept crude on purpose: comment prose is full of
#: ellipses, code, and missing terminal punctuation, and an over-clever splitter
#: on that input fails in ways that are harder to see than a blunt one.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n{2,}")


@dataclass
class Chunk:
    """One embeddable span of a nugget."""

    nugget_key: str
    index: int
    text: str

    @property
    def point_key(self) -> str:
        """Stable id for the vector store: a nugget's chunk 2 is always the
        same point, so re-indexing overwrites rather than accumulating."""
        return f"{self.nugget_key}#{self.index}"


def _split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENTENCE_END.split(text) if p and p.strip()]
    return parts or ([text.strip()] if text.strip() else [])


def _hard_split(sentence: str) -> List[str]:
    """A single 'sentence' longer than the budget — a wall-of-text paragraph
    with no punctuation, or pasted output. Split on whitespace so a word is
    never cut in half."""
    words, out, cur = sentence.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_CHARS:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


def chunk_text(text: str, key: str = "") -> List[Chunk]:
    """Split one nugget into chunks, in order.

    Short text yields exactly one chunk — the common case, and it must stay
    cheap: 1,700 nuggets averaging 359 characters should not pay for a splitter
    that has nothing to split.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= MAX_CHARS:
        return [Chunk(nugget_key=key, index=0, text=text)]

    pieces: List[str] = []
    for sentence in _split_sentences(text):
        if len(sentence) > MAX_CHARS:
            pieces.extend(_hard_split(sentence))
        else:
            pieces.append(sentence)

    chunks: List[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > MAX_CHARS:
            chunks.append(cur)
            # Carry the tail of the finished chunk into the next one so a point
            # split across the boundary is readable from either side.
            tail = cur[-OVERLAP:].lstrip()
            cur = f"{tail} {piece}".strip() if tail else piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        chunks.append(cur)

    # Fold a runt tail back into its predecessor rather than letting it stand
    # as its own vector.
    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHARS:
        chunks[-2] = f"{chunks[-2]} {chunks[-1]}".strip()
        chunks.pop()

    return [Chunk(nugget_key=key, index=i, text=c) for i, c in enumerate(chunks)]


def chunk_nugget(row) -> List[Chunk]:
    """Chunks for one nugget row (sqlite3.Row or dict).

    Embeds the extracted insight ALONGSIDE the raw text when the two differ:
    the insight is the extractor's one-line statement of the problem, which is
    exactly the signal themes should form around, while the raw text carries
    the specifics that make a theme concrete. Embedding the raw text alone lets
    shared vocabulary ("Docker", "my setup") pull unrelated complaints
    together.
    """
    def field(name):
        try:
            v = row[name]
        except (KeyError, IndexError, TypeError):
            v = None
        return (v or "").strip()

    key = field("unique_key")
    raw = field("raw_text")
    insight = field("extracted_insight")
    body = raw
    if insight and insight not in raw:
        body = f"{insight}\n\n{raw}" if raw else insight
    return chunk_text(body, key)
