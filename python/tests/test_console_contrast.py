"""WCAG 1.4.11: a form control's boundary must be visible on its own surface.

The console used `--line-strong` for input/select/textarea borders. On the
light theme that is #D8D8DE on #FFFFFF -- 1.31:1, measured. The border is the
only thing that says "this is an input" until focus, so the control was
effectively invisible to a low-vision operator. `--line-control` is the
separate token for that job; it must stay at or above 3:1 in BOTH themes.

This is a text-level check, not a browser test: the CSS ships as text and the
tokens are literal hex values, so the contrast can be computed from the file.
It also sweeps the text assets for U+FFFD, the replacement character that is
what a mis-encoded em dash looks like after it has been through the wrong
encoding once.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "jester" / "console" / "static"
CSS = (STATIC / "app.css").read_text(encoding="utf-8")

THEME = re.compile(r'html\[data-theme="(light|dark)"\]\{(.*?)\n\}', re.S)
VAR = re.compile(r"--([\w-]+)\s*:\s*(#[0-9A-Fa-f]{6})")


def _theme(name):
    for theme, body in THEME.findall(CSS):
        if theme == name:
            return dict(VAR.findall(body))
    raise AssertionError(f"theme {name!r} not found")


def _luminance(hex_colour):
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
              for v in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a, b):
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_control_border_meets_three_to_one_in_both_themes():
    for theme in ("light", "dark"):
        values = _theme(theme)
        assert "line-control" in values, f"{theme} theme has no --line-control"
        assert _contrast(values["line-control"], values["surface"]) >= 3.0


def test_form_controls_use_the_control_token_not_the_decorative_line():
    # The font-reset rule (`button,input,...{font:inherit}`) comes first and
    # has no border, so match the rule that actually draws the control.
    rules = re.findall(r"input,select,textarea\{([^}]*)\}", CSS)
    control = [r for r in rules if "background:var(--surface)" in r]
    assert control, "no form-control rule found"
    assert "border:1px solid var(--line-control)" in control[0]
    assert "var(--line-strong)" not in control[0]


def test_static_text_assets_have_no_replacement_characters():
    suffixes = {".html", ".css", ".js", ".json", ".svg", ".webmanifest"}
    for path in STATIC.rglob("*"):
        if path.suffix.lower() not in suffixes:
            continue
        text = path.read_text(encoding="utf-8")
        assert "\ufffd" not in text, f"replacement character in {path}"
