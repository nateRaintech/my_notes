"""Behavioral tests for deleting a note from the UI (ROADMAP.md M6).

Drives the real path the running app uses — a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection — and proves the "Delete" seam removes the note from the vault and the
list, leaves the editor untouched when a *different* note is open, and clears the
editor while detaching auto-save when the *open* note is deleted (so a later flush
never writes to the now-deleted row). Also checks the confirmation-prompt wiring.

Mirrors ``tests/test_new_note.py``. The public :meth:`MainWindow.delete_note` seam
is driven directly (the right-click menu's modal ``QMessageBox`` is exercised via a
monkeypatched ``question``), matching how the notebook-management tests drive their
seams without the modal event loop.

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
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

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


def _note_ids(window: MainWindow) -> set[int]:
    """The note ids currently shown in the list."""
    return {
        window.note_list.item(i).data(Qt.ItemDataRole.UserRole).id
        for i in range(window.note_list.count())
    }


def test_delete_note_removes_it_from_vault_and_list(qapp, repo):
    note = repo.create_note(title="Doomed", body="bye")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert _note_ids(window) == {note.id}

    deleted = window.delete_note(note.id)

    assert deleted is True
    assert repo.get_note(note.id) is None
    assert window.note_list.count() == 0


def test_delete_note_without_repository_returns_false(qapp):
    # Pre-unlock state: no repository bound. Must not crash, just no-op.
    window = MainWindow()
    assert window.delete_note(1) is False


def test_delete_note_returns_false_for_missing_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # No such note — the repository reports nothing was removed.
    assert window.delete_note(9999) is False


def test_delete_note_leaves_other_notes_in_the_list(qapp, repo):
    keep = repo.create_note(title="Keep", body="stay")
    drop = repo.create_note(title="Drop", body="go")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert _note_ids(window) == {keep.id, drop.id}

    window.delete_note(drop.id)

    assert _note_ids(window) == {keep.id}
    assert repo.get_note(keep.id) is not None


def test_deleting_the_open_note_clears_editor_and_detaches_autosave(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Create a note via the UI (selects + loads it into the editor) and edit it.
    note = window.new_note()
    window.editor.source.setPlainText("# Title\n\nsome text")
    assert window.autosave.saver.note_id == note.id

    window.delete_note(note.id)

    # The editor is cleared and auto-save no longer points at the deleted note.
    assert window.editor.markdown() == ""
    assert window.autosave.saver.note_id is None
    assert repo.get_note(note.id) is None


def test_deleting_the_open_dirty_note_does_not_resurrect_it_on_flush(qapp, repo):
    """Deleting a note with an unsaved edit must not flush to the deleted row.

    Without detaching the deleted note from auto-save, the next flush would call
    ``update_note`` on a missing id and raise ``NotFoundError`` (and a successful
    write would resurrect the row). The seam detaches first, so the flush is a
    no-op and the note stays gone.
    """
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()
    window.editor.source.setPlainText("unsaved edit")
    assert window.autosave.saver.is_dirty  # a pending write exists

    window.delete_note(note.id)

    # Flushing pending edits (as the app does on lock / shutdown) must not raise
    # and must not re-create the deleted note.
    window.flush_pending()
    assert repo.get_note(note.id) is None


def test_deleting_a_non_open_note_leaves_the_editor_untouched(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Open note A in the editor, then delete a different note B.
    open_note = window.new_note()
    window.editor.source.setPlainText("kept open")
    other = repo.create_note(title="Other", body="elsewhere")
    window.refresh_notes()

    window.delete_note(other.id)

    # The editor still shows the open note, still bound to auto-save.
    assert window.editor.markdown() == "kept open"
    assert window.autosave.saver.note_id == open_note.id
    assert repo.get_note(open_note.id) is not None


def test_prompt_delete_note_deletes_on_confirm(qapp, repo, monkeypatch):
    note = repo.create_note(title="Confirm me", body="x")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # User clicks "Yes" in the confirmation dialog.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    window._prompt_delete_note(note)

    assert repo.get_note(note.id) is None
    assert window.note_list.count() == 0


def test_prompt_delete_note_keeps_note_on_cancel(qapp, repo, monkeypatch):
    note = repo.create_note(title="Spare me", body="x")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # User clicks "No" — nothing is deleted.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )
    window._prompt_delete_note(note)

    assert repo.get_note(note.id) is not None
    assert _note_ids(window) == {note.id}
