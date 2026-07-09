"""Headless tests for issue #83 — Analyze text / note with AI (M8 slice 4).

Covers:
- analyze_selection() calls start_with_context with selected text + prompt.
- Paragraph separator (U+2029) and line separator (U+2028) converted to newlines.
- Blank prompt → start_with_context receives "" (panel defaults to "summarise").
- No selection → no start_with_context call; status message shown.
- analyze_note() calls start_with_context with note body + prompt.
- analyze_note() with explicit note arg (right-click path).
- No note selected → no call; status message shown.
- Cancelling prompt dialog (_ask_analysis_prompt returns None) → no call.
- Locked vault guard (analyze_selection and analyze_note, no crash).
- No API key guard (analyze_selection and analyze_note, no crash).
- AI menu has "Analyze text with AI" and "Analyze note with AI" actions.
- analyze_text_action disabled by default (no selection).
- Note-list right-click menu contains "Analyze note with AI".
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from sqlcipher3 import dbapi2 as sqlcipher  # noqa: E402

from core import schema  # noqa: E402
from core.repository import Repository  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    """In-memory repository, fully migrated, no API key."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


@pytest.fixture
def repo_with_key(repo):
    """Same in-memory repo but with a fake API key set."""
    repo.set_api_key("sk-test-fake-key")
    return repo


def _make_window(qapp) -> MainWindow:  # noqa: ARG001
    return MainWindow()


def _make_wired_window(qapp, repo) -> MainWindow:  # noqa: ARG001
    """Return a window with repository bound and chat seams wired."""
    w = MainWindow()
    w.bind_autosave(repo)
    w._wire_ai_chat_seams()
    return w


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Sentinel: track calls to start_with_context.
def _mock_start_with_context(calls: list) -> callable:
    def _mock(context_text: str, user_prompt: str = "") -> None:
        calls.append({"context": context_text, "prompt": user_prompt})
    return _mock


def _patch_prompt(window: MainWindow, return_value: str | None) -> None:
    """Replace _ask_analysis_prompt with a lambda that returns return_value."""
    window._ask_analysis_prompt = lambda: return_value


def _set_selection(window: MainWindow, text: str) -> None:
    """Open a note holding ``text`` in a tab and select all of it.

    The editor's editable source only exists once a note is open in a tab, so a
    selection is set by opening a note over the window's bound repository.
    """
    note = window.repository.create_note(title="", body=text)
    window.load_note(note)
    window.editor.source.selectAll()


# ---------------------------------------------------------------------------
# AI menu actions exist
# ---------------------------------------------------------------------------


def test_ai_menu_has_analyze_text_action(qapp):
    w = _make_window(qapp)
    assert hasattr(w, "analyze_text_action")
    assert "Analyze text" in w.analyze_text_action.text()


def test_ai_menu_has_analyze_note_action(qapp):
    w = _make_window(qapp)
    assert hasattr(w, "analyze_note_action")
    assert "Analyze note" in w.analyze_note_action.text()


def test_analyze_text_action_enabled_by_default(qapp):
    """The action is always enabled; analyze_selection guards on an actual selection."""
    w = _make_window(qapp)
    assert w.analyze_text_action.isEnabled()


# ---------------------------------------------------------------------------
# analyze_selection() — happy path
# ---------------------------------------------------------------------------


def test_analyze_selection_calls_start_with_context(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    _set_selection(w, "Hello world")
    _patch_prompt(w, "Summarise this please.")
    w.analyze_selection()
    assert len(calls) == 1
    assert "Hello world" in calls[0]["context"]
    assert calls[0]["prompt"] == "Summarise this please."


def test_analyze_selection_paragraph_sep_converted(qapp, repo_with_key):
    """Qt's U+2029 paragraph separator in selectedText() becomes \\n."""
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)

    # Simulate what Qt yields for multi-paragraph selectedText.
    para_sep = " "
    line_sep = " "
    fake_qt_text = f"Line one{para_sep}Line two{line_sep}Line three"

    # Monkeypatch selectedText to return the Qt-encoded string directly.
    from unittest.mock import MagicMock
    _set_selection(w, "seed")  # ensure a tab (and editable source) exists
    cursor = MagicMock()
    cursor.selectedText.return_value = fake_qt_text
    w.editor.source.textCursor = lambda: cursor

    _patch_prompt(w, "")
    w.analyze_selection()

    assert len(calls) == 1
    context = calls[0]["context"]
    assert " " not in context
    assert " " not in context
    assert "Line one\nLine two\nLine three" == context


def test_analyze_selection_blank_prompt_forwarded_as_blank(qapp, repo_with_key):
    """Blank prompt is passed straight through; AiChatPanel applies the default."""
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    _set_selection(w, "Some text")
    _patch_prompt(w, "")
    w.analyze_selection()
    assert len(calls) == 1
    assert calls[0]["prompt"] == ""


# ---------------------------------------------------------------------------
# analyze_selection() — no selection guard
# ---------------------------------------------------------------------------


