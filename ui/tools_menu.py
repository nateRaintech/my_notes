"""The Tools menu and the editor's right-click menu, both built from the registry.

Neither menu has a hand-written list of tools: both walk
:data:`core.tools.ALL_TOOLS`, grouped by :data:`core.tools.CATEGORIES`. That is
what keeps the surfaces from drifting — a tool added to the registry appears in
the menu bar, in the context menu, and in the palette at once, and one deleted
disappears from all three.

**Ownership matters here.** Neither ``QMenu.addMenu(str)`` nor
``QMenu.addAction(QAction*)`` gives the C++ side ownership of what it returns or
receives, so a submenu or action left unparented is owned by Python and collected
as soon as the local goes out of scope. The result is not an error — it is a menu
that quietly renders empty. Everything built here is therefore parented to the
menu it belongs to.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from core.tools import ALL_TOOLS, CATEGORIES, Tool, tools_in

#: Categories common enough to also sit in the editor's right-click menu. The
#: full set lives in the menu bar and the palette; the context menu stays short
#: enough to scan without scrolling.
_CONTEXT_CATEGORIES: tuple[str, ...] = ("JSON", "Case", "Lines")


def _add_tool_action(menu: QMenu, tool: Tool, invoke: Callable[[Tool], None]) -> QAction:
    """Add one tool to ``menu`` as an action that invokes it."""
    action = QAction(tool.name, menu)  # parented — see the module docstring
    action.setStatusTip(tool.description)
    action.setToolTip(tool.description)
    action.setData(tool.id)
    # The tool is bound as a default argument: a closure over the loop variable
    # would leave every action invoking whichever tool the loop ended on.
    action.triggered.connect(lambda _checked=False, t=tool: invoke(t))
    menu.addAction(action)
    return action


def populate_tools_menu(
    menu: QMenu,
    invoke: Callable[[Tool], None],
    *,
    categories: tuple[str, ...] = CATEGORIES,
) -> QMenu:
    """Fill ``menu`` with one submenu per category, in registry order."""
    for category in categories:
        tools = tools_in(category)
        if not tools:
            continue
        submenu = QMenu(category, menu)  # parented — see the module docstring
        menu.addMenu(submenu)
        for tool in tools:
            _add_tool_action(submenu, tool, invoke)
    return menu


def build_tools_menu(
    menu_bar, invoke: Callable[[Tool], None], *, title: str = "&Tools"
) -> QMenu:
    """Create the menu-bar "Tools" menu and populate it."""
    return populate_tools_menu(menu_bar.addMenu(title), invoke)


def build_context_menu_extras(
    parent: QWidget,
    invoke: Callable[[Tool], None],
    *,
    palette: Callable[[], None] | None = None,
) -> QMenu:
    """Build the "Tools" submenu appended to the editor's right-click menu.

    Carries the everyday categories plus an "All tools…" entry that opens the
    palette, so the long tail stays one keystroke away without making the
    right-click menu unusable.
    """
    submenu = QMenu("Tools", parent)
    populate_tools_menu(submenu, invoke, categories=_CONTEXT_CATEGORIES)
    if palette is not None:
        submenu.addSeparator()
        action = QAction("All tools…", submenu)
        action.setToolTip("Search every tool by name")
        action.triggered.connect(lambda _checked=False: palette())
        submenu.addAction(action)
    return submenu


def tool_ids_in_menu(menu: QMenu) -> list[str]:
    """Every tool id reachable from ``menu``, including its submenus.

    Exists for the test that asserts the menu covers the registry — walking the
    built menu is the only way to check the two have not drifted.
    """
    found: list[str] = []
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            found.extend(tool_ids_in_menu(submenu))
        elif isinstance(action.data(), str):
            found.append(action.data())
    return found


def registry_tool_ids() -> list[str]:
    """Every registered tool id, for symmetry with :func:`tool_ids_in_menu`."""
    return [tool.id for tool in ALL_TOOLS]
