"""The buyer brief, generated from the archive (2026-09-29).

It was built by a script outside the repo until now: nobody else could rebuild
it and no schedule could refresh it. These pin down what the page is for --
recent buyers from usable sources, one row per company, with what came of
them -- and what it must never carry.
"""
import json
from datetime import datetime, timezone

import pytest

from jester import cli
from jester import signals as sig
from jester.signals import brief

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


def _rec(db, sid, *, community="himalayas/contract", company="Acme", conf=0.9,
         created="2026-09-20T10:00:00Z", title=None, text="", audience="buyer", platform="himalayas"):
    s = sig.Signal(source_id=sid, platform=platform, community=community, kind="post",
                   source_url=f"https://example.test/{sid}", mode="live", audience=audience,
                   title=title or f"{company} | Senior Python Developer | Remote | Contractor",
                   text=text or "Contract role building internal tools.", company=company,
                   buyer_intent="contractor", match_confidence=conf, created_utc=created)
    sig.upsert(db, [s])
    return f"{platform}:{sid}"


@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


def test_one_row_per_company_with_its_strongest_advert(db):
    _rec(db, "1", company="Acme", conf=0.8)
    _rec(db, "2", company="acme ", conf=0.95, title="Acme | Staff Engineer | Remote | Contractor")
    _rec(db, "3", company="Beta", conf=0.9)
    rows = brief.build(db, now=NOW)["companies"]
    assert [c["company"] for c in rows] == ["acme ", "Beta"]
    assert rows[0]["times"] == 2 and rows[0]["role"] == "Staff Engineer"


def test_old_removed_and_reddit_records_are_kept_out_of_the_list(db):
    _rec(db, "1", company="Fresh")
    _rec(db, "2", company="Stale", created="2026-05-01T00:00:00Z")
    gone = _rec(db, "3", company="Gone")
    sig.mark_removed(db, [gone])
    _rec(db, "4", company="unknown", community="r/forhire", platform="reddit",
         title="[Hiring] contract Python developer $80/hr")
    _rec(db, "5", company="NotABuyer", audience="practitioner")
    data = brief.build(db, now=NOW)
    assert [c["company"] for c in data["companies"]] == ["Fresh"]
    assert data["older_count"] == 1
    assert data["reddit_records"] == 1 and data["reddit"][0]["community"] == "r/forhire"
    assert data["cutoff"] == "2026-07-31"


def test_an_undated_advert_is_not_counted_as_old(db):
    """The same rule as leads.csv: a missing date is not evidence of age."""
    _rec(db, "1", company="Undated", created="")
    assert [c["company"] for c in brief.build(db, now=NOW)["companies"]] == ["Undated"]


def test_a_company_carries_its_furthest_outcome_and_the_totals_count_picks(db):
    a = _rec(db, "1", company="Acme")
    b = _rec(db, "2", company="Acme")
    _rec(db, "3", company="Beta")
    sig.set_outcome(db, a, "picked")
    sig.set_outcome(db, b, "replied")
    data = brief.build(db, now=NOW)
    by = {c["company"]: c for c in data["companies"]}
    assert by["Acme"]["outcome"] == "replied" and by["Beta"]["outcome"] == ""
    assert (data["picked"], data["worked"]) == (1, 1)


def test_an_agency_doing_its_own_client_work_is_marked(db):
    _rec(db, "1", company="Studio", text="We build digital products for our clients across Europe.")
    _rec(db, "2", company="Direct", text="We are a SaaS company building our own product.")
    by = {c["company"]: c["agency"] for c in brief.build(db, now=NOW)["companies"]}
    assert by == {"Studio": True, "Direct": False}


def test_the_page_embeds_the_data_safely_and_names_no_one(db):
    _rec(db, "1", company="</script><b>x</b>")
    html = brief.render(brief.build(db, now=NOW))
    assert "/*DATA*/" not in html
    assert html.count("</script>") == 1, "a company name must not be able to close the script"
    assert '<meta charset="utf-8">' in html
    for name in ("Seif", "Omar", "Rassil"):
        assert name not in html, "the repo is public: roles, not people"


def test_the_command_writes_the_page_and_its_data(tmp_path, db, capsys):
    _rec(db, "1", company="Acme")
    path = str(tmp_path / "s.db")
    cli.main(["signals", "brief", "--db", path, "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "1 companies from 1 buyer signal(s)" in out
    page = tmp_path / "out" / "brief" / "buyer-brief.html"
    data = json.loads((tmp_path / "out" / "brief" / "brief.json").read_text(encoding="utf-8"))
    assert page.exists() and data["companies"][0]["company"] == "Acme"


def test_the_brief_is_a_schedulable_command():
    from jester import schedule
    assert schedule.COMMAND_SPEC["signals-brief"]["argv"] == ("signals", "brief")
    assert schedule.TASK_FOR_COMMAND["signals-brief"].endswith("SignalsBrief")
