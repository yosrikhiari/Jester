"""LLM client abstraction for the agents. Fake* classes are deterministic for tests/mock."""
import json
import os
import re
import time
import urllib.error
import urllib.request
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


_VALID_LLM_PROVIDERS = ("fake", "ollama", "groq")


def _require_provider(provider: str) -> None:
    if provider not in _VALID_LLM_PROVIDERS:
        raise ValueError(
            f"llm_provider={provider!r} not supported; valid: {', '.join(_VALID_LLM_PROVIDERS)}"
        )


def provider_for(cfg, role: str) -> str:
    """The backend serving one role: its override if set, else llm_provider.

    Kept here rather than in config so a caller holding any object with the
    right attributes (the console builds one, tests fake one) resolves a role
    the same way the pipeline does.
    """
    override = (getattr(cfg, f"{role}_provider", "") or "").strip()
    provider = override or getattr(cfg, "llm_provider", "fake")
    _require_provider(provider)
    return provider


def select_extractor_llm(cfg):
    provider = provider_for(cfg, "extractor")
    if provider == "ollama":
        return OllamaLLM(model=cfg.extractor_model)
    if provider == "groq":
        return GroqLLM(model=cfg.extractor_model)
    return FakeLLM()


def select_synthesizer_llm(cfg):
    provider = provider_for(cfg, "synthesizer")
    if provider == "ollama":
        return OllamaSynthesizerLLM(model=cfg.synthesizer_model)
    if provider == "groq":
        return GroqSynthesizerLLM(model=cfg.synthesizer_model)
    return FakeSynthesizerLLM()


def select_critic_llm(cfg):
    provider = provider_for(cfg, "critic")
    if provider == "ollama":
        return OllamaCriticLLM(model=cfg.critic_model)
    if provider == "groq":
        return GroqCriticLLM(model=cfg.critic_model)
    return FakeCriticLLM()


class FakeLLM:
    """Deterministic stand-in. Classifies by simple keyword heuristics."""

    name = "fake-llm"

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

    @property
    def name(self):
        return self._model

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

    name = "fake-synth"

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

    @property
    def name(self):
        return self._model

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

    name = "fake-critic"

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

    @property
    def name(self):
        return self._model

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


# ── Groq ────────────────────────────────────────────────────────────────────
# OpenAI-compatible chat completions over stdlib urllib. No SDK: `groq` and
# `openai` are both absent here, and one JSON POST does not justify a runtime
# dependency the console would crash without.

GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

#: Chat-capable Groq models, best-first. The audio (whisper), speech (orpheus)
#: and classifier (prompt-guard) models Groq also serves are deliberately
#: absent — offering them in a picker for the synthesizer role would be
#: offering a choice that can only fail.
GROQ_CHAT_MODELS = (
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
    "groq/compound",
    "groq/compound-mini",
    "allam-2-7b",
)


class GroqError(RuntimeError):
    """Groq refused or could not be reached. Raised by the transport only; the
    agent classes catch it and fall back to the deterministic stand-in."""


def _is_chat_model(model_id: str) -> bool:
    bad = ("whisper", "orpheus", "prompt-guard", "tts", "embed", "guard")
    return not any(b in (model_id or "").lower() for b in bad)


def _http_error_detail(exc) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8"))
        return str(body.get("error", {}).get("message") or body)[:300]
    except Exception:  # noqa: BLE001
        return getattr(exc, "reason", None) or "no detail"



#: Statuses worth retrying: the rate limit, and the transient 5xx family.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _retry_delay(exc, attempt):
    """Honour Retry-After when Groq sends one; otherwise back off 1s, 2s, 4s.

    Groq reports its reset window in `retry-after` (seconds) and, for the
    token bucket, in `x-ratelimit-reset-tokens` as a duration like `899ms` or
    `2m52.8s`. Guessing when the server has told you is how a retry storm
    starts.
    """
    headers = getattr(exc, "headers", None)
    raw = None
    if headers is not None:
        raw = headers.get("retry-after") or headers.get("x-ratelimit-reset-tokens")
    seconds = _parse_duration(raw)
    if seconds is None:
        seconds = float(2 ** attempt)
    # Never sleep longer than a run is willing to wait for one comment.
    return max(0.0, min(seconds, 30.0))


