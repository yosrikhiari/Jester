# Plan archive

Where a plan goes when it is finished with. This directory holds superseded
plans; the plans still in force stay in [`docs/plans/`](../).

## The rule

A plan moves here when, and only when, **its own header says it is
superseded** — a status line such as "Superseded by ...", "Closed", or
"Replaced by ...". Three consequences follow, and each one is deliberate:

- **Nothing is moved on someone's judgement of the date.** An old plan is not a
  finished plan: the 2026-10-29 roadmap is still the register the current work
  cites by item id, and a plan whose header says "Active" or "DRAFT" is still
  the statement of intent even if nothing has moved on it in months.
- **Reports are never moved.** Everything under `docs/reports/` is a dated
  record of what was measured and when. A report is not superseded by a later
  report; it is a different reading of a different tree, and moving it would
  break the links that quote it as evidence.
- **Inbound links are updated in the same change as the move**, and the moved
  plan keeps its filename so a link can be repaired by adding `archive/`.
  `docs/STATUS.md` remains the only index of what is done; this page is a
  filing rule, not a status list. `python/tests/test_doc_links.py` fails if a
  link is left pointing at the old path.

## As of 2026-11-02

No plan in `docs/plans/` qualifies, so nothing was moved. The five files and
the status their own headers carry:

| Plan | Status word in its header |
|---|---|
| `2026-08-31-real-estate-intelligence.md` | SAVED FOR REVIEW (awaiting approval before Phase 0) |
| `2026-09-02-cloakbrowser-cluster-plan.md` | Active (plan approved, implementation in progress) |
| `2026-10-29-implementation-iteration-log.md` | no status line; a dated iteration log, and a historical record |
| `2026-10-29-project-assessment-and-master-roadmap.md` | no status line; the dated master roadmap, still cited by item id |
| `realestate-compliance-audit.md` | DRAFT |

An empty archive is the honest state, not an unfinished one. Placeholder files
for these five are not created here: a copy would be a second document that
drifts, and the file that is linked to is the one in `docs/plans/`.
