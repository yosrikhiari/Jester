"""Point ids and embedding ids: pure functions of the unique key.

Every id below used to be `abs(hash(key)) % 2**63` — process-salted by
Python's hash randomisation, so the SAME key landed on a different point in
every interpreter. The probe that pinned this (dropped before this test was
written) ran the archivist's own stamping line in three fresh processes:

    8345280932478430340 / 3932554874945708019 / 999861363209644550

Three runs, three ids, one nugget — `reembed --all` quietly tripled the
collection and the stamped embedding_id pointed at coordinates that existed
in exactly none of them.
"""
import os
import subprocess
import sys

from jester.embed import FakeEmbedding
from jester.vector import VectorStore, point_id_for


PROBE = ("import sys; sys.path.insert(0, {jester_dir!r}); "
         "from jester.vector import point_id_for; "
         "print(point_id_for('collab-platform-nugget-01'))")


def _two_fresh_processes_values() -> list:
    jester_dir = os.path.join(os.path.dirname(__file__), "..")
    out = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", PROBE.format(jester_dir=jester_dir)],
            capture_output=True, text=True, timeout=120, env=os.environ)
        assert proc.returncode == 0, proc.stderr
        out.append(proc.stdout.strip())
    return out


def test_point_id_is_the_same_in_every_fresh_process():
    """Cross-process determinism is the whole point. With the old
    `abs(hash(...))` this test failed most of the time it ran: PYTHONHASHSEED
    salt differs per interpreter, so identical inputs printed different ids."""
    first, second = _two_fresh_processes_values()
    assert first.isdigit()
    assert first == second
    assert first == str(point_id_for("collab-platform-nugget-01"))


def test_point_id_fits_qdrants_unsigned_63_bit_slot():
    for key in ("a", "idea:12", "reddit/t3_x/t1_y:c1", "çömment-key"):
        pid = point_id_for(key)
        assert 0 <= pid < 2 ** 63
    assert point_id_for("call it one") != point_id_for("call it two")


def test_idempotent_upsert_same_key_is_one_point():
    """Same key twice is one point, not two — that is what a key FOR is."""
    store = VectorStore.in_memory(FakeEmbedding())
    store.upsert("nug/k1", "self host email is painful", {"category": "pain_point"})
    store.upsert("nug/k1", "self host email is painful", {"category": "pain_point"})
    assert store.count() == 1
    # batch path too: two items with the same key collapse into one point
    n = store.upsert_many([
        ("nug/k2", "deploy takes all weekend", {"category": "pain_point"}),
        ("nug/k2", "deploy takes all weekend", {"category": "pain_point"}),
        ("nug/k3", "quarterly tax filing deadlines", {"category": "pain_point"}),
    ])
    assert n == 3
    assert store.count() == 3
