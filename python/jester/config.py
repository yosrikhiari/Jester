"""Config loading for jester (thresholds + sources) with fail-fast validation."""
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml

# The curated-source vocabulary lives in jester.sources (shared with the Go
# worker's sources.yaml contract); re-exported here so existing importers of
# `jester.config.Source` / `load_sources` keep working.
from jester.sources import (  # noqa: F401
    PLATFORM_KINDS,
    PLATFORMS,
    SUPPORTED_KINDS,
    Source,
    SourceError,
    load_sources,
    parse_source,
    save_sources,
)


class ConfigError(Exception):
    pass


def _as_bool(value) -> bool:
    """YAML/console booleans, coerced honestly.

    ``bool("false")`` is True in Python, so the obvious caster would turn every
    string a user typed into an enabled flag. Anything unrecognised raises
    rather than silently picking a default.
    """
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    raise ConfigError(f"{value!r} is not a boolean (try true/false)")


#: Chat providers for the extractor / synthesizer / critic roles.
LLM_PROVIDERS = ("fake", "ollama", "groq")
#: Per-role provider override keys, in pipeline order.
ROLE_PROVIDER_KEYS = ("extractor_provider", "synthesizer_provider", "critic_provider")
#: Embedding providers. Groq is absent on purpose — it serves chat models only,
#: so accepting it here would validate a config that dies at the first vector
#: write, several minutes into a run.
EMBEDDING_PROVIDERS = ("fake", "ollama")


