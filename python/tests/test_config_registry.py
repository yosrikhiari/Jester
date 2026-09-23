"""§25 #8 / §4.3.1: the config-key registry lint the doc mandates but never had.

The doc says plainly: *"Any new knob MUST be added to this table before it is
read in code; a key read in code but absent here is a doc bug."* That rule was
enforced by nobody, and it drifted — ten keys were live in code and unregistered
before this test existed. A rule with no check is a comment.

This closes the loop in both directions:
  code/config -> registry   a knob that exists but is undocumented
  registry -> code          a row describing a knob that no longer exists
"""
import re
from pathlib import Path

import pytest
import yaml

from jester.config import Thresholds

REPO = Path(__file__).resolve().parents[2]
SETUP_DOC = REPO / "jester-setup.md"
CONFIG_DIRS = [REPO / "config", REPO / "config-live"]

# jester-setup.md is the first line of .gitignore, so a clean clone does not
# have it and every check in this file raised FileNotFoundError for anyone but
# the author. That is worse than not running: it reads as a broken suite
# rather than as a check that needs something the repo does not ship.
#
# Skipping says the true thing. The lint is real and worth keeping - it caught
# ten unregistered keys - but it compares the code against a document that
# lives outside the repo, so it can only run where that document is. Committing
# jester-setup.md would restore it for everyone; that is a call about whether
# the spec belongs in the repo, not one to make from inside a test.
pytestmark = pytest.mark.skipif(
    not SETUP_DOC.exists(),
    reason="jester-setup.md is gitignored and absent from a clean clone; "
           "this lint compares the code against that spec document",
)

# Rows that describe a *family* of keys in prose rather than naming each one.
# Each maps to the prefix it legitimately covers, so the lint stays honest
# about what the table actually documents instead of silently accepting
# anything unmatched.
PROSE_ROWS = {
    "pre-filter thresholds": "prefilter_",
    "rate-limit & quota budgets": "critic_web_",
}

# Keys the pipeline reads but which are not `thresholds.yaml`'s to own.
NOT_THRESHOLDS = {"models"}


def registry_keys():
    """Every key named in the §4.3.1 table, plus the prose-covered prefixes."""
    doc = SETUP_DOC.read_text(encoding="utf-8")
    start = doc.index("### 4.3.1 Required config-key registry")
    end = doc.index("### 4.4", start)
    table = doc[start:end]

    explicit, prose = set(), set()
    for line in table.splitlines():
        if not line.startswith("|") or line.startswith("| Key") or set(line) <= set("|- "):
            continue
        first = line.split("|")[1].strip()
        names = re.findall(r"`([a-z0-9_]+)`", first)
        if names:
            explicit.update(names)
            continue
        for label, prefix in PROSE_ROWS.items():
            if label in first.lower():
                prose.add(prefix)
    return explicit, prose


def covered(key, explicit, prose):
    return key in explicit or any(key.startswith(p) for p in prose)


def shipped_keys():
    out = {}
    for d in CONFIG_DIRS:
        path = d / "thresholds.yaml"
        if not path.exists():
            continue
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        out[d.name] = set(raw) - NOT_THRESHOLDS
    return out


def test_the_registry_table_parses():
    explicit, prose = registry_keys()
    assert explicit, "§4.3.1 table found no `key` rows — the parser or the table moved"
    assert prose, "the prose rows (pre-filter, quota budgets) are no longer recognised"


def test_every_editable_knob_is_registered():
    """A knob the console can write must be documented in §4.3.1."""
    explicit, prose = registry_keys()
    missing = sorted(k for k in Thresholds._EDITABLE if not covered(k, explicit, prose))
    assert missing == [], (
        "these knobs are read/written by code but absent from the §4.3.1 registry "
        f"(the doc calls that a doc bug): {missing}"
    )


