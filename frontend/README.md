# frontend/ — React + TypeScript + Vite scaffold (F-10)

**Status: scaffold, not the shipped UI.** The operator console that ships
with Jester is served by Python at `:8619` from
`python/jester/console/static/` (vanilla JS, no build step, travels with the
package). This directory is a Vite + React 19 shell kept for a future port:
tokens + base CSS are live, the App shell is a placeholder, and components
port from `inspiration/design-system-revision/`.

**Commands** (Node 24; `npm ci --ignore-scripts` per the Dockerfile):

```bash
npm run dev      # :5173 with hot reload
npm run lint     # oxlint (react + typescript rules, hooks as errors)
npm run build    # tsc -b && vite build — CI runs lint + build on every push
```

**Promotion criteria** (when this stops being a scaffold): real routes that
mirror the console's `/api/*` surface, `vitest` + Testing Library coverage
for them, and the dead-code rule — no stylesheet may ship selectors its own
`tsx` does not use (that is what F-10 pruned from `src/App.css`).
