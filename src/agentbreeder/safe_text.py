"""Untrusted text (model replies, judge explanations, container logs) made safe to print.

`excerpt` is for one-line fields (a case id, a judge's reason): every control character becomes a
space and the text is capped. `visible` is for log lines on a terminal (security finding S-08):
C0 controls except tab and newline, DEL and C1 controls, ESC included, are shown as `\\xNN`
escapes, so an echoed OSC 52 or CSI sequence cannot write the clipboard, forge a link or rewrite
earlier lines. Bidirectional overrides and the Unicode line and paragraph separators are escaped
the same way.
"""

from __future__ import annotations

import re

_BIDI = "‪-‮⁦-⁩‎‏؜"
# Line and paragraph separators: some readers break lines on them (a forged log line).
_SEPARATORS = "  "
_ANY_CONTROL = re.compile(f"[\x00-\x1f\x7f-\x9f{_BIDI}{_SEPARATORS}]")
_SHOWN = re.compile(f"[\x00-\x08\x0b-\x1f\x7f-\x9f{_BIDI}{_SEPARATORS}]")


def excerpt(text: object, limit: int) -> str:
    """One line, controls replaced by spaces, at most `limit` characters (with `…` if cut)."""
    flat = " ".join(_ANY_CONTROL.sub(" ", str(text)).split())
    return flat if len(flat) <= limit else flat[: max(0, limit - 1)] + "…"


def _escape(match: re.Match) -> str:
    code = ord(match.group())
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def visible(text: str) -> str:
    """Controls other than tab and newline as visible escapes (for a terminal)."""
    return _SHOWN.sub(_escape, text)
