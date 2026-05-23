"""Behavioral tests for keyboard-first pane navigation (ROADMAP.md M5).

Drives the real :class:`~ui.main_window.MainWindow` headlessly (offscreen Qt,
matching ``tests/test_theme_toggle.py`` / ``tests/test_word_count_status.py``) to
verify the focus seam methods move keyboard focus to the right widget. The
Ctrl+1/2/3/F shortcuts are wired to these methods, so testing the seam covers the
behaviour without synthesising key events through the modal event loop.

Focus is asserted via ``window.focusWidget()``: ``QWidget.setFocus`` records the
focus target on the window even when the window is not shown/active (so the
assertion is deterministic under offscreen Qt), and ``hasFocus()`` only becomes
true once the window is shown — both are checked here.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_focus_notebook_tree(qapp):
    window = MainWindow()
    window.focus_notebook_tree()
    assert window.focusWidget() is window.notebook_tree


def test_focus_note_list(qapp):
    window = MainWindow()
    window.focus_note_list()
    assert window.focusWidget() is window.note_list


def test_focus_editor_targets_the_editable_source(qapp):
    window = MainWindow()
    window.focus_editor()
    # The editor pane's editable half is what should take focus, not the preview.
    assert window.focusWidget() is window.editor.source


def test_focus_search(qapp):
    window = MainWindow()
    window.focus_search()
    assert window.focusWidget() is window.search_input


def test_focus_search_selects_existing_text(qapp):
    window = MainWindow()
    window.search_input.setText("project")
    window.focus_search()
    # The whole query is selected so the next keystroke replaces it.
    assert window.search_input.selectedText() == "project"


def test_focus_methods_are_safe_without_a_repository(qapp):
    # The seams only move focus; they must not require an open vault.
    window = MainWindow()
    assert window.repository is None
    window.focus_notebook_tree()
    window.focus_note_list()
    window.focus_editor()
    window.focus_search()  # no exception


def test_focus_actually_receives_keyboard_focus_when_shown(qapp):
    # hasFocus() is only true once the top-level window is shown/active; this is
    # the stronger end-to-end check that the target really holds keyboard focus.
    window = MainWindow()
    window.show()
    try:
        window.focus_editor()
        qapp.processEvents()
        assert window.editor.source.hasFocus()
        window.focus_note_list()
        qapp.processEvents()
        assert window.note_list.hasFocus()
    finally:
        window.close()


def test_each_shortcut_is_wired_to_its_focus_seam(qapp):
    # Emitting a shortcut's activated signal must move focus to its pane: this
    # guards the QShortcut -> seam connection end to end, not just that a method
    # exists. (We drive the signal rather than synthesise a key event so the
    # test does not depend on the modal event loop / window activation.)
    window = MainWindow()
    window.focus_tree_shortcut.activated.emit()
    assert window.focusWidget() is window.notebook_tree
    window.focus_list_shortcut.activated.emit()
    assert window.focusWidget() is window.note_list
    window.focus_editor_shortcut.activated.emit()
    assert window.focusWidget() is window.editor.source
    window.focus_search_shortcut.activated.emit()
    assert window.focusWidget() is window.search_input