def test_analyze_selection_no_selection_no_call(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    # Empty note open — nothing selected.
    _set_selection(w, "")
    _patch_prompt(w, "anything")
    w.analyze_selection()
    assert calls == []


def test_analyze_selection_no_selection_shows_status(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    _set_selection(w, "")
    _patch_prompt(w, "anything")
    w.analyze_selection()
    msg = w.statusBar().currentMessage().lower()
    assert "select" in msg


# ---------------------------------------------------------------------------
# analyze_selection() — cancel prompt
# ---------------------------------------------------------------------------


def test_analyze_selection_cancel_prompt_no_call(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    _set_selection(w, "Some text here")
    _patch_prompt(w, None)  # user cancelled
    w.analyze_selection()
    assert calls == []


# ---------------------------------------------------------------------------
# analyze_selection() — vault / key guards
# ---------------------------------------------------------------------------


def test_analyze_selection_locked_vault_no_crash(qapp, repo):
    w = _make_wired_window(qapp, repo)
    _set_selection(w, "Some text")
    w.repository = None  # simulate a locked vault while a tab is still open
    _patch_prompt(w, "test")
    w.analyze_selection()  # must not raise


def test_analyze_selection_locked_vault_shows_status(qapp, repo):
    w = _make_wired_window(qapp, repo)
    _set_selection(w, "Some text")
    w.repository = None  # simulate a locked vault while a tab is still open
    _patch_prompt(w, "test")
    w.analyze_selection()
    msg = w.statusBar().currentMessage().lower()
    assert "locked" in msg


def test_analyze_selection_no_key_no_crash(qapp, repo):
    w = _make_wired_window(qapp, repo)  # repo has no API key
    _set_selection(w, "Some text")
    _patch_prompt(w, "test")
    w.analyze_selection()  # must not raise


def test_analyze_selection_no_key_shows_status(qapp, repo):
    w = _make_wired_window(qapp, repo)
    _set_selection(w, "Some text")
    _patch_prompt(w, "test")
    w.analyze_selection()
    msg = w.statusBar().currentMessage()
    assert "API key" in msg


# ---------------------------------------------------------------------------
# analyze_note() — happy path
# ---------------------------------------------------------------------------


def test_analyze_note_with_explicit_note(qapp, repo_with_key):
    """Passing a Note directly (right-click path) works without a list selection."""
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    note = repo_with_key.create_note(title="Test", body="Note body text")
    _patch_prompt(w, "What does this mean?")
    w.analyze_note(note)
    assert len(calls) == 1
    assert calls[0]["context"] == "Note body text"
    assert calls[0]["prompt"] == "What does this mean?"


def test_analyze_note_uses_selected_note_when_no_arg(qapp, repo_with_key):
    """Without an explicit note, reads from the currently selected list item."""
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    repo_with_key.create_note(title="My Note", body="Body content here")
    w.refresh_notes()
    w.note_list.setCurrentRow(0)
    _patch_prompt(w, "")
    w.analyze_note()
    assert len(calls) == 1
    assert calls[0]["context"] == "Body content here"


def test_analyze_note_blank_prompt_forwarded(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    note = repo_with_key.create_note(title="X", body="content")
    _patch_prompt(w, "")
    w.analyze_note(note)
    assert calls[0]["prompt"] == ""


# ---------------------------------------------------------------------------
# analyze_note() — no note guard
# ---------------------------------------------------------------------------


def test_analyze_note_no_selection_no_call(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    # No notes in the list, no current item.
    _patch_prompt(w, "test")
    w.analyze_note()
    assert calls == []


def test_analyze_note_no_selection_shows_status(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    _patch_prompt(w, "test")
    w.analyze_note()
    msg = w.statusBar().currentMessage().lower()
    assert "select" in msg


# ---------------------------------------------------------------------------
# analyze_note() — cancel prompt
# ---------------------------------------------------------------------------


def test_analyze_note_cancel_prompt_no_call(qapp, repo_with_key):
    w = _make_wired_window(qapp, repo_with_key)
    calls = []
    w.ai_chat_panel.start_with_context = _mock_start_with_context(calls)
    note = repo_with_key.create_note(title="N", body="body")
    _patch_prompt(w, None)
    w.analyze_note(note)
    assert calls == []


# ---------------------------------------------------------------------------
# analyze_note() — vault / key guards
# ---------------------------------------------------------------------------


def test_analyze_note_locked_vault_no_crash(qapp, repo_with_key):
    w = _make_window(qapp)  # no repository bound
    note = repo_with_key.create_note(title="T", body="b")
    _patch_prompt(w, "test")
    w.analyze_note(note)  # must not raise


def test_analyze_note_locked_vault_shows_status(qapp, repo_with_key):
    w = _make_window(qapp)
    note = repo_with_key.create_note(title="T", body="b")
    _patch_prompt(w, "test")
    w.analyze_note(note)
    msg = w.statusBar().currentMessage().lower()
    assert "locked" in msg


def test_analyze_note_no_key_no_crash(qapp, repo):
    w = _make_wired_window(qapp, repo)  # no API key
    note = repo.create_note(title="T", body="b")
    _patch_prompt(w, "test")
    w.analyze_note(note)  # must not raise


def test_analyze_note_no_key_shows_status(qapp, repo):
    w = _make_wired_window(qapp, repo)
    note = repo.create_note(title="T", body="b")
    _patch_prompt(w, "test")
    w.analyze_note(note)
    msg = w.statusBar().currentMessage()
    assert "API key" in msg


# ---------------------------------------------------------------------------
# Note-list right-click menu contains "Analyze note with AI"
# ---------------------------------------------------------------------------


def test_note_list_menu_has_analyze_note_action(qapp, repo_with_key):
    """_show_note_menu source code wires 'Analyze note with AI' into the context menu.

    We verify this structurally by inspecting the source of _show_note_menu, which
    avoids both itemAt offscreen issues and QMenu.exec modal blocking in headless tests.
    The functional path (analyze_note is called correctly) is already covered by the
    analyze_note tests above.
    """
    import inspect
    from ui.main_window import MainWindow
    src = inspect.getsource(MainWindow._show_note_menu)
    assert "Analyze note with AI" in src
