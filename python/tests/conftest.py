"""Shared test fixtures.

The important one is `offline_config`. Several pipeline tests used the repo's
own `config/`, whose `synthesizer_provider` is `groq` — so with no network the
Groq client failed, fell back to the deterministic stand-in, and the test
asserted on the stand-in's output while believing it had exercised the real
path.

That worked only because the pipeline archived degraded output. It no longer
does: a group whose synthesis fell back is skipped rather than written to the
archive, because a stand-in "idea" is a truncated comment with a score
attached. Those tests then started failing, correctly.

The fix is for an offline test to SAY it is offline. `offline_config` is the
repo config with every provider forced to `fake` — the stand-in chosen
deliberately, which is a different thing from degrading into it by accident.
"""

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO_ROOT / "config"

#: Providers that reach the network, and what they become offline.
_OFFLINE_PROVIDERS = {
    "llm_provider": "fake",
    "embedding_provider": "fake",
    "extractor_provider": "fake",
    "synthesizer_provider": "fake",
    "critic_provider": "fake",
    "competitor_search_provider": "none",
}


def _force_offline(thresholds_path: Path) -> None:
    lines = thresholds_path.read_text(encoding="utf-8").splitlines()
    seen = set()
    out = []
    for line in lines:
        key = line.split(":", 1)[0].strip()
        if key in _OFFLINE_PROVIDERS:
            out.append(f"{key}: {_OFFLINE_PROVIDERS[key]}")
            seen.add(key)
        else:
            out.append(line)
    # A provider absent from the file still defaults to something; pin it.
    for key, value in _OFFLINE_PROVIDERS.items():
        if key not in seen:
            out.append(f"{key}: {value}")
    thresholds_path.write_text("\n".join(out) + "\n", encoding="utf-8")


@pytest.fixture
def offline_config(tmp_path):
    """A copy of the repo config with every provider forced offline.

    Returns the directory path. Use this anywhere a test exercises the
    pipeline rather than a specific model backend — which is almost
    everywhere.
    """
    dst = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, dst)
    _force_offline(dst / "thresholds.yaml")
    return dst