@dataclass
class Thresholds:
    max_comments_per_thread: int = 500
    min_upvotes: int = 1
    dedup_threshold: float = 0.87
    prefilter_min_chars: int = 40
    prefilter_max_chars: int = 600
    prefilter_max_emoji: int = 5
    prefilter_max_mentions: int = 3
    prefilter_min_words: int = 6
    request_delay_ms: int = 2000
    embedding_model: str = "nomic-embed-text"
    # Agent model keys (synced from the `models:` block in thresholds.yaml).
    extractor_model: str = "claude-sonnet-4-6"
    archivist_model: str = "claude-sonnet-4-6"
    synthesizer_model: str = "claude-opus-4-7"
    critic_model: str = "claude-opus-4-7"
    # §37.10 provider selection: which backend serves the agent roles.
    llm_provider: str = "fake"          # fake | ollama | groq
    embedding_provider: str = "fake"    # fake | ollama
    # Per-role overrides. Empty means "use llm_provider", so an untouched
    # config behaves exactly as before.
    #
    # These exist because the three chat roles have call volumes that differ by
    # three orders of magnitude: the extractor runs once per COMMENT (1,390 on
    # a routine night), while the synthesizer runs once per idea CLUSTER and
    # the critic once per idea. Sending all three to a rate-limited hosted API
    # because you wanted a better-written solution paragraph is how a run that
    # took 40 seconds starts taking 20 minutes and then 429s.
    extractor_provider: str = ""
    synthesizer_provider: str = ""
    critic_provider: str = ""
    # §37.15 critic web-step knobs (v3.5 #3) + competitor recheck TTL (R53).
    competitor_ttl_days: int = 30       # re-run the competitor check when older
    critic_web_rate_limit: int = 6      # calls/min; provisional default
    critic_web_daily_budget: int = 50   # calls/day; provisional default
    # M2.3/D-1: which provider verifies competition. "none" keeps the M2.2
    # behaviour (competition rides on the model's prior); "fake" is the
    # deterministic offline provider; "duckduckgo" hits the live web.
    competitor_search_provider: str = "none"  # none | fake | duckduckgo
    # §7.2 gate: an idea is "good" when overall >= good_idea_min.
    good_idea_min: float = 7.0
    # M1.1 live ingestion: how many threads/videos the worker walks per source
    # in one run. 1 keeps the original single-thread behaviour.
    max_threads_per_source: int = 1
    #: Per-platform override of max_threads_per_source. One number cannot
    #: serve all four adapters — Hacker News answers 1,000 stories from a
    #: keyless unmetered API in one request, while Reddit is a bot-detected
    #: browser scrape on a single session. Absent platform falls back to the
    #: scalar above; absent map reproduces the old behaviour exactly.
    max_threads_per_platform: dict = field(default_factory=dict)
    # Go-only in effect (the API-backed adapters are the only ones that get a
    # comment count before fetching), but it lives in the shared thresholds.yaml
    # so the console must know it — otherwise the Config page silently omits a
    # knob the pipeline reads.
    min_comments_per_thread: int = 0
    #: Steam's depth, in REVIEWS. A Steam source is one app, so "threads to
    #: walk per source" has nothing to walk there and the number that varies
    #: is how many of the app's reviews come back. It is not an entry in
    #: max_threads_per_platform because that knob is bounded [1,50] on the
    #: strength of its unit being threads, and a useful review count is not a
    #: thread count.
    max_reviews_per_app: int = 150
    #: Second fetch per real-estate result page, for fields the result page
    #: does not carry. Go-only in effect, but it lives in the shared
    #: thresholds.yaml so the console must know it.
    #:
    #: 0 = off, and that is the shipped value. It is ONE REQUEST PER LISTING on
    #: top of the result page, so a portal answering 33 tiles becomes 34
    #: requests instead of 1. Two profiles declare a detail block: Houni, whose
    #: result tiles carry no price at all, and Property24, whose street address
    #: appears only on the listing page.
    realestate_detail_per_page: int = 0
    # Write the archive out as CSV at the end of every run. The dataclass
    # default is False so no existing caller changes behaviour; the shipped
    # profiles turn it on, because a nightly run nobody watches should leave
    # something readable behind without a second command.
    export_after_run: bool = False
    # Rows per CSV shard. Excel stops near 1,048,576 rows and gets unusable
    # long before that; 100k opens quickly and keeps the file count small.
    export_rows_per_file: int = 100000
    # Seconds one Go worker run may take before it is killed. 0 keeps
    # worker.DEFAULT_INGEST_TIMEOUT.
    #
    # This was a constant in worker.py, tunable only by editing source, and
    # the shipped hour stopped fitting: a measured cycle spent ~23 minutes on
    # the other sources and was killed 37 minutes into the real-estate walk
    # with that walk unfinished, while the same ten portals take ~13 minutes
    # when run alone. The number that fits depends on which sources a
    # deployment enables, which is a config question, not a code one.
    ingest_timeout_seconds: int = 0

    _RANGES = {
        "max_comments_per_thread": (1, 100000),
        "min_upvotes": (0, 100000),
        "dedup_threshold": (0.5, 0.995),
        "prefilter_min_chars": (1, 10000),
        "prefilter_max_chars": (1, 100000),
        "prefilter_max_emoji": (0, 1000),
        "prefilter_max_mentions": (0, 1000),
        "prefilter_min_words": (1, 10000),
        "request_delay_ms": (0, 600000),
        "good_idea_min": (1, 10),
        "competitor_ttl_days": (1, 365),
        "critic_web_rate_limit": (1, 600),
        "critic_web_daily_budget": (0, 100000),
        "max_threads_per_source": (1, 50),
        "min_comments_per_thread": (0, 10000),
        "max_reviews_per_app": (1, 5000),
        "export_rows_per_file": (100, 1000000),
        "ingest_timeout_seconds": (0, 86400),
    }

    # Keys the operator console may write back to thresholds.yaml, with the
    # coercion used on the way in. The `models:` block is deliberately absent:
    # model choice is a code-review decision, not a slider.
    _EDITABLE = {
        "max_comments_per_thread": int,
        "min_upvotes": int,
        "dedup_threshold": float,
        "prefilter_min_chars": int,
        "prefilter_max_chars": int,
        "prefilter_max_emoji": int,
        "prefilter_max_mentions": int,
        "prefilter_min_words": int,
        "request_delay_ms": int,
        "good_idea_min": float,
        "competitor_ttl_days": int,
        "critic_web_rate_limit": int,
        "critic_web_daily_budget": int,
        "competitor_search_provider": str,
        "max_threads_per_source": int,
        "max_threads_per_platform": dict,
        "min_comments_per_thread": int,
        "max_reviews_per_app": int,
        "realestate_detail_per_page": int,
        "export_after_run": _as_bool,
        "export_rows_per_file": int,
        "ingest_timeout_seconds": int,
        "embedding_model": str,
        "llm_provider": str,
        "embedding_provider": str,
        "extractor_provider": str,
        "synthesizer_provider": str,
        "critic_provider": str,
    }

    def validate(self) -> "Thresholds":
        depth_lo, depth_hi = 1, 200
        for platform, n in (self.max_threads_per_platform or {}).items():
            if not isinstance(n, int) or isinstance(n, bool):
                raise ValueError(
                    f"max_threads_per_platform.{platform} must be a whole "
                    f"number, got {n!r}"
                )
            if not (depth_lo <= n <= depth_hi):
                raise ValueError(
                    f"max_threads_per_platform.{platform}={n} out of range "
                    f"[{depth_lo},{depth_hi}]"
                )
        for key, (lo, hi) in self._RANGES.items():
            v = getattr(self, key)
            if not (lo <= v <= hi):
                raise ConfigError(f"{key}={v} out of range [{lo},{hi}]")
        if self.prefilter_min_chars > self.prefilter_max_chars:
            raise ConfigError(
                f"prefilter_min_chars={self.prefilter_min_chars} exceeds "
                f"prefilter_max_chars={self.prefilter_max_chars}"
            )
        # Groq is a chat provider only: it serves no embedding model, so
        # `embedding_provider: groq` would validate and then fail at the first
        # vector write. The two keys get two vocabularies for that reason.
        if self.llm_provider not in LLM_PROVIDERS:
            raise ConfigError(
                f"llm_provider={self.llm_provider!r} invalid; "
                f"valid: {', '.join(LLM_PROVIDERS)}"
            )
        if self.embedding_provider not in EMBEDDING_PROVIDERS:
            raise ConfigError(
                f"embedding_provider={self.embedding_provider!r} invalid; "
                f"valid: {', '.join(EMBEDDING_PROVIDERS)}"
            )
        for key in ROLE_PROVIDER_KEYS:
            # Strip before the emptiness test, because `provider_for` does:
            # a whitespace-only override that resolves as "unset" but fails
            # validation would mean a config could load and route differently
            # depending on which of the two looked at it.
            v = (getattr(self, key) or "").strip()
            setattr(self, key, v)
            if v and v not in LLM_PROVIDERS:
                raise ConfigError(
                    f"{key}={v!r} invalid; valid: {', '.join(LLM_PROVIDERS)} "
                    "(or empty to follow llm_provider)"
                )
        if self.competitor_search_provider not in ("none", "fake", "duckduckgo"):
            raise ConfigError(
                f"competitor_search_provider={self.competitor_search_provider!r} "
                "invalid; valid: none, fake, duckduckgo"
            )
        return self


