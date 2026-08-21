"""Tests for the three tool surfaces: menu, context menu, palette, and window wiring.

The point of the registry design is that the surfaces cannot drift from each
other or from the tool list. The tests that matter most here are therefore the
*coverage* ones — the menu offers every registered tool, the palette can reach
every registered tool — because those are what break silently when someone adds
a tool and forgets a surface.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository
from core.tools import ALL_TOOLS, get_tool

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMenu  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402
from ui.tabbed_editor import TabbedEditor  # noqa: E402
from ui.tool_palette import ToolPalette  # noqa: E402
from ui.tools_menu import (  # noqa: E402
    build_context_menu_extras,
    populate_tools_menu,
    registry_tool_ids,
    tool_ids_in_menu,
)


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


@pytest.fixture
def window(qapp, repo):
    """A fresh window bound to an in-memory vault.

    Not explicitly deleted, matching the convention in ``test_panels.py``:
    ``deleteLater()`` only *schedules* destruction, and with no event loop
    running the widget is then torn down during interpreter shutdown, after the
    vault connection has already closed.
    """
    win = MainWindow()
    win.bind_autosave(repo)
    return win


# ---------------------------------------------------------------------------
# The menu
# ---------------------------------------------------------------------------

def test_the_menu_offers_every_registered_tool(qapp):
    """If a tool is added to the registry, the menu gets it for free."""
    menu = QMenu()
    invoked = []
    populate_tools_menu(menu, invoked.append)

    assert sorted(tool_ids_in_menu(menu)) == sorted(registry_tool_ids())


def test_the_menu_groups_tools_into_one_submenu_per_category(qapp):
    menu = QMenu()
    populate_tools_menu(menu, lambda _tool: None)

    submenus = [a.menu() for a in menu.actions() if a.menu() is not None]
    assert len(submenus) == len({tool.category for tool in ALL_TOOLS})
    for submenu in submenus:
        assert submenu.actions(), submenu.title()


def test_each_menu_action_invokes_its_own_tool(qapp):
    """Guards the classic closure-over-the-loop-variable bug: without binding,
    every action would run whichever tool the loop happened to end on."""
    menu = QMenu()
    invoked = []
    populate_tools_menu(menu, invoked.append)

    actions = []
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            actions.extend(submenu.actions())

    for action in actions:
        action.trigger()

    assert [tool.id for tool in invoked] == [a.data() for a in actions]


def test_menu_actions_carry_their_description_as_a_tooltip(qapp):
    menu = QMenu()
    populate_tools_menu(menu, lambda _tool: None)
    for action in menu.actions():
        submenu = action.menu()
        if submenu is None:
            continue
        for entry in submenu.actions():
            assert entry.toolTip() == get_tool(entry.data()).description


def test_the_context_submenu_is_short_but_reaches_the_rest(qapp):
    menu = QMenu()
    opened = []
    submenu = build_context_menu_extras(menu, lambda _t: None, palette=lambda: opened.append(1))

    ids = tool_ids_in_menu(submenu)
    assert ids, "the context menu offers no tools"
    assert len(ids) < len(registry_tool_ids()), "the context menu should be a subset"

    all_tools_action = [a for a in submenu.actions() if a.text() == "All tools…"]
    assert all_tools_action, "no escape hatch to the full list"
    all_tools_action[0].trigger()
    assert opened == [1]


# ---------------------------------------------------------------------------
# The palette
# ---------------------------------------------------------------------------

def test_the_palette_opens_showing_every_tool(qapp):
    palette = ToolPalette()
    assert palette.results.count() == len(ALL_TOOLS)


def test_the_palette_filters_as_you_type(qapp):
    palette = ToolPalette()
    palette.search_input.setText("base64")

    assert 0 < palette.results.count() < len(ALL_TOOLS)
    assert all(
        "b64" in palette.results.item(i).data(Qt.ItemDataRole.UserRole).id
        for i in range(palette.results.count())
    )


def test_the_palette_finds_a_tool_by_keyword_not_just_by_name(qapp):
    palette = ToolPalette()
    palette.search_input.setText("guid")
    assert palette.current_tool() is get_tool("insert.uuid")


def test_the_palette_auto_selects_the_best_match_so_enter_just_works(qapp):
    palette = ToolPalette()
    palette.search_input.setText("format json")
    assert palette.current_tool() is get_tool("json.format")


def test_accepting_the_palette_records_the_chosen_tool(qapp):
    palette = ToolPalette()
    palette.search_input.setText("minify json")

    assert palette.accept_selection() is True
    assert palette.selected_tool is get_tool("json.minify")


def test_the_palette_stays_open_when_nothing_matches(qapp):
    palette = ToolPalette()
    palette.search_input.setText("zzzzzzzzznotatool")

    assert palette.results.count() == 0
    assert palette.accept_selection() is False
    assert palette.selected_tool is None


def test_palette_navigation_wraps_in_both_directions(qapp):
    palette = ToolPalette()
    count = palette.results.count()

    palette.select_previous()
    assert palette.results.currentRow() == count - 1
    palette.select_next()
    assert palette.results.currentRow() == 0


def test_the_palette_can_reach_every_registered_tool(qapp):
    """Typing a tool's exact name must surface that tool — no tool is unreachable."""
    palette = ToolPalette()
    for tool in ALL_TOOLS:
        palette.search_input.setText(tool.name)
        found = {
            palette.results.item(i).data(Qt.ItemDataRole.UserRole).id
            for i in range(palette.results.count())
        }
        assert tool.id in found, f"{tool.name} is unreachable from the palette"


