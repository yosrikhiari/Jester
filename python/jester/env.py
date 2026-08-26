"""Load `.env` into os.environ.

Deliberately stdlib-only. The alternative is python-dotenv, which would be the
project's first runtime dependency added for four lines of parsing, and the
console has to boot on a machine where `pip install` has not been re-run.

Values already present in the real environment WIN over the file. A shell that
exported GROQ_API_KEY for one command is making a deliberate override, and a
dotfile silently outranking it is the kind of thing that costs an afternoon.
"""
import os
from pathlib import Path

# repo root: python/jester/env.py -> python/jester -> python -> <root>
REPO_ROOT = Path(__file__).resolve().parents[2]

#: Searched in order; the first hit wins. `config/local.env` is the path the
#: shipped example file documents, kept so existing setups keep working.
CANDIDATES = (".env", "config/local.env", "local.env")

_loaded = False


def parse_env(text: str) -> dict:
    """Parse dotenv text. Supports `KEY=value`, `export KEY=value`, comments,
    blank lines, and single/double quoted values. No interpolation — a literal
    `$` in a secret is a literal `$`."""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def load_env(root=None, *, override=False) -> dict:
    """Merge the first dotenv found under `root` into os.environ.

    Returns the keys it actually set, so a caller can log what changed without
    logging the values.
    """
    base = Path(root) if root else REPO_ROOT
    applied = {}
    for name in CANDIDATES:
        path = base / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            # An unreadable .env must not take the console down with it; the
            # missing key surfaces as a clear "no API key" error downstream.
            break
        for key, value in parse_env(text).items():
            if override or not os.environ.get(key):
                os.environ[key] = value
                applied[key] = value
        break
    return applied


def load_env_once(root=None) -> dict:
    """Idempotent `load_env` for entry points. The CLI, the console server and
    the test suite can each call it without re-reading the file per request."""
    global _loaded
    if _loaded:
        return {}
    _loaded = True
    return load_env(root)
