"""JSON tools — the suite's motivating case.

The reference behaviour is Notepad++'s JSTool plugin: select a block of valid
JSON, invoke the tool, and it comes back indented. Every function here parses
first and serialises second, so malformed input raises before anything is
returned and the document is never left half-transformed.

``json.JSONDecodeError`` carries the failure's line and column, which is
forwarded into :class:`~core.tools.base.ToolError` so the status bar can point at
the offending character instead of just saying "invalid".
"""

from __future__ import annotations

import json
from typing import Any

from ._util import require_text
from .base import ToolError


def _parse(text: str) -> Any:
    """Parse ``text`` as JSON, converting a decode error into a located ToolError."""
    source = require_text(text, "JSON")
    try:
        return json.loads(source)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"Invalid JSON: {exc.msg}", line=exc.lineno, column=exc.colno
        ) from exc


def _dump(value: Any, *, indent: int | None, sort_keys: bool = False) -> str:
    r"""Serialise with the suite's house style.

    ``ensure_ascii=False`` keeps non-ASCII readable — an accented name stays
    ``"café"`` rather than becoming ``"caf\u00e9"``, which matters in notes far
    more than in wire formats. ``indent=None`` selects the compact form, where
    the separators are tightened to drop JSON's default spaces.
    """
    separators = (",", ": ") if indent is not None else (",", ":")
    return json.dumps(
        value,
        indent=indent,
        ensure_ascii=False,
        sort_keys=sort_keys,
        separators=separators,
    )


def format_json(text: str) -> str:
    """Pretty-print JSON with two-space indentation."""
    return _dump(_parse(text), indent=2)


def format_json_4(text: str) -> str:
    """Pretty-print JSON with four-space indentation."""
    return _dump(_parse(text), indent=4)


def format_json_tab(text: str) -> str:
    """Pretty-print JSON with tab indentation."""
    return json.dumps(_parse(text), indent="\t", ensure_ascii=False)


def minify_json(text: str) -> str:
    """Collapse JSON to a single line with no insignificant whitespace."""
    return _dump(_parse(text), indent=None)


def sort_json_keys(text: str) -> str:
    """Pretty-print JSON with every object's keys sorted alphabetically."""
    return _dump(_parse(text), indent=2, sort_keys=True)


def _describe(value: Any) -> tuple[int, int]:
    """Return ``(node_count, max_depth)`` for a parsed JSON value."""
    if isinstance(value, dict):
        if not value:
            return 1, 1
        children = [_describe(v) for v in value.values()]
        return 1 + sum(c[0] for c in children), 1 + max(c[1] for c in children)
    if isinstance(value, list):
        if not value:
            return 1, 1
        children = [_describe(v) for v in value]
        return 1 + sum(c[0] for c in children), 1 + max(c[1] for c in children)
    return 1, 1


def validate_json(text: str) -> str:
    """Report whether the text is valid JSON, and describe it if so.

    An ``inspect`` tool: it never modifies the document. On failure the shared
    :func:`_parse` raises with the line and column, which is the whole point —
    "where is my missing comma" is the question this answers.
    """
    value = _parse(text)
    nodes, depth = _describe(value)
    kind = type(value).__name__
    if isinstance(value, dict):
        kind = f"object with {len(value)} key{'s' if len(value) != 1 else ''}"
    elif isinstance(value, list):
        kind = f"array of {len(value)} item{'s' if len(value) != 1 else ''}"
    return f"Valid JSON — {kind}, {nodes} nodes, {depth} levels deep"


def escape_json_string(text: str) -> str:
    """Turn the selection into a quoted JSON string literal.

    Note the deliberate absence of :func:`require_text`: escaping ``""`` to
    ``'""'`` is a meaningful, correct answer, unlike formatting an empty
    selection.
    """
    return json.dumps(text, ensure_ascii=False)


def unescape_json_string(text: str) -> str:
    """Turn a quoted JSON string literal back into its raw text."""
    value = _parse(text)
    if not isinstance(value, str):
        raise ToolError(
            f"Not a JSON string literal — got a {type(value).__name__}. "
            r'Select something like "hello\nworld", quotes included.'
        )
    return value