@dataclass
class Config:
    thresholds: Thresholds
    sources: List[Source] = field(default_factory=list)


def load_thresholds(path) -> Thresholds:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = {f for f in Thresholds.__dataclass_fields__}
    kwargs = {k: v for k, v in data.items() if k in known}
    # The `models:` block holds agent model names; map them onto Thresholds.
    model_map = {
        "extractor": "extractor_model",
        "archivist": "archivist_model",
        "synthesizer": "synthesizer_model",
        "critic": "critic_model",
    }
    for src, dst in model_map.items():
        if src in (data.get("models") or {}):
            kwargs[dst] = data["models"][src]
    return Thresholds(**kwargs).validate()


def coerce_thresholds_patch(patch: dict, current: Thresholds | None = None) -> dict:
    """Validate a console edit against the editable allow-list + ranges.

    Returns the coerced patch. Raises ConfigError with a per-field message so
    the UI can show which knob was refused and why. `current` supplies the
    untouched values so cross-field rules (min<=max) see the real file, not
    dataclass defaults.
    """
    out = {}
    for key, raw in (patch or {}).items():
        caster = Thresholds._EDITABLE.get(key)
        if caster is None:
            raise ConfigError(f"{key} is not an editable threshold")
        if caster is not str and isinstance(raw, str) and not raw.strip():
            raise ConfigError(f"{key} cannot be blank")
        try:
            out[key] = caster(raw)
        except (TypeError, ValueError):
            raise ConfigError(f"{key}={raw!r} is not a valid {caster.__name__}") from None
    base = {
        f: getattr(current, f)
        for f in Thresholds.__dataclass_fields__
        if current is not None
    }
    Thresholds(**{**base, **out}).validate()
    return out


_SCALAR_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:\s*)(.*)$")

# A bare word is safe in YAML when it has no whitespace and no trailing colon.
# Ollama tags (`qwen3:8b`, `hf.co/user/model:Q4_K_M`) qualify, so editing a
# model name does not churn the file into quoted strings.
_PLAIN_SCALAR = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./@+\-]*(?::[A-Za-z0-9_./@+\-]+)?$")


