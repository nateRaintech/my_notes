"""Markdown tools.

The app's notes *are* Markdown, so these are the tools most likely to be used on
a note rather than on a pasted blob: straightening a table whose pipes have
drifted out of alignment, turning pasted spreadsheet rows into a table, and
escaping text that must not be interpreted as markup.
"""

from __future__ import annotations

import csv
import io
import re

from ._util import join_lines, require_text, split_lines
from .base import ToolError

# A cell separator is a pipe that isn't backslash-escaped, so a cell containing
# an escaped pipe stays a single cell.
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
# A separator row's cells: dashes with optional leading/trailing colons for alignment.
_ALIGN_CELL = re.compile(r"^:?-+:?$")

_LEFT, _CENTER, _RIGHT = "left", "center", "right"


def _row_cells(line: str) -> list[str]:
    """Split one table row into trimmed cells, dropping the outer pipes."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in _CELL_SPLIT.split(stripped)]


def _alignments(cells: list[str]) -> list[str] | None:
    """Read a separator row's alignment markers, or None if it isn't one."""
    if not cells or not all(_ALIGN_CELL.match(cell) for cell in cells):
        return None
    out = []
    for cell in cells:
        left, right = cell.startswith(":"), cell.endswith(":")
        out.append(_CENTER if left and right else _RIGHT if right else _LEFT)
    return out


def _render(rows: list[list[str]], aligns: list[str], columns: int) -> list[str]:
    """Lay out parsed rows as a padded Markdown table."""
    widths = [3] * columns  # a separator cell needs room for at least ":-:"
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def pad(cell: str, width: int, align: str) -> str:
        if align == _RIGHT:
            return cell.rjust(width)
        if align == _CENTER:
            return cell.center(width)
        return cell.ljust(width)

    def separator(width: int, align: str) -> str:
        if align == _CENTER:
            return ":" + "-" * (width - 2) + ":"
        if align == _RIGHT:
            return "-" * (width - 1) + ":"
        return "-" * width

    header, *body = rows
    out = [
        "| "
        + " | ".join(pad(c, widths[i], aligns[i]) for i, c in enumerate(header))
        + " |",
        "| "
        + " | ".join(separator(widths[i], aligns[i]) for i in range(columns))
        + " |",
    ]
    for row in body:
        out.append(
            "| "
            + " | ".join(pad(c, widths[i], aligns[i]) for i, c in enumerate(row))
            + " |"
        )
    return out


def align_table(text: str) -> str:
    """Re-pad a Markdown table so every pipe lines up.

    Column alignment declared in the separator row (``:---``, ``:---:``,
    ``---:``) is honoured and preserved. Ragged rows are padded with empty cells
    rather than rejected — a half-typed table is the normal reason to reach for
    this tool.
    """
    source = require_text(text, "Markdown table")
    lines, trailing = split_lines(source)
    if not any("|" in line for line in lines):
        raise ToolError(
            "That doesn't look like a Markdown table — cells need to be "
            "separated by '|'"
        )
    rows = [_row_cells(line) for line in lines if line.strip()]
    if not rows:
        raise ToolError("No table rows found in the selection")

    aligns = None
    if len(rows) > 1:
        aligns = _alignments(rows[1])
        if aligns is not None:
            rows.pop(1)  # rebuilt from the alignments below

    columns = max(len(row) for row in rows)
    if aligns is None:
        aligns = [_LEFT] * columns
    aligns = (aligns + [_LEFT] * columns)[:columns]
    rows = [row + [""] * (columns - len(row)) for row in rows]
    return join_lines(_render(rows, aligns, columns), trailing)


def table_from_delimited(text: str) -> str:
    """Build a Markdown table from pasted CSV or tab-separated rows.

    The delimiter is tab if any tab is present (what a spreadsheet paste looks
    like) and comma otherwise. Quoted fields containing the delimiter are handled
    by the ``csv`` module, and the first row becomes the header.
    """
    source = require_text(text, "delimited text")
    delimiter = "\t" if "\t" in source else ","
    rows = [row for row in csv.reader(io.StringIO(source), delimiter=delimiter) if row]
    if not rows:
        raise ToolError("No rows found in the selection")

    columns = max(len(row) for row in rows)
    rows = [
        [cell.strip() for cell in row] + [""] * (columns - len(row)) for row in rows
    ]
    if len(rows) == 1:
        rows.append([""] * columns)  # a header with no data still needs a body row
    return join_lines(_render(rows, [_LEFT] * columns, columns))


# CommonMark's escapable punctuation. Escaping the full set is deliberate: this
# tool exists for text that must survive verbatim, so over-escaping is the safe
# direction.
_MD_SPECIAL = frozenset("\\`*_{}[]()#+-.!|<>~")

_MD_ESCAPED = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~])")


def escape_markdown(text: str) -> str:
    """Backslash-escape every Markdown special character in the selection."""
    return "".join("\\" + ch if ch in _MD_SPECIAL else ch for ch in text)


def unescape_markdown(text: str) -> str:
    """Remove backslash escapes from Markdown special characters."""
    return _MD_ESCAPED.sub(r"\1", text)
