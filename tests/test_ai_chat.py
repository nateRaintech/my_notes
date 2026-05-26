"""Headless tests for the AI chat panel and MainWindow wiring (issue #81, M8 slice 3).

Covers:
- AiChatPanel.send() appends a user turn and invokes run_chat_fn.
- run_chat_fn receives the full messages list.
- _on_reply() appends an assistant turn and re-enables input.
- _on_error() shows an error message and re-enables input.
- Cancel re-enables input and discards a subsequent _on_reply/_on_error.
- clear() resets the conversation.
- save_as_note() calls save_note_fn with the Markdown text.
- save_as_note() via a real temp repository creates the note.
- start_with_context() seeds + sends.
- start_with_context() with blank prompt uses a default.
- The "dock_ai_chat" dock exists on MainWindow with the correct objectName.
- The dock is hidden by default.
- AI menu has a "Chat" action.
- No-key guard: _send_chat shows a status message, does not crash.
- No-repo guard: _send_chat shows a status message, does not crash.
- No-repo guard for _save_chat_note.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from sqlcipher3 import dbapi2 as sqlcipher  # noqa: E402

from core import schema  # noqa: E402
from core.repository import Repository  # noqa: E402
from ui.ai_chat import AiChatPanel  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    """In-memory repository, fully migrated."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def make_window(qapp) -> MainWindow:  # noqa: ARG001
    return MainWindow()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_panel_with_mock_chat(replies: list[str] | None = None) -> tuple[AiChatPanel, list]:
    """Return (panel, calls) where calls records each run_chat_fn invocation.

    The injected run_chat_fn appends messages to ``calls`` then immediately
    calls ``panel._on_reply`` with the next reply from ``replies`` (cycling
    round-robin).  If ``replies`` is None the mock does NOT call _on_reply,
    leaving the panel in "Thinking…" state.
    """
    calls: list[list[dict]] = []
    panel = AiChatPanel()

    if replies is None:
        def run_chat(messages: list[dict]) -> None:
            calls.append(list(messages))
    else:
        reply_iter = iter(replies)

        def run_chat(messages: list[dict]) -> None:
            calls.append(list(messages))
            try:
                reply = next(reply_iter)
            except StopIteration:
                reply = "…"
            panel._on_reply(reply)

    panel.run_chat_fn = run_chat
    return panel, calls


# ---------------------------------------------------------------------------
# send() — appends user turn and invokes run_chat_fn
# ---------------------------------------------------------------------------


def test_send_appends_user_turn(qapp):
    panel, _ = _make_panel_with_mock_chat(["OK"])
    panel.send("Hello")
    assert panel.conversation.messages[0] == {"role": "user", "content": "Hello"}


def test_send_invokes_run_chat_fn(qapp):
    panel, calls = _make_panel_with_mock_chat(["OK"])
    panel.send("ping")
    assert len(calls) == 1


def test_send_passes_messages_to_run_chat_fn(qapp):
    panel, calls = _make_panel_with_mock_chat(["OK"])
    panel.send("ping")
    assert calls[0] == [{"role": "user", "content": "ping"}]


def test_send_empty_string_is_noop(qapp):
    panel, calls = _make_panel_with_mock_chat(["OK"])
    panel.send("   ")
    assert calls == []
    assert panel.conversation.messages == []


# ---------------------------------------------------------------------------
# _on_reply() — assistant turn appended, input re-enabled
# ---------------------------------------------------------------------------


def test_on_reply_appends_assistant_turn(qapp):
    panel, _ = _make_panel_with_mock_chat(["Hello back!"])
    panel.send("Hello")
    assert panel.conversation.messages[-1] == {"role": "assistant", "content": "Hello back!"}


def test_on_reply_re_enables_input(qapp):
    panel, _ = _make_panel_with_mock_chat(["OK"])
    panel.send("test")
    assert panel.input_edit.isEnabled()


def test_on_reply_clears_thinking_label(qapp):
    panel, _ = _make_panel_with_mock_chat(["OK"])
    panel.send("test")
    assert panel.status_label.text() != "Thinking…"


