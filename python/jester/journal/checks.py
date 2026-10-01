"""The four proof checks a saved version must pass before review.

    citations  every external link has a saved copy that answered 2xx, and
               every quotation of five words or more is attributed to a
               link whose saved copy really contains those words
    numbers    every number in the prose is linked to its proof:
                 [91.2%](run:12#accuracy)  a run of this journal's experiment
                                           (with #key, the value must match)
                 [3.5%](https://...)       a source whose saved copy has it
                 [4 hours](obs:labelling)  your own observation
               or sits in a block marked `<!-- backed-by: run:12,run:13 -->`,
               where every number must be a value those runs recorded (what
               `journal table` writes). A figure under `figures/` must be
               one `journal figure` drew, unedited.
               A name or version (`RTX 4060`, `Python 3.12`) goes in
               backticks, which says "this is not a measurement"
    lint       the article does not read like generated text, and its
               title is not clickbait
    origin     no paragraph a tool wrote speaks in the first person, gives
               an opinion, or claims an observation -- those are yours

Each check is deterministic and offline: it reads the article, the database
and the saved copies, never the network. Same version in, same verdict out.
That is what lets a verdict be stored per version and trusted later.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from jester import journal as J
from jester.journal import snapshots as S
from jester.journal import text as T

MIN_QUOTE_WORDS = 5


@dataclass
class Outcome:
    kind: str
    ok: bool = True
    findings: List[Dict] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)

    def fail(self, line: int, message: str) -> None:
        self.ok = False
        self.findings.append({"line": line, "message": message})

    def detail(self) -> Dict:
        return {"findings": self.findings, "stats": self.stats}


# ---- citations ----------------------------------------------------------------

_QUOTED = re.compile(r"“([^”]{3,})”|\"([^\"\n]{3,})\"")


def _found(quote: str, source_text: str) -> bool:
    """Is the quote in the source? Ellipses split it into parts that must all
    appear, in order -- a writer may skip words, not invent them."""
    hay = T.normalize(source_text)
    pos = 0
    for part in re.split(r"\.\.\.|…|\[\.\.\.\]", T.normalize(quote)):
        part = part.strip(" .,;:")
        if not part:
            continue
        i = hay.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


def _check_quote(out: Outcome, root: Path, db, line: int, quote: str, sources: List[T.Link]) -> None:
    out.stats["quotes"] += 1
    if not sources:
        out.fail(line, f'quote is not attributed to a source link: "{_short(quote)}"')
        return
    reasons = []
    for ln in sources:
        snap = S.latest(db, ln.target)
        body = S.read_text(root, snap) if S.is_ok(snap) else None
        if body is None:
            reasons.append(f"{ln.target}: {S.describe(snap)}")
        elif _found(quote, body):
            out.stats["quotes_found"] += 1
            return
        else:
            reasons.append(f"not in the saved copy of {ln.target}")
    out.fail(line, f'quote "{_short(quote)}" cannot be verified: ' + "; ".join(reasons))


def _short(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def check_citations(db: sqlite3.Connection, root: Path, md: str) -> Outcome:
    out = Outcome("citations", stats={"links": 0, "links_ok": 0, "quotes": 0, "quotes_found": 0})
    prose = T.prose_blocks(md)
    for idx, block in enumerate(prose):
        lks = T.links(block.text)
        external = [ln for ln in lks if ln.external]
        for ln in external:
            out.stats["links"] += 1
            snap = S.latest(db, ln.target)
            if S.is_ok(snap):
                out.stats["links_ok"] += 1
            else:
                out.fail(block.line_at(ln.start), f"{ln.target}: {S.describe(snap)}")
        sources = [ln for ln in external if not ln.image]
        if block.kind == "quote":
            body = T.quote_body(block)
            if body and len(T.words(body)) >= MIN_QUOTE_WORDS:
                nxt = prose[idx + 1] if idx + 1 < len(prose) else None
                if nxt is not None and nxt.line <= block.line + block.text.count("\n") + 2:
                    sources = sources + [ln for ln in T.links(nxt.text) if ln.external and not ln.image]
                plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
                _check_quote(out, root, db, block.line, plain, sources)
            continue
        masked = T.mask(block.text)
        for m in _QUOTED.finditer(masked):
            quote = m.group(1) or m.group(2)
            if len(T.words(quote)) >= MIN_QUOTE_WORDS:
                _check_quote(out, root, db, block.line_at(m.start()), quote, sources)
    return out


# ---- numbers ------------------------------------------------------------------

#: A figure a reader would take as a measurement. Digits glued to letters,
#: dots, slashes or hyphens are names, versions, dates or URLs and are not
#: matched: `gpt-oss-120b`, `v1.2.3`, `2026-10-01`, `H100`.
_NUMBER = re.compile(
    r"(?<![\w.\-:/#^·])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%|×|x(?![A-Za-z0-9]))?"
    r"(?![\w\-:]|\.\d)")
_DATE_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?\b")
_LIST_MARK = re.compile(r"^(\s*)(\d+[.)])(\s)", re.M)
_RANGE_DASH = re.compile(r"(?<=\d)-(?=\d)")


def _figures(masked: str) -> List[Tuple[int, int, str]]:
    def blank(m):
        return " " * len(m.group(0))

    text = _DATE_TIME.sub(blank, masked)
    text = _LIST_MARK.sub(lambda m: m.group(1) + " " * len(m.group(2)) + m.group(3), text)
    text = _RANGE_DASH.sub("–", text)
    out = []
    for m in _NUMBER.finditer(text):
        whole, frac, suffix = m.group(1), m.group(2), m.group(3)
        if not frac and not suffix and len(whole) == 4 and 1900 <= int(whole) <= 2099:
            continue  # a year
        out.append((m.start(), m.end(), m.group(0).strip()))
    return out


def _value(token: str) -> Tuple[Optional[float], int, bool]:
    pct = "%" in token
    digits = re.sub(r"[^\d.]", "", token.replace(",", ""))
    try:
        val = float(digits)
    except ValueError:
        return None, 0, pct
    decimals = len(digits.split(".", 1)[1]) if "." in digits else 0
    return val, decimals, pct


def _run_row(db, journal_id: int, run_id: str) -> Optional[sqlite3.Row]:
    if not run_id.isdigit():
        return None
    return db.execute(
        "SELECT r.* FROM experiment_run r JOIN experiment e ON e.id = r.experiment_id "
        "WHERE r.id = ? AND e.journal_id = ?", (int(run_id), journal_id)).fetchone()


def _dig(result: Dict, path: str):
    cur = result
    for key in path.split("."):
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
            cur = cur[int(key)]
        else:
            return None
    return cur


def _verify_run(db, journal_id: int, target: str, token: str) -> Optional[str]:
    """None if `run:N[#key]` backs `token`; otherwise why not."""
    ref = target[len("run:"):]
    run_id, _, path = ref.partition("#")
    row = _run_row(db, journal_id, run_id)
    if row is None:
        return f"{target}: no run {run_id or '?'} in this journal's experiments"
    if row["status"] != "ok":
        return f"{target}: that run did not finish ok ({row['status']})"
    if not path or token is None:
        return None
    got = _dig(json.loads(row["result"] or "{}"), path)
    if isinstance(got, bool) or not isinstance(got, (int, float)):
        return f"{target}: the run's result has no number at {path!r}"
    shown, decimals, pct = _value(token)
    if shown is None:
        return None
    if round(got, decimals) == shown or (pct and round(got * 100, decimals) == shown):
        return None
    return f"{target}: the text says {token} but the run recorded {got}"


def _number_in(token: str, body: str) -> bool:
    shown = re.sub(r"[^\d.]", "", token.replace(",", ""))
    hay = body.replace(",", "")
    return re.search(r"(?<![\d.])" + re.escape(shown) + r"(?![\d])", hay) is not None


def _verify_link(db, root: Path, journal_id: int, target: str, token: Optional[str]) -> Tuple[str, Optional[str]]:
    """(kind, problem) for a number linked to `target`."""
    if target.startswith("run:"):
        return "run", _verify_run(db, journal_id, target, token)
    if target.startswith("obs:"):
        return "observation", None
    if target.startswith(("http://", "https://")):
        snap = S.latest(db, target)
        body = S.read_text(root, snap) if S.is_ok(snap) else None
        if body is None:
            return "source", f"{target}: {S.describe(snap)}"
        if token is not None and not _number_in(token, body):
            return "source", f"{token} does not appear in the saved copy of {target}"
        return "source", None
    return "unknown", f"{target}: not a kind of proof (use run:, obs: or a source link)"


def _leaves(value) -> List[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [x for v in value.values() for x in _leaves(v)]
    if isinstance(value, list):
        return [x for v in value for x in _leaves(v)]
    return []


def _block_runs(db, journal_id: int, backing: str) -> Tuple[Optional[List[float]], Optional[str]]:
    """For `<!-- backed-by: run:1,run:2 -->`: every number those runs recorded,
    or why the proof does not hold."""
    targets = [t.strip() for t in backing.split(",") if t.strip()]
    if not all(t.startswith("run:") for t in targets):
        return None, None
    values: List[float] = []
    for t in targets:
        run_id = t[len("run:"):].partition("#")[0]
        row = _run_row(db, journal_id, run_id)
        if row is None:
            return None, f"{t}: no run {run_id or '?'} in this journal's experiments"
        if row["status"] != "ok":
            return None, f"{t}: that run did not finish ok ({row['status']})"
        values += _leaves(json.loads(row["result"] or "{}"))
    return values, None


def _shown_matches(token: str, values: List[float]) -> bool:
    shown, decimals, pct = _value(token)
    if shown is None:
        return False
    return any(round(v, decimals) == shown or (pct and round(v * 100, decimals) == shown) for v in values)


def _check_figures(db, root, journal_id: int, block: T.Block, out: Outcome) -> None:
    from jester.journal import lab as X

    row = db.execute("SELECT path FROM journal WHERE id = ?", (journal_id,)).fetchone()
    article_dir = Path(row["path"]).parent.as_posix() if row and row["path"] else ""
    for ln in T.links(block.text):
        if ln.image and ln.target.startswith("figures/"):
            out.stats["figures"] += 1
            problem = X.artifact_problem(db, root, journal_id, article_dir, ln.target)
            if problem:
                out.fail(block.line_at(ln.start), problem)


def check_numbers(db: sqlite3.Connection, root: Path, journal_id: int, md: str) -> Outcome:
    out = Outcome("numbers", stats={"numbers": 0, "run": 0, "source": 0, "observation": 0,
                                    "block": 0, "unbacked": 0, "figures": 0})
    for block in T.prose_blocks(md):
        if db is not None:
            _check_figures(db, root, journal_id, block, out)
        lks = [ln for ln in T.links(block.text) if not ln.image]
        block_problem, block_values = None, None
        if block.backing:
            block_values, block_problem = _block_runs(db, journal_id, block.backing)
            if block_values is None and block_problem is None:
                _, block_problem = _verify_link(db, root, journal_id, block.backing, None)
        for start, _end, token in _figures(T.mask(block.text)):
            out.stats["numbers"] += 1
            line = block.line_at(start)
            owner = next((ln for ln in lks if ln.text_start <= start < ln.text_end), None)
            if owner is not None:
                kind, problem = _verify_link(db, root, journal_id, owner.target, token)
                if problem:
                    out.fail(line, problem)
                else:
                    out.stats[kind] += 1
            elif block.backing:
                if block_problem:
                    out.fail(line, f"{token}: block proof {block_problem}")
                elif block_values is not None and not _shown_matches(token, block_values):
                    out.fail(line, f"{token} is not a value recorded by {block.backing}")
                else:
                    out.stats["block"] += 1
            else:
                out.stats["unbacked"] += 1
                out.fail(line, f"{token} has no proof: link it to a run, a source or obs:, "
                               "or put it in `backticks` if it is a name or version")
    return out


# ---- lint ---------------------------------------------------------------------

#: Phrases that mark generated prose, from Wikipedia's "Signs of AI writing"
#: and what HN/DEV moderators cite. Strong tells score 2, the rest 1. Words
#: engineers use honestly ("robust", "leverage") are deliberately left out:
#: the cost of a false alarm is that the writer stops trusting the lint.
STRONG_TELLS = (
    r"\bdelv(e|es|ed|ing)\b", r"\btapestry\b", r"\btestament to\b",
    r"\bin today'?s (fast-paced|digital|ever-changing|ever-evolving|modern) ",
    r"\bever-evolving\b", r"\bgame[- ]changer\b", r"\bunlock(s|ing)? (the|its|their) (full )?potential\b",
    r"\bharness(es|ing)? the power\b", r"\bnavigat(e|ing) the complexit", r"\bin the realm of\b",
    r"\bembark(s|ing)? on\b", r"\blook no further\b", r"\ba myriad of\b",
    r"\b(it'?s|it is) (worth noting|important to note)\b", r"\bplays? an? (crucial|pivotal|vital|key) role\b",
)
TELLS = (
    r"\bpivotal\b", r"\bseamless(ly)?\b", r"\bcrucial(ly)?\b", r"\bvibrant\b", r"\bintricate\b",
    r"\bmeticulous(ly)?\b", r"\bshowcas(e|es|ed|ing)\b", r"\bunderscor(e|es|ed|ing)\b",
    r"\bfoster(s|ed|ing)?\b", r"\bempower(s|ed|ing)?\b", r"\belevat(e|es|ing)\b",
    r"\bstreamlin(e|es|ed|ing)\b", r"\btransformative\b", r"\brevolutioni[sz]\w*",
    r"\bparadigm\b", r"\bsynerg\w*", r"\bholistic\b", r"\bat its core\b",
    r"\bfurthermore\b", r"\bmoreover\b", r"\badditionally\b", r"\bin (conclusion|summary)\b",
    r"\bwhether you'?re\b", r"\b(let'?s )?dive (deep |right )?into\b", r"\bdeep dive\b",
    r"\bever-changing\b", r"\brealm\b", r"\blandscape of\b", r"\bnuanced\b", r"\bcomprehensive\b",
    # The rest of Wikipedia's "words to watch" list, and the two verbs every
    # list of LLM tells carries ("leverage", "utilize").
    r"\benhanc(e|es|ed|ing)\b", r"\bessential\b", r"\bsignificant(ly)?\b", r"\binterplay\b",
    r"\bgarner(s|ed|ing)?\b", r"\benduring\b", r"\balign(s|ed)? with\b", r"\b(in)?valuable\b",
    r"\bhighlight(s|ed|ing)\b", r"\bemphasiz(e|es|ed|ing)\b", r"\bleverag(e|es|ed|ing)\b",
    r"\butiliz(e|es|ed|ing)\b",
)
#: "Superficial analysis" in Wikipedia's terms: a sentence that ends by
#: tacking on a present participle that claims a benefit.
_PARTICIPLE_TAIL = re.compile(r",\s+(ultimately\s+)?(ensuring|making|allowing|enabling|leading to|resulting in|"
                              r"highlighting|emphasizing|reflecting|fostering|paving the way)\b", re.I)
#: A sentence that opens by summing up what the paragraph already said.
_SUMMARY_OPENER = re.compile(r"(?:^|[.!?]\s+)(overall|ultimately|in essence|in summary|in conclusion|"
                             r"therefore|by (following|leveraging|respecting|adopting|implementing|"
                             r"combining|understanding|utilizing|embracing))\b", re.I)
_NOT_JUST = re.compile(r"\b(not (just|only|merely)\b[^.!?]{1,80}?\bbut\b|(isn'?t|is not|it'?s not) (just|only|merely)\b"
                       r"[^.!?]{1,60}?[,;—\-]+\s*(it'?s|it is)\b)", re.I)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
_OPENER = re.compile(r"^\s*(have you ever|imagine (a|if|you)|picture this)\b", re.I | re.M)
_BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")
FAIL_AT = 3

_CLICKBAIT = (
    r"you won'?t believe", r"blow your mind", r"ultimate guide", r"everything you need to know",
    r"^\s*\d+\s+(ways|things|reasons|tips|tricks|secrets)\b", r"this one (weird )?trick",
    r"game[- ]changer", r"\bshocking\b", r"\binsane\b",
)


def lint_title(title: str) -> List[str]:
    if not title:
        return ["the article has no # title"]
    out = []
    letters = re.sub(r"[^A-Za-z]", "", title)
    if len(letters) > 8 and letters.isupper():
        out.append("the title is in capitals")
    if "!" in title:
        out.append("the title has an exclamation mark")
    for pat in _CLICKBAIT:
        if re.search(pat, title, re.I):
            out.append(f"the title reads as clickbait ({re.search(pat, title, re.I).group(0).strip()})")
    if len(title) > 100:
        out.append(f"the title is {len(title)} characters; keep it under 100")
    return out


def lint_prose(md: str) -> Tuple[int, List[Dict], Dict]:
    """(score, findings, stats) for the prose. Score >= FAIL_AT fails."""
    blocks = [b for b in T.prose_blocks(md) if b.kind != "table"]
    findings: List[Dict] = []
    score = 0
    n_words = 0
    em = bold = exclaim = 0
    for b in blocks:
        masked = T.mask(b.text)
        n_words += len(T.words(masked))
        for weight, pats in ((2, STRONG_TELLS), (1, TELLS)):
            for pat in pats:
                for m in re.finditer(pat, masked, re.I):
                    score += weight
                    findings.append({"line": b.line_at(m.start()),
                                     "message": f"reads as generated: \"{m.group(0).strip()}\""})
        for m in _NOT_JUST.finditer(masked):
            score += 2
            findings.append({"line": b.line_at(m.start()),
                             "message": "the \"not just X, but Y\" construction"})
        for m in _OPENER.finditer(masked):
            score += 1
            findings.append({"line": b.line_at(m.start()), "message": "a generated-style opener"})
        for m in _PARTICIPLE_TAIL.finditer(masked):
            score += 1
            findings.append({"line": b.line_at(m.start()),
                             "message": f"a tacked-on benefit clause (\"{m.group(0).strip(', ')}\")"})
        for m in _SUMMARY_OPENER.finditer(masked):
            score += 1
            findings.append({"line": b.line_at(m.start()),
                             "message": f"a summing-up sentence (\"{m.group(1)}\")"})
        if _EMOJI.search(masked):
            score += 1
            findings.append({"line": b.line, "message": "emoji in the prose"})
        em += masked.count("—") + len(re.findall(r"\s--\s", masked))
        bold += len(_BOLD.findall(b.text))
        exclaim += masked.count("!")
    per100 = (lambda n: 100.0 * n / max(n_words, 1))
    if em >= 2 and per100(em) > 1.0:
        score += 2 if em >= 4 and per100(em) > 2.0 else 1
        findings.append({"line": 0, "message": f"{em} em dashes in {n_words} words"})
    if bold >= 3 and per100(bold) > 2.0:
        score += 1
        findings.append({"line": 0, "message": f"{bold} bold phrases in {n_words} words"})
    if exclaim >= 2:
        score += 1
        findings.append({"line": 0, "message": f"{exclaim} exclamation marks"})
    stats = {"words": n_words, "score": score, "fail_at": FAIL_AT, "em_dashes": em, "bold": bold}
    return score, findings, stats


def check_lint(md: str) -> Outcome:
    out = Outcome("lint")
    score, findings, stats = lint_prose(md)
    out.stats = stats
    title_problems = lint_title(T.title_of(md))
    for p in title_problems:
        out.fail(1, p)
    if score >= FAIL_AT:
        out.ok = False
    out.findings.extend(findings)
    return out


# ---- origin -------------------------------------------------------------------

#: "I" only as a capital (so "i.e." is not a speaker); the rest in any case,
#: because "We" and "My" open sentences.
_FIRST_PERSON = re.compile(r"\b(I|I'm|I've|I'd|I'll)\b|\b(?i:me|my|mine|we|we're|we've|our|ours|us)\b")
_OPINION = re.compile(r"\b(I think|I believe|in my (view|opinion)|should|must|surprising(ly)?|"
                      r"clearly|obviously|unfortunately|fortunately|we recommend|I recommend)\b", re.I)


def check_origin(db: sqlite3.Connection, journal_id: int, md: str) -> Outcome:
    generated = J.generated_hashes(db, journal_id)
    out = Outcome("origin", stats={"paragraphs": 0, "generated": 0})
    for block in T.prose_blocks(md):
        out.stats["paragraphs"] += 1
        role = generated.get(T.paragraph_hash(block.text))
        if role is None:
            continue
        out.stats["generated"] += 1
        masked = T.mask(block.text)
        voice = _FIRST_PERSON.search(masked)
        if voice:
            out.fail(block.line, f"written by {role} but speaks in the first person "
                                 f"(\"{voice.group(0)}\"); that sentence is yours to write")
        opinion = _OPINION.search(masked)
        if opinion:
            out.fail(block.line, f"written by {role} but gives an opinion "
                                 f"(\"{opinion.group(0)}\"); that sentence is yours to write")
        if any(ln.target.startswith("obs:") for ln in T.links(block.text)):
            out.fail(block.line, f"written by {role} but claims an observation (obs:); only you saw it")
    return out


# ---- all of them ----------------------------------------------------------------

def run_all(db: sqlite3.Connection, root: Path, journal_id: int, md: str) -> List[Outcome]:
    return [check_citations(db, root, md), check_numbers(db, root, journal_id, md),
            check_lint(md), check_origin(db, journal_id, md)]


def check_saved(db: sqlite3.Connection, root: Path, slug: str,
                at: Optional[str] = None) -> Tuple[sqlite3.Row, List[Outcome]]:
    """Run every check on the latest saved version and store the verdicts.

    Refuses if the file differs from that version: a verdict is stored
    against a version, so it must be about exactly that version's text.
    """
    from jester.journal import files as F

    j = J.get(db, slug)
    rev = J.latest_revision(db, j["id"])
    path = Path(root) / j["path"] if j["path"] else None
    if rev is None or path is None or not path.exists():
        raise J.JournalError("nothing saved to check yet (save)")
    if F.content_sha256(path) != rev["content_sha256"]:
        raise J.JournalError("the file has unsaved changes; save first, so the verdict "
                             "belongs to a version")
    md = path.read_text(encoding="utf-8")
    outcomes = run_all(db, root, j["id"], md)
    for o in outcomes:
        J.record_check(db, rev["id"], o.kind, o.ok, o.detail(), at=at)
    return rev, outcomes