def test_every_shipped_config_key_is_registered():
    """A key someone can set in thresholds.yaml must be documented too."""
    explicit, prose = registry_keys()
    problems = {}
    for profile, keys in shipped_keys().items():
        missing = sorted(k for k in keys if not covered(k, explicit, prose))
        if missing:
            problems[profile] = missing
    assert problems == {}, f"unregistered keys in shipped config: {problems}"


def test_every_shipped_config_key_is_known_to_the_loader():
    """A key Python's dataclass does not declare is silently dropped by
    load_thresholds — it looks configured and does nothing."""
    known = set(Thresholds.__dataclass_fields__)
    problems = {}
    for profile, keys in shipped_keys().items():
        unknown = sorted(k for k in keys if k not in known)
        if unknown:
            problems[profile] = unknown
    assert problems == {}, f"config keys Python ignores: {problems}"


def test_registry_does_not_describe_knobs_that_no_longer_exist():
    """The reverse drift: a row for a key that was renamed or removed.

    Registry rows naming things outside `Thresholds` are allowed only when they
    are explicitly known to live elsewhere — otherwise the table is describing
    a knob nobody can set.
    """
    explicit, _ = registry_keys()
    known = set(Thresholds.__dataclass_fields__)
    # Documented-but-unbuilt knobs the plan reserves for later milestones.
    # Listing them here is the honest alternative to silently passing.
    reserved = {"batch_size", "queue_ttl_days"}
    stale = sorted(k for k in explicit if k not in known and k not in reserved)
    assert stale == [], (
        f"§4.3.1 documents keys that no Thresholds field backs: {stale}. "
        "Either implement them, or move them to the reserved set with a note."
    )


@pytest.mark.parametrize("profile", ["config", "config-live"])
def test_shipped_profiles_load_and_validate(profile):
    """The registry is worthless if the file it describes does not parse."""
    from jester.config import load_thresholds

    path = REPO / profile / "thresholds.yaml"
    if not path.exists():
        pytest.skip(f"{profile}/thresholds.yaml not present")
    load_thresholds(path)  # validates ranges + cross-field rules, raises on drift


# ---- scraper.yaml parity (Python must know what Go reads) ------------------


def test_python_knows_every_scraper_key_go_reads():
    """A key only one language declares is a key the console cannot show.

    The Go worker acts on timezone / locale / warmup_navigations; Python's
    ScraperConfig has to declare them too, or the Config page silently renders
    a subset of the file and an operator edits a knob that appears absent.
    """
    from dataclasses import fields

    from jester.fetchers.cloak import ScraperConfig, load_scraper_config

    raw = yaml.safe_load((REPO / "config" / "scraper.yaml").read_text(encoding="utf-8")) or {}
    known = {f.name for f in fields(ScraperConfig)}
    unknown = sorted(set(raw) - known)
    assert unknown == [], f"scraper.yaml keys Python ignores: {unknown}"

    # and they actually round-trip, not just parse
    cfg = load_scraper_config(REPO / "config" / "scraper.yaml")
    for key in raw:
        assert hasattr(cfg, key), f"{key} declared but not loaded"


def test_the_35_3_tiktok_decision_is_recorded():
    """Checkpoint 5 asks for a written decision before any TikTok work.

    It is recorded in scraper.yaml comments; this asserts it stays there, and
    that the code still matches it — TikTok must have no adapter while the
    note says 'flag kept and left OFF'.
    """
    from jester.sources import SUPPORTED_KINDS

    note = (REPO / "config" / "scraper.yaml").read_text(encoding="utf-8")
    assert "35.3 DECISION" in note, "the §35.3 decision note was removed"
    if "NO adapter built" in note:
        tiktok = {k for k in SUPPORTED_KINDS if k[0] == "tiktok"}
        assert tiktok == set(), (
            "scraper.yaml records 'no adapter built' but sources.py claims "
            f"TikTok kinds are supported: {tiktok} — update the note or the code"
        )
