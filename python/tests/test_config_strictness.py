"""V3-10: a misspelt threshold key must fail loudly.

`load_thresholds` used to log "no code reads this key" and then drop it, so a
typo looked configured and did nothing. Unknown TOP-LEVEL keys are now refused
at load. Unknown `models.*` entries keep the warning: the block is read by Go
too, and a model name nobody in Python reads is a documented possibility.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from jester.config import ConfigError, load_thresholds

ROOT = Path(__file__).resolve().parents[2]


def _write(tmp_path, text):
    path = tmp_path / "thresholds.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_misspelt_top_level_key_is_refused_and_named(tmp_path):
    path = _write(tmp_path, "dedup_threshold: 0.87\ntemprature: 0.2\n")
    with pytest.raises(ConfigError) as exc:
        load_thresholds(path)
    assert "temprature" in str(exc.value)


@pytest.mark.parametrize("profile", ["config", "config-live", "docker/config"])
def test_every_shipped_thresholds_file_still_loads(profile):
    path = ROOT / profile / "thresholds.yaml"
    if not path.exists():
        pytest.skip(f"{profile}/thresholds.yaml not present")
    load_thresholds(path)  # must not raise


def test_an_unknown_models_key_still_only_warns(tmp_path, caplog):
    path = _write(tmp_path, "models:\n  archivist: phi4-mini\n")
    with caplog.at_level("WARNING", logger="jester.config"):
        load_thresholds(path)
    assert any("models.archivist" in r.message for r in caplog.records)


def test_journal_approval_ttl_is_a_known_top_level_key(tmp_path):
    path = _write(tmp_path, "journal_approval_ttl_days: 14\n")
    assert load_thresholds(path).journal_approval_ttl_days == 14


def test_the_cli_exits_one_and_names_the_misspelt_key(tmp_path):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    thresholds = cfg / "thresholds.yaml"
    thresholds.write_text(
        thresholds.read_text(encoding="utf-8") + "\ntemprature: 0.2\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "python")
    res = subprocess.run(
        [sys.executable, "-m", "jester.cli", "models", "--config", str(cfg)],
        capture_output=True, text=True, env=env,
    )
    assert res.returncode == 1, (res.returncode, res.stdout, res.stderr)
    assert "temprature" in (res.stderr + res.stdout)


def test_the_shipped_keys_are_exactly_the_dataclass_fields_plus_models():
    """The reconciliation behind the strict loader: every shipped key is a
    field (or the Go-only `models:` block). If this fails, the shipped config
    and the dataclass have drifted and the strict load would refuse a file
    the repo itself ships."""
    from jester.config import GO_ONLY_KEYS, Thresholds

    known = set(Thresholds.__dataclass_fields__) | set(GO_ONLY_KEYS)
    for profile in ("config", "config-live", "docker/config"):
        path = ROOT / profile / "thresholds.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        assert set(raw) <= known, f"{profile}: {sorted(set(raw) - known)}"
