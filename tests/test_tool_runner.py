"""Behavioural tests for `ui/tool_runner.py` — the selection-to-edit seam.

The transformations are tested in ``test_tools_core.py``; what is checked here is
the *behaviour around* them, which is what makes the suite feel like the Notepad++
plugin rather than a menu of functions: scope, single-step undo, re-selection,
and the guarantee that a failed tool leaves the document byte-identical.
"""

import os

import pytest

from core.tools import Tool, ToolError, get_tool

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from ui.tool_runner import run_tool  # noqa: E402

MESSY = '{"b":2,"a":1}'
FORMATTED = '{\n  "b": 2,\n  "a": 1\n}'


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp):
    widget = QPlainTextEdit()
    yield widget
    widget.deleteLater()


def select(editor, start, end):
    cursor = editor.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)


class Recorder:
    """A stand-in clipboard that records instead of touching the system one."""

    def __init__(self, works=True):
        self.text = None
        self._works = works

    def __call__(self, text):
        self.text = text
        return self._works


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def test_with_no_selection_the_tool_applies_to_the_whole_note(editor):
    editor.setPlainText(MESSY)
    result = run_tool(get_tool("json.format"), editor)
    assert result.ok
    assert editor.toPlainText() == FORMATTED


def test_with_a_selection_only_the_selection_changes(editor):
    editor.setPlainText(f"before\n{MESSY}\nafter")
    start = len("before\n")
    select(editor, start, start + len(MESSY))

    assert run_tool(get_tool("json.format"), editor).ok
    assert editor.toPlainText() == f"before\n{FORMATTED}\nafter"


def test_a_multi_line_selection_is_seen_as_multiple_lines(editor):
    """QTextCursor.selectedText() uses U+2029 for newlines; the runner must
    translate it, or every line tool would see one long line."""
    editor.setPlainText("c\nb\na")
    select(editor, 0, 5)

    assert run_tool(get_tool("lines.sortasc"), editor).ok
    assert editor.toPlainText() == "a\nb\nc"


def test_the_scope_is_named_in_the_message(editor):
    editor.setPlainText(MESSY)
    assert "note" in run_tool(get_tool("json.format"), editor).message

    editor.setPlainText(f"x{MESSY}")
    select(editor, 1, 1 + len(MESSY))
    assert "selection" in run_tool(get_tool("json.format"), editor).message


def test_no_editor_is_a_message_not_a_crash():
    result = run_tool(get_tool("json.format"), None)
    assert not result.ok
    assert "Open a note first" in result.message


# ---------------------------------------------------------------------------
# Undo and re-selection
# ---------------------------------------------------------------------------

def test_one_undo_reverts_the_whole_operation(editor):
    """The entire replacement is one edit block, so Ctrl+Z takes it all back."""
    editor.setPlainText(MESSY)
    run_tool(get_tool("json.format"), editor)
    assert editor.toPlainText() == FORMATTED

    editor.undo()

    assert editor.toPlainText() == MESSY


def test_one_undo_suffices_for_a_selection_too(editor):
    editor.setPlainText(f"before\n{MESSY}\nafter")
    start = len("before\n")
    select(editor, start, start + len(MESSY))
    run_tool(get_tool("json.format"), editor)

    editor.undo()

    assert editor.toPlainText() == f"before\n{MESSY}\nafter"


def test_the_result_is_left_selected_so_tools_can_be_chained(editor):
    editor.setPlainText(f"before\n{MESSY}\nafter")
    start = len("before\n")
    select(editor, start, start + len(MESSY))

    run_tool(get_tool("json.format"), editor)

    assert editor.textCursor().selectedText().replace(" ", "\n") == FORMATTED


def test_chaining_a_second_tool_acts_on_the_first_result(editor):
    editor.setPlainText(f"before\n{MESSY}\nafter")
    start = len("before\n")
    select(editor, start, start + len(MESSY))

    run_tool(get_tool("json.format"), editor)
    run_tool(get_tool("json.minify"), editor)

    assert editor.toPlainText() == f"before\n{MESSY}\nafter"


# ---------------------------------------------------------------------------
# Failure is inert
# ---------------------------------------------------------------------------

def test_a_failing_tool_leaves_the_document_byte_identical(editor):
    original = "this is definitely not json"
    editor.setPlainText(original)

    result = run_tool(get_tool("json.format"), editor)

    assert not result.ok
    assert not result.changed
    assert editor.toPlainText() == original


def test_a_failure_message_carries_the_line_and_column(editor):
    editor.setPlainText('{\n  "a": 1\n  "b": 2\n}')
    result = run_tool(get_tool("json.format"), editor)
    assert not result.ok
    assert "line 3" in result.message


