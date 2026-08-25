"""LLM client abstraction for the agents. Fake* classes are deterministic for tests/mock."""
import json
import re
from dataclasses import dataclass
from typing import List, Optional, Protocol, runtime_checkable

from jester.models import Idea, Nugget


@dataclass
class NuggetDraft:
    extracted_insight: str
    category: str


ALLOWED_CATEGORIES = {
    "pain_point",
    "feature_request",
    "workaround",
    "complaint",
    "question",
}

# §37.15 reproducibility (v3.8 #2/v3.7 #4): bump when a prompt or the rubric
# changes so runs.models_used can diff a score shift back to its cause.
PROMPT_VERSIONS = {"extractor": 1, "synthesizer": 1, "critic": 1}
RUBRIC_VERSION = 1


@runtime_checkable
class ExtractorLLM(Protocol):
    def extract(self, comment_body: str) -> NuggetDraft: ...


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    t = (text or "").strip()
    # Reasoning models (qwen3 et al.) emit a <think> block before the answer.
    t = _THINK_RE.sub("", t).strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()


def _parse_json_object(content: str):
    """Strictly a dict or None — anything else is treated as malformed output."""
    try:
        obj = json.loads(_strip_fences(content))
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _field(obj, name, default=None):
    """Read `name` from a pydantic model OR a dict (§37.16: the installed
    ollama SDK returns ChatResponse objects; older paths/tests use dicts)."""
    if obj is None:
        return default
    if hasattr(obj, name):
        return getattr(obj, name)
    if hasattr(obj, "get"):
        return obj.get(name, default)
    return default


def _chat_content(resp) -> str:
    message = _field(resp, "message") or {}
    return _field(message, "content", "") or ""


_VALID_LLM_PROVIDERS = ("fake", "ollama")


def _require_provider(provider: str) -> None:
    if provider not in _VALID_LLM_PROVIDERS:
        raise ValueError(
            f"llm_provider={provider!r} not supported; valid: {', '.join(_VALID_LLM_PROVIDERS)}"
        )


def select_extractor_llm(cfg):
    _require_provider(getattr(cfg, "llm_provider", "fake"))
    if cfg.llm_provider == "ollama":
        return OllamaLLM(model=cfg.extractor_model)
    return FakeLLM()


def select_synthesizer_llm(cfg):
    _require_provider(getattr(cfg, "llm_provider", "fake"))
    if cfg.llm_provider == "ollama":
        return OllamaSynthesizerLLM(model=cfg.synthesizer_model)
    return FakeSynthesizerLLM()


def select_critic_llm(cfg):
    _require_provider(getattr(cfg, "llm_provider", "fake"))
    if cfg.llm_provider == "ollama":
        return OllamaCriticLLM(model=cfg.critic_model)
    return FakeCriticLLM()


class FakeLLM:
    """Deterministic stand-in. Classifies by simple keyword heuristics."""

    def extract(self, comment_body: str) -> NuggetDraft:
        body = (comment_body or "").lower()
        if any(k in body for k in ("wish", "please build", "someone", "should")):
            category = "feature_request"
        elif any(k in body for k in ("insane", "painful", "weekend", "drops", "hate")):
            category = "pain_point"
        else:
            category = "pain_point"
        return NuggetDraft(extracted_insight=comment_body.strip()[:200], category=category)


class OllamaLLM:
    """Real LLM via Ollama (§37.10). The transport is injectable so parsing,
    validation, and fallback rules are testable without the ollama package;
    the lazy import only happens when no client is provided."""

    EXTRACT_SYSTEM = (
        "You classify developer-pain comments. Reply with ONLY a JSON object: "
        '{"category": <one of pain_point|feature_request|workaround|complaint|question>, '
        '"insight": "<one-sentence distilled insight>"}.'
    )

    def __init__(self, model: str = "claude-sonnet-4-6", client=None):
        self._model = model
        self._client = client

    def _ensure_client(self):
        if self._client is None:
            from ollama import Client  # lazy import: mock mode needs no package
            self._client = Client()
        return self._client

    def _complete(self, system: str, user: str) -> str:
        resp = self._ensure_client().chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return _chat_content(resp)

    def extract(self, comment_body: str) -> NuggetDraft:
        body = comment_body or ""
        obj = _parse_json_object(self._complete(self.EXTRACT_SYSTEM, body))
        if obj is not None:
            insight = str(obj.get("insight") or body.strip()[:200])
            category = obj.get("category")
            if category not in ALLOWED_CATEGORIES:
                category = "pain_point"  # mirror extractor.extract's coercion rule
            return NuggetDraft(extracted_insight=insight, category=category)
        return FakeLLM().extract(body)  # deterministic fallback: dead model != dead run


@dataclass
class IdeaDraft:
    """Structured synthesis output from the synthesizer LLM."""

    title: str
    problem_statement: str
    proposed_solution: str


