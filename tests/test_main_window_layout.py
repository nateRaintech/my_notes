"""Structure tests for the main window's 3-pane layout.

Verifies the resizable notebooks/note-list/editor shell (ROADMAP.md M3): the
central widget is a horizontal ``QSplitter`` with exactly three typed panes that
cannot be collapsed to nothing. Guarded by ``importorskip`` and run headless via
the ``offscreen`` Qt platform, matching ``tests/test_app_launch.py`` so the merge
gate stays green wherever the Qt runtime is present.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QListWidget,
    QSplitter,
    QTextEdit,
    QTreeWidget,
)

from ui.main_window import PANE_DEFAULT_SIZES, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_central_widget_is_horizontal_splitter_with_three_panes(qapp):
    window = MainWindow()
    splitter = window.centralWidget()
    assert isinstance(splitter, QSplitter)
    assert splitter is window.splitter
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.count() == 3


def test_panes_are_typed_attributes_in_order(qapp):
    window = MainWindow()
    assert isinstance(window.notebook_tree, QTreeWidget)
    assert isinstance(window.note_list, QListWidget)
    assert isinstance(window.editor, QTextEdit)
    # Panes appear in the splitter left-to-right: tree, list, editor.
    assert window.splitter.widget(0) is window.notebook_tree
    assert window.splitter.widget(1) is window.note_list
    assert window.splitter.widget(2) is window.editor


def test_panes_cannot_collapse_to_zero(qapp):
    window = MainWindow()
    assert window.splitter.childrenCollapsible() is False


def test_editor_pane_is_widest_by_default():
    # Three default sizes, weighted toward the editor (the last pane).
    assert len(PANE_DEFAULT_SIZES) == 3
    assert PANE_DEFAULT_SIZES[2] == max(PANE_DEFAULT_SIZES)


def test_editor_pane_absorbs_resize(qapp):
    window = MainWindow()
    # QSplitter.setStretchFactor records the factor on the child's size policy
    # (there is no stretchFactor getter on the splitter). Only the editor pane
    # stretches when the window widens.
    assert window.notebook_tree.sizePolicy().horizontalStretch() == 0
    assert window.note_list.sizePolicy().horizontalStretch() == 0
    assert window.editor.sizePolicy().horizontalStretch() == 1
