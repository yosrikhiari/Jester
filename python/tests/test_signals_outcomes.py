"""Outcomes and the handover list — the loop from signal to what happened.

The gap these close: the collector scored 753 adverts, kept 35, and every one
sat untouched, because outreach is somebody else's job and nobody had been
handed a list. A signal nobody works is indistinguishable from one that does
not work — and "80% precision" measured against our own rules is the rules
agreeing with themselves.

`outcome` is the only field in the archive a person writes. Nothing infers it,
because a guessed outcome would poison the one measurement here that comes
from reality.
"""
import csv

import pytest

from jester import signals as sig
from jester.signals import leads


@pytest.fixture()
def db(tmp_path):
    conn = sig.open_signals(str(tmp_path / "s.db"))
    yield conn
    conn.close()


def _buyer(db, source_id, text, conf=0.9):
    s = sig.Signal(source_id=source_id, platform="hackernews",
                   community="hn/hiring", kind="post", text=text,
                   mode="live", audience="buyer", match_confidence=conf,
                   company="Acme", buyer_intent="contract")
    sig.upsert(db, [s])
    return f"hackernews:{source_id}"


# ---- recording an outcome --------------------------------------------------

def test_a_new_record_has_no_outcome(db):
    rid = _buyer(db, "1", "Acme | Engineer | REMOTE | Contract | jobs@acme.com")
    row = db.execute(f"SELECT outcome, outcome_at FROM {sig.TABLE} "
                     "WHERE record_id = ?", (rid,)).fetchone()
    assert row["outcome"] == "" and not row["outcome_at"]


def test_recording_one_stamps_the_time_and_the_note(db):
    rid = _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    res = sig.set_outcome(db, rid, "contacted", note="emailed the address in the advert")
    assert res["was"] == "" and res["now"] == "contacted" and res["at"]
    row = db.execute(f"SELECT * FROM {sig.TABLE} WHERE record_id = ?", (rid,)).fetchone()
    assert row["outcome"] == "contacted"
    assert "emailed" in row["outcome_note"]


def test_a_lead_can_move(db):
    """Contacted today, replied on Friday, meeting next week. Re-recording
    overwrites rather than appending: a field that grows without bound is a
    field nobody reads."""
    rid = _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    sig.set_outcome(db, rid, "contacted")
    res = sig.set_outcome(db, rid, "meeting", note="Tuesday 10:00")
    assert res["was"] == "contacted" and res["now"] == "meeting"


def test_an_invented_outcome_is_refused(db):
    """Loud, because the alternative is a silent no-op: someone records the
    one meeting this archive has ever produced and the row stays empty."""
    rid = _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    with pytest.raises(sig.OutcomeError, match="not an outcome"):
        sig.set_outcome(db, rid, "maybe")


def test_marking_a_record_that_is_not_there_is_refused(db):
    with pytest.raises(sig.OutcomeError, match="no record"):
        sig.set_outcome(db, "hackernews:nope", "contacted")


def test_clearing_an_outcome_removes_its_timestamp(db):
    rid = _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    sig.set_outcome(db, rid, "contacted")
    sig.set_outcome(db, rid, "")
    row = db.execute(f"SELECT outcome, outcome_at FROM {sig.TABLE} "
                     "WHERE record_id = ?", (rid,)).fetchone()
    assert row["outcome"] == "" and not row["outcome_at"]


# ---- the numbers that are not circular -------------------------------------

def test_untouched_is_reported_because_it_is_the_early_number(db):
    """A high buyer count with nothing acted on means the collector is
    producing and nobody is consuming — a different problem from producing
    badly, and invisible without this."""
    for i in range(3):
        _buyer(db, str(i), "Acme | REMOTE | Contract | jobs@acme.com")
    o = sig.outcomes(db)
    assert o["buyers"] == 3 and o["worked"] == 0 and o["untouched"] == 3


