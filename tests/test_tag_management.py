"""Behavioral tests for vault-wide tag management (ROADMAP.md M7, capability 1:
rename a tag across the whole vault).

Drives the real path the running app uses -- a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection -- and proves that renaming a tag updates its label in the left
"Tags" section and on every note carrying it, that a name colliding with another
tag is refused (no half-committed ``IntegrityError``), that renaming to a tag's
own name is a harmless no-op, that the active tag filter survives a rename, and
that the right-click context menu offers "Rename..." on a tag row (and nothing on
the "Tags" header).

The public ``rename_tag`` seam and the ``_prompt_rename_tag`` prompt are driven
directly (the latter with ``QInputDialog.getText`` monkeypatched), and the menu
dispatch is exercised with ``itemAt`` / ``QMenu.exec`` patched -- mirroring
``tests/test_delete_note.py`` and ``tests/test_tag_filter.py`` so nothing depends
on running a modal event loop.

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

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QInputDialog,
    QMenu,
    QTreeWidgetItem,
)

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


def _kind_role() -> int:
    """The custom item-data role storing each tree item's kind."""
    from ui.main_window import _KIND_ROLE

    return _KIND_ROLE


def _tags_header(window: MainWindow) -> QTreeWidgetItem | None:
    """The "Tags" section header item, or ``None`` if there is no tag section."""
    tree = window.notebook_tree
    for i in range(tree.topLevelItemCount()):
        item = tree.topLevelItem(i)
        if item.data(0, _kind_role()) == _KIND_TAGS_HEADER:
            return item
    return None


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


# -- rename_tag seam ----------------------------------------------------------


def test_rename_tag_updates_tree_label_and_vault(qapp, repo):
    note = repo.create_note(title="N", body="x")
    tag = repo.create_tag("wrok")  # mistyped
    repo.add_tag_to_note(note.id, tag.id)

    window = MainWindow()
    window.bind_autosave(repo)
    assert _tag_child_labels(window) == ["wrok"]

    result = window.rename_tag(tag.id, "work")

    assert result is not None and result.name == "work"
    assert repo.get_tag(tag.id).name == "work"
    assert _tag_child_labels(window) == ["work"]
    # Same tag id, new label -- the note still carries it.
    assert [t.name for t in repo.tags_for_note(note.id)] == ["work"]


def test_rename_tag_no_repository_is_a_noop(qapp):
    window = MainWindow()  # never bound to a repository
    assert window.rename_tag(1, "anything") is None


def test_rename_tag_collision_with_another_tag_is_refused(qapp, repo):
    keep = repo.create_tag("urgent")
    other = repo.create_tag("later")

    window = MainWindow()
    window.bind_autosave(repo)

    # Renaming "later" onto the existing "urgent" is refused (no IntegrityError).
    assert window.rename_tag(other.id, "urgent") is None
    assert repo.get_tag(other.id).name == "later"
    assert repo.get_tag(keep.id).name == "urgent"


def test_rename_tag_to_its_own_name_succeeds(qapp, repo):
    tag = repo.create_tag("idea")

    window = MainWindow()
    window.bind_autosave(repo)

    result = window.rename_tag(tag.id, "idea")
    assert result is not None and result.name == "idea"


def test_rename_tag_keeps_the_active_tag_filter(qapp, repo):
    tag = repo.create_tag("draft")
    note = repo.create_note(title="Pinned", body="x")
    repo.add_tag_to_note(note.id, tag.id)

    window = MainWindow()
    window.bind_autosave(repo)
    window.select_tag(tag.id)
    assert window.current_tag_id == tag.id
    assert _note_labels(window) == ["Pinned"]

    window.rename_tag(tag.id, "published")

    # The filter is still the same (renamed) tag -- the list stays put and the
    # tree re-selects the renamed row.
    assert window.current_tag_id == tag.id
    assert _note_labels(window) == ["Pinned"]
    assert _find_tag_item(window, tag.id).text(0) == "published"


# -- _prompt_rename_tag (QInputDialog monkeypatched) --------------------------


def test_prompt_rename_tag_applies_entered_name(qapp, repo, monkeypatch):
    tag = repo.create_tag("wip")
    window = MainWindow()
    window.bind_autosave(repo)

    # User types a new name and clicks OK.
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("done", True))
    window._prompt_rename_tag(tag.id)

    assert repo.get_tag(tag.id).name == "done"


def test_prompt_rename_tag_cancel_leaves_name(qapp, repo, monkeypatch):
    tag = repo.create_tag("keep")
    window = MainWindow()
    window.bind_autosave(repo)

    # User cancels the dialog (ok=False) -- nothing changes.
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("ignored", False))
    window._prompt_rename_tag(tag.id)

    assert repo.get_tag(tag.id).name == "keep"


def test_prompt_rename_tag_blank_name_is_ignored(qapp, repo, monkeypatch):
    tag = repo.create_tag("keep")
    window = MainWindow()
    window.bind_autosave(repo)

    # OK clicked but the entry is whitespace-only -- treated as no rename.
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("   ", True))
    window._prompt_rename_tag(tag.id)

    assert repo.get_tag(tag.id).name == "keep"


# -- context-menu dispatch ----------------------------------------------------


def test_tag_context_menu_offers_rename(qapp, repo, monkeypatch):
    tag = repo.create_tag("work")
    window = MainWindow()
    window.bind_autosave(repo)

    captured: dict[str, list[str]] = {}

    def fake_exec(self, *a, **k):
        captured["actions"] = [action.text() for action in self.actions()]
        return None

    monkeypatch.setattr(QMenu, "exec", fake_exec)
    window._show_tag_menu(tag.id, QPoint(0, 0))

    assert any("Rename" in text for text in captured["actions"])


def test_notebook_menu_routes_tag_rows_to_the_tag_menu(qapp, repo, monkeypatch):
    tag = repo.create_tag("topic")
    window = MainWindow()
    window.bind_autosave(repo)
    tag_item = _find_tag_item(window, tag.id)

    monkeypatch.setattr(window.notebook_tree, "itemAt", lambda pos: tag_item)
    called: dict[str, int] = {}
    monkeypatch.setattr(
        window,
        "_show_tag_menu",
        lambda tag_id, pos: called.__setitem__("tag_id", tag_id),
    )

    window._show_notebook_menu(QPoint(0, 0))
    assert called.get("tag_id") == tag.id


def test_notebook_menu_ignores_the_tags_header(qapp, repo, monkeypatch):
    repo.create_tag("topic")  # so the "Tags" header exists
    window = MainWindow()
    window.bind_autosave(repo)
    header = _tags_header(window)
    assert header is not None

    monkeypatch.setattr(window.notebook_tree, "itemAt", lambda pos: header)
    tag_menu_calls: list[int] = []
    monkeypatch.setattr(window, "_show_tag_menu", lambda *a, **k: tag_menu_calls.append(1))
    exec_calls: list[int] = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: exec_calls.append(1))

    window._show_notebook_menu(QPoint(0, 0))

    assert tag_menu_calls == []  # the header is not a tag -> no tag menu
    assert exec_calls == []  # ...and not a notebook -> no notebook menu either
