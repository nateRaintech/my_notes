"""Pure-Python text helpers for notes.

Exposes :func:`derive_title`, which turns a note's Markdown body into a short
display title for the note list (M3) and quick-switcher (M4), and
:func:`count_words`, which counts the words in a note for the status-bar word
count (M5). No Qt, no Markdown rendering — just enough text handling to label
and measure a note.
"""

from __future__ import annotations

import re

# A leading ATX heading marker: one to six '#' followed by whitespace or the end
# of the line. Per CommonMark, '#hashtag' (no space) is *not* a heading, and a
# run of seven or more '#' is literal text — both fall out of the {1,6} bound.
_ATX_MARKER = re.compile(r"^(#{1,6})(?:\s|$)")

# Characters Windows forbids in file names, plus ASCII control characters.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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


def count_words(text: str) -> int:
    """Count the words in a note's text.

    A *word* is a whitespace-separated token containing at least one
    alphanumeric character. This means standalone Markdown punctuation — a lone
    ``#`` heading marker, a ``-`` bullet, a ``---`` rule — is not counted, while
    markup wrapped around a word (``**bold**``) still counts that word once.
    Splitting is on any run of whitespace, so newlines and runs of spaces behave
    like a single separator; empty or whitespace-only text has zero words.
    """
    return sum(
        1 for token in text.split() if any(char.isalnum() for char in token)
    )


def safe_filename(title: str, *, max_length: int = 100, fallback: str = "note") -> str:
    """Turn a note title into a default file name stem (no extension).

    Replaces the characters Windows rejects in file names — and control
    characters — with ``_``, collapses whitespace, caps the length, and trims
    trailing dots and spaces (Windows silently drops them, which would change
    the name the user saw in the dialog). Returns ``fallback`` if nothing usable
    is left. Used for the export dialogs' suggested file names (#105).
    """
    stem = " ".join(_UNSAFE_FILENAME.sub("_", title).split())
    stem = stem[:max_length].rstrip(" .")
    return stem or fallback