def test_replied_or_better_counts_only_what_a_company_did(db):
    for i in range(4):
        _buyer(db, str(i), "Acme | REMOTE | Contract | jobs@acme.com")
    sig.set_outcome(db, "hackernews:0", "contacted")
    sig.set_outcome(db, "hackernews:1", "replied")
    sig.set_outcome(db, "hackernews:2", "meeting")
    sig.set_outcome(db, "hackernews:3", "no")
    o = sig.outcomes(db)
    assert o["worked"] == 4
    # contacted is something WE did; the rest are things they did.
    assert o["replied_or_better"] == 2


def test_unfit_is_kept_apart_from_no(db):
    """A company that should never have reached outreach is a fault in the
    rules. One that said no is not. Collapsing them hides the only signal
    that can improve the classifier."""
    _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    _buyer(db, "2", "Acme | REMOTE | Contract | jobs@acme.com")
    sig.set_outcome(db, "hackernews:1", "no")
    sig.set_outcome(db, "hackernews:2", "unfit")
    o = sig.outcomes(db)
    assert o["unfit"] == 1
    assert o["by_outcome"]["no"] == 1


# ---- the handover list -----------------------------------------------------

def test_only_records_with_a_contact_route_are_handed_over(db):
    """A buyer with no way to reach them is real evidence and a dead end for
    today. It stays in the archive and out of the file, and both counts are
    reported so the distinction is visible."""
    _buyer(db, "1", "Acme | Engineer | REMOTE | Contract | jobs@acme.com")
    _buyer(db, "2", "Beta | Engineer | REMOTE | Contract | we are hiring, come find us")
    out = leads.gather(db)
    assert len(out["actionable"]) == 1
    assert len(out["unreachable"]) == 1


def test_a_worked_lead_is_not_handed_over_twice(db, tmp_path):
    """Two people emailing the same company in the same week is the failure
    this prevents."""
    _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    _buyer(db, "2", "Beta | REMOTE | Contract | jobs@beta.com")
    sig.set_outcome(db, "hackernews:1", "contacted")
    assert leads.write_csv(db, tmp_path)["written"] == 1
    assert leads.write_csv(db, tmp_path, include_worked=True)["written"] == 2


def test_the_file_puts_the_column_you_fill_in_first(db, tmp_path):
    """It is meant to be edited. The thing the reader has to write should not
    be off the right edge of the screen behind eleven columns they will not
    touch."""
    _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    res = leads.write_csv(db, tmp_path)
    rows = list(csv.DictReader(open(res["path"], encoding="utf-8")))
    assert list(rows[0])[0] == "outcome"
    assert rows[0]["email"] == "jobs@acme.com"
    assert rows[0]["record_id"] == "hackernews:1", \
        "the id must travel with the row, or an outcome cannot be recorded"


def test_the_route_is_pulled_out_so_the_reader_does_not_hunt(db, tmp_path):
    _buyer(db, "1", "Acme | REMOTE | Contract | apply at https://acme.com/jobs")
    rows = list(csv.DictReader(open(leads.write_csv(db, tmp_path)["path"],
                                    encoding="utf-8")))
    assert rows[0]["link"] == "https://acme.com/jobs"


# ---- the migration bug this work exposed -----------------------------------

def test_a_column_added_later_is_not_null_on_old_rows(db):
    """`ALTER TABLE ADD COLUMN` without a default leaves NULL on every
    existing row while new rows get ''. `WHERE outcome = ''` then silently
    skipped 34 of 35 real leads and looked like an ordinary query.

    The promise of the catch-up is that a migrated table is indistinguishable
    from a fresh one, and a column full of NULLs where a fresh table has ''
    breaks it."""
    db.execute(f"ALTER TABLE {sig.TABLE} DROP COLUMN outcome")
    db.commit()
    sig._catch_up_columns(db)
    _buyer(db, "1", "Acme | REMOTE | Contract | jobs@acme.com")
    nulls = db.execute(
        f"SELECT COUNT(*) FROM {sig.TABLE} WHERE outcome IS NULL").fetchone()[0]
    assert nulls == 0
    assert leads.gather(db)["actionable"], \
        "a NULL outcome would silently drop every pre-existing lead"
