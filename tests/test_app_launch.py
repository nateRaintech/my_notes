"""Launch smoke test for the application shell.

Verifies that the main window constructs with the expected title and a central
widget. Guarded by ``importorskip`` so it runs locally (and once PySide6 lands
in CI) but skips cleanly while CI does not yet install the Qt runtime — keeping
the merge gate green. Runs headless via the ``offscreen`` Qt platform so no real
window appears during the test run.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from ui.main_window import WINDOW_TITLE, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_main_window_constructs(qapp):
    window = MainWindow()
    assert isinstance(window, QMainWindow)
    assert window.windowTitle() == WINDOW_TITLE


def test_main_window_has_central_widget(qapp):
    window = MainWindow()
    assert window.centralWidget() is not None
