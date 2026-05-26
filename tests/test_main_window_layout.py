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
    QLineEdit,
    QListWidget,
    QSplitter,
    QTreeWidget,
    QWidget,
)

from ui.editor import MarkdownEditor  # noqa: E402
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
    assert isinstance(window.search_input, QLineEdit)
    assert isinstance(window.editor, MarkdownEditor)
    # Panes appear in the splitter left-to-right: notebook panel container,
    # middle pane, editor.  The notebook_tree is wrapped inside notebook_panel
    # (so it gets a header row with a collapse button); the splitter holds the
    # container, not the raw tree.
    assert window.splitter.widget(0) is window.notebook_panel
    assert window.splitter.widget(1) is window.note_pane
    assert window.splitter.widget(2) is window.editor
    assert isinstance(window.note_pane, QWidget)
    # The notebook tree lives inside the notebook_panel container.
    assert window.notebook_tree.parent() is window.notebook_panel
    # The search box and note list both live inside the middle pane.
    assert window.search_input.parent() is window.note_pane
    assert window.note_list.parent() is window.note_pane


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
    # stretches when the window widens.  The splitter's first child is now the
    # notebook_panel container (the tree is wrapped inside it).
    assert window.notebook_panel.sizePolicy().horizontalStretch() == 0
    # The middle splitter child is the composite note pane (index 1).
    assert window.note_pane.sizePolicy().horizontalStretch() == 0
    assert window.editor.sizePolicy().horizontalStretch() == 1
