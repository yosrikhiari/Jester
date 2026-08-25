"""Embedding client abstraction. FakeEmbedding is deterministic (hash-based, L2-normalized)."""
import hashlib
import math
from typing import List, Protocol, runtime_checkable


def _field(obj, name, default=None):
    """pydantic-or-dict accessor (§37.16): the installed ollama SDK returns
    EmbeddingsResponse objects; injected stubs/tests may use dicts."""
    if obj is None:
        return default
    if hasattr(obj, name):
        return getattr(obj, name)
    if hasattr(obj, "get"):
        return obj.get(name, default)
    return default


@runtime_checkable
class EmbeddingClient(Protocol):
    def embed(self, texts: List[str]) -> List[List[float]]: ...


class FakeEmbedding:
    dim: int = 32

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> List[float]:
        v = [0.0] * self.dim
        h = hashlib.sha256(text.encode("utf-8")).digest()
        for i, b in enumerate(h):
            v[i % self.dim] += (b - 128) / 128.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


class OllamaEmbedding:
    """Real embeddings via Ollama (§37.10). Transport injectable for offline tests;
    lazy import only when no client is provided."""

    dim: int = 768

    def __init__(self, model: str = "nomic-embed-text", client=None):
        self._model = model
        self._client = client

    def embed(self, texts: List[str]) -> List[List[float]]:
        resp = self._ensure_client().embed(model=self._model, input=list(texts))
        return _field(resp, "embeddings", []) or []

    def _ensure_client(self):
        if self._client is None:
            from ollama import Client  # lazy import
            self._client = Client()
        return self._client


_VALID_EMBEDDING_PROVIDERS = ("fake", "ollama")


def select_embedding(cfg):
    provider = getattr(cfg, "embedding_provider", "fake")
    if provider not in _VALID_EMBEDDING_PROVIDERS:
        raise ValueError(
            f"embedding_provider={provider!r} not supported; "
            f"valid: {', '.join(_VALID_EMBEDDING_PROVIDERS)}"
        )
    if provider == "ollama":
        return OllamaEmbedding(model=cfg.embedding_model)
    return FakeEmbedding()
