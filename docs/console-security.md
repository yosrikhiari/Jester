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
- Every mutating POST needs `Authorization: Bearer <token>` when
  `JESTER_CONSOLE_TOKEN` is set. The comparison is constant-time.
- A POST carrying an `Origin` header for another site is refused with 403
  before its body is read. `curl` and same-origin fetches are unaffected.

## Read auth

GETs are open by default so a dashboard can poll without holding a secret. If
another device can reach the port, set both:

```bash
export JESTER_CONSOLE_TOKEN=...        # already required for writes
jester console --read-auth             # also require it on GETs
```

`--read-auth` (or `JESTER_READ_AUTH=1`) guards `/api/*` and `/metrics`; static
files stay open so the page can load and ask for the token. The console's own
`fetch` wrapper sends the token from this tab's `sessionStorage` and prompts
once on a 401. A token typed into the prompt lasts for the tab, not for the
browser profile.

## Browser threat model

With a token and read auth, a cross-origin page cannot read the archive: its
fetch has no token, and preflighted writes fail because the console sends no
CORS headers. Without a token, a same-machine browser page can make a simple
POST; the same-origin check above is what closes that, and `--read-only` closes
the write path entirely.

Tests: `python/tests/test_console_server.py` (`S-10` section).
