"""A stalled model costs one comment, not the whole run.

There was no timeout on the Ollama client. That was invisible while the
extractor was the stub -- it returned instantly and never touched the network
-- and became load-bearing the moment a real local model was switched on.
Measured on this machine: ~15s for a 500-char comment, ~29s for a 3,000-char
one, and nothing bounding the tail.

The failure that matters is not a slow comment, it is a run that never ends:
the next scheduled tick stands down behind a row that still says 'running',
so one stuck generation stops the pipeline rather than delaying it.
"""
import pytest

from jester.llm import FakeLLM, OllamaLLM


class _Hangs:
    """A client whose chat call never succeeds, the way a timeout surfaces."""
    def __init__(self, exc=TimeoutError("read timed out")):
        self._exc = exc
        self.calls = 0

    def chat(self, **kw):
        self.calls += 1
        raise self._exc


class _Answers:
    def __init__(self, content):
        self._content = content

    def chat(self, **kw):
        return {"message": {"content": self._content}}


def test_a_timeout_returns_the_standin_instead_of_ending_the_run():
    """Raising here would lose every extraction the run had already done."""
    llm = OllamaLLM(model="qwen3:8b", client=_Hangs())
    draft = llm.extract("the build takes forty minutes and fails randomly")

    assert draft.model == FakeLLM.name, (
        "a timed-out extraction must be stamped as the stand-in, because that "
        "stamp is what makes cmd_run refuse to archive it"
    )


def test_the_standin_stamp_is_what_protects_the_archive():
    """The guard in cmd_run keys on this exact value. If the stamp changed,
    a timed-out extraction would be archived as though a model had answered."""
    assert FakeLLM.name == FakeLLM().extract("x").model


@pytest.mark.parametrize("boom", [
    TimeoutError("read timed out"),
    ConnectionError("connection refused"),
    RuntimeError("model not pulled"),
])
def test_every_transport_failure_behaves_the_same(boom):
    """Ollama not running, model missing, generation stalled -- from the
    run's point of view these are one situation: no answer for this comment."""
    llm = OllamaLLM(model="qwen3:8b", client=_Hangs(boom))
    assert llm.extract("anything").model == FakeLLM.name


def test_a_working_model_is_untouched():
    """Otherwise the guard is just an extractor that never extracts."""
    llm = OllamaLLM(model="qwen3:8b",
                    client=_Answers('{"category":"pain_point",'
                                    '"insight":"releases take all afternoon"}'))
    draft = llm.extract("our release process is a mess")

    assert draft.model == "qwen3:8b"
    assert draft.extracted_insight == "releases take all afternoon"


def test_the_client_is_built_with_a_timeout():
    """The default must be finite. An unbounded client is the original bug."""
    assert OllamaLLM.TIMEOUT_S > 0
    assert OllamaLLM(model="m")._timeout == OllamaLLM.TIMEOUT_S