def _fmt(value) -> str:
    """Render a scalar as YAML, quoting only when a bare word would be unsafe."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(round(value, 6))
    if isinstance(value, str) and not _PLAIN_SCALAR.match(value):
        return json.dumps(value)  # valid YAML double-quoted scalar
    return str(value)


def save_thresholds(path, patch: dict) -> dict:
    """Rewrite only the touched top-level keys, preserving comments and order.

    A whole-file yaml.safe_dump would strip the documented defaults that live
    in thresholds.yaml as comments, so this edits lines in place instead.
    """
    path = Path(path)
    current = load_thresholds(path) if path.exists() else Thresholds()
    patch = coerce_thresholds_patch(patch, current)
    if not patch:
        return {}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(patch)
    for i, line in enumerate(lines):
        m = _SCALAR_LINE.match(line)
        if not m:
            continue
        indent, key, sep, _value = m.groups()
        if indent:  # inside `models:` or another nested block — never touched
            continue
        if key in remaining:
            lines[i] = f"{indent}{key}{sep}{_fmt(remaining.pop(key))}"
    if remaining:
        # Append new keys before the `models:` block if there is one, so the
        # nested block stays last and readable.
        insert_at = len(lines)
        for i, line in enumerate(lines):
            if line.startswith("models:"):
                insert_at = i
                break
        added = [f"{k}: {_fmt(v)}" for k, v in remaining.items()]
        lines[insert_at:insert_at] = added
    _atomic_write(path, "\n".join(lines).rstrip("\n") + "\n", prefix=".thresholds-")
    load_thresholds(path)  # fail loudly if the rewrite produced junk
    return patch


def _atomic_write(path: Path, body: str, *, prefix: str) -> None:
    """Never leave a half-written config behind when the console crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=prefix, suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---- agent model block ------------------------------------------------------

# The `models:` keys in thresholds.yaml, in the order the pipeline uses them.
MODEL_ROLES = ("extractor", "archivist", "synthesizer", "critic")
# Every model name the console may write, including the top-level embedding key.
MODEL_KEYS = MODEL_ROLES + ("embedding_model",)

# Ollama-style tags: name, optionally `:tag`, optionally a registry path.
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+\-]*(?::[A-Za-z0-9._/@+\-]+)?$")


def coerce_models_patch(patch: dict) -> dict:
    out = {}
    for key, raw in (patch or {}).items():
        if key not in MODEL_KEYS:
            raise ConfigError(
                f"{key} is not a model slot; valid: {', '.join(MODEL_KEYS)}"
            )
        name = str(raw or "").strip()
        if not name:
            raise ConfigError(f"{key} cannot be blank")
        if not _MODEL_NAME.match(name):
            raise ConfigError(
                f"{key}={name!r} is not a usable model name "
                "(letters, digits, . _ - / @ + and one optional :tag)"
            )
        out[key] = name
    return out


def save_models(path, patch: dict) -> dict:
    """Write agent model names, editing the nested `models:` block in place.

    Model choice is exactly what distinguishes config/ from config-live/, so
    this never mirrors — the caller picks the profile.
    """
    path = Path(path)
    patch = coerce_models_patch(patch)
    if not patch:
        return {}
    applied = dict(patch)

    embedding = patch.pop("embedding_model", None)
    if embedding is not None:
        save_thresholds(path, {"embedding_model": embedding})

    if patch:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        start = next((i for i, ln in enumerate(lines) if ln.startswith("models:")), None)
        remaining = dict(patch)
        if start is None:
            lines.append("models:")
            start = len(lines) - 1
            end = len(lines)
            indent = "  "
        else:
            end = start + 1
            indent = None
            while end < len(lines):
                line = lines[end]
                if line.strip() and not line[:1].isspace():
                    break  # back at column 0: the block ended
                m = _SCALAR_LINE.match(line)
                if m and m.group(1):
                    if indent is None:
                        indent = m.group(1)
                    if m.group(2) in remaining:
                        lines[end] = f"{m.group(1)}{m.group(2)}{m.group(3)}" \
                                     f"{_fmt(remaining.pop(m.group(2)))}"
                end += 1
            indent = indent or "  "
        if remaining:
            addition = [f"{indent}{k}: {_fmt(v)}" for k, v in remaining.items()]
            lines[end:end] = addition
        body = "\n".join(lines).rstrip("\n") + "\n"
        _atomic_write(path, body, prefix=".thresholds-")

    load_thresholds(path)  # fail loudly if the rewrite produced junk
    return applied


def load_config(config_dir) -> Config:
    config_dir = Path(config_dir)
    thresholds = load_thresholds(config_dir / "thresholds.yaml")
    sources = load_sources(config_dir / "sources.yaml")
    return Config(thresholds=thresholds, sources=sources)
