# ADR 0001 — Frontend strategy: the Python console ships, `frontend/` is a gated scaffold

- **Status:** Accepted (2026-10-30). Codifies the decision already visible in
  the README, `frontend/README.md` and the CI job; no product code moves
  because of this record.
- **Deciders:** repository owner (the two-UI ambiguity is a product call, not
  a code one).
- **Context:** F-10 in `docs/CODEBASE_IMPROVEMENT_REPORT.md`; the
  "delete or archive the frontend" question raised during the 2026-10-30
  execution review; `JESTER-FRONTEND-SYSTEM-DESIGN.md`.

## Context

Jester has two frontend artefacts:

1. **The operator console** at `:8619`, served by Python from
   `python/jester/console/static/` — vanilla JS, no build step, no runtime
   dependency, ships inside the Python package. It is the UI in the README
   screenshots and the one the scheduler and API were built around.
2. **`frontend/`** — a React 19 + TypeScript + Vite scaffold with design
   tokens and an App shell, a `:5173` dev server in Docker Compose, and a CI
   job that runs `npm run lint` and `npm run build` on every push.

Two UIs in one repository read as ambiguity: which one is the product? The
2026-10-30 review proposed resolving it by deleting `frontend/` or renaming it
`frontend-future/`.

## Decision

**The Python console is the shipped UI. `frontend/` stays in the tree as an
explicitly labelled scaffold, kept building and linting in CI, and is not
promoted until it meets the criteria already written down.**

The label is load-bearing, not decorative:

- `README.md` marks the compose service "(scaffold)", the project-structure
  tree says "not the shipped UI", and a callout explains the console travels
  with the package.
- `frontend/README.md` opens with "Status: scaffold, not the shipped UI" and
  lists the promotion criteria: real routes mirroring the console's `/api/*`
  surface, `vitest` + Testing Library coverage for them, and no dead CSS.
- The CI job's comment names F-10 and its purpose: fail on template drift
  rather than let the scaffold rot silently.

## Alternatives considered

| Option | Why not |
|---|---|
| **Delete `frontend/`** | It removes the only worked example of the design-token system in a compiled stack, and it deletes a green CI job whose cost is one npm install. The ambiguity it addresses is already handled by the three labels above; deleting is irreversible in practice (the design exploration would have to be redone) for a problem that is documentation-shaped. |
| **Rename to `frontend-future/`** | Renaming changes every path that names it — the compose service, the Dockerfile, the CI job, both READMEs, and the design document — and buys nothing the label does not already say. A directory name is a weaker signal than a status paragraph at the top of the directory. |
| **Promote it now** | It has no routes, no tests and no API client. Promoting would put a second, untested UI in front of the same archive, which is the actual two-UI hazard. |

## Consequences

- A reviewer seeing `frontend/` can find its status in one paragraph and its
  promotion bar in the same file; "which UI is real?" has a written answer.
- The scaffold costs one CI job and must keep building. If it stops building,
  the failure is the signal to either fix it or bring the archive/delete
  question back — not to disable the job.
- **Review trigger:** when real routes land, this ADR is superseded by a
  promotion ADR that names the console pages being ported and the test bar
  they meet. Until then, no feature work goes into `frontend/`.
