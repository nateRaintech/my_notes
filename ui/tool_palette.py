"""The Ctrl+Shift+T tool palette: find any of the ~60 tools by typing.

Deliberately the same shape as the Ctrl+P quick-switcher — a search box over a
ranked list — because it is the same interaction applied to a different corpus,
and reusing the pattern means one thing to learn instead of two. The ranking is
the same Qt-free :mod:`core.fuzzy` matcher, scoring against each tool's name,
category, and keywords together, which is what lets "fmt json" find *Format
JSON* and "guid" find *UUID (v4)*.

A menu of sixty tools is unusable; this is what keeps the suite navigable.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` never imports
this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.tools import ALL_TOOLS, Tool, search

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

WINDOW_TITLE = "Tools"
_DEFAULT_SIZE = (560, 420)


def _label(tool: Tool) -> str:
    """The row's text: the tool, its category, and what it does."""
    return f"{tool.name}    ·  {tool.category}  —  {tool.description}"


class ToolPalette(QDialog):
    """Fuzzy tool finder.

    On accept, :attr:`selected_tool` holds the chosen :class:`~core.tools.Tool`
    (``None`` while open or after a cancel).

    Test seams mirror :class:`ui.quick_switcher.QuickSwitcher`: set
    :attr:`search_input` text to refilter, read :attr:`results`, and call
    :meth:`accept_selection` / :meth:`select_next` / :meth:`select_previous`
    without running the modal event loop.
    """

    def __init__(
        self, tools: tuple[Tool, ...] = ALL_TOOLS, *, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._tools = tuple(tools)
        self.selected_tool: Tool | None = None

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*_DEFAULT_SIZE)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search tools…  (try 'json', 'sort', 'b64')")
        self.search_input.setClearButtonEnabled(True)
        layout.addWidget(self.search_input)

        self.results = QListWidget()
        layout.addWidget(self.results)

        self.search_input.textChanged.connect(self._refilter)
        self.search_input.returnPressed.connect(self.accept_selection)
        self.results.itemActivated.connect(lambda _item: self.accept_selection())
        self.search_input.installEventFilter(self)

        self._refilter()  # every tool, best row selected

    def current_tool(self) -> Tool | None:
        """The :class:`~core.tools.Tool` for the highlighted row, or ``None``."""
        item = self.results.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def accept_selection(self) -> bool:
        """Set :attr:`selected_tool` from the current row and accept the dialog.

        Returns ``False`` and stays open when nothing is selected — the query
        matched no tool — mirroring the quick-switcher.
        """
        tool = self.current_tool()
        if tool is None:
            return False
        self.selected_tool = tool
        self.accept()
        return True

    def select_next(self) -> None:
        """Move the highlight down one row, wrapping past the end."""
        self._move_selection(1)

    def select_previous(self) -> None:
        """Move the highlight up one row, wrapping past the start."""
        self._move_selection(-1)

    def _move_selection(self, delta: int) -> None:
        count = self.results.count()
        if count == 0:
            return
        row = max(self.results.currentRow(), 0)
        self.results.setCurrentRow((row + delta) % count)

    def _refilter(self, _text: str | None = None) -> None:
        """Rebuild the list, ranked by fuzzy match against the query."""
        query = self.search_input.text().strip()
        ranked = (
            search(query) if query else list(self._tools)
        )
        # `search` ranks the whole registry; honour a caller-supplied subset.
        if self._tools != ALL_TOOLS:
            allowed = {tool.id for tool in self._tools}
            ranked = [tool for tool in ranked if tool.id in allowed]

        self.results.clear()
        for tool in ranked:
            item = QListWidgetItem(_label(tool))
            item.setData(Qt.ItemDataRole.UserRole, tool)
            item.setToolTip(tool.description)
            self.results.addItem(item)
        if self.results.count() > 0:
            self.results.setCurrentRow(0)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Route Up/Down in the search box to results-list navigation."""
        if obj is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.select_next()
                return True
            if key == Qt.Key.Key_Up:
                self.select_previous()
                return True
        return super().eventFilter(obj, event)