def test_a_failing_tool_does_not_touch_the_undo_stack(editor):
    """Nothing was written, so there is nothing to undo — undo must not eat the text."""
    editor.setPlainText("not json")
    run_tool(get_tool("json.format"), editor)

    editor.undo()

    assert editor.toPlainText() == "not json"


def test_an_unexpected_exception_is_reported_rather_than_raised(editor):
    exploding = Tool(
        "test.boom", "Boom", "JSON", "raises",
        lambda _text: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    editor.setPlainText("anything")

    result = run_tool(exploding, editor)

    assert not result.ok
    assert "kaboom" in result.message
    assert editor.toPlainText() == "anything"


def test_a_tool_that_changes_nothing_says_so_without_editing(editor):
    editor.setPlainText(FORMATTED)
    result = run_tool(get_tool("json.format"), editor)
    assert result.ok
    assert not result.changed
    assert "no change" in result.message


# ---------------------------------------------------------------------------
# inspect mode
# ---------------------------------------------------------------------------

def test_an_inspect_tool_never_touches_the_document(editor):
    editor.setPlainText("hello")
    clipboard = Recorder()

    result = run_tool(get_tool("hash.sha256"), editor, clipboard=clipboard)

    assert result.ok
    assert not result.changed
    assert editor.toPlainText() == "hello"


def test_an_inspect_result_goes_to_the_clipboard_and_the_message(editor):
    editor.setPlainText("hello")
    clipboard = Recorder()

    result = run_tool(get_tool("hash.md5"), editor, clipboard=clipboard)

    assert "5d41402abc4b2a76b9719d911017c592" in clipboard.text
    assert "5d41402abc4b2a76b9719d911017c592" in result.message
    assert "(copied)" in result.message


def test_an_inspect_result_still_reports_when_there_is_no_clipboard(editor):
    editor.setPlainText("hello")
    result = run_tool(get_tool("hash.md5"), editor, clipboard=Recorder(works=False))
    assert result.ok
    assert "(copied)" not in result.message
    assert "5d41402abc4b2a76b9719d911017c592" in result.message


def test_a_long_inspect_result_is_elided_in_the_message(editor):
    editor.setPlainText("x" * 5000)
    result = run_tool(get_tool("lines.stats"), editor, clipboard=Recorder())
    assert len(result.message) < 400


def test_a_failing_inspect_tool_reports_the_error(editor):
    editor.setPlainText("not json")
    result = run_tool(get_tool("json.validate"), editor, clipboard=Recorder())
    assert not result.ok
    assert "Invalid JSON" in result.message


# ---------------------------------------------------------------------------
# generate mode
# ---------------------------------------------------------------------------

def test_a_generator_inserts_at_the_caret_without_eating_the_note(editor):
    """The bug this guards: with no selection, replacing 'the whole document'
    would wipe the note and leave only the UUID."""
    editor.setPlainText("before after")
    cursor = editor.textCursor()
    cursor.setPosition(len("before "))
    editor.setTextCursor(cursor)

    result = run_tool(get_tool("insert.uuid"), editor)

    assert result.ok and result.changed
    text = editor.toPlainText()
    assert text.startswith("before ")
    assert text.endswith("after")
    assert len(text) > len("before after")


def test_a_generator_replaces_the_selection_when_there_is_one(editor):
    editor.setPlainText("keep REPLACEME keep")
    select(editor, 5, 14)

    run_tool(get_tool("insert.today"), editor)

    text = editor.toPlainText()
    assert "REPLACEME" not in text
    assert text.startswith("keep ") and text.endswith(" keep")


def test_a_generator_ignores_an_unparseable_selection(editor):
    """A generator must not fail because the selected text isn't JSON."""
    editor.setPlainText("<<< not anything >>>")
    select(editor, 0, 20)
    assert run_tool(get_tool("insert.uuid"), editor).ok


def test_a_generated_value_is_undoable_in_one_step(editor):
    editor.setPlainText("before after")
    cursor = editor.textCursor()
    cursor.setPosition(len("before "))
    editor.setTextCursor(cursor)
    run_tool(get_tool("insert.uuid"), editor)

    editor.undo()

    assert editor.toPlainText() == "before after"


# ---------------------------------------------------------------------------
# Every registered tool, against a live editor
# ---------------------------------------------------------------------------

def test_no_tool_ever_raises_out_of_the_runner(editor):
    """Whatever a tool is handed, the runner returns a result — never an exception."""
    from core.tools import ALL_TOOLS

    for tool in ALL_TOOLS:
        editor.setPlainText("some arbitrary text that suits almost no tool")
        try:
            result = run_tool(tool, editor, clipboard=Recorder())
        except ToolError:  # pragma: no cover - the failure this test guards
            pytest.fail(f"{tool.id} raised out of the runner")
        assert isinstance(result.message, str) and result.message, tool.id
