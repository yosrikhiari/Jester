"""What may leave the process, and what counts as configured.

The console's `/api/config` rendered `scraper.yaml` verbatim, which is where
the CloakBrowser licence key and the residential proxy live. A proxy URL
carries its own `user:password`, so both are credentials, and the Config page
never needed either one: it needed to know whether one is configured.

Two questions, one module, because they have to agree:

* `is_secret_key` — is this field a credential? The console blanks every key
  that answers yes, so a new credential added to scraper.yaml cannot leak by
  being forgotten here.
* `is_configured` — is there a real value behind it? Python's
  `os.path.expandvars` leaves `${NAME}` literal when NAME is unset while the
  Go side blanks it, so a truthiness test called an unset licence key "set".
  Both readers now ask this function instead (see go/internal/config's
  `sanitize`, which applies the same rule on the way in).
"""
import re
from typing import Mapping, Tuple

#: Case-insensitive, deliberately broad: `license_key`, `proxy`,
#: `CLOAKBROWSER_TOKEN`, `api_secret`, `password`. A false positive blanking a
#: harmless field is cheap; a false negative serves a credential.
_SECRET_KEY_RE = re.compile(r"license|proxy|secret|token|key|password", re.I)

#: A value `expandvars` could not expand: `${NAME}`, `${NAME:-default}` or a
#: bare `$NAME`. Mirrors `unexpandedRE` in go/internal/config/config.go.
_PLACEHOLDER_RE = re.compile(r"^\$(?:\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*)$")


def is_secret_key(name) -> bool:
    """Whether `name` (a config field) holds a credential."""
    return bool(_SECRET_KEY_RE.search(str(name or "")))


def is_configured(value) -> bool:
    """Whether `value` holds something real, as opposed to nothing at all.

    Empty, whitespace and a still-literal `${NAME}` placeholder all mean "not
    configured" — the placeholder in particular, because that is exactly what
    an unset environment variable leaves behind.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return False
    return _PLACEHOLDER_RE.match(text) is None


def redact_secrets(mapping: Mapping) -> Tuple[dict, dict]:
    """Split `mapping` into what may be served and which secrets are set.

    Returns `(public, secrets_set)`. A secret field keeps its key — the Config
    page lists the fields scraper.yaml declares — but never its value; the
    boolean beside it is what the page renders.
    """
    public, secrets_set = {}, {}
    for key, value in mapping.items():
        if is_secret_key(key):
            secrets_set[key] = is_configured(value)
            public[key] = ""
        else:
            public[key] = value
    return public, secrets_set
