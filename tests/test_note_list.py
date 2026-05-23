"""Behavioral tests for the note-list / search-results pane (ROADMAP.md M4).

Drives the real path the running app uses — a :class:`ui.main_window.MainWindow`
bound to a :class:`core.repository.Repository` over a real (in-memory) SQLCipher
connection — and proves the middle pane lists notes from the vault, filters them
live through full-text search, falls back to a derived title when a note has
none, and loads a selected note into the editor.

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


def _labels(window: MainWindow) -> list[str]:
    """The text shown for each row, top to bottom."""
    return [window.note_list.item(i).text() for i in range(window.note_list.count())]


def _stored_notes(window: MainWindow):
    """The Note object carried by each row, top to bottom."""
    return [
        window.note_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(window.note_list.count())
    ]


def test_empty_repository_shows_an_empty_list(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert window.note_list.count() == 0


def test_unbound_window_has_an_empty_list_and_refresh_is_a_noop(qapp):
    # No repository bound yet (pre-unlock state): nothing to list, no crash.
    window = MainWindow()
    assert window.note_list.count() == 0
    window.refresh_notes()  # must not raise
    assert window.note_list.count() == 0


def test_lists_all_notes_most_recently_updated_first(qapp, repo):
    repo.create_note(title="First", body="alpha")
    repo.create_note(title="Second", body="beta")
    third = repo.create_note(title="Third", body="gamma")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # list_notes orders updated_at DESC, id DESC: newest-created first.
    assert _labels(window) == ["Third", "Second", "First"]
    assert _stored_notes(window)[0].id == third.id


def test_row_label_falls_back_to_derived_title_when_title_empty(qapp, repo):
    repo.create_note(title="", body="# Recipe\n\nflour and sugar")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Empty stored title → derive_title(body) strips the ATX heading marker.
    assert _labels(window) == ["Recipe"]


def test_typing_a_query_filters_the_list(qapp, repo):
    repo.create_note(title="Shopping", body="# Shopping\n\nmilk and eggs")
    repo.create_note(title="Meeting", body="# Meeting\n\nproject deadline")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    assert window.note_list.count() == 2

    # Typing emits textChanged → refresh_notes → search_notes.
    window.search_input.setText("deadline")
    assert _labels(window) == ["Meeting"]

    window.search_input.setText("milk")
    assert _labels(window) == ["Shopping"]


def test_clearing_the_search_restores_the_full_list(qapp, repo):
    repo.create_note(title="One", body="apple")
    repo.create_note(title="Two", body="banana")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.search_input.setText("apple")
    assert _labels(window) == ["One"]

    window.search_input.setText("")  # clearing restores the all-notes baseline
    assert sorted(_labels(window)) == ["One", "Two"]


def test_no_match_query_yields_an_empty_list(qapp, repo):
    repo.create_note(title="Only", body="hello world")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.search_input.setText("nonexistentterm")
    assert window.note_list.count() == 0


def test_fts_metacharacters_in_query_do_not_raise(qapp, repo):
    repo.create_note(title="Note", body="some content here")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Bare FTS5 metacharacters / keywords would be a query-syntax error if passed
    # straight to MATCH; _fts_match_expr quotes each token so they match literally.
    for query in ['"', "*", ":", "^", "-", "(", ")", "AND", "OR", "NOT", '* : " ( )']:
        window.search_input.setText(query)  # must not raise
        assert window.note_list.count() >= 0


def test_selecting_a_note_loads_its_body_into_the_editor(qapp, repo):
    repo.create_note(title="Shopping", body="# Shopping\n\nmilk and eggs")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Selecting row 0 emits currentItemChanged → load_note → editor shows the body.
    window.note_list.setCurrentRow(0)
    assert window.editor.markdown() == "# Shopping\n\nmilk and eggs"


def test_refilter_does_not_spuriously_load_a_note(qapp, repo):
    repo.create_note(title="Kept", body="keep this in the editor")
    repo.create_note(title="Other", body="other body")

    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Open a note, then refilter the list. Rebuilding the list must not change
    # what's loaded in the editor (selection loads only on an explicit click).
    window.note_list.setCurrentRow(0)
    loaded = window.editor.markdown()
    window.search_input.setText("other")
    assert window.editor.markdown() == loaded
