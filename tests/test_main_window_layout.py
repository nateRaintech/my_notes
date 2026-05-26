"""Structure tests for the main window's dock-based layout (issue #77).

Verifies the dock architecture introduced in M8 slice 1: the central widget is
the editor source, the three QDockWidgets carry the expected objectNames and
wrap the correct sub-widgets, and all the named attributes (notebook_tree,
note_list, search_input, editor, editor.source, editor.preview) remain accessible
as they were in the splitter layout. Guarded by ``importorskip`` and run headless
via the ``offscreen`` Qt platform.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDockWidget,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTextEdit,
    QTreeWidget,
)

from ui.editor import MarkdownEditor  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# Central widget is the editor source
# ---------------------------------------------------------------------------


def test_central_widget_is_editor_source(qapp):
    window = MainWindow()
    assert window.centralWidget() is window.editor.source
    assert isinstance(window.centralWidget(), QPlainTextEdit)


# ---------------------------------------------------------------------------
# Named widget attributes still exist and have the right types
# ---------------------------------------------------------------------------


def test_notebook_tree_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.notebook_tree, QTreeWidget)


def test_note_list_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.note_list, QListWidget)


def test_search_input_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.search_input, QLineEdit)


def test_editor_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.editor, MarkdownEditor)


def test_editor_source_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.editor.source, QPlainTextEdit)


def test_editor_preview_attribute(qapp):
    window = MainWindow()
    assert isinstance(window.editor.preview, QTextEdit)


# ---------------------------------------------------------------------------
# Three QDockWidgets with stable objectNames
# ---------------------------------------------------------------------------


def test_dock_notebooks_is_qdockwidget(qapp):
    window = MainWindow()
    assert isinstance(window.dock_notebooks, QDockWidget)


def test_dock_notelist_is_qdockwidget(qapp):
    window = MainWindow()
    assert isinstance(window.dock_notelist, QDockWidget)


def test_dock_preview_is_qdockwidget(qapp):
    window = MainWindow()
    assert isinstance(window.dock_preview, QDockWidget)


def test_dock_objectnames(qapp):
    window = MainWindow()
    assert window.dock_notebooks.objectName() == "dock_notebooks"
    assert window.dock_notelist.objectName() == "dock_notelist"
    assert window.dock_preview.objectName() == "dock_preview"


# ---------------------------------------------------------------------------
# Dock contents
# ---------------------------------------------------------------------------


def test_dock_notebooks_wraps_notebook_tree(qapp):
    window = MainWindow()
    assert window.dock_notebooks.widget() is window.notebook_tree


def test_dock_preview_wraps_editor_preview(qapp):
    window = MainWindow()
    assert window.dock_preview.widget() is window.editor.preview


def test_dock_notelist_contains_search_input(qapp):
    window = MainWindow()
    container = window.dock_notelist.widget()
    assert container is not None
    assert window.search_input.parent() is container


def test_dock_notelist_contains_note_list(qapp):
    window = MainWindow()
    container = window.dock_notelist.widget()
    assert container is not None
    assert window.note_list.parent() is container


# ---------------------------------------------------------------------------
# Dock initial areas
# ---------------------------------------------------------------------------


def test_dock_notebooks_in_left_area(qapp):
    from PySide6.QtCore import Qt

    window = MainWindow()
    area = window.dockWidgetArea(window.dock_notebooks)
    assert area == Qt.DockWidgetArea.LeftDockWidgetArea


def test_dock_notelist_in_left_area(qapp):
    from PySide6.QtCore import Qt

    window = MainWindow()
    area = window.dockWidgetArea(window.dock_notelist)
    assert area == Qt.DockWidgetArea.LeftDockWidgetArea


def test_dock_preview_in_right_area(qapp):
    from PySide6.QtCore import Qt

    window = MainWindow()
    area = window.dockWidgetArea(window.dock_preview)
    assert area == Qt.DockWidgetArea.RightDockWidgetArea


# ---------------------------------------------------------------------------
# Dock nesting enabled
# ---------------------------------------------------------------------------


def test_dock_nesting_enabled(qapp):
    window = MainWindow()
    assert window.isDockNestingEnabled()


# ---------------------------------------------------------------------------
# Editor splitter exists as an attribute (structural backward-compat check)
# ---------------------------------------------------------------------------


def test_editor_splitter_attribute_exists(qapp):
    """MarkdownEditor still exposes a .splitter attribute (used by editor tests)."""
    from PySide6.QtWidgets import QSplitter

    window = MainWindow()
    assert isinstance(window.editor.splitter, QSplitter)
    # In the dock layout, MainWindow re-parents source (central widget) and
    # preview (dock) away from the splitter, so splitter.count() may be 0.
    # What matters is the attribute exists and the source/preview attributes
    # point to the right widgets — confirmed by the tests above.
