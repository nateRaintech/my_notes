"""Behavioral tests for filtering the note list by tag (ROADMAP.md M6, Tag UI
slice 2 of 2: filter the note list by tag).

Drives the real path the running app uses -- a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection -- and proves the left "notebooks/tags" tree grows a "Tags" section,
that selecting a tag filters the note list to notes carrying it (across all
notebooks), that the tag filter and the notebook filter are mutually exclusive,
that full-text search stays global, and that creating a note / editing a note's
tags keeps the tree and list in sync.

The tree's tag rows are driven both directly (via the public :meth:`select_tag`
seam) and through the selection signal (``setCurrentItem`` ->
``currentItemChanged`` -> ``_on_notebook_selected``), mirroring how
``tests/test_notebook_tree.py`` exercises the notebook rows -- without running a
modal event loop.

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

from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: E402

from ui.main_window import (  # noqa: E402
    _KIND_TAG,
    _KIND_TAGS_HEADER,
    MainWindow,
)


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


def _top_level_labels(window: MainWindow) -> list[str]:
    """The text shown for each top-level tree row, top to bottom."""
    tree = window.notebook_tree
    return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


def _tags_header(window: MainWindow) -> QTreeWidgetItem | None:
    """The "Tags" section header item, or ``None`` if there is no tag section."""
    tree = window.notebook_tree
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item.data(0, _kind_role()) == _KIND_TAGS_HEADER:
            return item
    return None


def _kind_role() -> int:
    """The custom item-data role storing each tree item's kind."""
    from ui.main_window import _KIND_ROLE

    return _KIND_ROLE


def _tag_child_labels(window: MainWindow) -> list[str]:
    """The tag names listed under the "Tags" header (empty if no header)."""
    header = _tags_header(window)
    if header is None:
        return []
    return [header.child(i).text(0) for i in range(header.childCount())]


def _find_tag_item(window: MainWindow, tag_id: int) -> QTreeWidgetItem:
    """Locate the tree item for ``tag_id`` (a tag-kind row)."""
    from PySide6.QtCore import Qt

    tree = window.notebook_tree
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        if (
            item.data(0, _kind_role()) == _KIND_TAG
            and item.data(0, Qt.ItemDataRole.UserRole) == tag_id
        ):
            return item
        stack.extend(item.child(i) for i in range(item.childCount()))
    raise AssertionError(f"no tree item for tag {tag_id}")


def test_select_tag_filters_the_note_list(qapp, repo):
    work = repo.create_tag("work")
    tagged = repo.create_note(title="Standup", body="agenda")
    repo.add_tag_to_note(tagged.id, work.id)
    repo.create_note(title="Personal", body="groceries")  # untagged

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert sorted(_note_labels(window)) == ["Personal", "Standup"]

    window.select_tag(work.id)
    assert window.current_tag_id == work.id
    assert _note_labels(window) == ["Standup"]


def test_selecting_a_tag_clears_the_notebook_filter(qapp, repo):
    nb = repo.create_notebook("Work")
    work = repo.create_tag("work")
    # A tagged note in a *different* notebook (root) -- the tag view must show it
    # even though the notebook filter was on "Work".
    root_tagged = repo.create_note(title="Root tagged", body="x")
    repo.add_tag_to_note(root_tagged.id, work.id)
    repo.create_note(notebook_id=nb.id, title="In Work", body="y")

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_notebook(nb.id)
    assert _note_labels(window) == ["In Work"]

    window.select_tag(work.id)
    assert window.current_tag_id == work.id
    assert window.current_notebook_id is None  # notebook filter cleared
    assert _note_labels(window) == ["Root tagged"]


def test_selecting_a_notebook_clears_the_tag_filter(qapp, repo):
    work = repo.create_tag("work")
    tagged = repo.create_note(title="Tagged", body="x")
    repo.add_tag_to_note(tagged.id, work.id)
    repo.create_note(title="Plain", body="y")

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(work.id)
    assert _note_labels(window) == ["Tagged"]

    window.select_notebook(None)  # "All Notes"
    assert window.current_tag_id is None
    assert sorted(_note_labels(window)) == ["Plain", "Tagged"]


def test_tree_shows_a_tags_section_listing_all_tags(qapp, repo):
    note = repo.create_note(title="Note", body="x")
    # Created out of alpha order to prove the tree lists them ordered by name.
    for name in ("zeta", "alpha"):
        tag = repo.create_tag(name)
        repo.add_tag_to_note(note.id, tag.id)

    window = MainWindow()
    window.bind_autosave(repo)

    assert "Tags" in _top_level_labels(window)
    assert _tag_child_labels(window) == ["alpha", "zeta"]