# ---------------------------------------------------------------------------
# Window wiring
# ---------------------------------------------------------------------------

def test_the_window_has_a_tools_menu_covering_the_registry(window):
    assert sorted(tool_ids_in_menu(window.tools_menu)) == sorted(registry_tool_ids())


def test_the_window_binds_the_palette_shortcut(window):
    assert window.tool_palette_shortcut.key().toString() == "Ctrl+Shift+T"


def test_running_a_tool_from_the_window_edits_the_active_tab(window, repo):
    note = repo.create_note(title="cfg", body='{"b":2,"a":1}')
    window.load_note(note)

    assert window.run_tool(get_tool("json.format")) is True

    assert window.editor.markdown() == '{\n  "b": 2,\n  "a": 1\n}'


def test_running_a_tool_refreshes_the_live_preview(window, repo):
    note = repo.create_note(title="cfg", body="hello world")
    window.load_note(note)

    window.run_tool(get_tool("case.upper"))

    assert window.editor.markdown() == "HELLO WORLD"
    assert "HELLO WORLD" in window.preview.toPlainText()


def test_running_a_tool_refreshes_the_word_count(window, repo):
    note = repo.create_note(title="cfg", body="hello world")
    window.load_note(note)
    assert "2 words" in window.word_count_label.text()

    window.run_tool(get_tool("lines.number"))  # prepends "1." — a third word

    assert "3 words" in window.word_count_label.text()


def test_running_a_tool_retitles_the_tab_it_changed(window, repo):
    note = repo.create_note(title="", body="hello world")
    window.load_note(note)

    window.run_tool(get_tool("case.upper"))

    assert window.tabbed_editor._tabs.tabText(0) == "HELLO WORLD"


def test_running_a_tool_reports_to_the_status_bar(window, repo):
    note = repo.create_note(title="cfg", body='{"a":1}')
    window.load_note(note)

    window.run_tool(get_tool("json.minify"))

    assert window.statusBar().currentMessage()


def test_a_failing_tool_from_the_window_leaves_the_note_alone(window, repo):
    note = repo.create_note(title="prose", body="not json at all")
    window.load_note(note)

    assert window.run_tool(get_tool("json.format")) is False

    assert window.editor.markdown() == "not json at all"
    assert "Invalid JSON" in window.statusBar().currentMessage()


def test_running_a_tool_with_no_note_open_is_a_message_not_a_crash(window):
    assert window.run_tool(get_tool("json.format")) is False
    assert "Open a note first" in window.statusBar().currentMessage()


def test_the_palette_choice_is_applied_to_the_editor(window, repo, monkeypatch):
    """Drives open_tool_palette without the modal loop, mirroring the
    quick-switcher's _make_quick_switcher seam."""
    note = repo.create_note(title="cfg", body='{"b":2,"a":1}')
    window.load_note(note)

    class FakePalette:
        selected_tool = get_tool("json.minify")

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(window, "_make_tool_palette", lambda: FakePalette())
    window.open_tool_palette()

    assert window.editor.markdown() == '{"b":2,"a":1}'


def test_cancelling_the_palette_changes_nothing(window, repo, monkeypatch):
    note = repo.create_note(title="cfg", body='{"b":2,"a":1}')
    window.load_note(note)

    class CancelledPalette:
        selected_tool = None

        def exec(self):
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(window, "_make_tool_palette", lambda: CancelledPalette())
    window.open_tool_palette()

    assert window.editor.markdown() == '{"b":2,"a":1}'


def test_the_editor_routes_right_clicks_up_to_its_owner(qapp, repo):
    """The tab re-emits its source's context-menu request so the window can add
    the Tools submenu without the tab knowing what a tool is.

    Driven through a bare :class:`TabbedEditor` rather than the window: the
    window's own handler pops the menu up, and showing a menu runs a modal event
    loop that never returns in a headless test.
    """
    note = repo.create_note(title="n", body="x")
    editor = TabbedEditor(repo)
    tab = editor.open(note)

    seen = []
    editor.tab_context_menu_requested.connect(seen.append)
    tab.source.customContextMenuRequested.emit(QPoint(3, 4))

    assert seen == [QPoint(3, 4)]


def test_the_editor_surface_asks_for_a_custom_context_menu(qapp, repo):
    """Without CustomContextMenu the right-click never reaches us at all."""
    note = repo.create_note(title="n", body="x")
    tab = TabbedEditor(repo).open(note)
    assert (
        tab.source.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    )


def test_the_editor_context_menu_keeps_the_standard_entries_and_adds_tools(
    window, repo
):
    """Qt's own Undo/Cut/Copy/Paste must survive — the tools are appended to the
    standard menu, not a replacement for it."""
    note = repo.create_note(title="n", body='{"a":1}')
    window.load_note(note)

    menu = window.build_editor_context_menu(window.editor.source)

    labels = [action.text() for action in menu.actions()]
    assert any("Undo" in label for label in labels), labels
    assert any("Paste" in label for label in labels), labels
    assert any(label == "Tools" for label in labels), labels


def test_the_editor_context_menu_tools_act_on_the_note(window, repo):
    note = repo.create_note(title="n", body='{"b":2,"a":1}')
    window.load_note(note)
    menu = window.build_editor_context_menu(window.editor.source)

    tools_submenu = next(a.menu() for a in menu.actions() if a.text() == "Tools")
    format_action = next(
        entry
        for submenu in (a.menu() for a in tools_submenu.actions() if a.menu())
        for entry in submenu.actions()
        if entry.data() == "json.format"
    )
    format_action.trigger()

    assert window.editor.markdown() == '{\n  "b": 2,\n  "a": 1\n}'
