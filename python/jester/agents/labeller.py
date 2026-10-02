"""Name a cluster: turn a bag of similar comments into a stated theme.

A cluster arrives as N chunks of text that a cosine threshold decided belong
together. That is a mathematical fact about vectors, not a claim anyone can
read. The labeller's job is to state what the members actually have in common —
or to say plainly that they have nothing in common, which is a real outcome
when the threshold is loose or the corpus is thin.

WHY A SEPARATE AGENT rather than reusing the synthesizer: the synthesizer's job
is to propose a PRODUCT, and it is instructed to. Asked to name a group it
invents a solution and names the group after it, so the label describes an app
nobody mentioned instead of the complaint everybody did. Naming and solving are
different tasks and the prompts pull in different directions.

The fallback is deliberately dumb — the most common words, and an explicit
"unnamed theme" — because a fabricated label on an unreachable model is worse
than an obviously mechanical one. A reviewer can see that the second is
machine-made; the first reads exactly like a real answer.
"""

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, runtime_checkable

from jester.llm import _GroqAgent, _OllamaAgent, provider_for

#: Words too common to describe anything. Not a full stop-word list: only what
#: actually swamps the fallback labels on this corpus.
_STOP = frozenset("""
a an the and or but if then than that this these those there here it its it's
is are was were be been being am do does did doing have has had having
i you he she they we me him her them us my your his their our mine yours
to of in on at by for with from into over under about as not no nor so such
can could will would shall should may might must just also very really quite
what which who whom whose when where why how all any both each few more most
other some only own same too s t don now get got make made use used using
one two like know think want need thing things way ways lot lots time
""".split())

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#.-]{2,}")


@dataclass
class ClusterLabel:
    label: str
    problem_statement: str
    #: True when the model said the members do NOT share a theme. A cluster is
    #: a threshold's opinion; the label step is the first thing able to
    #: disagree with it, and that disagreement is worth keeping.
    incoherent: bool = False
    #: WHO wrote the label: the model's name, or "fake-labeller".
    model: str = ""


@runtime_checkable
class LabellerLLM(Protocol):
    def label(self, texts: Sequence[str]) -> ClusterLabel: ...


def keyword_label(texts: Sequence[str], k: int = 4) -> str:
    """The most distinctive words in a cluster, joined.

    Used by the fallback and as the seed for the empty case. Reads as
    mechanical on purpose.
    """
    counts = Counter()
    for t in texts:
        for w in _WORD.findall((t or "").lower()):
            if w not in _STOP:
                counts[w] += 1
    top = [w for w, _ in counts.most_common(k)]
    return " · ".join(top) if top else "unnamed theme"


class FakeLabeller:
    """Deterministic stand-in. Names the cluster after its commonest words."""

    name = "fake-labeller"

    def label(self, texts: Sequence[str]) -> ClusterLabel:
        kw = keyword_label(texts)
        return ClusterLabel(
            label=kw,
            problem_statement=(
                f"{len(texts)} related comment(s) mentioning {kw}."
                if texts else "Empty cluster."
            ),
            model=self.name,
        )


class _LabellerRole:
    """The labeller's prompt and parsing, shared by both providers. Falls back
    to keywords when the model is unreachable or its reply is unusable."""

    LABEL_SYSTEM = (
        "You are given several comments that an embedding model grouped "
        "together. Say what they actually have in common.\n\n"
        "Reply with a JSON object and nothing else:\n"
        '  {"label": str, "problem_statement": str, "coherent": bool}\n\n'
        "label\n"
        "  - The shared problem, 3 to 7 words, in the commenters' own register.\n"
        "  - Name the PROBLEM, never a product or a solution. "
        '"Backups fail silently after upgrades", not "Backup monitoring tool".\n'
        "  - No marketing words. No 'seamless', 'robust', 'platform'.\n\n"
        "problem_statement\n"
        "  - Two or three sentences on what these people are struggling with "
        "and why it matters to them.\n"
        "  - Ground every claim in the comments. Do not add a user base, a "
        "market size, or a cause nobody stated.\n"
        "  - Plain sentences. No bullets, no markdown.\n\n"
        "coherent\n"
        "  - false if these comments do NOT share a real theme and were "
        "grouped by shared vocabulary alone.\n"
        "  - Say false when it is false. A grouping that does not hold is a "
        "useful finding, not a failure to be papered over."
    )

    def label(self, texts: Sequence[str]) -> ClusterLabel:
        if not texts:
            return ClusterLabel(label="unnamed theme", problem_statement="Empty cluster.")
        corpus = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        obj = self._obj(self.LABEL_SYSTEM, corpus)
        if obj is not None:
            if all(isinstance(obj.get(k), str) and obj.get(k).strip()
                   for k in ("label", "problem_statement")):
                return ClusterLabel(
                    label=obj["label"].strip(),
                    problem_statement=obj["problem_statement"].strip(),
                    # Absent `coherent` is treated as coherent: the model not
                    # objecting is not the same as the model objecting, and
                    # defaulting to "incoherent" would flag every reply from a
                    # model that simply omitted the field.
                    incoherent=obj.get("coherent") is False,
                    model=self._model,
                )
            self._reject("reply is missing label or problem_statement")
        return FakeLabeller().label(texts)


class GroqLabeller(_LabellerRole, _GroqAgent):
    """Live labeller via Groq."""

    def __init__(self, model="openai/gpt-oss-120b", **kw):
        super().__init__(model, **kw)


class OllamaLabeller(_LabellerRole, _OllamaAgent):
    """Live labeller over a local Ollama model."""

    #: A cluster preview is up to 12 comments; the synthesizer's allowance.
    TIMEOUT_S = 300.0

    def __init__(self, model="qwen3:8b", client=None, timeout=None):
        super().__init__(model, client=client, timeout=timeout)


def select_labeller(cfg, provider: Optional[str] = None) -> LabellerLLM:
    """Pick a labeller the same way the other agents are picked.

    Follows the SYNTHESIZER, provider and model: naming a theme and framing an
    idea are the same class of work, and an operator who set up one good model
    should not have to discover a second knob to get a labelled cluster
    instead of a bag of keywords.

    It used to read `cfg.models`, which the loaded config does not have (the
    YAML block is copied into `synthesizer_model`), so the configured model was
    ignored; and it read only `synthesizer_provider`, never `llm_provider`, so
    a profile serving every role from Ollama got the keyword stand-in.
    """
    provider = (provider or provider_for(cfg, "synthesizer")).lower()
    model = (getattr(cfg, "synthesizer_model", "") or "").strip()
    if provider == "groq":
        return GroqLabeller(model=model or "openai/gpt-oss-120b")
    if provider == "ollama":
        return OllamaLabeller(model=model or "qwen3:8b")
    return FakeLabeller()


def label_clusters(clusters: List, llm: LabellerLLM, preview: int = 12) -> None:
    """Label each cluster in place from its most central members.

    Most central, not first: a label written from the cluster's edge describes
    the outlier that only just qualified.
    """
    for c in clusters:
        got = llm.label(c.preview(preview))
        c.label = got.label
        c.problem_statement = got.problem_statement
        # Carried so the console can mark it. A cluster the model refused to
        # name is still shown — hiding it would leave a silent gap between the
        # cluster count and the visible list.
        setattr(c, "incoherent", got.incoherent)
