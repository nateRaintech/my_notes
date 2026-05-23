"""Behavioral tests for the dark-theme toggle (ROADMAP.md M5).

Drives the real :class:`~ui.main_window.MainWindow` headlessly (offscreen Qt,
matching ``tests/test_word_count_status.py``) to verify :meth:`MainWindow.apply_theme`
applies the QSS to the window and the **View -> Dark Theme** action toggles
between the dark theme and the native light look, staying in sync. The QSS text
itself is the Qt-free ``core.theme.load_stylesheet`` (covered in
``tests/test_theme.py``); these tests cover the UI seam.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core import theme  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_fresh_window_uses_the_default_theme(qapp):
    window = MainWindow()
    assert window.current_theme == theme.DEFAULT_THEME
    # The window's stylesheet matches whatever the default theme loads.
    assert window.styleSheet() == theme.load_stylesheet(theme.DEFAULT_THEME)


def test_initial_action_state_matches_the_active_theme(qapp):
    window = MainWindow()
    assert window.dark_theme_action.isCheckable()
    assert window.dark_theme_action.isChecked() == (window.current_theme == "dark")


def test_apply_dark_theme_sets_the_window_stylesheet(qapp):
    window = MainWindow()
    window.apply_theme("dark")
    assert window.current_theme == "dark"
    assert window.styleSheet() == theme.load_stylesheet("dark")
    assert window.styleSheet().strip()  # actually styled, not empty


def test_apply_light_theme_restores_native_look(qapp):
    window = MainWindow()
    window.apply_theme("dark")
    window.apply_theme("light")
    assert window.current_theme == "light"
    assert window.styleSheet() == ""


def test_apply_theme_syncs_the_menu_action(qapp):
    window = MainWindow()
    window.apply_theme("dark")
    assert window.dark_theme_action.isChecked()
    window.apply_theme("light")
    assert not window.dark_theme_action.isChecked()


def test_triggering_the_action_toggles_the_theme(qapp):
    window = MainWindow()
    start = window.current_theme
    # Simulate the user clicking the menu item: it flips checked + emits triggered.
    window.dark_theme_action.trigger()
    assert window.current_theme == "dark"
    assert window.styleSheet() == theme.load_stylesheet("dark")
    window.dark_theme_action.trigger()
    assert window.current_theme == "light"
    assert window.styleSheet() == ""
    assert start == "light"  # guards the assumption above (default is light)


def test_apply_unknown_theme_raises(qapp):
    window = MainWindow()
    with pytest.raises(ValueError):
        window.apply_theme("solarized")
