"""Behavioral tests for the status-bar word count (ROADMAP.md M5).

Drives the real :class:`~ui.main_window.MainWindow` headlessly (offscreen Qt,
matching ``tests/test_main_window_layout.py``) to verify the word count shown in
the status bar tracks the editor's text live. The counting itself is the Qt-free
``core.text.count_words`` (covered in ``tests/test_text.py``); these tests cover
the UI seam — that editing updates the label, with correct singular/plural and
Markdown-punctuation handling.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_word_count_label_is_a_status_bar_widget(qapp):
    window = MainWindow()
    assert isinstance(window.word_count_label, QLabel)
    # Added as a permanent status-bar widget, so it is reparented under it.
    assert window.word_count_label in window.statusBar().findChildren(QLabel)


def test_fresh_window_shows_zero_words(qapp):
    window = MainWindow()
    assert window.word_count_label.text() == "0 words"


def test_word_count_updates_live_when_editor_changes(qapp):
    window = MainWindow()
    window.editor.set_markdown("hello world")
    assert window.word_count_label.text() == "2 words"


def test_word_count_singular_for_one_word(qapp):
    window = MainWindow()
    window.editor.set_markdown("solo")
    assert window.word_count_label.text() == "1 word"


def test_word_count_ignores_markdown_punctuation(qapp):
    window = MainWindow()
    window.editor.set_markdown("# Title\n\nbody text")
    assert window.word_count_label.text() == "3 words"


def test_word_count_returns_to_zero_when_cleared(qapp):
    window = MainWindow()
    window.editor.set_markdown("some words here")
    assert window.word_count_label.text() == "3 words"
    window.editor.set_markdown("")
    assert window.word_count_label.text() == "0 words"
