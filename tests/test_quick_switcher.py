"""Behavioral tests for the Ctrl+P quick-switcher (``ui/quick_switcher.py``) and
its wiring into the main window.

Drives the dialog the way the app does — over real :class:`core.repository.Note`
objects — and checks fuzzy filtering/ranking, top-result auto-selection, the
``accept_selection`` seam, keyboard navigation, the derived-title fallback, and
that :class:`ui.main_window.MainWindow` exposes a Ctrl+P shortcut that builds the
switcher from the bound repository (and is a no-op when no vault is open).

Headless via the offscreen Qt platform, ``importorskip``-guarded — matching the
other Qt tests so the merge gate stays green wherever Qt is present.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Note, Repository

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QKeySequence  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.quick_switcher import QuickSwitcher  # noqa: E402


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


def _note(note_id: int, title: str, body: str = "") -> Note:
    return Note(
        id=note_id,
        notebook_id=None,
        title=title,
        body=body,
        created_at="t",
        updated_at="t",
    )


def _labels(switcher: QuickSwitcher) -> list[str]:
    """The text shown for each result row, top to bottom."""
    return [switcher.results.item(i).text() for i in range(switcher.results.count())]


# --- QuickSwitcher dialog ----------------------------------------------------


def test_lists_all_notes_initially_with_top_selected(qapp):
    switcher = QuickSwitcher([_note(1, "Alpha"), _note(2, "Beta")])
    assert _labels(switcher) == ["Alpha", "Beta"]
    # The best (first) row is auto-selected so Enter works immediately.
    assert switcher.results.currentRow() == 0


def test_typing_filters_by_fuzzy_title(qapp):
    notes = [_note(1, "Shopping list"), _note(2, "Meeting notes"), _note(3, "Recipe")]
    switcher = QuickSwitcher(notes)

    switcher.search_input.setText("meet")
    assert _labels(switcher) == ["Meeting notes"]

    # "rcp" is a (non-contiguous) subsequence of "Recipe" only.
    switcher.search_input.setText("rcp")
    assert _labels(switcher) == ["Recipe"]


def test_ranks_better_matches_first(qapp):
    switcher = QuickSwitcher([_note(1, "Microwave"), _note(2, "Main Window")])
    switcher.search_input.setText("mw")
    # Word-boundary match (Main Window) ranks above the mid-word one (Microwave).
    assert _labels(switcher) == ["Main Window", "Microwave"]


def test_derived_title_used_when_stored_title_blank(qapp):
    switcher = QuickSwitcher([_note(1, "", body="# Recipe\n\nflour and sugar")])
    assert _labels(switcher) == ["Recipe"]  # derive_title strips the ATX marker
    switcher.search_input.setText("rec")
    assert _labels(switcher) == ["Recipe"]


def test_no_match_empties_the_list(qapp):
    switcher = QuickSwitcher([_note(1, "Alpha")])
    switcher.search_input.setText("zzz")
    assert switcher.results.count() == 0


def test_accept_selection_sets_selected_note(qapp):
    switcher = QuickSwitcher([_note(1, "Alpha"), _note(2, "Beta")])
    switcher.search_input.setText("beta")
    assert switcher.accept_selection() is True
    assert switcher.selected_note is not None
    assert switcher.selected_note.title == "Beta"


def test_accept_selection_with_no_match_keeps_dialog_open(qapp):
    switcher = QuickSwitcher([_note(1, "Alpha")])
    switcher.search_input.setText("zzz")  # no rows -> nothing to choose
    assert switcher.accept_selection() is False
    assert switcher.selected_note is None


def test_navigation_moves_and_wraps(qapp):
    switcher = QuickSwitcher([_note(1, "A"), _note(2, "B"), _note(3, "C")])
    assert switcher.results.currentRow() == 0
    switcher.select_next()
    assert switcher.results.currentRow() == 1
    switcher.select_previous()
    assert switcher.results.currentRow() == 0
    switcher.select_previous()  # wrap past the top -> bottom
    assert switcher.results.currentRow() == 2
    switcher.select_next()  # wrap past the bottom -> top
    assert switcher.results.currentRow() == 0


def test_current_note_reflects_the_highlighted_row(qapp):
    switcher = QuickSwitcher([_note(1, "Alpha"), _note(2, "Beta")])
    switcher.results.setCurrentRow(1)
    current = switcher.current_note()
    assert current is not None and current.title == "Beta"


# --- MainWindow wiring -------------------------------------------------------


def test_mainwindow_registers_ctrl_p_shortcut(qapp):
    window = MainWindow()
    assert window.quick_switch_shortcut.key() == QKeySequence("Ctrl+P")


def test_quick_switcher_is_a_noop_without_a_repository(qapp):
    window = MainWindow()  # no vault bound yet
    assert window._make_quick_switcher() is None
    window.open_quick_switcher()  # must not raise (no modal loop entered)


def test_quick_switcher_lists_the_repository_notes(qapp, repo):
    repo.create_note(title="First", body="alpha")
    repo.create_note(title="Second", body="beta")

    window = MainWindow()
    window.bind_autosave(repo)
    switcher = window._make_quick_switcher()
    assert switcher is not None
    assert sorted(_labels(switcher)) == ["First", "Second"]


def test_choosing_a_note_loads_it_into_the_editor(qapp, repo):
    repo.create_note(title="Loadme", body="the body text")

    window = MainWindow()
    window.bind_autosave(repo)
    switcher = window._make_quick_switcher()
    assert switcher is not None

    switcher.search_input.setText("load")
    assert switcher.accept_selection() is True
    # open_quick_switcher loads selected_note on accept; drive that step directly
    # (the exec() modal loop is not entered headlessly).
    window.load_note(switcher.selected_note)
    assert window.editor.markdown() == "the body text"
