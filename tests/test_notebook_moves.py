"""Behavioral tests for moving notebooks and notes (ROADMAP.md M4).

Completes the "Notebook management" capability: re-parenting an existing notebook
(:meth:`ui.main_window.MainWindow.move_notebook`) and moving a note between
notebooks (:meth:`ui.main_window.MainWindow.move_note`). As with the rest of the
notebook-tree tests, the modal pick dialogs aren't exercised — the tests drive the
public seams those handlers call directly, against a real (in-memory) SQLCipher
``Repository``.

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


# -- move_notebook (re-parent) --------------------------------------------


def test_move_notebook_nests_it_under_a_new_parent(qapp, repo):
    projects = repo.create_notebook("Projects")
    loose = repo.create_notebook("Loose")  # starts at the top level
    window = MainWindow()
    window.bind_autosave(repo)

    moved = window.move_notebook(loose.id, projects.id)

    assert moved is not None
    assert moved.parent_id == projects.id
    assert repo.get_notebook(loose.id).parent_id == projects.id
    # The tree re-nests it under Projects.
    projects_item = _find_notebook_item(window, projects.id)
    assert [projects_item.child(i).text(0) for i in range(projects_item.childCount())] == [
        "Loose"
    ]


def test_move_notebook_to_root_unnests_it(qapp, repo):
    parent = repo.create_notebook("Parent")
    child = repo.create_notebook("Child", parent_id=parent.id)
    window = MainWindow()
    window.bind_autosave(repo)

    moved = window.move_notebook(child.id, None)

    assert moved.parent_id is None
    assert repo.get_notebook(child.id).parent_id is None
    # Now a top-level row alongside "All Notes" and "Parent".
    labels = [
        window.notebook_tree.topLevelItem(i).text(0)
        for i in range(window.notebook_tree.topLevelItemCount())
    ]
    assert labels == ["All Notes", "Child", "Parent"]


def test_move_notebook_rejects_a_cycle(qapp, repo):
    parent = repo.create_notebook("Parent")
    child = repo.create_notebook("Child", parent_id=parent.id)
    window = MainWindow()
    window.bind_autosave(repo)

    # Moving Parent under its own Child would cycle — refused, nothing changes.
    result = window.move_notebook(parent.id, child.id)

    assert result is None
    assert repo.get_notebook(parent.id).parent_id is None
    assert repo.get_notebook(child.id).parent_id == parent.id


def test_move_notebook_is_a_noop_without_a_repository(qapp):
    window = MainWindow()
    assert window.move_notebook(1, None) is None


# -- move_note -------------------------------------------------------------


def test_move_note_into_a_notebook(qapp, repo):
    work = repo.create_notebook("Work")
    note = repo.create_note(title="Standup", body="agenda")  # starts at the root
    window = MainWindow()
    window.bind_autosave(repo)

    moved = window.move_note(note.id, work.id)

    assert moved is not None
    assert moved.notebook_id == work.id
    assert repo.get_note(note.id).notebook_id == work.id


def test_move_note_updates_the_filtered_list(qapp, repo):
    work = repo.create_notebook("Work")
    note = repo.create_note(title="Standup", body="agenda")
    window = MainWindow()
    window.bind_autosave(repo)

    # Viewing the Work notebook: the root note isn't shown yet.
    window.select_notebook(work.id)
    assert _note_labels(window) == []

    window.move_note(note.id, work.id)
    # The move refreshes the list, so it now appears under Work.
    assert _note_labels(window) == ["Standup"]


def test_move_note_to_root(qapp, repo):
    work = repo.create_notebook("Work")
    note = repo.create_note(notebook_id=work.id, title="Standup", body="agenda")
    window = MainWindow()
    window.bind_autosave(repo)

    window.move_note(note.id, None)

    assert repo.get_note(note.id).notebook_id is None


def test_move_note_is_a_noop_without_a_repository(qapp):
    window = MainWindow()
    assert window.move_note(1, None) is None
