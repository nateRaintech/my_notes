"""Headless tests for the AI API-key dialog and related MainWindow seams.

Drives :class:`ui.api_key_dialog.APIKeyDialog` and
:class:`ui.main_window.MainWindow` without the modal event loop, using the
same offscreen + importorskip pattern as the rest of the UI test suite.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from sqlcipher3 import dbapi2 as sqlcipher  # noqa: E402

from core import schema  # noqa: E402
from core.repository import Repository  # noqa: E402
from ui.api_key_dialog import APIKeyDialog  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


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


# ---------------------------------------------------------------------------
# APIKeyDialog: state label
# ---------------------------------------------------------------------------


def test_state_label_no_key_when_none_stored(qapp, repo):
    dialog = APIKeyDialog(repo)
    assert "No key stored" in dialog.current_state_text()
    assert "No key stored" in dialog.state_label.text()


def test_state_label_has_key_after_storing(qapp, repo):
    repo.set_api_key("sk-abc")
    dialog = APIKeyDialog(repo)
    assert "A key is stored" in dialog.current_state_text()
    assert "A key is stored" in dialog.state_label.text()
    repo.clear_api_key()  # cleanup


# ---------------------------------------------------------------------------
# APIKeyDialog: save_key seam
# ---------------------------------------------------------------------------


def test_save_key_stores_and_updates_label(qapp, repo):
    dialog = APIKeyDialog(repo)
    result = dialog.save_key("sk-my-key-123")
    assert result is True
    assert repo.has_api_key()
    assert "A key is stored" in dialog.state_label.text()
    repo.clear_api_key()


def test_save_key_empty_string_returns_false_and_shows_error(qapp, repo):
    dialog = APIKeyDialog(repo)
    result = dialog.save_key("   ")
    assert result is False
    assert dialog.error_label.text() != ""
    assert not repo.has_api_key()


def test_save_key_replaces_existing_key(qapp, repo):
    repo.set_api_key("old-key")
    dialog = APIKeyDialog(repo)
    dialog.save_key("new-key")
    assert repo.get_api_key() == "new-key"
    repo.clear_api_key()


def test_save_key_clears_input_field_on_success(qapp, repo):
    dialog = APIKeyDialog(repo)
    dialog.key_edit.setText("sk-clearme")
    dialog.save_key(dialog.key_edit.text())
    assert dialog.key_edit.text() == ""
    repo.clear_api_key()


# ---------------------------------------------------------------------------
# APIKeyDialog: clear_key seam
# ---------------------------------------------------------------------------


def test_clear_key_removes_stored_key(qapp, repo):
    repo.set_api_key("sk-to-clear")
    dialog = APIKeyDialog(repo)
    dialog.clear_key()
    assert not repo.has_api_key()
    assert "No key stored" in dialog.state_label.text()


def test_clear_key_is_no_op_when_no_key_stored(qapp, repo):
    dialog = APIKeyDialog(repo)
    dialog.clear_key()  # must not raise
    assert not repo.has_api_key()


# ---------------------------------------------------------------------------
# APIKeyDialog: never reveals the stored key
# ---------------------------------------------------------------------------


def test_key_edit_is_password_masked(qapp, repo):
    from PySide6.QtWidgets import QLineEdit

    dialog = APIKeyDialog(repo)
    assert dialog.key_edit.echoMode() == QLineEdit.EchoMode.Password


def test_dialog_does_not_display_stored_key_value(qapp, repo):
    """After storing a key, the dialog must NOT show its value anywhere."""
    secret = "super-secret-api-key-xyz789"
    repo.set_api_key(secret)
    dialog = APIKeyDialog(repo)

    # The state label must not contain the secret.
    assert secret not in dialog.state_label.text()
    # The input field must be empty (no pre-fill with stored value).
    assert dialog.key_edit.text() == ""

    repo.clear_api_key()


# ---------------------------------------------------------------------------
# MainWindow seams: _make_api_key_dialog
# ---------------------------------------------------------------------------


def test_make_api_key_dialog_returns_none_when_no_repository(qapp):
    window = MainWindow()
    assert window._make_api_key_dialog() is None


def test_make_api_key_dialog_returns_dialog_when_repository_bound(qapp, repo):
    window = MainWindow()
    window.repository = repo
    dialog = window._make_api_key_dialog()
    assert isinstance(dialog, APIKeyDialog)


# ---------------------------------------------------------------------------
# MainWindow: AI menu actions exist
# ---------------------------------------------------------------------------


def test_ai_menu_exists_on_main_window(qapp):
    window = MainWindow()
    menu_titles = [a.text() for a in window.menuBar().actions()]
    assert any("AI" in t for t in menu_titles)


def test_set_api_key_action_exists(qapp):
    window = MainWindow()
    assert window.set_api_key_action is not None
    assert "API key" in window.set_api_key_action.text()


def test_test_connection_action_exists(qapp):
    window = MainWindow()
    assert window.test_connection_action is not None
    assert "connection" in window.test_connection_action.text().lower()


# ---------------------------------------------------------------------------
# AiWorker: testability seam (no real network, no real QThread)
# ---------------------------------------------------------------------------


def test_ai_worker_run_with_emits_finished_on_success(qapp):
    from ui.ai_worker import AiWorker

    worker = AiWorker("key", [{"role": "user", "content": "hi"}])
    results = []
    worker.finished.connect(results.append)

    worker.run_with(lambda key, msgs, timeout=120.0: "OK")

    assert results == ["OK"]


def test_ai_worker_run_with_emits_error_on_exception(qapp):
    from ui.ai_worker import AiWorker

    worker = AiWorker("key", [{"role": "user", "content": "hi"}])
    errors = []
    worker.error.connect(errors.append)

    def failing_chat(key, msgs, timeout=120.0):
        raise RuntimeError("network is down")

    worker.run_with(failing_chat)

    assert len(errors) == 1
    assert "network is down" in errors[0]


def test_make_ai_worker_returns_worker_and_thread(qapp, repo):
    window = MainWindow()
    window.repository = repo
    worker, thread = window._make_ai_worker("test-key", [{"role": "user", "content": "hi"}])
    from ui.ai_worker import AiWorker
    from PySide6.QtCore import QThread

    assert isinstance(worker, AiWorker)
    assert isinstance(thread, QThread)
    # Clean up.
    thread.deleteLater()