@runtime_checkable
class SynthesizerLLM(Protocol):
    def synthesize(self, nuggets: List[Nugget]) -> IdeaDraft: ...


class FakeSynthesizerLLM:
    """Deterministic stand-in: frames an idea from the clustered nuggets."""

    def synthesize(self, nuggets: List[Nugget]) -> IdeaDraft:
        insights = [n.extracted_insight for n in nuggets if n.extracted_insight]
        joined = " ".join(insights).strip() or "unspecified pain point"
        platform = nuggets[0].platform if nuggets else "unknown"
        title = f"[{platform}] {joined[:60]}".strip()
        problem = joined[:400] if joined else "Users report an unmet need."
        return IdeaDraft(
            title=title,
            problem_statement=problem,
            proposed_solution=(
                "Build a focused tool that addresses: " + joined[:200]
            ),
        )


class OllamaSynthesizerLLM:
    """Live synthesizer (§37.10). Falls back to the Fake behavior on malformed
    output so a bad model response never kills a nightly run."""

    SYNTH_SYSTEM = (
        "You frame product ideas from clustered user-pain nuggets. Reply with ONLY "
        "a JSON object: {\"title\": str, \"problem_statement\": str, "
        "\"proposed_solution\": str}."
    )

    def __init__(self, model: str = "claude-opus-4-7", client=None):
        self._model = model
        self._client = client

    def _complete(self, system: str, user: str) -> str:
        resp = self._ensure_client().chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return _chat_content(resp)

    def _ensure_client(self):
        if self._client is None:
            from ollama import Client
            self._client = Client()
        return self._client

    def synthesize(self, nuggets: List[Nugget]) -> IdeaDraft:
        corpus = "\n".join(
            f"{i + 1}. [{n.platform}] {n.extracted_insight}" for i, n in enumerate(nuggets)
        )
        obj = _parse_json_object(self._complete(self.SYNTH_SYSTEM, corpus))
        if obj is not None and all(
            isinstance(obj.get(k), str) and obj.get(k)
            for k in ("title", "problem_statement", "proposed_solution")
        ):
            return IdeaDraft(
                title=obj["title"],
                problem_statement=obj["problem_statement"],
                proposed_solution=obj["proposed_solution"],
            )
        return FakeSynthesizerLLM().synthesize(nuggets)


@dataclass
class CriticDraft:
    """Structured critic output. ``competition`` is Optional: None means the
    critic did NOT check competition (never fabricated)."""

    demand_signal: float
    feasibility: float
    competition: Optional[float] = None


@runtime_checkable
class CriticLLM(Protocol):
    def score(self, idea: Idea) -> CriticDraft: ...


class FakeCriticLLM:
    """Deterministic stand-in. Demand scales with evidence (nugget count +
    engagement); feasibility is a stable default. Competition is left None
    (unchecked) so the rubric imputes it (R29) -- this mirrors the offline
    critic, which never fabricates competition."""

    def score(self, idea: Idea) -> CriticDraft:
        support = idea.supporting_nuggets
        n = len(support)
        # crude demand proxy from evidence volume
        demand = min(10.0, 2.0 + n)
        feasibility = 5.0
        return CriticDraft(demand_signal=demand, feasibility=feasibility, competition=None)


class OllamaCriticLLM:
    """Live critic (§37.10). Clamps subscores to the 1-10 rubric; competition is
    None unless the model explicitly supplies a number (R29: never fabricate).
    Falls back to the Fake behavior on malformed output."""

    CRITIC_SYSTEM = (
        "You score product ideas. Reply with ONLY a JSON object: "
        '{"demand_signal": <1-10>, "feasibility": <1-10>'
        ', "competition": <1-10 or null if you did not check>}.'
    )

    def __init__(self, model: str = "claude-opus-4-7", client=None):
        self._model = model
        self._client = client

    def _ensure_client(self):
        if self._client is None:
            from ollama import Client
            self._client = Client()
        return self._client

    def _complete(self, system: str, user: str) -> str:
        resp = self._ensure_client().chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return _chat_content(resp)

    def score(self, idea: Idea) -> CriticDraft:
        payload = json.dumps({
            "title": idea.title,
            "supporting_nuggets": len(idea.supporting_nuggets),
            "problem_statement": idea.problem_statement,
        })
        obj = _parse_json_object(self._complete(self.CRITIC_SYSTEM, payload))
        if obj is not None:
            demand = _clamp_1_10(obj.get("demand_signal"))
            feasibility = _clamp_1_10(obj.get("feasibility"))
            if demand is not None and feasibility is not None:
                raw_comp = obj.get("competition")
                comp = None if raw_comp in (None, "", "unchecked") else _clamp_1_10(raw_comp)
                return CriticDraft(demand_signal=demand, feasibility=feasibility, competition=comp)
        return FakeCriticLLM().score(idea)


def _clamp_1_10(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return min(10.0, max(1.0, f))

