"""the collector W2 — the classifier, the rules file and the scope document.

The gate is "20 labeled cases pass, all deterministic checks pass", so
what matters is not that the classifier is clever: it is that it is
*decidable*. Same text, same verdict, no network, no model, and a reason a
human can check against the post.

Most of these tests exist because something failed on real data. Version 1 of
the rules scored 1 signal in 2,600 archived records and that one was a false
positive; version 2 was measured against 184 real archived posts, and the
false positives it produced — an engineer's essay, a contractor asking about
the market, a freelancer asking how to scale — are each pinned below so they
cannot come back.
"""
from pathlib import Path

import pytest
import yaml

from jester import signals as sig
from jester.signals import filters as f
from jester.signals import scope as sc

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "config" / "signal_rules.yaml"


@pytest.fixture()
def rules():
    return f.load_rules(RULES)


def _rules_file(tmp_path, **families):
    base = {
        "owner_voice": {"weight": 0.4, "role": "buyer_required", "phrases": ["my business"]},
        "work_pain": {"weight": 0.3, "role": "pain_marker", "phrases": ["manually"]},
    }
    base.update(families)
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({"families": base}), encoding="utf-8")
    return p


# ---- the shipped rule set is usable ---------------------------------------

def test_shipped_rules_load_and_separate(rules):
    assert rules.by_role("buyer_required") and rules.by_role("pain_marker") and rules.by_role("negative")
    assert 0 < rules.practitioner_at <= rules.buyer_at <= 1
    assert 3 <= len(rules.communities_for("buyer")) <= 5, "the task asks for 3-5 communities"
    assert rules.communities_for("practitioner"), "content sources are named apart from buyer sources"
    assert rules.queries
    assert rules.approved is False, "scope stays provisional until the scope owner signs it"


def test_every_community_carries_its_evidence(rules):
    """A community is in scope because something was measured or because a
    reason was written down - never because it was assumed."""
    for c in rules.communities + rules.excluded:
        evidence = c.get("evidence", "").strip()
        assert evidence, f"{c.get('name')} has no evidence line"
        assert evidence.startswith(("measured", "proposed")), \
            f"{c['name']}: evidence must say whether it was measured or proposed"


def test_rules_file_refuses_to_be_silently_useless(tmp_path):
    """No pain family means every post is rejected and the run still looks
    healthy - exactly the silent failure the card lists under Fail."""
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({"families": {
        "owner_voice": {"weight": 0.4, "role": "buyer_required", "phrases": ["my business"]}}}),
        encoding="utf-8")
    with pytest.raises(f.RulesError, match="no pain_marker"):
        f.load_rules(p)


def test_rules_file_refuses_to_have_no_buyer_marker(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({"families": {
        "work_pain": {"weight": 0.3, "role": "pain_marker", "phrases": ["manually"]}}}),
        encoding="utf-8")
    with pytest.raises(f.RulesError, match="no buyer_required"):
        f.load_rules(p)