def test_on_reply_multiple_turns(qapp):
    panel, _ = _make_panel_with_mock_chat(["A1", "A2"])
    panel.send("Q1")
    panel.send("Q2")
    msgs = panel.conversation.messages
    assert msgs[1]["content"] == "A1"
    assert msgs[3]["content"] == "A2"


# ---------------------------------------------------------------------------
# _on_error() — error shown, input re-enabled
# ---------------------------------------------------------------------------


def test_on_error_shows_message(qapp):
    panel = AiChatPanel()
    # Manually set thinking, then fire error.
    panel._set_thinking(True)
    panel._on_error("timeout")
    assert "timeout" in panel.status_label.text()


def test_on_error_re_enables_input(qapp):
    panel = AiChatPanel()
    panel._set_thinking(True)
    panel._on_error("network down")
    assert panel.input_edit.isEnabled()
    assert panel.send_button.isEnabled()


def test_on_error_does_not_append_assistant_turn(qapp):
    panel = AiChatPanel()
    panel.conversation.add_user("Q")
    panel._set_thinking(True)
    panel._on_error("fail")
    # No assistant turn should have been added.
    assert len(panel.conversation.messages) == 1


# ---------------------------------------------------------------------------
# Cancel — re-enables input, discards reply/error
# ---------------------------------------------------------------------------


def test_cancel_re_enables_input(qapp):
    # Use replies=None so mock does NOT auto-call _on_reply.
    panel, _ = _make_panel_with_mock_chat(replies=None)
    panel.send("test")
    assert not panel.input_edit.isEnabled()  # in-flight
    panel._on_cancel_clicked()
    assert panel.input_edit.isEnabled()


def test_cancel_discards_subsequent_reply(qapp):
    panel, _ = _make_panel_with_mock_chat(replies=None)
    panel.send("test")
    panel._on_cancel_clicked()
    panel._on_reply("should be ignored")
    # No assistant turn added.
    assert all(m["role"] == "user" for m in panel.conversation.messages)


def test_cancel_discards_subsequent_error(qapp):
    panel, _ = _make_panel_with_mock_chat(replies=None)
    panel.send("test")
    panel._on_cancel_clicked()
    panel._on_error("ignored error")
    # Status should not contain "Error:" from the discarded error.
    assert "ignored error" not in panel.status_label.text()


def test_cancel_clears_thinking_label(qapp):
    panel, _ = _make_panel_with_mock_chat(replies=None)
    panel.send("test")
    panel._on_cancel_clicked()
    assert panel.status_label.text() != "Thinking…"


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------


def test_clear_resets_conversation(qapp):
    panel, _ = _make_panel_with_mock_chat(["OK"])
    panel.send("hi")
    panel.clear()
    assert panel.conversation.messages == []


def test_clear_clears_status_label(qapp):
    panel = AiChatPanel()
    panel.status_label.setText("some status")
    panel.clear()
    assert panel.status_label.text() == ""


# ---------------------------------------------------------------------------
# save_as_note() with injected mock
# ---------------------------------------------------------------------------


def test_save_as_note_calls_save_note_fn(qapp):
    saved: list[str] = []
    panel = AiChatPanel(save_note_fn=saved.append)
    panel.conversation.add_user("Q")
    panel.conversation.add_assistant("A")
    panel.save_as_note()
    assert len(saved) == 1
    assert "**You:** Q" in saved[0]
    assert "**AI:** A" in saved[0]


def test_save_as_note_noop_when_fn_is_none(qapp):
    panel = AiChatPanel()  # save_note_fn=None
    panel.conversation.add_user("Q")
    panel.save_as_note()  # must not raise


def test_save_as_note_noop_when_conversation_empty(qapp):
    saved: list[str] = []
    panel = AiChatPanel(save_note_fn=saved.append)
    panel.save_as_note()  # empty conversation
    assert saved == []


# ---------------------------------------------------------------------------
# save_as_note() via real repository
# ---------------------------------------------------------------------------


def test_save_as_note_creates_note_in_repository(qapp, repo):
    """Inject a real repository's create_note as the save_note_fn."""

    def save_fn(markdown: str) -> None:
        repo.create_note(title="AI Chat", body=markdown)

    panel = AiChatPanel(save_note_fn=save_fn)
    panel.conversation.add_user("Hello")
    panel.conversation.add_assistant("Hi!")
    panel.save_as_note()

    notes = repo.list_notes()
    assert len(notes) == 1
    assert "**You:** Hello" in notes[0].body
    assert "**AI:** Hi!" in notes[0].body


