"""R4-04: config-live/ must stay an overlay on config/, never a fork of it.

`config/` is the defaults tree; an absent key resolves from there. So a
live-only key has no default behind it, and a config-only key outside the
declared `DEFAULTS_ONLY` set is a forgotten promotion. `scripts/check_config_parity.py`
is the gate (CI runs it); these tests drive its functions against temp trees,
because the shipped trees include the read-only `config-live/` profile and a
drift shape has to be provable without touching it.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_config_parity.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("check_config_parity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parity = _load_script()


def _tree(directory, keys):
    directory.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{key}: 1\n" for key in sorted(keys))
    (directory / "thresholds.yaml").write_text(body, encoding="utf-8")
    return directory


def test_identical_key_sets_pass(tmp_path):
    config = _tree(tmp_path / "config", {"alpha", "beta"})
    live = _tree(tmp_path / "config-live", {"alpha", "beta"})

    report = parity.compare(config, live, defaults_only=set())

    assert report["problems"] == []
    assert report["config_count"] == 2
    assert report["live_count"] == 2
    assert report["allowlisted"] == []


def test_a_live_only_key_fails_and_is_named(tmp_path):
    config = _tree(tmp_path / "config", {"alpha"})
    live = _tree(tmp_path / "config-live", {"alpha", "live_only"})

    report = parity.compare(config, live, defaults_only=set())

    assert len(report["problems"]) == 1
    problem = report["problems"][0]
    assert "live_only" in problem
    assert "only in" in problem and "config-live" in problem
    assert "add it to config/" in problem


def test_an_unexpected_defaults_only_key_fails_and_is_named(tmp_path):
    config = _tree(tmp_path / "config", {"alpha", "unpromoted"})
    live = _tree(tmp_path / "config-live", {"alpha"})

    report = parity.compare(config, live, defaults_only=set())

    assert len(report["problems"]) == 1
    problem = report["problems"][0]
    assert "unpromoted" in problem
    assert "DEFAULTS_ONLY" in problem
    assert "Promote it to config-live/" in problem


def test_an_allowlisted_defaults_only_key_passes(tmp_path):
    config = _tree(tmp_path / "config", {"alpha", "dev_only"})
    live = _tree(tmp_path / "config-live", {"alpha"})

    report = parity.compare(config, live, defaults_only={"dev_only"})

    assert report["problems"] == []
    assert report["allowlisted"] == ["dev_only"]
    # The counts still report both trees honestly; allowlisting suppresses the
    # problem, not the key.
    assert report["config_count"] == 2
    assert report["live_count"] == 1


def test_the_shipped_trees_pass_and_every_allowlist_entry_has_a_reason():
    report = parity.compare()

    assert report["problems"] == []
    assert len(report["allowlisted"]) == len(parity.DEFAULTS_ONLY)
    assert report["allowlisted"] == sorted(parity.DEFAULTS_ONLY)
    for key, reason in parity.DEFAULTS_ONLY.items():
        assert isinstance(reason, str) and reason.strip(), f"{key} has no reason"
        assert key not in parity.read_keys(parity.DEFAULT_LIVE_DIR), key


def test_the_cli_exits_zero_on_agreement_and_one_naming_a_live_only_key(tmp_path):
    config = _tree(tmp_path / "config", {"alpha"})
    live = _tree(tmp_path / "config-live", {"alpha"})

    ok = subprocess.run(
        [sys.executable, str(SCRIPT), "--config-dir", str(config),
         "--live-dir", str(live)],
        capture_output=True, text=True, cwd=ROOT)
    assert ok.returncode == 0, (ok.stdout, ok.stderr)
    assert "1 key(s) in config/" in ok.stdout

    _tree(live, {"alpha", "stray"})
    drift = subprocess.run(
        [sys.executable, str(SCRIPT), "--config-dir", str(config),
         "--live-dir", str(live)],
        capture_output=True, text=True, cwd=ROOT)
    assert drift.returncode == 1, (drift.stdout, drift.stderr)
    assert "stray" in drift.stderr
    assert "1 problem(s)" in drift.stderr
