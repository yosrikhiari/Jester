# Console security

The console serves the archive and can start pipeline work. Its defaults are
loopback-only and write-gated; everything else is opt-in.

## Defaults

- `--host 127.0.0.1`: another machine cannot connect. Docker publishes the
  port on `127.0.0.1` for the same reason.
- Every response carries `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and a CSP that
  allows only same-origin scripts, styles and connections. The page cannot be
  framed; a static file cannot be re-typed by the browser.
- Every mutating POST needs `Authorization: Bearer <token>`, compared in
  constant time. With no `JESTER_CONSOLE_TOKEN` there is no credential a
  write could present, so every POST is refused with 401: a tokenless host
  console is read-only in effect, and GETs stay open. The container goes
  further: its entrypoint passes `--read-only` when the token is empty, so
  the shipped stack refuses every write before routing (403), not only at
  the auth check.
- A bind to anything other than `127.0.0.1`, `localhost`, `::1` or `[::1]`
  is refused at startup (non-zero exit) unless a token is set or
  `--read-only` is passed. Docker listens on every interface inside the
  container, which is why the entrypoint selects `--read-only` when compose
  passes an empty token, and why compose passes `JESTER_CONSOLE_TOKEN`
  through from `.env` when writes are wanted.
- A POST carrying an `Origin` header for another site is refused with 403
  before its body is read. `curl` and same-origin fetches are unaffected.
- `/api/export` only writes inside the exports root — `JESTER_EXPORTS_DIR`
  when set, else `exports/` beside the database. A target that resolves
  elsewhere (`..`, an absolute path, a symlink out) is refused with 400.

## Read auth

GETs are open by default so a dashboard can poll without holding a secret. If
another device can reach the port, set both:

```bash
export JESTER_CONSOLE_TOKEN=...        # required for writes
jester console --read-auth             # also require it on GETs
```

`--read-auth` (or `JESTER_READ_AUTH=1`) guards `/api/*` and `/metrics`; static
files stay open so the page can load and ask for the token. It fails closed:
with no token configured nothing can satisfy the check, so reads are refused
too until one is set. The console's own
`fetch` wrapper sends the token from this tab's `sessionStorage` and prompts
once on a 401. A token typed into the prompt lasts for the tab, not for the
browser profile.

## Browser threat model

With a token and read auth, a cross-origin page cannot read the archive: its
fetch has no token, and preflighted writes fail because the console sends no
CORS headers. Without a token, a same-machine browser page cannot make a
write either: the POST is 401 before its body is read, and a cross-origin
POST is refused by the same-origin check. `--read-only` (which the container
selects when the token is empty) refuses every write even earlier. Reads
remain open on loopback unless `--read-auth` is set.

Tests: `python/tests/test_console_server.py` (`S-10` section).
