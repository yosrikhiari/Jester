"""Config loading for jester (thresholds + sources) with fail-fast validation."""
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


class ConfigError(Exception):
    pass


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
    llm_provider: str = "fake"          # fake | ollama
    embedding_provider: str = "fake"    # fake | ollama
    # §37.15 critic web-step knobs (v3.5 #3) + competitor recheck TTL (R53).
    competitor_ttl_days: int = 30       # re-run the competitor check when older
    critic_web_rate_limit: int = 6      # calls/min; provisional default
    critic_web_daily_budget: int = 50   # calls/day; provisional default
    # §7.2 gate: an idea is "good" when overall >= good_idea_min.
    good_idea_min: float = 7.0

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
    }

    def validate(self) -> "Thresholds":
        for key, (lo, hi) in self._RANGES.items():
            v = getattr(self, key)
            if not (lo <= v <= hi):
                raise ConfigError(f"{key}={v} out of range [{lo},{hi}]")
        return self


@dataclass
class Source:
    name: str
    platform: str
    url: str


@dataclass
class Config:
    thresholds: Thresholds
    sources: List[Source] = field(default_factory=list)


def load_thresholds(path) -> Thresholds:
    data = yaml.safe_load(Path(path).read_text()) or {}
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


def load_sources(path) -> List[Source]:
    data = yaml.safe_load(Path(path).read_text()) or []
    if isinstance(data, dict):
        data = data.get("sources", [])
    return [Source(name=s.get("name", ""), platform=s.get("platform", ""), url=s.get("url", "")) for s in data]


def load_config(config_dir) -> Config:
    config_dir = Path(config_dir)
    thresholds = load_thresholds(config_dir / "thresholds.yaml")
    sources = load_sources(config_dir / "sources.yaml")
    return Config(thresholds=thresholds, sources=sources)
