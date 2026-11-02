#!/usr/bin/env python3
"""Check that config-live/ stays an overlay on config/ (R4-04).

`config-live/` is the fully local profile: it overrides the keys it means to
change and *inherits* everything else from `config/`, which is where the
loader takes an absent key's value from. Two shapes of drift break that
reading, and both used to go unnoticed:

* a key in `config-live/` that `config/` does not have. The live profile then
  depends on a value that exists nowhere as a default, and "what does live
  inherit?" has no answer;
* a key in `config/` that `config-live/` does not have and that is not a
  declared defaults-only key. That is either a forgotten promotion or a
  dev-only knob nobody wrote down.

`DEFAULTS_ONLY` is the second list: keys the live profile deliberately leaves
unset, each with the reason. It is the only permitted omission set, and it
lives here rather than in a document so the gate and the explanation cannot
drift apart. A key that reaches `config-live/` must leave this list.

Stdlib + PyYAML.
"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_DIR = ROOT / "config"
DEFAULT_LIVE_DIR = ROOT / "config-live"

#: Keys `config/` carries and `config-live/` deliberately does not, with the
#: reason. Omission is a decision, so it is recorded as one.
DEFAULTS_ONLY = {
    "chat_enabled": "chat is off by default; live never turns it on",
    "chat_max_rounds": "chat tool-loop budget, only read when chat is enabled",
    "chat_proposal_ttl_hours": "chat proposal lifetime, only read when chat is enabled",
    "critic_provider": "live resolves every role from llm_provider: ollama",
    "dedup_threshold_by_category": "empty per-category table; live uses the global bar",
    "extractor_provider": "live resolves every role from llm_provider: ollama",
    "ingest_timeout_seconds": "worker kill timeout tuned for the host's source list",
    "maintenance_null_rate_spike": "maintenance sweep flag; live keeps the default",
    "maintenance_stale_days": "maintenance sweep flag; live keeps the default",
    "max_reviews_per_app": "Steam depth; live's source mix leaves it at the default",
    "max_threads_per_platform": "per-platform depth table tuned by measured yield",
    "realestate_detail_per_page": "second fetch per listing; live keeps it off",
    "rescore_per_run": "re-score budget sized for the Groq free tier, unused live",
    "scheduler_enabled": "source rotation switch; live keeps it off",
    "scheduler_window_batches": "rotation window, only read when rotation is on",
    "specs_max_samples": "spec-review sample size, used only by the review gate",
    "synthesizer_provider": "live resolves every role from llm_provider: ollama",
}


def read_keys(directory) -> set:
    """The top-level keys of `directory/thresholds.yaml`."""
    path = Path(directory) / "thresholds.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return set(data)


def compare(config_dir=DEFAULT_CONFIG_DIR, live_dir=DEFAULT_LIVE_DIR,
            defaults_only=None) -> dict:
    """Return the counts, the allowlisted keys, and every parity problem.

    Empty `problems` means the live profile is a legal overlay of the dev one.
    """
    allowlist = DEFAULTS_ONLY if defaults_only is None else defaults_only
    config_keys = read_keys(config_dir)
    live_keys = read_keys(live_dir)

    problems = []
    for key in sorted(live_keys - config_keys):
        problems.append(
            f"{key}: only in {live_dir}/thresholds.yaml. A live-only key has no "
            "default behind it; add it to config/ instead")
    for key in sorted(config_keys - live_keys):
        if key in allowlist:
            continue
        problems.append(
            f"{key}: only in {config_dir}/thresholds.yaml and not in "
            "DEFAULTS_ONLY. Promote it to config-live/ or allowlist it with a "
            "reason")
    return {
        "config_count": len(config_keys),
        "live_count": len(live_keys),
        "allowlisted": sorted((config_keys - live_keys) & set(allowlist)),
        "problems": problems,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="config/ and config-live/thresholds.yaml must agree on "
                    "every key that is not a declared defaults-only key")
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--live-dir", type=Path, default=DEFAULT_LIVE_DIR)
    args = parser.parse_args(argv)
    try:
        report = compare(args.config_dir, args.live_dir)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"config parity: {exc}", file=sys.stderr)
        return 2
    if report["problems"]:
        for problem in report["problems"]:
            print(f"config parity: {problem}", file=sys.stderr)
        print(f"config parity: {len(report['problems'])} problem(s); "
              "config-live/ must be an overlay on config/", file=sys.stderr)
        return 1
    print(f"config parity: {report['config_count']} key(s) in config/, "
          f"{report['live_count']} in config-live/, "
          f"{len(report['allowlisted'])} allowlisted defaults-only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
