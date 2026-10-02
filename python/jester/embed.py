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

    #: Seconds before one embed call is abandoned. Generous on purpose: a
    #: `reembed` hands over whole batches, and a slow batch is not a stall.
    #: The point is only that the bound is finite; there was none. A timeout
    #: raises like any other embed failure, which the archivist already turns
    #: into "defer this batch" rather than a dead run.
    TIMEOUT_S = 600.0

    def __init__(self, model: str = "nomic-embed-text", client=None, timeout=None,
                 host=None):
        self._model = model
        self._client = client
        self._timeout = self.TIMEOUT_S if timeout is None else timeout
        self._host = host

    def embed(self, texts: List[str]) -> List[List[float]]:
        resp = self._ensure_client().embed(model=self._model, input=list(texts))
        return _field(resp, "embeddings", []) or []

    def _ensure_client(self):
        if self._client is None:
            from ollama import Client  # lazy import
            kw = {"timeout": self._timeout}
            if self._host:
                kw["host"] = self._host
            self._client = Client(**kw)
        return self._client


def select_embedding(cfg):
    """The embedding client for the configured embedding role (jester.roles).
    Only Ollama or the hash stand-in can serve it; the resolver refuses any
    other provider at load time."""
    from jester.roles import resolve_role

    r = resolve_role(cfg, "embedding")
    if r.kind == "ollama":
        return OllamaEmbedding(model=r.model, timeout=r.timeout_s, host=r.host)
    return FakeEmbedding()
