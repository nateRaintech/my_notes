"""Behavioral tests for the notebook tree pane (ROADMAP.md M4).

Drives the real path the running app uses — a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection — and proves the left pane shows an "All Notes" root above the vault's
notebooks nested by parent, that selecting a notebook filters the note list to
it, and that the create / rename / delete seams keep the tree and note list in
sync.

The create/rename/delete *menu* actions pop modal ``QInputDialog`` /
``QMessageBox`` dialogs, so the tests drive the public seams those handlers call
(:meth:`add_notebook` / :meth:`rename_notebook` / :meth:`remove_notebook` /
:meth:`select_notebook`) directly — mirroring how the quick-switcher and unlock
dialog are tested without the modal event loop.

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
from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

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


def _top_level_labels(window: MainWindow) -> list[str]:
    """The text shown for each top-level tree row, top to bottom."""
    tree = window.notebook_tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _note_labels(window: MainWindow) -> list[str]:
    """The text shown for each note-list row, top to bottom."""
    return [window.note_list.item(i).text() for i in range(window.note_list.count())]


def _find_notebook_item(window: MainWindow, notebook_id: int) -> QTreeWidgetItem:
    """Locate the tree item carrying ``notebook_id`` (depth-first)."""
    tree = window.notebook_tree
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if item.data(0, Qt.ItemDataRole.UserRole) == notebook_id:
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    raise AssertionError(f"no tree item for notebook {notebook_id}")


def test_unbound_window_has_empty_tree_and_seams_are_noops(qapp):
    window = MainWindow()
    assert window.notebook_tree.topLevelItemCount() == 0
    assert window.add_notebook("Work") is None  # no repository bound
    assert window.remove_notebook(1) is False
    window.select_notebook(None)  # must not raise


def test_empty_repository_shows_only_all_notes(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    assert _top_level_labels(window) == ["All Notes"]


def test_tree_shows_notebooks_nested_under_their_parent(qapp, repo):
    parent = repo.create_notebook("Projects")
    repo.create_notebook("my_notes", parent_id=parent.id)

    window = MainWindow()
    window.bind_autosave(repo)

    # Top level: All Notes + the one top-level notebook.
    assert _top_level_labels(window) == ["All Notes", "Projects"]
    projects = _find_notebook_item(window, parent.id)
    assert projects.childCount() == 1
    assert projects.child(0).text(0) == "my_notes"


def test_selecting_a_notebook_filters_the_note_list(qapp, repo):
    work = repo.create_notebook("Work")
    repo.create_note(notebook_id=work.id, title="Standup", body="agenda")
    repo.create_note(title="Personal", body="groceries")  # root note

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert sorted(_note_labels(window)) == ["Personal", "Standup"]

    window.select_notebook(work.id)
    assert _note_labels(window) == ["Standup"]

    window.select_notebook(None)  # "All Notes" → no filter
    assert sorted(_note_labels(window)) == ["Personal", "Standup"]


def test_selecting_a_notebook_via_the_tree_filters_the_list(qapp, repo):
    # Exercise the signal path: setting the current tree item fires
    # currentItemChanged → _on_notebook_selected → select_notebook.
    work = repo.create_notebook("Work")
    repo.create_note(notebook_id=work.id, title="Standup", body="agenda")
    repo.create_note(title="Personal", body="groceries")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.notebook_tree.setCurrentItem(_find_notebook_item(window, work.id))
    assert window.current_notebook_id == work.id
    assert _note_labels(window) == ["Standup"]


def test_add_notebook_creates_and_shows_it(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)

    created = window.add_notebook("Work")
    assert created is not None
    assert repo.get_notebook(created.id) is not None
    assert _top_level_labels(window) == ["All Notes", "Work"]


def test_add_sub_notebook_nests_it(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)

    parent = window.add_notebook("Projects")
    child = window.add_notebook("my_notes", parent_id=parent.id)

    assert child.parent_id == parent.id
    parent_item = _find_notebook_item(window, parent.id)
    assert parent_item.childCount() == 1
    assert parent_item.child(0).text(0) == "my_notes"


def test_rename_notebook_updates_the_label(qapp, repo):
    notebook = repo.create_notebook("Untitled")
    window = MainWindow()
    window.bind_autosave(repo)

    window.rename_notebook(notebook.id, "Renamed")
    assert _top_level_labels(window) == ["All Notes", "Renamed"]
    assert repo.get_notebook(notebook.id).name == "Renamed"


def test_remove_notebook_deletes_it_and_refreshes(qapp, repo):
    notebook = repo.create_notebook("Scratch")
    window = MainWindow()
    window.bind_autosave(repo)
    assert _top_level_labels(window) == ["All Notes", "Scratch"]

    assert window.remove_notebook(notebook.id) is True
    assert _top_level_labels(window) == ["All Notes"]
    assert repo.get_notebook(notebook.id) is None


def test_removing_the_active_notebook_falls_back_to_all_notes(qapp, repo):
    notebook = repo.create_notebook("Work")
    repo.create_note(notebook_id=notebook.id, title="Task", body="do it")

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_notebook(notebook.id)
    assert _note_labels(window) == ["Task"]

    window.remove_notebook(notebook.id)
    # Filter reset to All Notes; the note orphaned to the root still shows.
    assert window.current_notebook_id is None
    assert _note_labels(window) == ["Task"]


def test_deleting_a_notebook_orphans_its_notes_to_root(qapp, repo):
    notebook = repo.create_notebook("Work")
    note = repo.create_note(notebook_id=notebook.id, title="Task", body="do it")

    window = MainWindow()
    window.bind_autosave(repo)
    window.remove_notebook(notebook.id)

    # The note survives with notebook_id cleared (FK ON DELETE SET NULL).
    assert repo.get_note(note.id).notebook_id is None
