"""Case conversion, line operations, and text statistics.

Two families with different notions of scope. **Case tools** come in two kinds:
prose tools (UPPER, Title, Sentence) treat the selection as writing and act on
the whole thing, while identifier tools (camelCase, snake_case, ...) act
**line by line**, so selecting a column of names converts each one instead of
mashing the block into a single identifier.

**Line tools** all preserve whether the selection ended in a newline (see
:func:`core.tools._util.split_lines`), which is what makes them idempotent on a
selected block.
"""

from __future__ import annotations

import re
import textwrap
from typing import Callable

from ._util import join_lines, split_lines
from .base import ToolError

# Runs of characters that separate words in an identifier or phrase.
_SEPARATORS = re.compile(r"[^0-9A-Za-z]+")
# Word pieces inside a run of letters/digits, splitting camelCase and acronyms:
# "HTTPServer" -> ["HTTP", "Server"], "note2Self" -> ["note", "2", "Self"].
_CAMEL_PIECES = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _words(text: str) -> list[str]:
    """Break text into lower-cased word pieces, however it was cased."""
    pieces: list[str] = []
    for chunk in _SEPARATORS.split(text):
        if chunk:
            pieces.extend(part.lower() for part in _CAMEL_PIECES.findall(chunk))
    return pieces


def _per_line(transform: Callable[[str], str]) -> Callable[[str], str]:
    """Lift a single-line transform to run on each line independently."""

    def run(text: str) -> str:
        lines, trailing = split_lines(text)
        return join_lines([transform(line) for line in lines], trailing)

    return run


# -- prose case --------------------------------------------------------------

def to_upper(text: str) -> str:
    """UPPER CASE the selection."""
    return text.upper()


def to_lower(text: str) -> str:
    """lower case the selection."""
    return text.lower()


def to_title(text: str) -> str:
    """Capitalise The First Letter Of Every Word.

    Hand-rolled rather than ``str.title()``, which mangles apostrophes —
    ``"don't"`` becomes ``"Don'T"``. Here the apostrophe is part of the word.
    """
    return re.sub(
        r"[A-Za-z][A-Za-z'’]*",
        lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(),
        text,
    )


def to_sentence(text: str) -> str:
    """Lower-case the selection, then capitalise the first letter of each sentence."""
    lowered = text.lower()
    # Start of the text, or the first letter after . ! ? (with its trailing
    # space/newline), or the start of any new line.
    return re.sub(
        r"(^|[.!?]\s+|\n\s*)([a-z])",
        lambda m: m.group(1) + m.group(2).upper(),
        lowered,
    )


# -- identifier case (per line) ----------------------------------------------

def _camel(line: str) -> str:
    words = _words(line)
    if not words:
        return line
    return words[0] + "".join(w.capitalize() for w in words[1:])


def _pascal(line: str) -> str:
    words = _words(line)
    return "".join(w.capitalize() for w in words) if words else line


def _snake(line: str) -> str:
    words = _words(line)
    return "_".join(words) if words else line


def _kebab(line: str) -> str:
    words = _words(line)
    return "-".join(words) if words else line


def _constant(line: str) -> str:
    words = _words(line)
    return "_".join(w.upper() for w in words) if words else line


to_camel_case = _per_line(_camel)
to_pascal_case = _per_line(_pascal)
to_snake_case = _per_line(_snake)
to_kebab_case = _per_line(_kebab)
to_constant_case = _per_line(_constant)


# -- line operations ---------------------------------------------------------

_LEADING_NUMBER = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)")
_DIGIT_RUN = re.compile(r"(\d+)")


def _numeric_key(line: str) -> tuple[int, float, str]:
    """Sort key that orders by a line's leading number, non-numbers last."""
    match = _LEADING_NUMBER.match(line)
    if match is None:
        return (1, 0.0, line.lower())
    return (0, float(match.group(1)), line.lower())


def _natural_key(line: str) -> list:
    """Sort key where digit runs compare as numbers: file2 before file10."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in _DIGIT_RUN.split(line)
    ]


def _sorter(key=None, *, reverse: bool = False):
    def run(text: str) -> str:
        lines, trailing = split_lines(text)
        return join_lines(sorted(lines, key=key, reverse=reverse), trailing)

    return run


sort_lines_asc = _sorter()
sort_lines_desc = _sorter(reverse=True)
sort_lines_ci = _sorter(key=str.lower)
sort_lines_numeric = _sorter(key=_numeric_key)
sort_lines_natural = _sorter(key=_natural_key)


def remove_duplicate_lines(text: str) -> str:
    """Keep only the first occurrence of each line, preserving order.

    Order-preserving rather than sort-and-unique: the surrounding lines are
    usually meaningful, and a tool that reorders as a side effect of deduping is
    two tools badly welded together.
    """
    lines, trailing = split_lines(text)
    seen: set[str] = set()
    kept = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            kept.append(line)
    return join_lines(kept, trailing)


def remove_blank_lines(text: str) -> str:
    """Drop every line that is empty or whitespace-only."""
    lines, trailing = split_lines(text)
    return join_lines([line for line in lines if line.strip()], trailing)


def trim_trailing_whitespace(text: str) -> str:
    """Strip trailing spaces and tabs from every line."""
    lines, trailing = split_lines(text)
    return join_lines([line.rstrip() for line in lines], trailing)


def reverse_lines(text: str) -> str:
    """Reverse the order of the lines."""
    lines, trailing = split_lines(text)
    return join_lines(list(reversed(lines)), trailing)


def number_lines(text: str) -> str:
    """Prefix each line with its 1-based number, right-aligned."""
    lines, trailing = split_lines(text)
    width = len(str(len(lines))) if lines else 1
    return join_lines(
        [f"{i:>{width}}. {line}" for i, line in enumerate(lines, start=1)], trailing
    )


def join_lines_tool(text: str) -> str:
    """Join every line into one, separating with single spaces."""
    lines, _ = split_lines(text)
    return " ".join(line.strip() for line in lines if line.strip())


def wrap_lines(text: str, width: int = 80) -> str:
    """Re-wrap the selection to ``width`` columns, keeping paragraph breaks.

    Paragraphs (blank-line separated) are wrapped independently so a bulleted
    list or a run of short paragraphs isn't reflowed into one block.
    """
    paragraphs = re.split(r"\n\s*\n", text)
    wrapped = [
        textwrap.fill(
            paragraph.strip(),
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        )
        for paragraph in paragraphs
        if paragraph.strip()
    ]
    if not wrapped:
        raise ToolError("Nothing to wrap — the selection is empty")
    return "\n\n".join(wrapped)


def text_stats(text: str) -> str:
    """Report character, word, line, and paragraph counts for the selection."""
    lines = text.splitlines() or [""]
    words = sum(1 for token in text.split() if any(c.isalnum() for c in token))
    paragraphs = len([p for p in re.split(r"\n\s*\n", text) if p.strip()])
    chars_no_space = sum(1 for c in text if not c.isspace())
    return (
        f"{len(text):,} characters ({chars_no_space:,} without spaces) · "
        f"{words:,} words · {len(lines):,} lines · {paragraphs:,} paragraphs"
    )