_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)(ms|s)?)?$")


def _parse_duration(raw):
    """`"3"` -> 3.0, `"899ms"` -> 0.899, `"2m52.8s"` -> 172.8, junk -> None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:  # a bare number of seconds, the common Retry-After form
        return float(text)
    except ValueError:
        pass
    m = _DURATION_RE.match(text)
    if not m or not any(m.groups()):
        return None
    minutes, value, unit = m.groups()
    total = float(minutes) * 60 if minutes else 0.0
    if value:
        total += float(value) / 1000.0 if unit == "ms" else float(value)
    return total


def _as_chat_response(body):
    """Reshape a chat-completions body into the ollama shape `_chat_content`
    reads, so both providers flow through one parser."""
    choices = body.get("choices") or []
    if not choices:
        raise GroqError("groq returned no choices: " + str(body)[:200])
    content = (choices[0].get("message") or {}).get("content", "")
    return {"message": {"content": content}}


class GroqClient:
    """Minimal chat-completions transport.

    Shaped like the ollama client — `.chat(model=..., messages=[...])` returning
    something `_chat_content` understands — so the agent classes below differ
    from their Ollama twins only in which client they build.
    """

    def __init__(self, api_key=None, base_url=None, timeout=60, opener=None,
                 max_retries=3, sleep=None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.base_url = (base_url or os.environ.get("GROQ_BASE_URL")
                         or GROQ_DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._opener = opener or urllib.request.urlopen
        self._sleep = sleep or time.sleep

    def _headers(self):
        return {
            "Authorization": "Bearer " + self.api_key,
            "Accept": "application/json",
            # Not optional: Groq sits behind Cloudflare, which 403s the default
            # `Python-urllib/3.x` user agent with error 1010.
            "User-Agent": "jester/1.0 (+local operator console)",
        }

    def chat(self, model, messages, temperature=0.4, json_object=True):
        if not self.api_key:
            raise GroqError(
                "GROQ_API_KEY is not set - put it in .env at the repo root "
                "(see config/local.env.example)"
            )
        payload = {"model": model, "messages": messages, "temperature": temperature}
        if json_object:
            # Groq honours OpenAI's json_object mode, which removes the whole
            # class of "the model wrapped its JSON in prose" parse failures.
            payload["response_format"] = {"type": "json_object"}
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        # A 429 is the expected steady state, not an exception: this key is
        # capped at 8,000 tokens/minute, and the critic fires once per idea.
        # Without a retry the run would quietly swap in the deterministic
        # stand-in for whichever ideas happened to land after the cap.
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._opener(req, timeout=self.timeout) as resp:
                    return _as_chat_response(json.loads(resp.read().decode("utf-8")))
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRY_STATUS or attempt == self.max_retries:
                    # `exc` wraps a one-shot file object, so the detail is read
                    # only on the attempt that raises — reading it per attempt
                    # left the final message with an empty body to report.
                    raise GroqError(
                        "groq HTTP %s: %s" % (exc.code, _http_error_detail(exc))) from exc
                last = GroqError("groq HTTP %s" % exc.code)
                self._sleep(_retry_delay(exc, attempt))
            except GroqError:
                raise
            except Exception as exc:  # noqa: BLE001 - network, DNS, TLS, bad JSON
                raise GroqError(
                    "groq request failed: %s: %s" % (type(exc).__name__, exc)) from exc
        raise last

    def list_models(self):
        """Chat-capable model ids this key can actually reach, best-first.

        Returns [] when Groq is unreachable rather than raising: this feeds the
        console's model picker, and a dead network should grey out a dropdown,
        not 500 the config page.
        """
        req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
        try:
            with self._opener(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return []
        served = {m.get("id") for m in (body.get("data") or [])}
        ranked = [m for m in GROQ_CHAT_MODELS if m in served]
        # A model Groq has added since this list was written still belongs in
        # the picker, as long as it is not one of the non-chat families.
        extra = sorted(m for m in served - set(GROQ_CHAT_MODELS) if m and _is_chat_model(m))
        return ranked + extra


class _GroqAgent:
    """Shared plumbing: lazy client, one completion, JSON in / JSON out."""

    def __init__(self, model, client=None, api_key=None, base_url=None):
        self._model = model
        self._client = client
        self._api_key = api_key
        self._base_url = base_url
        #: Set when a call fell back to the deterministic stand-in, so a caller
        #: can tell "the model wrote this" from "the model was unreachable".
        self.last_error = None
        #: How many calls fell back, and why. A rate limit degrades quality
        #: without failing anything, which is exactly the silent degradation
        #: the run summary exists to catch — `cmd_run` reports this.
        self.fallbacks = 0
        self.fallback_reasons = []

    @property
    def name(self):
        return self._model

    def _ensure_client(self):
        if self._client is None:
            self._client = GroqClient(api_key=self._api_key, base_url=self._base_url)
        return self._client

    def _complete(self, system, user, temperature=0.4):
        resp = self._ensure_client().chat(
            model=self._model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature,
        )
        return _chat_content(resp)

    def _obj(self, system, user, temperature=0.4):
        """The parsed object, or None having recorded why on `last_error`."""
        self.last_error = None
        try:
            obj = _parse_json_object(self._complete(system, user, temperature))
        except GroqError as exc:
            self._note_fallback(str(exc))
            return None
        if obj is None:
            self._note_fallback("malformed reply (not a JSON object)")
        return obj

    def _note_fallback(self, reason):
        self.last_error = reason
        self.fallbacks += 1
        # Keep a bounded sample: 400 identical rate-limit lines say no more
        # than the first one, and the run summary has to stay readable.
        if reason not in self.fallback_reasons and len(self.fallback_reasons) < 5:
            self.fallback_reasons.append(reason)


class GroqLLM(_GroqAgent):
    """Live extractor via Groq. Falls back to the deterministic stand-in on an
    unreachable model or a malformed reply — a bad response never kills a run.

    The default is the 20b model, not the 120b one: the extractor runs once per
    comment, so it is the only role where model choice is a throughput decision.
    """

    EXTRACT_SYSTEM = OllamaLLM.EXTRACT_SYSTEM

    def __init__(self, model="openai/gpt-oss-20b", **kw):
        super().__init__(model, **kw)

    def extract(self, comment_body: str) -> NuggetDraft:
        body = comment_body or ""
        obj = self._obj(self.EXTRACT_SYSTEM, body, temperature=0.2)
        if obj is not None:
            insight = str(obj.get("insight") or body.strip()[:200])
            category = obj.get("category")
            if category not in ALLOWED_CATEGORIES:
                category = "pain_point"  # mirror extractor.extract's coercion rule
            return NuggetDraft(extracted_insight=insight, category=category)
        return FakeLLM().extract(body)


class GroqSynthesizerLLM(_GroqAgent):
    """Live synthesizer via Groq.

    The prompt is the whole point of this class. The deterministic stand-in
    fills `proposed_solution` with "Build a focused tool that addresses: " plus
    the problem text — which is not a solution, it is the problem with a prefix.
    Every rule below exists to stop a real model doing the same thing in more
    words, which is the default failure mode when the only context it gets is
    a list of complaints.
    """

    SYNTH_SYSTEM = (
        "You are a product strategist. From clustered user-pain notes you frame ONE "
        "product idea.\n"
        "Reply with ONLY a JSON object with these keys:\n"
        '  "title": a concrete product name, 2-5 words, no platform prefix.\n'
        '  "problem_statement": 1-2 sentences naming who hurts and what it costs them.\n'
        '  "proposed_solution": 2-4 sentences describing what the product DOES.\n'
        "\n"
        "Rules for proposed_solution - these separate an idea from a restatement:\n"
        "  - Describe the MECHANISM: what it watches, computes, stores, or automates.\n"
        "  - It must read on its own, without the problem sitting next to it.\n"
        "  - Never restate the problem. Never open with 'Build a tool that' or "
        "'A solution that addresses'.\n"
        "  - Ground it in the notes. Do not invent a user base or a market size.\n"
        "  - Plain sentences. No bullets, no headings, no markdown."
    )

    def __init__(self, model="openai/gpt-oss-120b", **kw):
        super().__init__(model, **kw)

    def synthesize(self, nuggets: List[Nugget]) -> IdeaDraft:
        corpus = "\n".join(
            "%d. [%s] %s" % (i + 1, n.platform, n.extracted_insight)
            for i, n in enumerate(nuggets)
        )
        obj = self._obj(self.SYNTH_SYSTEM, corpus)
        if obj is not None and all(
            isinstance(obj.get(k), str) and obj.get(k).strip()
            for k in ("title", "problem_statement", "proposed_solution")
        ):
            return IdeaDraft(
                title=obj["title"].strip(),
                problem_statement=obj["problem_statement"].strip(),
                proposed_solution=obj["proposed_solution"].strip(),
            )
        return FakeSynthesizerLLM().synthesize(nuggets)


class GroqCriticLLM(_GroqAgent):
    """Live critic via Groq. Clamps subscores to the 1-10 rubric; competition
    stays None unless the model supplies a number (R29: never fabricate)."""

    CRITIC_SYSTEM = OllamaCriticLLM.CRITIC_SYSTEM

    def __init__(self, model="openai/gpt-oss-120b", **kw):
        super().__init__(model, **kw)

    def score(self, idea: Idea) -> CriticDraft:
        payload = json.dumps({
            "title": idea.title,
            "supporting_nuggets": len(idea.supporting_nuggets),
            "problem_statement": idea.problem_statement,
        })
        obj = self._obj(self.CRITIC_SYSTEM, payload, temperature=0.2)
        if obj is not None:
            demand = _clamp_1_10(obj.get("demand_signal"))
            feasibility = _clamp_1_10(obj.get("feasibility"))
            if demand is not None and feasibility is not None:
                raw_comp = obj.get("competition")
                comp = None if raw_comp in (None, "", "unchecked") else _clamp_1_10(raw_comp)
                return CriticDraft(demand_signal=demand, feasibility=feasibility,
                                   competition=comp)
        return FakeCriticLLM().score(idea)


# ── model provenance ────────────────────────────────────────────────────────
# `runs.models_used` records the fake names, but the idea row used to record
# `cfg.synthesizer_model` no matter which class actually ran. An archive that
# says a frontier model wrote a line the deterministic stand-in produced is
# worse than one that says nothing: it is the field you would use to decide
# whether a scoring shift came from a prompt change or a model change.

def model_name(llm, fallback=""):
    """What actually produced this output, for the archive's provenance field."""
    return getattr(llm, "name", None) or fallback


def fallback_report(*llms):
    """One line per agent that quietly used the stand-in, or [] if none did.

    A provider that answers 90% of the time and silently substitutes canned
    text for the rest is worse than one that is simply down: the archive fills
    with plausible rows nobody flagged.
    """
    lines = []
    for llm in llms:
        n = getattr(llm, "fallbacks", 0)
        if not n:
            continue
        why = "; ".join(getattr(llm, "fallback_reasons", []) or ["unknown"])
        lines.append("%s fell back to the deterministic stand-in %d time(s): %s"
                     % (model_name(llm, "model"), n, why))
    return lines
