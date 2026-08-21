"""Turning a tool into an edit — the one place that knows about cursors.

``core/tools/`` holds pure ``str -> str`` functions. This module is the seam that
applies one to a live editor, and it is where the behaviour that makes the suite
feel native lives, rather than in the transformations themselves:

* **Scope** — the selection, or the whole note when nothing is selected. Running
  "Format JSON" on a note that is entirely JSON needs no selection at all.
* **Undo** — the replacement is a single ``QTextCursor`` edit wrapped in
  ``beginEditBlock``/``endEditBlock``, so **one Ctrl+Z** reverts the whole
  operation instead of unwinding it character by character.
* **Chaining** — the result is left selected, so a second tool applies to it.
* **Failure is inert** — on :class:`ToolError` the document is left
  byte-identical and the error is reported. Nothing is half-applied.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QGuiApplication, QTextCursor

from core.tools import Tool, ToolError

if TYPE_CHECKING:
    from PySide6.QtWidgets import QPlainTextEdit

#: How much of an ``inspect`` result to show in the status bar before eliding.
_STATUS_LIMIT = 160

#: ``QTextCursor.selectedText()`` substitutes this for every line break, so it
#: has to be translated back before a line-oriented tool sees the text.
_QT_PARAGRAPH_SEPARATOR = " "


class ToolResult:
    """What happened when a tool ran, for the caller to report and for tests.

    Returned rather than raised even on failure: invoking a tool on unsuitable
    text is ordinary use, not an exception, and every caller wants the same
    thing — a message to put in the status bar.
    """

    def __init__(self, *, ok: bool, message: str, changed: bool = False) -> None:
        self.ok = ok
        self.message = message
        self.changed = changed

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolResult(ok={self.ok}, changed={self.changed}, {self.message!r})"


def _copy_to_clipboard(text: str) -> bool:
    """Put ``text`` on the clipboard, returning whether that worked.

    Guarded because a headless run (``QT_QPA_PLATFORM=offscreen``) may have no
    clipboard; an inspect tool must still report its result in that case.
    """
    clipboard = QGuiApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text)
    return True


def run_tool(
    tool: Tool,
    editor: QPlainTextEdit | None,
    *,
    clipboard: Callable[[str], bool] = _copy_to_clipboard,
) -> ToolResult:
    """Apply ``tool`` to ``editor``, and describe what happened.

    ``editor`` may be ``None`` — with no note open there is nothing to act on,
    which is a message rather than a crash.
    """
    if editor is None:
        return ToolResult(ok=False, message="Open a note first")

    cursor = editor.textCursor()
    selecting = cursor.hasSelection()

    if tool.mode == "generate":
        # A generator ignores its input, so there is nothing to read and — with
        # no selection — nothing to replace: it inserts at the caret.
        source = ""
    elif selecting:
        source = cursor.selectedText().replace(_QT_PARAGRAPH_SEPARATOR, "\n")
    else:
        source = editor.toPlainText()

    try:
        result = tool.run(source)
    except ToolError as error:
        # The document is untouched — nothing has been written at this point.
        return ToolResult(ok=False, message=str(error))

    if tool.mode == "inspect":
        copied = clipboard(result)
        shown = result if len(result) <= _STATUS_LIMIT else result[:_STATUS_LIMIT] + "…"
        return ToolResult(ok=True, message=shown + (" (copied)" if copied else ""))

    if tool.mode == "generate":
        _write(editor, cursor, result, whole_document=False, reselect=False)
        return ToolResult(ok=True, message=f"Inserted {result}", changed=True)

    if result == source:
        return ToolResult(ok=True, message=f"{tool.name}: no change")

    _write(editor, cursor, result, whole_document=not selecting, reselect=True)
    scope = "selection" if selecting else "note"
    return ToolResult(
        ok=True, message=f"{tool.name} applied to the {scope}", changed=True
    )


def _write(
    editor: QPlainTextEdit,
    cursor: QTextCursor,
    text: str,
    *,
    whole_document: bool,
    reselect: bool,
) -> None:
    """Write ``text`` into the document as one undoable step.

    ``beginEditBlock``/``endEditBlock`` merge the implicit remove-then-insert
    pair into a single entry on the undo stack. Without it, Ctrl+Z after
    formatting a large block would walk back through the edit piecemeal — the
    difference between an operation the user can take back and one they have to
    fight.
    """
    if whole_document:
        cursor.select(QTextCursor.SelectionType.Document)

    cursor.beginEditBlock()
    try:
        start = cursor.selectionStart()
        cursor.insertText(text)  # replaces the selection, if there is one
        end = cursor.position()
    finally:
        cursor.endEditBlock()

    if reselect:
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
