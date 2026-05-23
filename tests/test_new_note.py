"""Behavioral tests for creating a note from the UI (ROADMAP.md M6).

Drives the real path the running app uses — a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection — and proves the "New Note" seam creates an empty note in the current
notebook, surfaces and selects it (loading it into the editor with focus), clears
any active search so it is visible, and that subsequent edits auto-save to the
vault. Also checks the File-menu action / Ctrl+N wiring.

Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform,
matching the other Qt tests so the merge gate stays green wherever Qt is present.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    """A repository over a migrated, FK-enforcing in-memory connection."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def _current_note(window: MainWindow):
    """The Note carried by the currently-selected list row, or ``None``."""
    item = window.note_list.currentItem()
    return item.data(Qt.ItemDataRole.UserRole) if item is not None else None


def test_new_note_creates_and_returns_a_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()

    assert note is not None
    # The note was actually persisted to the vault.
    assert repo.get_note(note.id) is not None
    assert len(repo.list_notes()) == 1


def test_new_note_without_repository_returns_none(qapp):
    # Pre-unlock state: no repository bound. Must not crash, just no-op.
    window = MainWindow()
    assert window.new_note() is None
    assert window.note_list.count() == 0


def test_new_note_lands_in_the_selected_notebook(qapp, repo):
    notebook = repo.create_notebook("Work")

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_notebook(notebook.id)

    note = window.new_note()

    assert note is not None
    assert note.notebook_id == notebook.id


def test_new_note_lands_at_root_when_all_notes_selected(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.select_notebook(None)  # "All Notes"

    note = window.new_note()

    assert note is not None
    assert note.notebook_id is None


def test_new_note_is_selected_loaded_and_focused(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()

    # The new note is the selected list row...
    current = _current_note(window)
    assert current is not None
    assert current.id == note.id
    # ...its (empty) body is loaded into the editor...
    assert window.editor.markdown() == ""
    # ...and focus moved to the editable source so the user can type at once.
    assert window.focusWidget() is window.editor.source


def test_new_empty_note_lists_as_untitled(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.new_note()

    # An empty body has no usable first line, so derive_title falls back.
    labels = [window.note_list.item(i).text() for i in range(window.note_list.count())]
    assert labels == ["Untitled"]


def test_new_note_clears_an_active_search_so_it_is_visible(qapp, repo):
    repo.create_note(title="Existing", body="apple")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # A query that the new empty note would not match.
    window.search_input.setText("apple")
    assert window.note_list.count() == 1

    note = window.new_note()

    # The search was cleared and the new note is in the (now unfiltered) list.
    assert window.search_input.text() == ""
    listed_ids = {
        window.note_list.item(i).data(Qt.ItemDataRole.UserRole).id
        for i in range(window.note_list.count())
    }
    assert note.id in listed_ids


def test_new_note_action_exists_with_ctrl_n_and_triggers_new_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    assert window.new_note_action.shortcut() == QKeySequence("Ctrl+N")

    # Triggering the menu action runs new_note() -> a note is created.
    window.new_note_action.trigger()
    assert len(repo.list_notes()) == 1


def test_editing_a_new_note_auto_saves_to_the_vault(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()

    # Type into the editor source, then flush the debounced auto-save.
    window.editor.source.setPlainText("# Groceries\n\nmilk and eggs")
    window.flush_pending()

    reloaded = repo.get_note(note.id)
    assert reloaded is not None
    assert reloaded.body == "# Groceries\n\nmilk and eggs"
    # Title is re-derived from the body by the auto-save layer.
    assert reloaded.title == "Groceries"
