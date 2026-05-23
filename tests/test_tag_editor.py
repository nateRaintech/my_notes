"""Behavioral tests for the tag editor (``ui/tag_editor.py``) and its wiring into
the main window (ROADMAP.md M6, Tag UI slice 1: assign / remove tags on a note).

Drives the dialog the way the app does — over a real (in-memory) SQLCipher
:class:`core.repository.Repository` and a real note — and proves the public seams
get-or-create tags by name (reuse, no duplicate), are idempotent, strip and
ignore blank names, detach tags without deleting them or touching other notes,
and that the dialog seeds with (and reflects changes to) the note's tags. Also
checks the :class:`ui.main_window.MainWindow` construction seam.

Mirrors ``tests/test_delete_note.py`` / ``tests/test_quick_switcher.py``: the
public ``assign_tag`` / ``remove_tag`` / ``current_tags`` seams are driven
directly (and the Add / Remove button handlers exercised) without running the
modal event loop.

Headless via the offscreen Qt platform, ``importorskip``-guarded — matching the
other Qt tests so the merge gate stays green wherever Qt is present.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.tag_editor import TagEditorDialog  # noqa: E402


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


def _list_labels(dialog: TagEditorDialog) -> list[str]:
    """The tag names currently shown in the dialog's list."""
    return [
        dialog.tag_list.item(i).text() for i in range(dialog.tag_list.count())
    ]


def test_assign_tag_creates_and_attaches(qapp, repo):
    note = repo.create_note(title="Note", body="body")

    dialog = TagEditorDialog(repo, note.id)
    tag = dialog.assign_tag("work")

    assert tag is not None
    assert tag.name == "work"
    # Persisted to the vault and reflected in the dialog list.
    assert [t.name for t in repo.tags_for_note(note.id)] == ["work"]
    assert _list_labels(dialog) == ["work"]


def test_assign_tag_is_idempotent(qapp, repo):
    note = repo.create_note(title="Note", body="body")

    dialog = TagEditorDialog(repo, note.id)
    first = dialog.assign_tag("work")
    second = dialog.assign_tag("work")  # re-assigning is a no-op, not an error

    assert first.id == second.id
    assert [t.name for t in repo.tags_for_note(note.id)] == ["work"]


def test_assign_tag_reuses_existing_tag_by_name(qapp, repo):
    # The tag already exists (e.g. created on another note); assigning the same
    # name must reuse it, not raise on the UNIQUE constraint.
    other = repo.create_note(title="Other", body="x")
    existing = repo.create_tag("shared")
    repo.add_tag_to_note(other.id, existing.id)

    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)
    tag = dialog.assign_tag("shared")

    assert tag.id == existing.id
    assert len(repo.list_tags()) == 1  # no duplicate tag created


def test_assign_blank_name_is_noop(qapp, repo):
    note = repo.create_note(title="Note", body="body")

    dialog = TagEditorDialog(repo, note.id)

    assert dialog.assign_tag("   ") is None
    assert repo.tags_for_note(note.id) == []
    assert dialog.tag_list.count() == 0


def test_assign_tag_strips_whitespace(qapp, repo):
    note = repo.create_note(title="Note", body="body")

    dialog = TagEditorDialog(repo, note.id)
    tag = dialog.assign_tag("  work  ")

    assert tag.name == "work"
    assert [t.name for t in repo.tags_for_note(note.id)] == ["work"]


def test_remove_tag_detaches_from_note(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)
    tag = dialog.assign_tag("work")

    removed = dialog.remove_tag(tag.id)

    assert removed is True
    assert repo.tags_for_note(note.id) == []
    assert dialog.tag_list.count() == 0
    # The tag itself still exists (it was only detached, not deleted).
    assert repo.get_tag(tag.id) is not None


def test_remove_unattached_tag_returns_false(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)

    assert dialog.remove_tag(9999) is False


def test_remove_tag_leaves_it_on_other_notes(qapp, repo):
    shared = repo.create_tag("shared")
    keep = repo.create_note(title="Keep", body="x")
    repo.add_tag_to_note(keep.id, shared.id)

    note = repo.create_note(title="Note", body="body")
    repo.add_tag_to_note(note.id, shared.id)

    dialog = TagEditorDialog(repo, note.id)
    dialog.remove_tag(shared.id)

    # Detached from this note, still on the other.
    assert repo.tags_for_note(note.id) == []
    assert [t.name for t in repo.tags_for_note(keep.id)] == ["shared"]


def test_dialog_seeds_with_existing_tags(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    for name in ("alpha", "beta"):
        tag = repo.create_tag(name)
        repo.add_tag_to_note(note.id, tag.id)

    dialog = TagEditorDialog(repo, note.id)

    # Seeded from the note's tags (ordered by name).
    assert _list_labels(dialog) == ["alpha", "beta"]
    assert [t.name for t in dialog.current_tags()] == ["alpha", "beta"]


def test_current_tags_reflects_adds_and_removes(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)

    a = dialog.assign_tag("alpha")
    dialog.assign_tag("beta")
    assert [t.name for t in dialog.current_tags()] == ["alpha", "beta"]

    dialog.remove_tag(a.id)
    assert [t.name for t in dialog.current_tags()] == ["beta"]


def test_add_button_assigns_and_clears_input(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)

    dialog.name_input.setText("work")
    dialog.add_button.click()

    assert [t.name for t in repo.tags_for_note(note.id)] == ["work"]
    assert dialog.name_input.text() == ""  # cleared on success


def test_add_button_keeps_input_on_blank(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)

    dialog.name_input.setText("   ")
    dialog.add_button.click()

    # Nothing assigned; the (whitespace) input is left as-is, not cleared.
    assert repo.tags_for_note(note.id) == []
    assert dialog.name_input.text() == "   "


def test_remove_button_detaches_selected_tag(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    dialog = TagEditorDialog(repo, note.id)
    dialog.assign_tag("work")

    dialog.tag_list.setCurrentRow(0)
    dialog.remove_button.click()

    assert repo.tags_for_note(note.id) == []
    assert dialog.tag_list.count() == 0


def test_make_tag_editor_returns_none_without_repository(qapp, repo):
    # Pre-unlock state: no repository bound. Must not crash, just no-op.
    note = repo.create_note(title="Note", body="body")
    window = MainWindow()

    assert window._make_tag_editor(note) is None


def test_make_tag_editor_seeded_for_note(qapp, repo):
    note = repo.create_note(title="Note", body="body")
    tag = repo.create_tag("work")
    repo.add_tag_to_note(note.id, tag.id)

    window = MainWindow()
    window.bind_autosave(repo)
    dialog = window._make_tag_editor(note)

    assert dialog is not None
    assert [t.name for t in dialog.current_tags()] == ["work"]
