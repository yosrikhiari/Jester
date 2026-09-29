# W1 pack — the 28 Sep milestone, with the proof recorded

Everything the W1 checkpoint asks for, in the repo, from one run of the
documented commands on a **fresh clone of `main` at `3815fe5`**, installed from
the lock exactly as CI installs it. Captured 2026-09-29 10:56 UTC on Linux
x86_64, Python 3.12 (`python:3.12` container — CI's platform; see the note on
Windows below).

| What the checkpoint asks | Where the proof is | What it shows |
|---|---|---|
| A fresh clone, nothing pre-built | [`proof/01-clone.txt`](proof/01-clone.txt) | the commit, 352 files, **0 files in `data/`** |
| Install from the lock | [`proof/02-install.txt`](proof/02-install.txt) | `pip install -r python/pylock.toml`, imports ok |
| One command writes the CSV | [`proof/03-export-run1.txt`](proof/03-export-run1.txt) | 5 new rows, CSV of 5, `reconciles=True` |
| …and running it again does not duplicate | [`proof/04-export-run2.txt`](proof/04-export-run2.txt) | **0 new, 5 seen again**, still 5 rows |
| The 5-row CSV | [`signals-synthetic.csv`](signals-synthetic.csv) | every row says `mode=synthetic` — never counted as live |
| The field map prints | [`proof/06-field-map.txt`](proof/06-field-map.txt), [`field-map.md`](field-map.md) | 30 fields, one map for SQLite, ClickHouse and the CSV |
| The fixture gate | [`proof/05-check.txt`](proof/05-check.txt), [`fixture-gate.json`](fixture-gate.json) | **20/20** across relevant, irrelevant, ambiguous, edits and failures |
| Edited, removed, failed records | `fixture-gate.json` | an edit dated once; a removal recorded not deleted; a removal that comes back; source down, rate-limited, malformed, blocked |
| What a run costs | [`run-ledger-sample.csv`](run-ledger-sample.csv), [`requests-per-source.csv`](requests-per-source.csv) | the collector calls **no model**; a run's cost is its requests, counted per source per day against a budget |
| Scope, fields and access | [`../scope-and-access.md`](../scope-and-access.md) | generated from the rules and the live archive |
| The export's own manifest | [`export-manifest.json`](export-manifest.json) | rows in the database = rows in the CSV |

**CI repeats the clean-clone check on every commit** — the "Python — clean
clone, lock install, suite" job installs from the lock with no cache, runs the
export twice and fails unless the second run adds nothing and the manifest
reconciles, then runs the fixture gate. This pack is one recorded instance of
what that job proves continuously.

The run-ledger sample is from the live archive on this machine, not the clean
clone: the clone has no live data by design. It shows the morning chain on
29 Sep — HN "Who is hiring", then the job boards, then the Reddit rooms scored
from the worker's archive with no requests to Reddit — and, on 28 Sep, a
board that had spent its daily budget being skipped rather than asked.

**Data design review:** done 25 Sep 2026 by the data-design reviewer —
ClickHouse holds insert and query only; no defects raised.

## Reproduce it

```bash
git clone https://github.com/yosrikhiari/Jester.git w1-check && cd w1-check
python -m pip install --only-binary :all: "pip==26.2.1"
python -m pip install --only-binary :all: -r python/pylock.toml
export PYTHONPATH=python JESTER_MOCK=1
python -m jester.cli signals export --db /tmp/s.db --out /tmp/out   # twice
python -m jester.cli signals check
python -m jester.cli signals field-map
```

**On Windows the lock does not install.** `pylock.toml` pins the wheels CI
uses (Linux); on Windows pip finds no matching `coverage` wheel and stops
before anything is installed. Run it on Linux or in a `python:3.12`
container, as this pack was.