def test_no_tags_section_when_there_are_no_tags(qapp, repo):
    repo.create_note(title="Note", body="x")
    window = MainWindow()
    window.bind_autosave(repo)

    assert _tags_header(window) is None
    assert "Tags" not in _top_level_labels(window)


def test_selecting_a_tag_via_the_tree_filters_the_list(qapp, repo):
    # Exercise the signal path: setting the current tree item fires
    # currentItemChanged -> _on_notebook_selected -> select_tag.
    work = repo.create_tag("work")
    tagged = repo.create_note(title="Tagged", body="x")
    repo.add_tag_to_note(tagged.id, work.id)
    repo.create_note(title="Plain", body="y")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.notebook_tree.setCurrentItem(_find_tag_item(window, work.id))
    assert window.current_tag_id == work.id
    assert _note_labels(window) == ["Tagged"]


def test_search_overrides_the_tag_filter_and_clearing_returns_to_it(qapp, repo):
    work = repo.create_tag("work")
    tagged = repo.create_note(title="Tagged apple", body="x")
    repo.add_tag_to_note(tagged.id, work.id)
    # Untagged note that matches the search term but not the tag.
    repo.create_note(title="Plain apple", body="y")

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(work.id)
    assert _note_labels(window) == ["Tagged apple"]

    # Search is global -- it shows matches across all notes, ignoring the tag.
    window.search_input.setText("apple")
    assert sorted(_note_labels(window)) == ["Plain apple", "Tagged apple"]

    # Clearing the search returns to the tag filter.
    window.search_input.clear()
    assert _note_labels(window) == ["Tagged apple"]


def test_new_note_while_tag_filter_active_stays_visible(qapp, repo):
    work = repo.create_tag("work")
    tagged = repo.create_note(title="Tagged", body="x")
    repo.add_tag_to_note(tagged.id, work.id)

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(work.id)
    assert _note_labels(window) == ["Tagged"]

    note = window.new_note()

    assert note is not None
    # The new (untagged) note would be hidden by the tag filter, so the view
    # resets to All Notes: the tag filter is cleared and the note is visible.
    assert window.current_tag_id is None
    assert note.id in {
        window.note_list.item(i).data(_user_role()).id
        for i in range(window.note_list.count())
    }
    # And it is the selected row (loaded into the editor).
    current = window.note_list.currentItem()
    assert current is not None
    assert current.data(_user_role()).id == note.id


def test_open_tag_editor_adds_new_tag_to_the_tree(qapp, repo, monkeypatch):
    note = repo.create_note(title="Note", body="x")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert _tags_header(window) is None  # no tags yet

    # Simulate the user opening the tag editor, assigning a brand-new tag, and
    # closing it. open_tag_editor must refresh the tree afterwards.
    real_make = window._make_tag_editor

    def fake_make(n):
        dialog = real_make(n)
        monkeypatch.setattr(dialog, "exec", lambda: dialog.assign_tag("urgent"))
        return dialog

    monkeypatch.setattr(window, "_make_tag_editor", fake_make)
    window.open_tag_editor(note)

    assert _tag_child_labels(window) == ["urgent"]


def test_open_tag_editor_refreshes_tag_filtered_list(qapp, repo, monkeypatch):
    work = repo.create_tag("work")
    note = repo.create_note(title="Tagged", body="x")
    repo.add_tag_to_note(note.id, work.id)

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(work.id)
    assert _note_labels(window) == ["Tagged"]

    # Removing the tag via the editor must drop the note from the tag view once
    # the editor closes (open_tag_editor refreshes the filtered list).
    real_make = window._make_tag_editor

    def fake_make(n):
        dialog = real_make(n)
        monkeypatch.setattr(dialog, "exec", lambda: dialog.remove_tag(work.id))
        return dialog

    monkeypatch.setattr(window, "_make_tag_editor", fake_make)
    window.open_tag_editor(note)

    assert _note_labels(window) == []


def test_lock_session_resets_the_tag_filter(qapp, repo):
    work = repo.create_tag("work")
    note = repo.create_note(title="Tagged", body="x")
    repo.add_tag_to_note(note.id, work.id)

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(work.id)
    assert window.current_tag_id == work.id

    window.lock_session()
    assert window.current_tag_id is None


def test_unbound_window_select_tag_is_a_noop(qapp):
    # Pre-unlock state: no repository bound. Must not crash, just no-op.
    window = MainWindow()
    window.select_tag(1)  # must not raise
    assert window.current_tag_id == 1
    assert window.note_list.count() == 0


def _user_role() -> int:
    """The Qt.UserRole used by the note list to carry the Note object."""
    from PySide6.QtCore import Qt

    return Qt.ItemDataRole.UserRole