def test_rules_file_refuses_impossible_thresholds(tmp_path):
    p = _rules_file(tmp_path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data.update({"buyer_at": 0.3, "practitioner_at": 0.6})
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(f.RulesError, match="thresholds"):
        f.load_rules(p)


def test_rules_file_refuses_a_weight_outside_zero_to_one(tmp_path):
    p = _rules_file(tmp_path, cost={"weight": 3, "role": "buyer_boost", "phrases": ["budget"]})
    with pytest.raises(f.RulesError, match="weight"):
        f.load_rules(p)


def test_rules_file_refuses_a_negative_family_with_a_positive_weight(tmp_path):
    p = _rules_file(tmp_path, career={"weight": 0.4, "role": "negative", "phrases": ["resume"]})
    with pytest.raises(f.RulesError, match="negative"):
        f.load_rules(p)


# ---- classification is decidable ------------------------------------------

def test_same_text_always_gets_the_same_verdict(rules):
    text = "We run a small shop and do the invoicing by hand every week."
    first = f.classify(text, rules)
    for _ in range(5):
        assert f.classify(text, rules) == first


def test_a_buyer_needs_both_halves(rules):
    """Ownership language alone is a business saying hello; work pain alone is
    an engineer. A buyer is the combination, and nothing else is."""
    both = f.classify(
        "We run a small parts distributor. Two of us spend six hours a week copying "
        "orders into a spreadsheet by hand. Is it worth paying someone to build this?", rules)
    owner_only = f.classify("I run a small studio and business is good.", rules)
    pain_only = f.classify("Our deploy is manual and the integration breaks every release.", rules)
    assert both.audience == f.BUYER and both.relevant == 1
    assert owner_only.audience != f.BUYER
    assert pain_only.audience != f.BUYER, "an engineer describing toil is not someone who will pay"


def test_a_practitioner_is_content_and_says_so(rules):
    v = f.classify(
        "We automate the manual steps with a script but the integration breaks every "
        "time the provider changes their API. It costs us a day a month.", rules)
    assert v.audience == f.PRACTITIONER and v.relevant == 1
    assert "not a lead" in v.reason


def test_ambiguous_is_kept_and_flagged(rules):
    v = f.classify("I run a small studio and things are getting busier. How do you handle it?", rules)
    assert v.audience == f.BUYER and v.ambiguous is True
    assert v.confidence < rules.buyer_at


def test_a_buyer_can_need_software_built_without_any_manual_work(rules):
    """Found by a control test: "we need someone to build an order portal" is
    as clear a buyer as it gets and contains no manual-work language."""
    v = f.classify(
        "I own a small manufacturing business. We need someone to build a simple order "
        "portal that talks to our ERP. No idea whether to hire a developer or outsource.", rules)
    assert v.audience == f.BUYER and "needs_built" in v.families


def test_someone_selling_their_own_time_is_not_a_buyer(rules):
    """Three of the first measured run's seven buyer signals were a contractor,
    a freelancer and a founder asking how to find customers. They are peers."""
    for text in ("I run a small consultancy and I am trying to find clients for my freelance work.",
                 "I freelance for US clients and my contract ended. How do I get clients?"):
        v = f.classify(text, rules)
        assert v.audience != f.BUYER, f"supply side classified as a buyer: {text!r}"


def test_an_industry_essay_is_not_a_problem(rules):
    """The worst false positive was a well-written essay on organisational
    speed, cross-posted to two communities and scored twice."""
    v = f.classify(
        "We all know large organisations are slow. In my experience the org around "
        "implementation never speeds up, however fast the manual work gets automated.", rules)
    assert v.audience == f.NONE


def test_a_rejection_names_what_rejected_it(rules):
    v = f.classify("Finally finished cable management on my homelab. Just showing off.", rules)
    assert v.against and "hobby" in v.reason
    # A rejection nobody can audit is a guess with a number on it.
    assert v.reason.startswith("rejected:")


def test_a_rejection_says_which_half_was_missing(rules):
    """The reason must not contradict itself: "no business voice" on a post
    whose ownership language fired is worse than no reason at all."""
    owner_only = f.classify("I run a small studio and business is good.", rules)
    assert "never says what work it needs done" in owner_only.reason
    pain_only = f.classify("Our deploy is manual.", rules)
    assert "no business voice" in pain_only.reason


def test_a_hiring_post_is_hard_rejected_however_much_pain_it_describes(rules):
    v = f.classify(
        "We run a small agency and everything is done manually by hand every week. "
        "We are hiring a release engineer.", rules)
    assert v.audience == f.NONE and v.confidence == 0.0
    assert "hard reject" in v.reason


def test_the_title_is_scored_with_the_body(rules):
    body = "Details in the thread."
    assert f.classify(body, rules).audience == f.NONE
    v = f.classify("We do it all manually and it is killing us.", rules,
                   title="I run a small business and need someone to build a tool")
    assert v.audience == f.BUYER


def test_phrases_match_on_word_boundaries(rules):
    """A URL slug must not vote: '/manually-curated-list' sits between two
    non-word characters, and a Reddit post is mostly links."""
    assert f.classify("see https://x.invalid/manually-curated-list", rules).matched == ()
    assert "manually" in f.classify("we do it manually", rules).matched


def test_a_family_counts_once_however_many_of_its_phrases_matched(rules):
    """Otherwise a long rambling post out-scores a precise one."""
    one = f.classify("Our deploy is manual.", rules)
    many = f.classify(
        "Our deploy is manual, done by hand, tracked in a spreadsheet, with copy paste "
        "between an excel sheet and airtable, and the integration keeps breaking.", rules)
    assert many.confidence == one.confidence


def test_empty_text_is_classified_not_crashed(rules):
    v = f.classify("", rules)
    assert v.audience == f.NONE and v.reason == "no text to classify"


def test_apply_to_writes_the_verdict_onto_the_signal(rules):
    s = sig.Signal(source_id="t3_x",
                   text="We run a small shop and do the invoicing by hand. Worth paying someone?")
    f.apply_to(s, rules)
    assert s.match_reason and 0 <= s.match_confidence <= 1
    assert s.relevant in (0, 1) and s.audience in (f.BUYER, f.PRACTITIONER, f.NONE)


# ---- fixtures are graded by the classifier, not by themselves --------------

def test_every_fixture_case_lands_in_its_declared_audience(rules):
    res = sig.check_fixtures(rules)
    assert res["failed"] == [], f"fixture cases missed their label: {res['failed']}"
    assert res["passed"] == res["total"] == 5
    assert res["categories"] == ["ambiguous", "edited", "failure", "irrelevant", "relevant"], \
        "the five categories the fixture gate names must all be exercised"
    assert "buyer" in res["audiences"] and "practitioner" in res["audiences"], \
        "both consumers of this task must be exercised by the fixtures"


def test_exported_records_carry_the_classifier_output_not_typed_values(rules):
    """A CSV showing numbers no code produced would make the gate check a
    label against itself."""
    for s in sig.synthetic_signals(rules=rules):
        title = "" if s.kind == "comment" else s.title
        v = f.classify(s.text, rules, title=title)
        assert s.match_confidence == v.confidence
        assert s.match_reason.startswith(v.reason)
        assert s.relevant == v.relevant
        assert s.audience == v.audience


def test_a_stored_reason_names_the_words_that_fired_it(rules):
    """The verdict is computed from the whole post; the archive keeps a
    600-character excerpt. So a record can say "owner_voice + work_pain" while
    the text stored beside it contains neither phrase — and a reviewer checking
    the claim finds nothing and concludes the classifier is broken. Naming the
    phrases makes the verdict checkable against the source link."""
    kept = [s for s in sig.synthetic_signals(rules=rules) if s.audience != "none"]
    assert kept, "the fixture set must keep something, or this proves nothing"
    for s in kept:
        assert "[matched:" in s.match_reason, s.match_reason
        quoted = s.match_reason.split("[matched:", 1)[1].rstrip("]")
        for phrase in [p.strip() for p in quoted.split(",") if p.strip()]:
            assert phrase in f"{s.title}\n{s.text}".lower(), \
                f"reason names {phrase!r} but the record does not contain it"


def test_counts_separate_buyer_from_practitioner(tmp_path, rules):
    """Adding them together would inflate the only number anyone reads."""
    db = sig.open_signals(str(tmp_path / "c.db"))
    sig.upsert(db, sig.synthetic_signals(rules=rules))
    c = sig.counts(db)["fixture"]
    assert c["buyer"] == 2 and c["practitioner"] == 1
    assert c["relevant"] == c["buyer"] + c["practitioner"]


# ---- crossposts are reported, never merged --------------------------------

def test_crossposts_are_reported_with_their_communities(tmp_path):
    db = sig.open_signals(str(tmp_path / "x.db"))
    same = "We run the release checklist by hand and it breaks every time."
    sig.upsert(db, [
        sig.Signal(source_id="t3_a", community="r/devops", text=same),
        sig.Signal(source_id="t3_b", community="r/sysadmin", text=same),
        sig.Signal(source_id="t3_c", community="r/devops", text="something else entirely"),
    ])
    found = f.crossposts(db)
    assert len(found) == 1
    assert found[0]["count"] == 2
    assert sorted(found[0]["record_ids"]) == ["reddit:t3_a", "reddit:t3_b"]
    assert found[0]["communities"] == ["r/devops", "r/sysadmin"]
    # Both rows survive: two communities raising the same question is the more
    # useful fact, not the less.
    assert db.execute(f"SELECT COUNT(*) FROM {sig.TABLE}").fetchone()[0] == 3


# ---- the scope document is generated, not typed ---------------------------

def test_scope_document_states_the_things_seif_has_to_decide(rules):
    md = sc.scope_markdown(rules, today="2026-09-22")
    assert "PROVISIONAL" in md
    for c in rules.communities:
        assert c["name"] in md
    for q in rules.queries:
        assert q in md
    assert "commercial" in md.lower() and "non-commercial" in md.lower(), \
        "the free tier being non-commercial is the reason this decision exists"
    assert "replacement" in md.lower(), \
        "the document must name what happens if access is refused"
    for c in rules.excluded:
        assert c["name"] in md, "a community that was measured and dropped must say so"
    for name, _access, _why in sc.REPLACEMENT_SOURCES:
        assert name.split(" (")[0] in md, "the fork's alternatives are pre-named, not decided later"


def test_scope_document_follows_the_rules_file(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({
        "families": {
            "owner_voice": {"weight": 0.4, "role": "buyer_required", "phrases": ["my business"]},
            "work_pain": {"weight": 0.3, "role": "pain_marker", "phrases": ["manually"]}},
        "scope": {"status": "approved", "decided_by": "the scope owner", "decided_on": "2026-10-02",
                  "communities": [{"name": "r/onlyone", "serves": "buyer",
                                   "evidence": "proposed - test", "why": "because"}],
                  "queries": ["one query"]},
    }), encoding="utf-8")
    md = sc.scope_markdown(f.load_rules(p), today="2026-10-02")
    assert "APPROVED" in md and "r/onlyone" in md and "one query" in md
    assert "r/devops" not in md, "the document must not carry a community the rules no longer name"


def test_write_scope_puts_the_file_where_the_export_is(tmp_path, rules):
    path = sc.write_scope(tmp_path, rules, today="2026-09-22")
    assert path.name == "scope-and-access.md" and path.read_text(encoding="utf-8").startswith("# the collector")