# ---------------------------------------------------------------------------
# start_with_context()
# ---------------------------------------------------------------------------


def test_start_with_context_seeds_and_sends(qapp):
    panel, calls = _make_panel_with_mock_chat(["OK"])
    panel.start_with_context("Some text.", "Summarise this.")
    assert len(calls) == 1
    content = calls[0][0]["content"]
    assert "Some text." in content
    assert "Summarise this." in content


def test_start_with_context_blank_prompt_uses_default(qapp):
    panel, calls = _make_panel_with_mock_chat(["OK"])
    panel.start_with_context("Context here.", "")
    content = calls[0][0]["content"]
    assert "Please summarise this." in content


def test_start_with_context_clears_prior_conversation(qapp):
    panel, _ = _make_panel_with_mock_chat(["OK", "OK"])
    panel.send("existing message")
    panel.start_with_context("new context", "new prompt")
    # Only one user turn from start_with_context (the earlier was cleared).
    user_turns = [m for m in panel.conversation.messages if m["role"] == "user"]
    assert len(user_turns) == 1


# ---------------------------------------------------------------------------
# MainWindow dock: dock_ai_chat
# ---------------------------------------------------------------------------


def test_dock_ai_chat_exists(qapp):
    window = make_window(qapp)
    assert hasattr(window, "dock_ai_chat")


def test_dock_ai_chat_objectname(qapp):
    window = make_window(qapp)
    assert window.dock_ai_chat.objectName() == "dock_ai_chat"


def test_dock_ai_chat_hidden_by_default(qapp):
    window = make_window(qapp)
    assert not window.dock_ai_chat.isVisible()


def test_dock_ai_chat_contains_ai_chat_panel(qapp):
    window = make_window(qapp)
    assert window.dock_ai_chat.widget() is window.ai_chat_panel


def test_dock_ai_chat_features(qapp):
    from PySide6.QtWidgets import QDockWidget

    required = (
        QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        | QDockWidget.DockWidgetFeature.DockWidgetClosable
    )
    window = make_window(qapp)
    assert window.dock_ai_chat.features() & required == required


def test_toggle_ai_chat_action_is_docks_toggle_view_action(qapp):
    window = make_window(qapp)
    assert window.toggle_ai_chat_action is window.dock_ai_chat.toggleViewAction()


# ---------------------------------------------------------------------------
# AI menu has "Chat" action
# ---------------------------------------------------------------------------


def test_ai_menu_has_chat_action(qapp):
    window = make_window(qapp)
    assert window.chat_action is not None
    assert "Chat" in window.chat_action.text()


# ---------------------------------------------------------------------------
# No-key / no-repo guards on _send_chat
# ---------------------------------------------------------------------------


def test_send_chat_no_repo_shows_status(qapp):
    window = make_window(qapp)
    # No repository — vault is locked.
    window._send_chat([{"role": "user", "content": "hi"}])
    assert "locked" in window.ai_chat_panel.status_label.text().lower()


def test_send_chat_no_key_shows_status(qapp, repo):
    window = make_window(qapp)
    window.repository = repo  # bound but no key stored
    window._send_chat([{"role": "user", "content": "hi"}])
    assert "API key" in window.ai_chat_panel.status_label.text()


def test_send_chat_no_repo_does_not_crash(qapp):
    window = make_window(qapp)
    window._send_chat([{"role": "user", "content": "hi"}])  # must not raise


# ---------------------------------------------------------------------------
# No-repo guard on _save_chat_note
# ---------------------------------------------------------------------------


def test_save_chat_note_no_repo_shows_status(qapp):
    window = make_window(qapp)
    window._save_chat_note("**You:** Q\n\n**AI:** A")
    assert "locked" in window.statusBar().currentMessage().lower()


def test_save_chat_note_no_repo_does_not_crash(qapp):
    window = make_window(qapp)
    window._save_chat_note("some markdown")  # must not raise
