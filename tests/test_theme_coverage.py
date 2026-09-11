"""The dark theme covers every widget the app puts on screen (#98).

The bug that prompted this: the tab bar arrived with the tabbed editor and was
never added to ``resources/dark.qss``, so it fell back to the native Windows
style — which is drawn for a *light* palette — and painted dark labels on the
dark tab strip. Nothing caught it because nothing was checking.

Rather than pin the specific classes that were missing (which would pass forever
once fixed and never catch the next one), this walks the **live** widget tree of
a fully constructed window and every dialog, and fails if any Qt widget class on
screen has no selector in the stylesheet. Adding a widget without styling it
fails the suite instead of shipping unreadable.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository
from core.settings import DEFAULT_SETTINGS
from core.theme import available_themes, load_stylesheet

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from ui.icons import DARK_GLYPH, LIGHT_GLYPH, cross_icon, float_icon, glyph_color  # noqa: E402
from ui.import_wizard import ImportWizard  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.quick_switcher import QuickSwitcher  # noqa: E402
from ui.settings_dialog import SettingsDialog  # noqa: E402
from ui.tag_editor import TagEditorDialog  # noqa: E402
from ui.tool_palette import ToolPalette  # noqa: E402
from ui.unlock_dialog import UnlockDialog  # noqa: E402

# Qt classes that legitimately need no selector of their own.
#
# Each entry is a deliberate decision, not a convenience: a class belongs here
# only when the QWidget base rule (which sets background and foreground for
# everything) is genuinely sufficient, or when the widget is drawn through a
# subcontrol of an ancestor that *is* styled.
_NO_RULE_NEEDED = {
    # An abstract base. Every concrete button the app shows (QPushButton,
    # QToolButton, QCheckBox, QRadioButton) has its own rule; Qt's private
    # title-bar buttons surface under this name and are drawn through the
    # QDockWidget::close-button / ::float-button subcontrols.
    "QAbstractButton",
    # Pure layout containers: they show the QWidget background and nothing else.
    "QWidget",
    "QScrollArea",
    "QSplitter",
}


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def _qt_class_name(widget: QWidget) -> str:
    """The nearest Qt class for ``widget`` — the name a stylesheet selector uses.

    A ``QuickSwitcher`` is styled by the ``QDialog`` rule, so what matters is the
    first class in the MRO that Qt itself defines, not our subclass's name.
    """
    for base in type(widget).__mro__:
        if base.__module__.startswith("PySide6"):
            return base.__name__
    return type(widget).__name__


def _classes_on_screen(qapp, repo) -> set[str]:
    """Every Qt widget class reachable from the window and each dialog."""
    window = MainWindow()
    window.bind_autosave(repo)
    note = repo.create_note(title="Sample", body="# Body\n\nsome text")
    window.load_note(note)

    roots = [
        window,
        SettingsDialog(DEFAULT_SETTINGS),
        QuickSwitcher([note]),
        ToolPalette(),
        TagEditorDialog(repo, note.id),
        ImportWizard(repo),
        UnlockDialog(os.path.join("nonexistent", "vault.db")),
    ]

    names: set[str] = set()
    for root in roots:
        names.add(_qt_class_name(root))
        for child in root.findChildren(QWidget):
            names.add(_qt_class_name(child))
    return names


def test_every_widget_class_on_screen_is_styled_by_the_dark_theme(qapp, repo):
    stylesheet = load_stylesheet("dark")
    unstyled = sorted(
        name
        for name in _classes_on_screen(qapp, repo)
        if name not in _NO_RULE_NEEDED and name not in stylesheet
    )
    assert not unstyled, (
        "these widget classes appear in the app but have no rule in "
        f"resources/dark.qss, so they fall back to the native light style: {unstyled}"
    )


def test_the_tab_bar_is_styled_with_a_readable_selected_state(qapp):
    """The reported bug: the tab strip had no rules at all."""
    stylesheet = load_stylesheet("dark")
    assert "QTabBar::tab" in stylesheet
    assert "QTabBar::tab:selected" in stylesheet
    # The selected tab must set an explicit colour rather than inheriting one.
    selected = stylesheet.split("QTabBar::tab:selected")[1].split("}")[0]
    assert "color:" in selected


def test_the_light_theme_stays_the_native_look(qapp):
    """Light is the absence of a stylesheet; the audit only applies to dark."""
    assert load_stylesheet("light") == ""
    assert set(available_themes()) == {"light", "dark"}


def test_applying_a_theme_repaints_the_tab_close_buttons(qapp, repo):
    """Qt paints the close glyph from the native style — dark on dark (#98)."""
    window = MainWindow()
    window.bind_autosave(repo)
    note = repo.create_note(title="n", body="body")
    window.load_note(note)

    window.apply_theme("dark")

    tab_bar = window.tabbed_editor._tabs.tabBar()
    from PySide6.QtWidgets import QTabBar

    button = tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide)
    assert button is not None, "the tab has no close button"
    assert button.objectName() == "tabCloseButton"
    assert not button.icon().isNull(), "the close button has no painted glyph"


def test_a_tab_close_button_closes_its_own_tab_after_reordering(qapp, repo):
    """Bound to the widget, not the index: closing the first tab renumbers the rest."""
    from PySide6.QtWidgets import QTabBar

    window = MainWindow()
    window.bind_autosave(repo)
    first = repo.create_note(title="first", body="a")
    second = repo.create_note(title="second", body="b")
    window.load_note(first)
    window.load_note(second)
    window.apply_theme("dark")

    tab_bar = window.tabbed_editor._tabs.tabBar()
    tab_bar.tabButton(0, QTabBar.ButtonPosition.RightSide).click()

    assert window.tabbed_editor.count() == 1
    assert window.tabbed_editor.tab_for_note(second.id) is not None
    assert window.tabbed_editor.tab_for_note(first.id) is None


def test_tabs_opened_after_a_theme_change_match_that_theme(qapp, repo):
    from PySide6.QtWidgets import QTabBar

    window = MainWindow()
    window.bind_autosave(repo)
    window.apply_theme("dark")
    note = repo.create_note(title="later", body="x")
    window.load_note(note)

    button = window.tabbed_editor._tabs.tabBar().tabButton(
        0, QTabBar.ButtonPosition.RightSide
    )
    assert button is not None and not button.icon().isNull()


def test_the_dock_title_buttons_are_repainted_for_the_theme(qapp, repo):
    from PySide6.QtWidgets import QAbstractButton

    window = MainWindow()
    window.bind_autosave(repo)
    window.apply_theme("dark")

    buttons = [
        button
        for dock in (window.dock_notebooks, window.dock_notelist, window.dock_preview)
        for button in dock.findChildren(QAbstractButton)
        if button.objectName().startswith("qt_dockwidget_")
    ]
    assert buttons, "no dock title-bar buttons found to repaint"
    assert all(not button.icon().isNull() for button in buttons)


def test_the_painted_icons_differ_between_themes(qapp):
    assert glyph_color("dark") == DARK_GLYPH
    assert glyph_color("light") == LIGHT_GLYPH
    assert glyph_color("dark") != glyph_color("light")


def test_the_painted_icons_are_not_blank(qapp):
    for icon in (cross_icon("#ffffff"), float_icon("#ffffff")):
        assert not icon.isNull()
        image = icon.pixmap(12, 12).toImage()
        painted = sum(
            1
            for x in range(image.width())
            for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0
        )
        assert painted > 4, "the glyph is effectively empty"
