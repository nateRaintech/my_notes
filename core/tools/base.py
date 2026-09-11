"""The tool contract: what a tool *is*, and how one fails.

A *tool* is a named, pure string transformation — "Format JSON", "snake_case",
"Base64 decode". :class:`Tool` is the value object describing one; the transform
itself is an ordinary function of ``str -> str`` living in a sibling module. Both
this module and every tool module are pure Python: per CLAUDE.md's layering rule
``core/`` never imports Qt, which is also why ~50 tools can be tested in
milliseconds without a ``QApplication``.

The UI layer (``ui/tool_runner.py``) is the only code that knows about cursors and
selections; it calls :meth:`Tool.run` and reacts to :class:`ToolError`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

#: How a tool's result reaches the document.
#:
#: ``"transform"`` replaces the input text with the result (the common case).
#: ``"generate"`` ignores its input and inserts at the cursor (UUID, timestamp).
#: ``"inspect"`` does not touch the document at all — the result is reported to
#: the user (hashes, validation), so an accidental invocation can never damage a
#: note.
ToolMode = Literal["transform", "generate", "inspect"]


class ToolError(Exception):
    """A tool could not process its input.

    Raised instead of returning a partial result: the runner's contract is that a
    failed tool leaves the document byte-identical. Tools raise *only* this — a
    bare ``ValueError`` escaping a tool is a bug, because the runner would let it
    reach the user as a crash rather than a message.

    ``line`` and ``column`` are 1-based and optional; when present, ``str()``
    locates the failure so the user can find it in a 200-line block of JSON.
    """

    def __init__(
        self, message: str, *, line: int | None = None, column: int | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        if self.line is None:
            return self.message
        if self.column is None:
            return f"{self.message} (line {self.line})"
        return f"{self.message} (line {self.line}, column {self.column})"


@dataclass(frozen=True)
class Tool:
    """One entry in the tool registry.

    Attributes:
        id: stable dotted identifier (``"json.format"``). Tests, settings, and
            shortcuts key off this, so it must not change once shipped even if
            ``name`` does.
        name: the menu label ("Format JSON").
        category: the submenu / palette grouping ("JSON").
        description: one line, shown as the palette subtitle and menu tooltip.
        func: the transformation. Takes the input text, returns the result, and
            raises :class:`ToolError` on bad input.
        keywords: extra terms the palette's fuzzy search should match, for the
            words a user is likely to type that aren't in the name ("beautify",
            "pretty", "indent" for Format JSON).
        mode: see :data:`ToolMode`.
    """

    id: str
    name: str
    category: str
    description: str
    func: Callable[[str], str]
    keywords: tuple[str, ...] = field(default=())
    mode: ToolMode = "transform"

    def run(self, text: str) -> str:
        """Apply the tool to ``text``.

        Any exception that is not already a :class:`ToolError` is wrapped in one,
        so a tool with an unanticipated edge case (a malformed surrogate, a
        recursion limit) still surfaces as a message rather than crashing the
        app. The original type is named in the message to keep such bugs
        diagnosable.
        """
        try:
            return self.func(text)
        except ToolError:
            raise
        except Exception as exc:  # noqa: BLE001 — deliberate catch-all, see docstring
            raise ToolError(f"{self.name} failed: {type(exc).__name__}: {exc}") from exc

    @property
    def searchable(self) -> str:
        """The text the palette's fuzzy matcher scores a query against."""
        return " ".join((self.name, self.category, *self.keywords))
