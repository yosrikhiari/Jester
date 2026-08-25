"""Live Ollama verification (§37.16) — probe-gated; skips without daemon/models."""
import json
import math
import urllib.request
from pathlib import Path

import pytest

from jester.config import Thresholds
from jester.models import Nugget

OLLAMA_HOST = "http://localhost:11434"
REQUIRED_MODELS = {"qwen3:8b", "nomic-embed-text"}


def _server_models():
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=10) as resp:
            data = json.loads(resp.read())
        # Tag-normalize: "nomic-embed-text:latest" satisfies "nomic-embed-text".
        return {
            m["name"].split(":latest")[0] for m in data.get("models", []) if m.get("name")
        }
    except Exception:
        return None


_AVAILABLE = _server_models()
pytestmark = [
    pytest.mark.skipif(
        _AVAILABLE is None or not REQUIRED_MODELS.issubset(_AVAILABLE),
        reason=f"Ollama daemon/models unavailable (found: {_AVAILABLE})",
    ),
    pytest.mark.live_ollama,
]


def _thr():
    return Thresholds(
        llm_provider="ollama",
        embedding_provider="ollama",
        extractor_model="qwen3:8b",
        synthesizer_model="qwen3:8b",
        critic_model="qwen3:8b",
        embedding_model="nomic-embed-text",
    )


def test_live_extractor_returns_valid_draft():
    from jester.llm import ALLOWED_CATEGORIES, select_extractor_llm

    draft = select_extractor_llm(_thr()).extract(
        "Splitting a 40GB CSV in Excel freezes my machine for minutes every "
        "time I export the monthly report."
    )
    assert draft.category in ALLOWED_CATEGORIES
    assert draft.extracted_insight.strip()


def test_live_synthesizer_frames_idea():
    from jester.llm import select_synthesizer_llm

    nuggets = [
        Nugget(unique_key="a", platform="reddit", extracted_insight="Backups vanish when updates crash"),
        Nugget(unique_key="b", platform="reddit", extracted_insight="No auto-snapshot before package upgrades"),
    ]
    out = select_synthesizer_llm(_thr()).synthesize(nuggets)
    assert isinstance(out.title, str) and out.title.strip()
    assert isinstance(out.problem_statement, str) and out.problem_statement.strip()
    assert isinstance(out.proposed_solution, str) and out.proposed_solution.strip()


def test_live_critic_scores_within_rubric():
    from jester.llm import select_critic_llm
    from jester.models import Idea, IdeaScores

    idea = Idea(title="Pre-update snapshots", supporting_nuggets=["a", "b"],
                problem_statement="Configs lost on crashing updates",
                scores=IdeaScores())
    out = select_critic_llm(_thr()).score(idea)
    assert 1.0 <= out.demand_signal <= 10.0
    assert 1.0 <= out.feasibility <= 10.0
    assert out.competition is None or 1.0 <= out.competition <= 10.0


def test_live_embedding_dim768_finite():
    from jester.embed import select_embedding

    vecs = select_embedding(_thr()).embed(["hello world"])
    assert len(vecs) == 1 and len(vecs[0]) == 768
    assert all(math.isfinite(x) for x in vecs[0])
