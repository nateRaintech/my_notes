"""Pure-Python text helpers for notes.

Currently exposes :func:`derive_title`, which turns a note's Markdown body into
a short display title for the note list (M3) and quick-switcher (M4). No Qt, no
Markdown rendering — just enough text handling to label a note.
"""

from __future__ import annotations

import re

# A leading ATX heading marker: one to six '#' followed by whitespace or the end
# of the line. Per CommonMark, '#hashtag' (no space) is *not* a heading, and a
# run of seven or more '#' is literal text — both fall out of the {1,6} bound.
_ATX_MARKER = re.compile(r"^(#{1,6})(?:\s|$)")


def derive_title(markdown: str, *, max_length: int = 120, fallback: str = "Untitled") -> str:
    """Derive a human-readable title from a note's Markdown body.

    Uses the first non-blank line: a leading ATX heading marker is stripped,
    internal whitespace is collapsed to single spaces, and the result is
    truncated to ``max_length`` characters (with a trailing ellipsis) if needed.
    Returns ``fallback`` when the body has no usable text.
    """
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if _ATX_MARKER.match(line):
            hashes = len(line) - len(line.lstrip("#"))
            line = line[hashes:]

        # Collapse any run of whitespace to a single space and trim the ends.
        title = " ".join(line.split())
        if not title:
            # The first non-blank line was an empty heading (e.g. a lone '#'):
            # no usable text, so use the fallback.
            return fallback

        if len(title) > max_length:
            title = title[: max_length - 1].rstrip() + "…"
        return title

    return fallback
